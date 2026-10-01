"""Superuser bootstrap: roles, database and schema (run once per deployment; idempotent).

Roles (ADR-012):

* ``aegis_owner``   LOGIN; owns the schema and tables; used only by migrations.
* ``aegis_app``     LOGIN; owns nothing; NOBYPASSRLS; used by the API and the worker.
* ``aegis_definer`` NOLOGIN; BYPASSRLS; owns only the narrow SECURITY DEFINER functions
  (session/API-key lookup, job claim, user upsert) that must run before an organization is
  known. Under ``FORCE ROW LEVEL SECURITY`` even the table owner is filtered, so these
  functions need a BYPASSRLS owner.

Re-running the bootstrap re-asserts the role attributes (a pre-existing ``aegis_app`` with
BYPASSRLS is corrected, not trusted) and sets the passwords to the given values.

Passwords never pass through a dollar-quoted ``DO`` body: the statement text is produced by
the server's own ``format('%L')`` quoting, so no password content can break out of the
literal. ``--print-sql`` (for manual review) quotes client-side and refuses passwords
containing characters that are unsafe to embed (NUL, backslash).

Creating a BYPASSRLS role requires a superuser. On managed PostgreSQL services, check that
your administrative role may create it (see docs/deployment.md). Usage:

    python -m aegis.db.bootstrap --superuser-url postgresql://postgres:...@host/postgres \\
        --owner-password ... --app-password ... [--database aegis]
    python -m aegis.db.bootstrap --print-sql ...   # review/apply manually
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

import asyncpg

# Role attributes are fixed here (never derived from input).
ROLE_ATTRIBUTES: dict[str, str] = {
    "aegis_owner": "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS",
    "aegis_app": "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS",
    "aegis_definer": "NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS",
}
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
MIN_PASSWORD = 12


def ident(value: str) -> str:
    """Validate an SQL identifier (database name); never quote untrusted input into DDL."""
    if not _IDENT.fullmatch(value):
        raise ValueError(f"unsafe identifier: {value!r}")
    return value


def check_password(value: str) -> str:
    if len(value) < MIN_PASSWORD:
        raise ValueError(f"passwords must be at least {MIN_PASSWORD} characters")
    if "\x00" in value or "\\" in value:
        raise ValueError("passwords must not contain NUL or backslash")
    return value


def client_literal(value: str) -> str:
    """Client-side SQL literal, for --print-sql only (standard_conforming_strings = on)."""
    return "'" + check_password(value).replace("'", "''") + "'"


def role_statements(
    passwords: dict[str, str | None], existing: set[str]
) -> list[tuple[str, str | None]]:
    """(statement template, password) pairs; ``%L`` in the template is the password literal."""
    out: list[tuple[str, str | None]] = []
    for role, attrs in ROLE_ATTRIBUTES.items():
        verb = "ALTER" if role in existing else "CREATE"
        pw = passwords.get(role)
        if pw is None:
            out.append((f"{verb} ROLE {role} {attrs}", None))
        else:
            out.append((f"{verb} ROLE {role} {attrs} PASSWORD %L", pw))
    # the owner must be able to hand function ownership to the definer role
    out.append(("GRANT aegis_definer TO aegis_owner", None))
    return out


def create_database_sql(database: str) -> str:
    return f"CREATE DATABASE {ident(database)} OWNER aegis_owner"


def database_sql(database: str) -> list[str]:
    db = ident(database)
    return [
        "REVOKE ALL ON SCHEMA public FROM PUBLIC",
        f"REVOKE ALL ON DATABASE {db} FROM PUBLIC",
        f"GRANT CONNECT ON DATABASE {db} TO aegis_owner, aegis_app",
        "CREATE SCHEMA IF NOT EXISTS aegis AUTHORIZATION aegis_owner",
        "GRANT USAGE ON SCHEMA aegis TO aegis_app, aegis_definer",
        f"ALTER ROLE aegis_app IN DATABASE {db} SET search_path = aegis",
        f"ALTER ROLE aegis_owner IN DATABASE {db} SET search_path = aegis",
        f"ALTER ROLE aegis_app IN DATABASE {db} SET statement_timeout = '15s'",
        f"ALTER ROLE aegis_app IN DATABASE {db} SET idle_in_transaction_session_timeout = '60s'",
    ]


async def _apply_roles(conn: asyncpg.Connection, owner_password: str, app_password: str) -> None:
    rows = await conn.fetch(
        "SELECT rolname FROM pg_roles WHERE rolname = ANY($1::text[])", list(ROLE_ATTRIBUTES)
    )
    existing = {r["rolname"] for r in rows}
    passwords: dict[str, str | None] = {
        "aegis_owner": check_password(owner_password),
        "aegis_app": check_password(app_password),
        "aegis_definer": None,
    }
    for template, pw in role_statements(passwords, existing):
        if pw is None:
            await conn.execute(template)
        else:
            # server-side quoting: format('%L') cannot be broken out of
            stmt = await conn.fetchval("SELECT format($1::text, $2::text)", template, pw)
            await conn.execute(stmt)


async def bootstrap(
    superuser_url: str, owner_password: str, app_password: str, database: str
) -> None:
    ident(database)
    conn = await asyncpg.connect(superuser_url)
    try:
        async with conn.transaction():
            await _apply_roles(conn, owner_password, app_password)
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database)
        if not exists:
            await conn.execute(create_database_sql(database))
    finally:
        await conn.close()
    # connect to the new database (same server, superuser)
    base, _, _ = superuser_url.rpartition("/")
    conn = await asyncpg.connect(f"{base}/{database}")
    try:
        for stmt in database_sql(database):
            await conn.execute(stmt)
    finally:
        await conn.close()


def print_sql(owner_password: str, app_password: str, database: str) -> str:
    """Reviewable script. Assumes the roles do not exist yet (uses CREATE)."""
    pws = {"aegis_owner": owner_password, "aegis_app": app_password, "aegis_definer": None}
    stmts = [
        t.replace("%L", client_literal(pw)) if pw is not None else t
        for t, pw in role_statements(pws, existing=set())
    ]
    stmts += [
        create_database_sql(database),
        f"\\connect {ident(database)}",
        *database_sql(database),
    ]
    return ";\n".join(stmts) + ";\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--superuser-url", default=os.environ.get("AEGIS_BOOTSTRAP_SUPERUSER_URL"))
    parser.add_argument(
        "--owner-password", default=os.environ.get("AEGIS_BOOTSTRAP_OWNER_PASSWORD")
    )
    parser.add_argument("--app-password", default=os.environ.get("AEGIS_BOOTSTRAP_APP_PASSWORD"))
    parser.add_argument("--database", default="aegis")
    parser.add_argument("--print-sql", action="store_true")
    args = parser.parse_args(argv)
    if not args.owner_password or not args.app_password:
        parser.error("owner and app passwords are required")
    try:
        if args.print_sql:
            sys.stdout.write(print_sql(args.owner_password, args.app_password, args.database))
            return 0
        if not args.superuser_url:
            parser.error("--superuser-url is required")
        asyncio.run(
            bootstrap(args.superuser_url, args.owner_password, args.app_password, args.database)
        )
    except ValueError as exc:
        parser.error(str(exc))
    sys.stdout.write("bootstrap complete\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
