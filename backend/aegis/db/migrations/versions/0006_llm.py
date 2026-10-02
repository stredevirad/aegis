"""LLM call records and slot pool

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-02
"""

from __future__ import annotations

from pathlib import Path

from aegis.db.migrations.sqlscript import run_sql_file

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_HERE = Path(__file__).resolve().parent


def upgrade() -> None:
    run_sql_file(_HERE / "0006_llm.sql")


def downgrade() -> None:
    run_sql_file(_HERE / "0006_llm_down.sql")
