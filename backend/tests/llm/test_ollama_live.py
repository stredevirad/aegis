"""Live adjudication against a local Ollama (marker ``live``; skipped when Ollama is not running).

Model: ``AEGIS_LIVE_OLLAMA_MODEL`` (default ``llama3.2:1b``). This verifies the adapter end to
end — egress client, structured output via ``format``, parsing, validation — on a real model.
It does **not** assert that the model's assessment is accepted: small local models frequently
produce outputs the validator rejects, and that is the designed outcome (the deterministic
verdict stands). The observed result is printed for the verification ledger.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from aegis.config import Profile, Settings
from aegis.integrations.egress import Credential, EgressClient
from aegis.integrations.registry import Connector, ConnectorKind
from aegis.llm import prompt as prompts
from aegis.llm.providers import Message, ModelCall, Provider, ProviderError, prompt_budget_chars
from aegis.llm.schema import OUTPUT_SCHEMA, Adjudication
from aegis.llm.validate import validate
from tests.llm.test_validate import analysis

pytestmark = pytest.mark.live
MODEL = os.environ.get("AEGIS_LIVE_OLLAMA_MODEL", "llama3.2:1b")


def _ollama_up() -> bool:
    try:
        tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2).json()
        return any(m.get("name") == MODEL for m in tags.get("models", []))
    except (httpx.HTTPError, ValueError):
        return False


@pytest.mark.parametrize("scenario", ["s05-deployment-regression", "s10-prompt-injection-in-logs"])
async def test_live_ollama_adjudication(scenario):
    if not _ollama_up():
        pytest.skip(f"Ollama with {MODEL} not running")
    settings = Settings(profile=Profile.DEVELOPMENT)
    conn = Connector(
        id="ollama-local",
        kind=ConnectorKind.LLM,
        url="http://127.0.0.1:11434",
        orgs=["acme"],
        allow_loopback=True,
        timeout_seconds=600,
        options={"api": "ollama", "locality": "local", "default_model": MODEL},
    )
    egress = EgressClient(settings)
    a = analysis(scenario)
    built = prompts.build(a, max_chars=prompt_budget_chars(conn, 900))
    try:
        provider = Provider(conn, Credential("none"), egress)
        try:
            result = await provider.complete(
                ModelCall(MODEL, built.system, [Message("user", built.user)], OUTPUT_SCHEMA, 900)
            )
        except ProviderError as exc:
            pytest.fail(f"provider error {exc.category}: {exc}")
    finally:
        await egress.aclose()
    try:
        parsed = Adjudication.model_validate(json.loads(result.text))
    except ValueError as exc:  # e.g. output cut off by the token cap: invalid JSON
        print(  # noqa: T201 - recorded in the verification ledger
            f"\n[{scenario}] model={MODEL} latency_ms={result.latency_ms} "
            f"prompt_tokens={result.prompt_tokens} completion_tokens={result.completion_tokens} "
            f"output_invalid={type(exc).__name__}"
        )
        assert result.prompt_tokens is not None and result.prompt_tokens > 2100
        return  # the adjudicator turns this into a repair attempt, then the deterministic fallback
    checked = validate(parsed, a)
    print(  # noqa: T201 - recorded in the verification ledger
        f"\n[{scenario}] model={MODEL} latency_ms={result.latency_ms} prompt_tokens={result.prompt_tokens} "
        f"completion_tokens={result.completion_tokens} accepted={checked.accepted} "
        f"model_class={parsed.classification}/{parsed.support} deterministic={a.verdict.classification.value}/"
        f"{a.verdict.support.value} errors={checked.errors[:3]}"
    )
    # the whole prompt reached the model (no silent truncation by a default context window)
    assert result.prompt_tokens is not None and result.prompt_tokens > 2100
    if checked.accepted:  # whatever the model said, it can never exceed the ceiling
        from aegis.core.result import CLASSIFICATION_STRENGTH, Classification

        assert (
            CLASSIFICATION_STRENGTH[Classification(checked.final["classification"])]
            <= CLASSIFICATION_STRENGTH[a.verdict.classification]
        )
