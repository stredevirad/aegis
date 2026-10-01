"""The Core table descriptions in aegis.db.tables match the migrated schema."""

from __future__ import annotations

import pytest
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from aegis.db.tables import metadata

pytestmark = pytest.mark.db

_PG_TYPE = [
    (UUID, ("uuid", "uuid")),
    (JSONB, ("jsonb", "jsonb")),
    (ARRAY, ("ARRAY", "_text")),
    (DateTime, ("timestamp with time zone", "timestamptz")),
    (LargeBinary, ("bytea", "bytea")),
    (BigInteger, ("bigint", "int8")),
    (SmallInteger, ("smallint", "int2")),
    (Integer, ("integer", "int4")),
    (Boolean, ("boolean", "bool")),
    (Text, ("text", "text")),
]


def _expected(col_type) -> tuple[str, str]:
    for cls, pg in _PG_TYPE:
        if isinstance(col_type, cls):
            return pg
    raise AssertionError(f"unmapped type {col_type!r}")


async def test_declared_columns_exist_with_matching_types(owner_conn):
    rows = await owner_conn.fetch(
        "SELECT table_name, column_name, data_type, udt_name FROM information_schema.columns "
        "WHERE table_schema = 'aegis'"
    )
    actual = {(r["table_name"], r["column_name"]): (r["data_type"], r["udt_name"]) for r in rows}
    problems = []
    for table in metadata.sorted_tables:
        for col in table.columns:
            key = (table.name, col.name)
            if key not in actual:
                problems.append(f"{table.name}.{col.name} missing in database")
            elif actual[key] != _expected(col.type):
                problems.append(
                    f"{table.name}.{col.name}: db {actual[key]} vs {_expected(col.type)}"
                )
    assert not problems, problems
