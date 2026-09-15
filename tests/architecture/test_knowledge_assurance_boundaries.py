"""Architecture guards for AX-120..124 completion."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_assurance_core_remains_kernel_independent() -> None:
    path = _REPO_ROOT / "src" / "agentx" / "core" / "knowledge_assurance.py"
    imports = _imports(path)
    assert not any(module.startswith("agentx.kernel") for module in imports)
    assert not any(module.startswith("agentx.capabilities") for module in imports)


def test_scope_retrieval_does_not_import_kernel_or_models() -> None:
    path = _REPO_ROOT / "src" / "agentx" / "hive" / "scope_retrieval.py"
    imports = _imports(path)
    assert not any(module.startswith("agentx.kernel") for module in imports)
    assert not any("model" in module for module in imports)


def test_assurance_ledger_reuses_event_journal_not_second_database() -> None:
    path = _REPO_ROOT / "src" / "agentx" / "infrastructure" / "knowledge_assurance_ledger.py"
    source = path.read_text(encoding="utf-8")
    imports = _imports(path)
    assert "agentx.infrastructure.event_journal" in imports
    assert "CREATE TABLE" not in source
    assert "ALTER TABLE" not in source
    assert "sqlite3" not in imports


def test_relationship_query_is_read_only_over_canonical_store() -> None:
    path = _REPO_ROOT / "src" / "agentx" / "infrastructure" / "knowledge_relationships.py"
    source = path.read_text(encoding="utf-8")
    assert ".record_contradiction(" not in source
    assert ".apply_supersession(" not in source
    assert ".update_status(" not in source
