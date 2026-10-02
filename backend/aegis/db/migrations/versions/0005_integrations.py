"""tenant integrations

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-02
"""

from __future__ import annotations

from pathlib import Path

from aegis.db.migrations.sqlscript import run_sql_file

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_HERE = Path(__file__).resolve().parent


def upgrade() -> None:
    run_sql_file(_HERE / "0005_integrations.sql")


def downgrade() -> None:
    run_sql_file(_HERE / "0005_integrations_down.sql")
