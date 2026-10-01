"""ADR-007: the product package must never import the research engine (`src`)."""

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "aegis"
FORBIDDEN_TOP_LEVEL = {"src", "tests"}


def _imported_top_level_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_package_does_not_import_research_or_tests() -> None:
    violations: list[str] = []
    files = sorted(PACKAGE_ROOT.rglob("*.py"))
    assert files, "no package files found; is the test pointed at the right directory?"
    for path in files:
        bad = _imported_top_level_modules(path.read_text(encoding="utf-8")) & FORBIDDEN_TOP_LEVEL
        if bad:
            violations.append(f"{path.relative_to(PACKAGE_ROOT.parent)} imports {sorted(bad)}")
    assert not violations, "\n".join(violations)


def test_boundary_checker_detects_violations() -> None:
    assert _imported_top_level_modules("from src.domain import Evidence") == {"src"}
    assert _imported_top_level_modules("import src.persistence.repository") == {"src"}
    assert _imported_top_level_modules("from . import x") == set()
