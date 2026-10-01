"""ADR-008: the core performs no I/O, reads no clock and uses no randomness."""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "aegis" / "core"
FORBIDDEN_MODULES = {
    "os",
    "sys",
    "time",
    "random",
    "secrets",
    "socket",
    "ssl",
    "subprocess",
    "asyncio",
    "threading",
    "multiprocessing",
    "httpx",
    "requests",
    "urllib",
    "sqlalchemy",
    "asyncpg",
    "pathlib",
    "shutil",
    "tempfile",
    "logging",
    "uuid",
}
FORBIDDEN_CALLS = {"now", "utcnow", "today", "open", "getenv", "uuid4"}


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                    out.append(f"imports {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in FORBIDDEN_MODULES:
                out.append(f"imports from {node.module}")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = (
                fn.attr
                if isinstance(fn, ast.Attribute)
                else fn.id
                if isinstance(fn, ast.Name)
                else ""
            )
            if name in FORBIDDEN_CALLS:
                out.append(f"calls {name}() at line {node.lineno}")
    return out


def test_core_is_pure():
    problems = {
        str(p.relative_to(CORE)): v for p in sorted(CORE.rglob("*.py")) if (v := _violations(p))
    }
    assert not problems, problems


def test_purity_checker_detects_violations(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import time\nfrom datetime import datetime\nx = datetime.now()\n")
    v = _violations(bad)
    assert any("time" in x for x in v) and any("now" in x for x in v)
