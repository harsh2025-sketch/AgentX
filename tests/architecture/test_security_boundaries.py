"""Architecture checks for C1.09 Trusted Kernel security boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SECURITY_MODULES = (
    _REPO_ROOT / "src" / "agentx" / "kernel" / "audit.py",
    _REPO_ROOT / "src" / "agentx" / "kernel" / "secrets.py",
    _REPO_ROOT / "src" / "agentx" / "kernel" / "emergency_stop.py",
)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(imported)


def test_security_boundaries_do_not_depend_on_infrastructure_or_sqlite() -> None:
    for path in _SECURITY_MODULES:
        imports = _imports(path)
        assert all(
            module != "agentx.infrastructure" and not module.startswith("agentx.infrastructure.")
            for module in imports
        )
        assert "sqlite3" not in imports


def test_security_boundaries_do_not_import_persistence_or_transport_symbols() -> None:
    forbidden_names = {"SQLiteDatabase", "EventJournal", "EventBus"}

    for path in _SECURITY_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                imported_names.update(alias.asname or alias.name for alias in node.names)

        assert imported_names.isdisjoint(forbidden_names)
