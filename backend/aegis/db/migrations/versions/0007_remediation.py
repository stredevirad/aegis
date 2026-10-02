"""human-gated remediation

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-02
"""

from __future__ import annotations

from pathlib import Path

from aegis.db.migrations.sqlscript import run_sql_file

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_HERE = Path(__file__).resolve().parent


def upgrade() -> None:
    run_sql_file(_HERE / "0007_remediation.sql")


def downgrade() -> None:
    run_sql_file(_HERE / "0007_remediation_down.sql")
