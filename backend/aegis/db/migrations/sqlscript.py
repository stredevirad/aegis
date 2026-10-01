"""Run a multi-statement SQL script inside the current Alembic transaction.

SQLAlchemy's asyncpg dialect prepares statements, which cannot contain several commands. The
raw asyncpg connection's ``execute`` uses the simple-query protocol, which can, and it runs in
the same transaction Alembic opened.
"""

from __future__ import annotations

from pathlib import Path

from alembic import op
from sqlalchemy.util import await_only


def run_sql_file(path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    raw = op.get_bind().connection.dbapi_connection
    if raw is None:
        raise RuntimeError("no DBAPI connection")
    await_only(raw.driver_connection.execute(sql))
