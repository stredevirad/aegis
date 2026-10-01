"""Programmatic migration entry point (used by the migrate container and tests).

python -m aegis.db.migrate upgrade            # to head
python -m aegis.db.migrate downgrade base
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def alembic_config(url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    if url:
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str | None = None, revision: str = "head") -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(url: str | None = None, revision: str = "base") -> None:
    command.downgrade(alembic_config(url), revision)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] not in ("upgrade", "downgrade"):
        sys.stderr.write("usage: python -m aegis.db.migrate upgrade|downgrade [revision]\n")
        return 2
    revision = args[1] if len(args) > 1 else ("head" if args[0] == "upgrade" else "base")
    (upgrade if args[0] == "upgrade" else downgrade)(None, revision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
