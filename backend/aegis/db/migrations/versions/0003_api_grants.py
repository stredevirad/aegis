"""API grants (readiness probe)

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-02
"""

from __future__ import annotations

from pathlib import Path

from aegis.db.migrations.sqlscript import run_sql_file

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_HERE = Path(__file__).resolve().parent


def upgrade() -> None:
    run_sql_file(_HERE / "0003_api_grants.sql")


def downgrade() -> None:
    run_sql_file(_HERE / "0003_api_grants_down.sql")
