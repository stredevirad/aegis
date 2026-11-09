"""Model providers behind one interface (ADR-004/009, roadmap M3).

All calls go through the egress client to an operator-registered ``llm`` connector (the
destination is never chosen by a tenant or a model). Connector options:

* ``api``: ``ollama`` | ``openai`` (OpenAI-compatible chat completions) | ``anthropic``
* ``locality``: ``local`` | ``cloud`` — cloud providers need the organization's explicit
  opt-in (``ai_allow_cloud``); there is **no fallback** from one provider to another
* ``default_model``, optional ``allowed_models`` (the operator's allowlist), ``context_window``

Structured output: Ollama ``format`` = JSON schema; OpenAI ``response_format`` = strict
``json_schema``; Anthropic forced tool use (``tool_choice`` = the single ``submit_assessment``
tool whose input schema is the output schema). Reasoning/"thinking" output is never
requested (Ollama ``think: false``) and is discarded if present.

Failures raise :class:`ProviderError` with a stable category; a per-connector circuit breaker
short-circuits calls after repeated failures so a dead provider does not consume the
investigation's time budget.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from aegis.integrations.egress import Credential, EgressClient, EgressDenied, EgressError
from aegis.integrations.registry import Connector

Api = Literal["ollama", "openai", "anthropic"]
TOOL_NAME = "submit_assessment"


class ProviderError(Exception):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class Message:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ModelCall:
    model: str
    system: str
    messages: list[Message]
    schema: dict[str, Any]
    max_output_tokens: int = 1200


@dataclass(frozen=True)
class ModelResult:
    text: str  # the JSON object as text
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int


@dataclass
class CircuitBreaker:
    threshold: int = 3
    reset_seconds: float = 60.0
    failures: int = 0
    opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if time.monotonic() - self.opened_at >= self.reset_seconds:
            self.opened_at = None  # half-open: one trial call
            self.failures = self.threshold - 1
            return True
        return False

    def record(self, ok: bool) -> None:
        if ok:
            self.failures, self.opened_at = 0, None
            return
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = time.monotonic()


_BREAKERS: dict[str, CircuitBreaker] = {}
# Failures that say nothing about the provider's health (they do not trip the breaker).
NOT_PROVIDER_HEALTH = frozenset(
    {"invalid_output", "refused", "client_error", "denied", "misconfigured"}
)


def breaker(connector_id: str) -> CircuitBreaker:
    return _BREAKERS.setdefault(connector_id, CircuitBreaker())


def provider_options(
    connector: Connector,
) -> tuple[Api, Literal["local", "cloud"], str | None, list[str]]:
    o = connector.options
    api = o.get("api")
    if api not in ("ollama", "openai", "anthropic"):
        raise ProviderError(
            "misconfigured", "llm connector option 'api' must be ollama, openai or anthropic"
        )
    locality = o.get("locality")
    if locality not in ("local", "cloud"):
        raise ProviderError(
            "misconfigured", "llm connector option 'locality' must be local or cloud"
        )
    allowed = [str(m) for m in (o.get("allowed_models") or [])]
    default = o.get("default_model")
    return api, locality, str(default) if default else None, allowed


@dataclass
class Provider:
    connector: Connector
    credential: Credential
    egress: EgressClient
    # Breakers are per integration (organization + connector): a connector may be shared by
    # several organizations, and one tenant's bad credential must not trip it for the others.
    breaker_key: str | None = None
    api: Api = field(init=False)
    locality: Literal["local", "cloud"] = field(init=False)

    def __post_init__(self) -> None:
        self.api, self.locality, _, _ = provider_options(self.connector)

    async def complete(self, call: ModelCall) -> ModelResult:
        cb = breaker(self.breaker_key or self.connector.id)
        if not cb.allow():
            raise ProviderError(
                "circuit_open",
                f"provider {self.connector.id} is temporarily disabled after repeated failures",
            )
        started = time.perf_counter()
        try:
            result = await {
                "ollama": self._ollama,
                "openai": self._openai,
                "anthropic": self._anthropic,
            }[self.api](call)
        except ProviderError as exc:
            if exc.category not in NOT_PROVIDER_HEALTH:
                cb.record(False)
            raise
        cb.record(True)
        return ModelResult(
            result[0], result[1], result[2], int((time.perf_counter() - started) * 1000)
        )

    async def _post(
        self, path: str, body: dict[str, Any], headers: dict[str, str] | None = None
    ) -> Any:
        try:
            resp = await self.egress.request(
                self.connector,
                "POST",
                path,
                json_body=body,
                credential=self.credential,
                headers=headers,
                retries=0,
            )
        except EgressDenied as exc:
            raise ProviderError("denied", exc.reason) from None
        except EgressError as exc:
            if exc.status is not None and 400 <= exc.status < 500 and exc.status != 429:
                # the request or credential was rejected: not a sign the provider is down
                raise ProviderError("client_error", str(exc)) from None
            raise ProviderError(exc.category, str(exc)) from None
        try:
            return resp.json()
        except EgressError:
            raise ProviderError("invalid_response", "provider returned non-JSON") from None

    async def _ollama(self, call: ModelCall) -> tuple[str, int | None, int | None]:
        body = {
            "model": call.model,
            "stream": False,
            "think": False,
            "format": call.schema,
            "messages": [{"role": "system", "content": call.system}]
            + [{"role": m.role, "content": m.content} for m in call.messages],
            "options": {
                "temperature": 0,
                "num_predict": call.max_output_tokens,
                # Always explicit: Ollama's default context (2048 tokens) silently truncates
                # the prompt — observed live with llama3.2:1b (prompt_eval_count 2050).
                "num_ctx": context_window(self.connector),
            },
        }
        data = await self._post("/api/chat", body)
        content = ((data or {}).get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("invalid_output", "empty model output")
        return content, _int(data.get("prompt_eval_count")), _int(data.get("eval_count"))

    async def _openai(self, call: ModelCall) -> tuple[str, int | None, int | None]:
        body = {
            "model": call.model,
            "temperature": 0,
            "max_tokens": call.max_output_tokens,
            "messages": [{"role": "system", "content": call.system}]
            + [{"role": m.role, "content": m.content} for m in call.messages],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "aegis_adjudication",
                    "strict": True,
                    "schema": call.schema,
                },
            },
        }
        data = await self._post("/v1/chat/completions", body)
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise ProviderError("invalid_response", "unexpected response shape") from None
        if msg.get("refusal"):
            raise ProviderError("refused", "the model refused")
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("invalid_output", "empty model output")
        usage = data.get("usage") or {}
        return content, _int(usage.get("prompt_tokens")), _int(usage.get("completion_tokens"))

    async def _anthropic(self, call: ModelCall) -> tuple[str, int | None, int | None]:
        body = {
            "model": call.model,
            "max_tokens": call.max_output_tokens,
            "temperature": 0,
            "system": call.system,
            "messages": [{"role": m.role, "content": m.content} for m in call.messages],
            "tools": [
                {
                    "name": TOOL_NAME,
                    "description": "Submit the adjudication.",
                    "input_schema": call.schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        data = await self._post("/v1/messages", body, headers={"anthropic-version": "2023-06-01"})
        blocks = (data or {}).get("content") or []
        tool = next(
            (
                b
                for b in blocks
                if isinstance(b, dict)
                and b.get("type") == "tool_use"
                and b.get("name") == TOOL_NAME
            ),
            None,
        )
        if tool is None or not isinstance(tool.get("input"), dict):
            if (data or {}).get("stop_reason") == "refusal":
                raise ProviderError("refused", "the model refused")
            raise ProviderError("invalid_output", "no tool output")
        usage = data.get("usage") or {}
        return (
            json.dumps(tool["input"]),
            _int(usage.get("input_tokens")),
            _int(usage.get("output_tokens")),
        )

    async def health(self) -> tuple[bool, str]:
        path = {"ollama": "/api/version", "openai": "/v1/models", "anthropic": None}[self.api]
        if path is None:
            return True, "no side-effect-free health endpoint; checked on first call"
        try:
            await self.egress.request(
                self.connector, "GET", path, credential=self.credential, retries=0
            )
        except (EgressDenied, EgressError) as exc:
            return False, str(exc)[:200]
        return True, "reachable"


def _int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


DEFAULT_CONTEXT_WINDOW = 8192


def context_window(connector: Connector) -> int:
    """Tokens of context the operator configured for this model (default 8192)."""
    try:
        return max(
            2048,
            min(int(connector.options.get("context_window", DEFAULT_CONTEXT_WINDOW)), 1_000_000),
        )
    except (TypeError, ValueError):
        return DEFAULT_CONTEXT_WINDOW


def prompt_budget_chars(connector: Connector, max_output_tokens: int) -> int:
    """Characters of data the prompt may carry so prompt + output fit the context window.
    Conservative: ~3 characters per token for JSON-heavy text, 800 tokens for instructions."""
    tokens = context_window(connector) - max_output_tokens - 800
    return max(4_000, min(tokens * 3, 200_000))
