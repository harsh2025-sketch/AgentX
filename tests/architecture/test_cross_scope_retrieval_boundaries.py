"""Architecture guards for the C6.08 cross-scope retrieval protection boundary.

The protection must live where BOTH guarded consumers may reach it without a
new dependency edge: a pure-data policy module in ``agentx.core``. These tests
pin its single canonical placement, its import horizon (core contracts only,
never the kernel), the fact that the three retrieval surfaces delegate to it,
and that C6.08 adds no storage, migrations, threads, or runtime dependencies.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from agentx import _architecture
from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_BOUNDARY = _AGENTX_SRC / "core" / "retrieval_scope.py"
_RETRIEVAL = _AGENTX_SRC / "infrastructure" / "knowledge_retrieval.py"
_SEMANTIC = _AGENTX_SRC / "hive" / "semantic_memory.py"
_EXPERIENCE = _AGENTX_SRC / "hive" / "experience_memory.py"

_CONSUMER_MODULES = (_RETRIEVAL, _SEMANTIC, _EXPERIENCE)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in sorted(_AGENTX_SRC.rglob("*.py")):
        tree = _tree(path)
        if any(isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree)):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


def _function_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in sorted(_AGENTX_SRC.rglob("*.py")):
        tree = _tree(path)
        if any(
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name
            for node in ast.walk(tree)
        ):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


def _imported_modules(path: Path) -> list[str]:
    imported: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return imported


def _called_names(path: Path) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


# ---------------------------------------------------------------------------
# Single canonical placement
# ---------------------------------------------------------------------------


def test_boundary_classes_have_exactly_one_canonical_definition() -> None:
    expected = Path("agentx/core/retrieval_scope.py")
    for name in (
        "RetrievalScopeGuard",
        "RetrievalScopeError",
        "ScopeAccessReason",
        "ScopeDecision",
        "ScopeDenial",
        "ScopePartition",
    ):
        assert _class_definitions(name) == [expected], name
    assert _function_definitions("evaluate_record_scope") == [expected]


def test_boundary_lives_in_core_so_both_consumers_may_compose_it() -> None:
    assert _BOUNDARY.is_file()
    assert (_architecture.CORE, _architecture.HIVE) not in (
        _architecture.ALLOWED_ARCHITECTURE_EDGES
    )
    for source in (_architecture.HIVE, _architecture.INFRASTRUCTURE):
        assert (source, _architecture.CORE) in _architecture.ALLOWED_ARCHITECTURE_EDGES


def test_boundary_imports_only_core_contracts_and_the_stdlib() -> None:
    for module in _imported_modules(_BOUNDARY):
        assert module in {"agentx.core.knowledge", "agentx.core.negative_experience"} or any(
            module == top or module.startswith(top + ".") for top in sys.stdlib_module_names
        ), module


def test_boundary_touches_no_authority_or_execution_machinery() -> None:
    imported = _imported_modules(_BOUNDARY)
    for module in imported:
        assert not module.startswith("agentx.kernel")
        assert not module.startswith("agentx.capabilities")
        assert not module.startswith("agentx.cognition")
        assert module not in {
            "subprocess",
            "socket",
            "http",
            "urllib",
            "importlib",
            "pickle",
            "threading",
            "asyncio",
            "sqlite3",
            "os",
            "shutil",
            "ctypes",
        }
    assert _called_names(_BOUNDARY) & {"eval", "exec", "compile", "__import__"} == set()


def test_boundary_implements_no_retrieval_and_no_lifecycle_surface() -> None:
    tree = _tree(_BOUNDARY)
    methods = {
        node.name
        for top in tree.body
        if isinstance(top, ast.ClassDef)
        for node in top.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }
    # Pure decision surface only: no storage reads, no status changes, no
    # context construction, no mutation hooks.
    assert methods == {"allowed", "denied_count", "evaluate", "partition", "filter"}
    for forbidden in ("update_status", "promote", "insert", "retrieve", "store", "fetch"):
        assert not any(forbidden in name for name in methods), forbidden


# ---------------------------------------------------------------------------
# Retrieval surfaces delegate to the boundary
# ---------------------------------------------------------------------------


def test_all_three_retrieval_surfaces_compose_the_boundary() -> None:
    for path in _CONSUMER_MODULES:
        assert "agentx.core.retrieval_scope" in _imported_modules(path), path


def test_consumers_gate_every_read_path_on_the_guard() -> None:
    semantic_source = _SEMANTIC.read_text(encoding="utf-8")
    assert "scope_guard.evaluate(record).allowed" in semantic_source  # recall
    assert "scope_guard.filter(matches)" in semantic_source  # recall_all
    retrieval_source = _RETRIEVAL.read_text(encoding="utf-8")
    assert "scope_guard.filter(matched)" in retrieval_source
    experience_source = _EXPERIENCE.read_text(encoding="utf-8")
    assert "_scope_guard.evaluate(record).allowed" in experience_source
    assert "_scope_guard.filter(records)" in experience_source


def test_no_consumer_exposes_a_scope_bypass_surface() -> None:
    for path in _CONSUMER_MODULES:
        source = path.read_text(encoding="utf-8").lower()
        for escape in (
            "allow_all",
            "bypass_scope",
            "ignore_scope",
            "scope_override",
            "include_all_scopes",
        ):
            assert escape not in source, (path.name, escape)


# ---------------------------------------------------------------------------
# C6.08 adds no storage, no migrations, and no dependencies
# ---------------------------------------------------------------------------


def test_c608_adds_no_migrations_and_no_tables() -> None:
    names = tuple(migration.name for migration in _MIGRATIONS)
    assert [name for name in names if "scope" in name or "retrieval" in name] == []
    persistence_source = (_AGENTX_SRC / "infrastructure" / "persistence.py").read_text(
        encoding="utf-8"
    )
    created = [
        table for table in ("scope", "retrieval") if f"CREATE TABLE {table}" in persistence_source
    ]
    assert created == []


def test_no_new_runtime_dependencies() -> None:
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject


def test_boundary_source_stays_free_of_scoring_vocabulary() -> None:
    names: set[str] = set()
    for node in ast.walk(_tree(_BOUNDARY)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.FunctionDef | ast.ClassDef):
            names.add(node.name)
    identifiers = {name.lower() for name in names}
    for token in ("rank", "score", "similar", "vector", "embed", "cosine", "relevan"):
        assert not any(token in identifier for identifier in identifiers), token
