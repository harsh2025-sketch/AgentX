"""Architecture tests for C2.09 Hive retrieval + environmental TTL cache.

Mirrors the placement guards used by the knowledge contract (C2.02):

* deterministic retrieval lives in ``agentx.infrastructure`` next to the
  canonical ``KnowledgeStore`` it reads through (the Hive subsystem cannot
  depend on infrastructure, and the manifest deliberately grants no such
  edge);
* the environmental TTL cache is a pure-standard-library ``agentx.hive``
  implementation module — no infrastructure, no SQLite, no kernel, no threads;
* C2.09 adds NO persistence: it appends no migration and leaves v8 (consumed
  by C2.06 after this work started) untouched;
* no semantic/vector/model machinery and no new runtime dependencies.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_RETRIEVAL_MODULE = _AGENTX_SRC / "infrastructure" / "knowledge_retrieval.py"
_CACHE_MODULE = _AGENTX_SRC / "hive" / "environmental_cache.py"


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in _AGENTX_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree)):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


# ---------------------------------------------------------------------------
# Single canonical placement
# ---------------------------------------------------------------------------


def test_retrieval_boundary_has_exactly_one_canonical_definition() -> None:
    assert _class_definitions("KnowledgeRetrieval") == [
        Path("agentx/infrastructure/knowledge_retrieval.py")
    ]
    assert _class_definitions("KnowledgeRetrievalQuery") == [
        Path("agentx/infrastructure/knowledge_retrieval.py")
    ]


def test_environmental_cache_has_exactly_one_canonical_definition() -> None:
    assert _class_definitions("EnvironmentalCache") == [Path("agentx/hive/environmental_cache.py")]
    assert _class_definitions("EnvironmentalCacheEntry") == [
        Path("agentx/hive/environmental_cache.py")
    ]


# ---------------------------------------------------------------------------
# Dependency boundaries
# ---------------------------------------------------------------------------


def test_retrieval_depends_only_on_core_contracts_and_the_store() -> None:
    imported = _imported_modules(_RETRIEVAL_MODULE)
    agentx_imports = {module for module in imported if module.startswith("agentx.")}
    assert agentx_imports <= {
        "agentx.core.ids",
        "agentx.core.knowledge",
        "agentx.core.retrieval_scope",
        "agentx.infrastructure.knowledge_store",
    }


def test_retrieval_avoids_kernel_authority_and_transports() -> None:
    imported = _imported_modules(_RETRIEVAL_MODULE)
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.hive",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.procedures",
        "agentx.learning",
    )
    violations = [module for module in imported if module.startswith(forbidden_prefixes)]
    violations += [
        module
        for module in imported
        if module
        in (
            "sqlite3",
            "agentx.infrastructure.event_bus",
            "agentx.infrastructure.event_journal",
        )
    ]
    assert violations == []


def test_environmental_cache_is_pure_standard_library() -> None:
    imported = _imported_modules(_CACHE_MODULE)
    non_stdlib = [
        module for module in imported if module.split(".")[0] not in sys.stdlib_module_names
    ]
    assert non_stdlib == []
    assert "sqlite3" not in imported


def test_environmental_cache_spawns_no_threads_or_timers() -> None:
    names = _imported_names(_CACHE_MODULE)
    assert {"Thread", "Timer", "ThreadPoolExecutor"}.isdisjoint(names)


def test_environmental_cache_lives_in_the_hive_package() -> None:
    """The cache joins the Hive subsystem's canonical implementation modules."""
    hive_modules = sorted(path.name for path in (_AGENTX_SRC / "hive").glob("*.py"))
    assert "environmental_cache.py" in hive_modules
    assert "__init__.py" in hive_modules


# ---------------------------------------------------------------------------
# No semantic/vector/model machinery
# ---------------------------------------------------------------------------


_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "embedding",
    "vector",
    "cosine",
    "similarity",
    "openai",
    "anthropic",
    "llm",
    "numpy",
    "faiss",
    "chroma",
    "reputation",
    "trust_score",
    "score",
    "ranking",
)


@pytest.mark.parametrize("token", _FORBIDDEN_TOKENS)
@pytest.mark.parametrize("path", [_RETRIEVAL_MODULE, _CACHE_MODULE])
def test_modules_contain_no_semantic_scoring_or_model_vocabulary(path: Path, token: str) -> None:
    source = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"\b{re.escape(token)}\w*", re.IGNORECASE)
    assert pattern.search(source) is None, f"{path.name} mentions {token!r}"


def test_retrieval_module_declares_no_limit_rank_or_scoring_api() -> None:
    """The public retrieval API is exactly: one query type + one service type."""
    tree = ast.parse(_RETRIEVAL_MODULE.read_text(encoding="utf-8"))
    public_classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }
    assert public_classes == {
        "KnowledgeQueryValidationError",
        "KnowledgeRetrieval",
        "KnowledgeRetrievalQuery",
    }
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "KnowledgeRetrieval":
            methods = {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not item.name.startswith("__")
            }
            assert methods == {"retrieve"}


# ---------------------------------------------------------------------------
# Persistence: v8 stays unreserved, v1-v7 stay immutable
# ---------------------------------------------------------------------------


def test_c209_adds_no_migrations() -> None:
    versions = tuple(migration.version for migration in _MIGRATIONS)
    names = tuple(migration.name for migration in _MIGRATIONS)

    assert versions == tuple(range(1, len(_MIGRATIONS) + 1))
    # C2.09 appends nothing: the ephemeral cache is deliberately in-memory and
    # deterministic retrieval needs no schema.
    assert not any(
        "cache" in name or "environment" in name or "retrieval" in name for name in names
    )


def test_v8_belongs_to_c206_and_v1_v7_remain_immutable() -> None:
    names = tuple(migration.name for migration in _MIGRATIONS)

    # Migration v8 was consumed by C2.06 (negative experience store), exactly
    # as the C2.09 task required: C2.09 must not reserve or steal it.
    assert names[:7] == (
        "create_persistence_metadata",
        "create_event_journal",
        "create_knowledge_store",
        "create_episode_store",
        "create_procedure_store",
        "create_artifact_and_audit_stores",
        "create_knowledge_integrity",
    )
    assert "create_negative_experience_store" in names


def test_environmental_cache_persists_nothing() -> None:
    """The cache module never touches the persistence foundation."""
    source = _CACHE_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("agentx.infrastructure")
        ):
            raise AssertionError("environmental cache must not depend on infrastructure")
    assert "SQLiteDatabase" not in source


# ---------------------------------------------------------------------------
# Zero new runtime dependencies
# ---------------------------------------------------------------------------


def test_no_new_runtime_dependencies_for_c209() -> None:
    import tomllib

    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []
