"""operator functions for the admin CLI

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-02
"""

from __future__ import annotations

from pathlib import Path

from aegis.db.migrations.sqlscript import run_sql_file

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_HERE = Path(__file__).resolve().parent


def upgrade() -> None:
    run_sql_file(_HERE / "0004_operator_functions.sql")


def downgrade() -> None:
    run_sql_file(_HERE / "0004_operator_functions_down.sql")
