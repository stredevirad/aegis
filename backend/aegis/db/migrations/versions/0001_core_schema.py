"""core schema: tenancy, incidents, investigations, jobs, evidence, audit

Revision ID: 0001
Revises:
Create Date: 2026-10-02
"""

from __future__ import annotations

from pathlib import Path

from aegis.db.migrations.sqlscript import run_sql_file

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_HERE = Path(__file__).resolve().parent


def upgrade() -> None:
    run_sql_file(_HERE / "0001_core_schema.sql")


def downgrade() -> None:
    run_sql_file(_HERE / "0001_core_schema_down.sql")
