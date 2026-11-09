"""Prompt construction (untrusted-data channel, redaction, bounds) and provider adapters."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from aegis.core.model import LogRecord, Provenance, Severity
from aegis.integrations.egress import Credential, EgressError, EgressResponse
from aegis.integrations.registry import Connector, ConnectorKind
from aegis.llm import prompt as prompts
from aegis.llm.providers import CircuitBreaker, Message, ModelCall, Provider, ProviderError, breaker
from aegis.llm.schema import OUTPUT_SCHEMA
from tests.llm.test_validate import analysis

# --- prompt -----------------------------------------------------------------------------------


def test_prompt_separates_instructions_from_untrusted_data():
    a = analysis("s10-prompt-injection-in-logs")
    p = prompts.build(a)
    assert "UNTRUSTED" in p.system and "never raise" in p.system.lower()
    marker = f"AEGIS-DATA-{p.nonce}"
    assert p.user.count(marker) == 2
    body = p.user.split(marker)[1].strip()
    data = json.loads(body)  # the whole block is one JSON document
    assert data["ceiling"] == {
        "classification": a.verdict.classification.value,
        "support": a.verdict.support.value,
    }
    # hostile log text is present only as JSON-escaped data: no raw newline can forge a section
    assert "\n" not in body
    assert p.nonce not in json.dumps(data)


def test_prompt_redacts_and_bounds():
    from aegis.core import analyze
    from aegis.core.model import AnalysisRequest, TelemetrySnapshot, TimeWindow

    t0 = datetime(2026, 1, 1, 12, tzinfo=UTC)
    prov = Provenance(provider="loki", collected_at=t0)
    logs = [
        LogRecord(
            ts=t0.replace(minute=m % 60, hour=12 + m // 60),
            service="svc",
            severity=Severity.ERROR,
            body=f"db failure password=hunter2 Authorization: Bearer abcdefghijklmnop #{m % 3}",
            instance=f"i{m % 3}",
            provenance=prov,
        )
        for m in range(30, 110)
    ]
    snap = TelemetrySnapshot(
        captured_at=t0,
        collected_window=TimeWindow(start=t0.replace(hour=11), end=t0.replace(hour=14)),
        logs=logs,
    )
    req = AnalysisRequest(
        incident=TimeWindow(start=t0.replace(hour=13), end=t0.replace(hour=14)),
        baselines=[TimeWindow(start=t0.replace(hour=11), end=t0.replace(hour=13))],
    )
    p = prompts.build(analyze(snap, req), max_evidence=5, max_chars=4000)
    assert "hunter2" not in p.user and "abcdefghijklmnop" not in p.user
    assert len(p.evidence_ids) <= 5 and len(p.user) < 6000
    assert len(p.sha256) == 64


def test_cited_evidence_is_included_first():
    a = analysis("s05-deployment-regression")
    p = prompts.build(a, max_evidence=8)
    cited = set(a.verdict.root_causes[0].supporting_evidence_ids)
    assert cited & set(p.evidence_ids) == cited & set(p.evidence_ids[: len(cited) + 8])
    assert set(a.verdict.root_causes[0].supporting_evidence_ids[:3]) <= set(p.evidence_ids)


# --- providers ----------------------------------------------------------------------------------


class CapturingEgress:
    def __init__(self, reply):
        self.reply = reply
        self.sent: list[dict] = []

    async def request(
        self, connector, method, path, *, json_body=None, headers=None, credential=None, **_
    ):
        self.sent.append(
            {"path": path, "body": json_body, "headers": headers or {}, "credential": credential}
        )
        r = self.reply(path, json_body)
        if isinstance(r, Exception):
            raise r
        return EgressResponse(200, {}, json.dumps(r).encode(), "x")


def llm(api: str, locality="local", cid=None) -> Connector:
    return Connector(
        id=cid or f"llm-{api}",
        kind=ConnectorKind.LLM,
        url="http://llm.test:1",
        orgs=["acme"],
        options={"api": api, "locality": locality, "default_model": "m"},
    )


CALL = ModelCall("m", "SYS", [Message("user", "DATA")], OUTPUT_SCHEMA, 500)


async def test_ollama_request_and_response():
    eg = CapturingEgress(
        lambda p, b: {
            "message": {"content": '{"a":1}', "thinking": "secret thoughts"},
            "prompt_eval_count": 120,
            "eval_count": 30,
        }
    )
    r = await Provider(llm("ollama"), Credential("none"), eg).complete(CALL)
    sent = eg.sent[0]
    assert (
        sent["path"] == "/api/chat"
        and sent["body"]["format"] == OUTPUT_SCHEMA
        and sent["body"]["think"] is False
    )
    assert sent["body"]["options"]["num_ctx"] == 8192  # never Ollama's silent 2048 default
    assert sent["body"]["options"]["temperature"] == 0 and sent["body"]["messages"][0] == {
        "role": "system",
        "content": "SYS",
    }
    assert (r.text, r.prompt_tokens, r.completion_tokens) == ('{"a":1}', 120, 30)
    assert "secret thoughts" not in r.text


async def test_openai_compatible_request_and_refusal():
    eg = CapturingEgress(
        lambda p, b: {
            "choices": [{"message": {"content": '{"x":2}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        }
    )
    r = await Provider(llm("openai"), Credential("bearer", token="sk-test"), eg).complete(CALL)
    rf = eg.sent[0]["body"]["response_format"]
    assert (
        eg.sent[0]["path"] == "/v1/chat/completions"
        and rf["type"] == "json_schema"
        and rf["json_schema"]["strict"] is True
    )
    assert (r.prompt_tokens, r.completion_tokens) == (5, 7)
    eg = CapturingEgress(
        lambda p, b: {"choices": [{"message": {"content": None, "refusal": "no"}}]}
    )
    with pytest.raises(ProviderError) as e:
        await Provider(llm("openai", cid="llm-openai-2"), Credential("none"), eg).complete(CALL)
    assert e.value.category == "refused"


async def test_anthropic_forced_tool_use():
    reply = {
        "content": [
            {"type": "text", "text": "ignored"},
            {"type": "tool_use", "name": "submit_assessment", "input": {"k": "v"}},
        ],
        "usage": {"input_tokens": 11, "output_tokens": 3},
        "stop_reason": "tool_use",
    }
    eg = CapturingEgress(lambda p, b: reply)
    r = await Provider(
        llm("anthropic", "cloud"),
        Credential("header", header_name="x-api-key", header_value="k"),
        eg,
    ).complete(CALL)
    sent = eg.sent[0]
    assert sent["path"] == "/v1/messages" and sent["headers"]["anthropic-version"] == "2023-06-01"
    assert sent["body"]["tool_choice"] == {"type": "tool", "name": "submit_assessment"}
    assert (
        sent["body"]["tools"][0]["input_schema"] == OUTPUT_SCHEMA
        and sent["body"]["system"] == "SYS"
    )
    assert json.loads(r.text) == {"k": "v"} and (r.prompt_tokens, r.completion_tokens) == (11, 3)


@pytest.mark.parametrize(
    ("api", "reply", "category"),
    [
        ("ollama", {"message": {"content": "  "}}, "invalid_output"),
        ("ollama", {"unexpected": True}, "invalid_output"),
        ("openai", {"choices": []}, "invalid_response"),
        ("anthropic", {"content": [{"type": "text", "text": "I think..."}]}, "invalid_output"),
        ("anthropic", {"content": [], "stop_reason": "refusal"}, "refused"),
    ],
)
async def test_malformed_provider_responses(api, reply, category):
    eg = CapturingEgress(lambda p, b: reply)
    with pytest.raises(ProviderError) as e:
        await Provider(
            llm(api, cid=f"llm-bad-{api}-{category.replace('_', '-')}"), Credential("none"), eg
        ).complete(CALL)
    assert e.value.category == category


async def test_circuit_breaker_opens_after_repeated_failures():
    eg = CapturingEgress(lambda p, b: EgressError("connect", "down"))
    p = Provider(llm("ollama", cid="llm-flaky"), Credential("none"), eg)
    for _ in range(3):
        with pytest.raises(ProviderError):
            await p.complete(CALL)
    with pytest.raises(ProviderError) as e:
        await p.complete(CALL)
    assert e.value.category == "circuit_open" and len(eg.sent) == 3
    cb = CircuitBreaker(threshold=1, reset_seconds=0)
    cb.record(False)
    assert cb.allow()  # half-open after the reset period
    assert breaker("llm-flaky").opened_at is not None


def test_provider_options_must_be_explicit():
    bad = Connector(
        id="llm-x",
        kind=ConnectorKind.LLM,
        url="http://l:1",
        orgs=["acme"],
        options={"api": "ollama"},
    )
    with pytest.raises(ProviderError):
        Provider(bad, Credential("none"), CapturingEgress(lambda *a: {}))


async def test_breaker_is_per_integration_and_ignores_client_errors():
    conn = llm("ollama", cid="llm-shared")
    bad_key = CapturingEgress(lambda p, b: EgressError("http_status", "401", status=401))
    tenant_a = Provider(
        conn, Credential("bearer", token="wrong"), bad_key, breaker_key="integration:a"
    )
    for _ in range(5):
        with pytest.raises(ProviderError) as e:
            await tenant_a.complete(CALL)
        assert e.value.category == "client_error"
    assert breaker("integration:a").opened_at is None  # credential errors are not provider health
    down = CapturingEgress(lambda p, b: EgressError("connect", "down"))
    tenant_a_down = Provider(conn, Credential("none"), down, breaker_key="integration:a2")
    for _ in range(3):
        with pytest.raises(ProviderError):
            await tenant_a_down.complete(CALL)
    assert breaker("integration:a2").opened_at is not None
    ok = CapturingEgress(lambda p, b: {"message": {"content": "{}"}})
    tenant_b = Provider(conn, Credential("none"), ok, breaker_key="integration:b")
    assert (await tenant_b.complete(CALL)).text == "{}"  # same connector, other tenant: unaffected
