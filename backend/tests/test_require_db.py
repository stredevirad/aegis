"""AEGIS_REQUIRE_DB=1 turns "PostgreSQL unreachable" from a skip into a failure (CI must not go
green with the database tests silently skipped)."""

from __future__ import annotations

import pytest

from tests.conftest import skip_or_fail_without_db


def test_unreachable_database_skips_by_default(monkeypatch):
    monkeypatch.delenv("AEGIS_REQUIRE_DB", raising=False)
    with pytest.raises(pytest.skip.Exception):
        skip_or_fail_without_db("PostgreSQL not reachable")


def test_unreachable_database_fails_when_required(monkeypatch):
    monkeypatch.setenv("AEGIS_REQUIRE_DB", "1")
    with pytest.raises(pytest.fail.Exception, match="AEGIS_REQUIRE_DB=1"):
        skip_or_fail_without_db("PostgreSQL not reachable")
