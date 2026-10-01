"""ingestion, API keys, invitations, change events, queue deferral

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02
"""

from __future__ import annotations

from pathlib import Path

from aegis.db.migrations.sqlscript import run_sql_file

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_HERE = Path(__file__).resolve().parent


def upgrade() -> None:
    run_sql_file(_HERE / "0002_ingestion_keys.sql")


def downgrade() -> None:
    run_sql_file(_HERE / "0002_ingestion_keys_down.sql")
