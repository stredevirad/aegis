"""reconcile stranded remediations

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-02
"""

from __future__ import annotations

from pathlib import Path

from aegis.db.migrations.sqlscript import run_sql_file

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_HERE = Path(__file__).resolve().parent


def upgrade() -> None:
    run_sql_file(_HERE / "0008_remediation_sweep.sql")


def downgrade() -> None:
    run_sql_file(_HERE / "0008_remediation_sweep_down.sql")
