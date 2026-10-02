"""``aegis`` command line: process entry points and operator tooling.

    aegis api [--host H] [--port P]          serve the API (uvicorn)
    aegis worker                             run the investigation worker
    aegis migrate upgrade|downgrade [rev]    schema migrations (owner role)
    aegis bootstrap-db ...                   roles/database/schema (superuser; see aegis.db.bootstrap)
    aegis admin create-org --slug S --name N [--admin-email E]
    aegis admin invite --org S --email E --role R
    aegis admin set-user-disabled --email E (--disabled | --enabled)
    aegis audit verify --org S

Operator commands connect with ``AEGIS_MIGRATION_DATABASE_URL`` (the owning role): they act
for the deployment operator, who is trusted (security model §2). Because tables use
``FORCE ROW LEVEL SECURITY``, each command sets the organization context explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

from aegis.config import Settings, get_settings, validate_for_profile
from aegis.observability import logging as aegis_logging

log = logging.getLogger("aegis.cli")


def _owner_dsn(settings: Settings) -> str:
    if settings.migration_database_url is None:
        raise SystemExit("AEGIS_MIGRATION_DATABASE_URL (owner role) is required for this command")
    return settings.migration_database_url.get_secret_value().replace("+asyncpg", "")


def _fail_on_unsafe(settings: Settings) -> None:
    problems = validate_for_profile(settings)
    if problems:
        for p in problems:
            sys.stderr.write(f"configuration error: {p}\n")
        raise SystemExit(2)


# --- processes ------------------------------------------------------------------------------


def run_api(host: str, port: int) -> int:
    import uvicorn

    from aegis.api.app import create_app

    settings = get_settings()
    _fail_on_unsafe(settings)
    aegis_logging.configure(settings.log_level, settings.log_json)
    app = create_app(settings)
    uvicorn.run(app, host=host, port=port, log_config=None, access_log=False, proxy_headers=True)
    return 0


async def _run_worker(settings: Settings) -> None:
    from aegis.collection.live import LiveCollector
    from aegis.db.engine import make_engine
    from aegis.integrations import registry as connector_registry
    from aegis.integrations.egress import EgressClient
    from aegis.investigations.sources import ScenarioRegistry
    from aegis.security.crypto import Keyring
    from aegis.worker.runner import Worker

    engine = make_engine(settings)
    egress = EgressClient(settings)
    from aegis.llm.adjudicate import Adjudicator

    registry, keyring = connector_registry.load(settings), Keyring.from_settings(settings)
    collector = LiveCollector(engine, registry, keyring, egress)
    adjudicator = Adjudicator(engine, settings, registry, keyring, egress)
    worker = Worker(
        engine,
        settings,
        ScenarioRegistry(settings.scenario_dir),
        collector=collector,
        adjudicator=adjudicator,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(
            NotImplementedError, AttributeError
        ):  # Windows: no signal handlers
            loop.add_signal_handler(sig, stop.set)
    log.info(
        "worker starting",
        extra={"worker_id": worker.worker_id, "concurrency": settings.worker_concurrency},
    )
    try:
        await worker.run(stop)
    finally:
        await egress.aclose()
        await engine.dispose()
        log.info("worker stopped", extra={"processed": dict(worker.processed)})


def run_worker() -> int:
    settings = get_settings()
    _fail_on_unsafe(settings)
    aegis_logging.configure(settings.log_level, settings.log_json)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run_worker(settings))
    return 0


# --- operator tooling ------------------------------------------------------------------------


async def create_org(dsn: str, slug: str, name: str, admin_email: str | None) -> uuid.UUID:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            existing = await conn.fetchval("SELECT aegis.org_id_for_slug($1)", slug)
            org_id = existing or uuid.uuid4()
            await conn.execute("SELECT set_config('aegis.org_id', $1, true)", str(org_id))
            if existing is None:
                await conn.execute(
                    "INSERT INTO aegis.organizations (id, slug, name) VALUES ($1, $2, $3)",
                    org_id,
                    slug,
                    name,
                )
            if admin_email:
                await _invite(conn, org_id, admin_email, "admin")
        return org_id
    finally:
        await conn.close()


async def _invite(conn: asyncpg.Connection, org_id: uuid.UUID, email: str, role: str) -> None:
    email = email.strip().lower()
    already = await conn.fetchval(
        "SELECT 1 FROM aegis.invitations WHERE org_id = $1 AND email = $2 AND accepted_at IS NULL "
        "AND revoked_at IS NULL",
        org_id,
        email,
    )
    if already:
        return
    await conn.execute(
        "INSERT INTO aegis.invitations (org_id, email, role, expires_at) VALUES ($1, $2, $3, $4)",
        org_id,
        email,
        role,
        datetime.now(UTC) + timedelta(days=30),
    )
    await conn.execute(
        "INSERT INTO aegis.audit_events (org_id, actor_type, actor_id, action, target_type, details) "
        "VALUES ($1, 'system', 'operator-cli', 'membership.invitation_created', 'invitation', $2::jsonb)",
        org_id,
        f'{{"role": "{role}"}}',
    )


async def invite(dsn: str, slug: str, email: str, role: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            org_id = await conn.fetchval("SELECT aegis.org_id_for_slug($1)", slug)
            if org_id is None:
                raise SystemExit(f"no organization {slug!r}")
            await conn.execute("SELECT set_config('aegis.org_id', $1, true)", str(org_id))
            await _invite(conn, org_id, email, role)
    finally:
        await conn.close()


async def set_user_disabled(dsn: str, email: str, disabled: bool) -> int:
    """Disable (or re-enable) every account with this email. Disabled users' sessions stop
    resolving immediately; their API keys are revoked in every organization."""
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT * FROM aegis.admin_set_user_disabled($1, $2)", email.lower(), disabled
            )
            for r in rows if disabled else []:
                await conn.execute("SELECT set_config('aegis.org_id', $1, true)", str(r["org_id"]))
                await conn.execute(
                    "UPDATE aegis.api_keys SET revoked_at = now(), revoked_reason = 'creator disabled' "
                    "WHERE org_id = $1 AND created_by = $2 AND revoked_at IS NULL",
                    r["org_id"],
                    r["user_id"],
                )
            return len({r["user_id"] for r in rows})
    finally:
        await conn.close()


async def verify_audit(dsn: str, slug: str) -> int | None:
    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            org_id = await conn.fetchval("SELECT aegis.org_id_for_slug($1)", slug)
            if org_id is None:
                raise SystemExit(f"no organization {slug!r}")
            await conn.execute("SELECT set_config('aegis.org_id', $1, true)", str(org_id))
            broken: int | None = await conn.fetchval("SELECT aegis.audit_verify()")
            return broken
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aegis", description="Aegis platform")
    sub = parser.add_subparsers(dest="command", required=True)
    api = sub.add_parser("api", help="serve the API")
    api.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container default
    api.add_argument("--port", type=int, default=8000)
    sub.add_parser("worker", help="run the investigation worker")
    mig = sub.add_parser("migrate", help="schema migrations (owner role)")
    mig.add_argument("direction", choices=["upgrade", "downgrade"])
    mig.add_argument("revision", nargs="?")
    sub.add_parser("bootstrap-db", help="create roles/database/schema (superuser)", add_help=False)
    adm = sub.add_parser("admin", help="operator tooling").add_subparsers(
        dest="admin_command", required=True
    )
    co = adm.add_parser("create-org")
    co.add_argument("--slug", required=True)
    co.add_argument("--name", required=True)
    co.add_argument("--admin-email")
    inv = adm.add_parser("invite")
    inv.add_argument("--org", required=True)
    inv.add_argument("--email", required=True)
    inv.add_argument(
        "--role", required=True, choices=["admin", "investigator", "reviewer", "viewer"]
    )
    dis = adm.add_parser("set-user-disabled")
    dis.add_argument("--email", required=True)
    flag = dis.add_mutually_exclusive_group(required=True)
    flag.add_argument("--disabled", action="store_true")
    flag.add_argument("--enabled", action="store_true")
    aud = sub.add_parser("audit").add_subparsers(dest="audit_command", required=True)
    av = aud.add_parser("verify")
    av.add_argument("--org", required=True)

    args, rest = parser.parse_known_args(argv)
    if args.command == "bootstrap-db":
        from aegis.db import bootstrap

        return bootstrap.main(rest)
    if rest:
        parser.error(f"unrecognized arguments: {' '.join(rest)}")
    if args.command == "api":
        return run_api(args.host, args.port)
    if args.command == "worker":
        return run_worker()
    settings = get_settings()
    if args.command == "migrate":
        from aegis.db import migrate

        url = (
            settings.migration_database_url.get_secret_value()
            if settings.migration_database_url
            else None
        )
        if args.direction == "upgrade":
            migrate.upgrade(url, args.revision or "head")
        else:
            migrate.downgrade(url, args.revision or "base")
        return 0
    if args.command == "admin":
        dsn = _owner_dsn(settings)
        if args.admin_command == "create-org":
            org_id = asyncio.run(create_org(dsn, args.slug, args.name, args.admin_email))
            sys.stdout.write(f"organization {args.slug} {org_id}\n")
        elif args.admin_command == "invite":
            asyncio.run(invite(dsn, args.org, args.email, args.role))
            sys.stdout.write(f"invited {args.email.lower()} to {args.org} as {args.role}\n")
        else:
            n = asyncio.run(set_user_disabled(dsn, args.email, bool(args.disabled)))
            sys.stdout.write(f"{'disabled' if args.disabled else 'enabled'} {n} account(s)\n")
        return 0
    if args.command == "audit":
        broken = asyncio.run(verify_audit(_owner_dsn(settings), args.org))
        sys.stdout.write(
            "audit chain intact\n" if broken is None else f"audit chain broken at seq {broken}\n"
        )
        return 0 if broken is None else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
