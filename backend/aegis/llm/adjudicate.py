"""AI adjudication of a completed deterministic analysis (ADR-009, roadmap M3).

Policy, in order (each refusal leaves the deterministic verdict as the answer and records why):

1. ``ai_enabled`` is off by default → ``disabled``;
2. the configured integration must exist, be enabled, reference an ``llm`` connector bound to
   this organization, and decrypt under its binding → else ``unavailable``;
3. cloud connectors need ``ai_allow_cloud`` → else ``refused`` (**no fallback to any other
   provider**, ever);
4. the model must be in the connector's ``allowed_models`` when the operator set one;
5. the organization's rolling 24-hour token budget must not be exhausted → else
   ``budget_exhausted``;
6. a slot from the global pool must become free within ``llm_slot_wait_seconds`` → else
   ``unavailable`` (capacity);
7. one call; invalid output gets exactly one repair attempt; still invalid → ``invalid``.

Every call writes an ``llm_calls`` row (provider, model, locality, policy, prompt hash/size,
redaction counts, tokens, latency, status) — the record of which provider saw which
organization's redacted data, when, and under what policy (prompt §67).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from aegis import audit
from aegis.audit import Action, Actor
from aegis.config import Settings
from aegis.core.result import Analysis
from aegis.db.engine import tenant_tx
from aegis.db.tables import llm_calls, organizations
from aegis.integrations.egress import EgressClient
from aegis.integrations.registry import ConnectorKind, Registry
from aegis.integrations.store import usable_integrations
from aegis.llm import prompt as prompts
from aegis.llm import slots
from aegis.llm.providers import Message, ModelCall, Provider, ProviderError, provider_options
from aegis.llm.schema import OUTPUT_SCHEMA, Adjudication
from aegis.llm.validate import validate
from aegis.org_settings import read_settings
from aegis.security.crypto import Keyring

ProviderFactory = Callable[..., Provider]


@dataclass
class Adjudicator:
    engine: AsyncEngine
    settings: Settings
    registry: Registry
    keyring: Keyring
    egress: EgressClient
    provider_factory: ProviderFactory = Provider

    async def run(
        self,
        org_id: UUID,
        investigation_id: UUID,
        analysis: Analysis,
        holder: str,
        cancelled: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict[str, Any]:
        async with tenant_tx(self.engine, org_id) as c:
            org = read_settings(
                (
                    await c.execute(
                        select(organizations.c.settings).where(organizations.c.id == org_id)
                    )
                ).scalar_one()
            )
            if not org.ai_enabled:
                return {"status": "disabled"}
            if org.ai_integration_id is None:
                return {"status": "unavailable", "reason": "no AI provider integration selected"}
            usable, unusable = await usable_integrations(
                c, org_id, self.registry, self.keyring, frozenset({ConnectorKind.LLM})
            )
            used: int = (
                await c.execute(
                    select(
                        func.coalesce(
                            func.sum(llm_calls.c.prompt_tokens + llm_calls.c.completion_tokens), 0
                        )
                    ).where(
                        llm_calls.c.org_id == org_id,
                        llm_calls.c.started_at >= datetime.now(UTC) - timedelta(days=1),
                    )
                )
            ).scalar_one()
        integ = next((u for u in usable if u.id == org.ai_integration_id), None)
        if integ is None:
            reason = next(
                (u.reason for u in unusable if u.id == org.ai_integration_id),
                "integration missing or disabled",
            )
            return {"status": "unavailable", "reason": reason}
        try:
            api, locality, default_model, allowed = provider_options(integ.connector)
        except ProviderError as exc:
            return {"status": "unavailable", "reason": str(exc)}
        if locality == "cloud" and not org.ai_allow_cloud:
            return {
                "status": "refused",
                "reason": "cloud AI providers are not enabled for this organization",
            }
        model = org.ai_model or default_model
        if not model:
            return {"status": "unavailable", "reason": "no model configured"}
        if allowed and model not in allowed:
            return {
                "status": "refused",
                "reason": f"model {model!r} is not in the operator's allowlist",
            }
        if int(used) >= org.ai_daily_token_budget:
            return {
                "status": "budget_exhausted",
                "reason": f"{int(used)} tokens used in the last 24 h",
            }

        policy = {
            "ai_enabled": True,
            "integration_id": str(integ.id),
            "connector_id": integ.connector.id,
            "locality": locality,
            "cloud_opt_in": org.ai_allow_cloud,
            "budget_24h": org.ai_daily_token_budget,
        }
        built = prompts.build(analysis)
        provider = self.provider_factory(integ.connector, integ.credential, self.egress)
        base = {"provider": integ.connector.id, "api": api, "model": model, "locality": locality}

        async with slots.hold(
            self.engine,
            holder,
            self.settings.llm_global_concurrency,
            self.settings.llm_slot_lease_seconds,
            self.settings.llm_slot_wait_seconds,
        ) as slot:
            if slot is None:
                return {
                    **base,
                    "status": "unavailable",
                    "reason": "no model capacity (all slots busy)",
                }
            async with tenant_tx(self.engine, org_id) as c:
                await audit.record(
                    c,
                    org_id,
                    Actor.system(holder),
                    Action.MODEL_SELECTED,
                    target_type="investigation",
                    target_id=investigation_id,
                    details={**base, "policy": policy},
                )
            messages = [Message("user", built.user)]
            errors: list[str] = []
            calls = 0
            for attempt in ("initial", "repair"):
                if cancelled is not None and await cancelled():
                    return {**base, "status": "cancelled", "calls": calls}
                started = datetime.now(UTC)
                status: str = "ok"
                raw: str | None = None
                category: str | None = None
                usage: tuple[int | None, int | None, int | None] = (None, None, None)
                try:
                    result = await provider.complete(
                        ModelCall(
                            model, built.system, messages, OUTPUT_SCHEMA, org.ai_max_output_tokens
                        )
                    )
                    raw, usage = (
                        result.text,
                        (result.prompt_tokens, result.completion_tokens, result.latency_ms),
                    )
                except ProviderError as exc:
                    status = "timeout" if exc.category == "timeout" else "error"
                    category = exc.category
                calls += 1
                outcome: dict[str, Any] | None = None
                if raw is not None:
                    try:
                        adj = Adjudication.model_validate(json.loads(raw))
                        checked = validate(adj, analysis)
                        if checked.accepted:
                            outcome = checked.final
                        else:
                            errors = checked.errors
                    except (ValueError, ValidationError) as exc:
                        errors = [f"output does not match the schema: {type(exc).__name__}"]
                    if outcome is None:
                        status = "invalid"
                await self._record(
                    org_id,
                    investigation_id,
                    integ.id,
                    base,
                    attempt,
                    started,
                    status,
                    category,
                    usage,
                    built,
                    policy,
                )
                if outcome is not None:
                    async with tenant_tx(self.engine, org_id) as c:
                        await audit.record(
                            c,
                            org_id,
                            Actor.system(holder),
                            Action.MODEL_OUTPUT_ACCEPTED,
                            target_type="investigation",
                            target_id=investigation_id,
                            details={
                                **base,
                                "classification": outcome["classification"],
                                "lowered": outcome["lowered"],
                            },
                        )
                    return {**base, "status": "accepted", "calls": calls, **outcome}
                if raw is None:  # provider failure: no repair (it is not an output problem)
                    return {
                        **base,
                        "status": "unavailable",
                        "reason": category or "provider error",
                        "calls": calls,
                    }
                if attempt == "initial":
                    messages = [
                        *messages,
                        Message("assistant", raw[:4000]),
                        Message("user", prompts.repair_message(errors)),
                    ]
            async with tenant_tx(self.engine, org_id) as c:
                await audit.record(
                    c,
                    org_id,
                    Actor.system(holder),
                    Action.MODEL_OUTPUT_REJECTED,
                    target_type="investigation",
                    target_id=investigation_id,
                    details={**base, "errors": errors[:10]},
                )
            return {**base, "status": "invalid", "errors": errors[:10], "calls": calls}

    async def _record(
        self,
        org_id: UUID,
        investigation_id: UUID,
        integration_id: UUID,
        base: dict[str, Any],
        attempt: str,
        started: datetime,
        status: str,
        category: str | None,
        usage: tuple[int | None, int | None, int | None],
        built: prompts.BuiltPrompt,
        policy: dict[str, Any],
    ) -> None:
        async with tenant_tx(self.engine, org_id) as c:
            await c.execute(
                insert(llm_calls).values(
                    org_id=org_id,
                    investigation_id=investigation_id,
                    integration_id=integration_id,
                    connector_id=base["provider"],
                    api=base["api"],
                    model=base["model"][:200],
                    locality=base["locality"],
                    attempt=attempt,
                    started_at=started,
                    latency_ms=usage[2],
                    prompt_tokens=usage[0],
                    completion_tokens=usage[1],
                    status=status,
                    error_category=category,
                    prompt_sha256=built.sha256,
                    prompt_bytes=built.size_bytes,
                    redaction=built.redaction,
                    policy=policy,
                )
            )
