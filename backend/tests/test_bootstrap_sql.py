"""Bootstrap DDL generation (no database): identifiers and passwords cannot break out."""

from __future__ import annotations

import pytest

from aegis.db import bootstrap


def test_print_sql_doubles_quotes_and_has_no_dollar_quoted_bodies():
    sql = bootstrap.print_sql("owner-pw-'x'-long", "app-pw-$$-'; DROP ROLE x; --", "aegis")
    assert "DO $$" not in sql
    assert "PASSWORD 'app-pw-$$-''; DROP ROLE x; --'" in sql
    assert "PASSWORD 'owner-pw-''x''-long'" in sql


@pytest.mark.parametrize("bad", ["short", "has-backslash-\\-long", "has-nul-\x00-long-enough"])
def test_unsafe_passwords_are_rejected(bad):
    with pytest.raises(ValueError):
        bootstrap.print_sql(bad, "a-perfectly-fine-password", "aegis")


@pytest.mark.parametrize("name", ["aegis; DROP DATABASE x", "Aegis", "a-b", "", "x" * 64, "1abc"])
def test_unsafe_database_names_are_rejected(name):
    with pytest.raises(ValueError):
        bootstrap.database_sql(name)


def test_role_attributes_are_reasserted_for_existing_roles():
    stmts = bootstrap.role_statements(
        {"aegis_owner": "x" * 12, "aegis_app": "y" * 12, "aegis_definer": None},
        existing={"aegis_app"},
    )
    by_role = {t.split()[2]: t for t, _ in stmts if " ROLE " in t and not t.startswith("GRANT")}
    assert by_role["aegis_app"].startswith("ALTER ROLE aegis_app LOGIN")
    assert "NOBYPASSRLS" in by_role["aegis_app"]
    assert by_role["aegis_owner"].startswith("CREATE ROLE")
    assert "BYPASSRLS" in by_role["aegis_definer"] and "NOLOGIN" in by_role["aegis_definer"]
