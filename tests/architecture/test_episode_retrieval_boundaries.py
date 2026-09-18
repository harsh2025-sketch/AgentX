"""Architecture tests for the M1.04 episode retrieval boundary.

Pins the placement and read-only composition decisions of
``agentx.episode_retrieval``:

* the retrieval boundary is a single top-level ``agentx`` namespace-root
  module (like the A2.10 composition module) — inside no canonical
  subsystem, and no subsystem depends on it;
* it composes the canonical Hive memory *contract*
  (``agentx.hive.experience_memory.EpisodeStoreLike``) and reads through
  caller-supplied canonical store instances; it owns no persistence and no
  memory semantics, defines no canonical contract, and performs no writes;
* EpisodeStore (C2.01) stays the persistence owner and ExperienceMemory
  (C2.06) stays the Hive memory owner;
* no migration, no architecture-manifest change, no new runtime dependency,
  and no semantic/vector/model machinery.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

from agentx import _architecture
from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_RETRIEVAL_MODULE = _AGENTX_SRC / "episode_retrieval.py"
_EXPERIENCE_MEMORY = _AGENTX_SRC / "hive" / "experience_memory.py"
_EPISODE_STORE = _AGENTX_SRC / "infrastructure" / "episode_store.py"
_PERSISTENCE = _AGENTX_SRC / "infrastructure" / "persistence.py"
_CORE_EPISODES = _AGENTX_SRC / "core" / "episodes.py"
_MANIFEST = _AGENTX_SRC / "_architecture.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in _AGENTX_SRC.rglob("*.py"):
        try:
            tree = _tree(path)
        except SyntaxError:
            # Newer-grammar modules parse on the supported runtime only; they
            # cannot define the names this boundary owns or borrows.
            continue
        if any(isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree)):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


def _imported_modules(path: Path) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _class_names(path: Path) -> set[str]:
    return {node.name for node in ast.walk(_tree(path)) if isinstance(node, ast.ClassDef)}


def _public_methods(path: Path, class_name: str) -> set[str]:
    tree = _tree(path)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                item.name
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not item.name.startswith("_")
            }
    raise AssertionError(f"class {class_name} not found in {path}")


def _executable_source(path: Path) -> str:
    """Module source with every docstring blanked, comments already removed."""
    tree = _tree(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            node.value.value = ""
    return ast.unparse(tree)


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def test_retrieval_boundary_has_exactly_one_canonical_definition() -> None:
    assert _RETRIEVAL_MODULE.is_file()
    assert _class_definitions("EpisodeRetrieval") == [Path("agentx/episode_retrieval.py")]
    assert _class_definitions("EpisodeRetrievalQuery") == [Path("agentx/episode_retrieval.py")]


def test_retrieval_boundary_is_a_namespace_root_module() -> None:
    assert _RETRIEVAL_MODULE.parent == _AGENTX_SRC
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        assert not _RETRIEVAL_MODULE.is_relative_to(package)
    # No new package or subsystem directory was introduced.
    assert not (_AGENTX_SRC / "episode_retrieval").exists()
    assert "agentx.episode_retrieval" not in _architecture.SUBSYSTEMS


def test_no_subsystem_depends_on_the_retrieval_boundary() -> None:
    offenders: list[Path] = []
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        for path in sorted(package.rglob("*.py")):
            if "agentx.episode_retrieval" in path.read_text(encoding="utf-8"):
                offenders.append(path.relative_to(_SRC_ROOT))
    assert offenders == []


# ---------------------------------------------------------------------------
# Dependency boundaries
# ---------------------------------------------------------------------------


def test_retrieval_depends_only_on_core_contracts_and_the_hive_memory_contract() -> None:
    imported = _imported_modules(_RETRIEVAL_MODULE)
    agentx_imports = {module for module in imported if module.startswith("agentx.")}
    assert agentx_imports <= {
        "agentx.core.episodes",
        "agentx.core.ids",
        "agentx.hive.experience_memory",
    }


def test_retrieval_avoids_kernel_cognition_capabilities_and_persistence() -> None:
    imported = _imported_modules(_RETRIEVAL_MODULE)
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.cognition",
        "agentx.capabilities",
        "agentx.procedures",
        "agentx.learning",
        "agentx.infrastructure",
    )
    violations = [module for module in imported if module.startswith(forbidden_prefixes)]
    assert violations == []
    assert "sqlite3" not in imported


def test_retrieval_imports_only_lean_standard_library_modules() -> None:
    imported = _imported_modules(_RETRIEVAL_MODULE)
    allowed_stdlib = {"__future__", "dataclasses", "typing", "uuid"}
    for module in imported:
        if module.startswith("agentx."):
            continue
        assert module in allowed_stdlib, module


# ---------------------------------------------------------------------------
# Ownership: stores and memory keep their canonical homes
# ---------------------------------------------------------------------------


def test_retrieval_defines_no_canonical_contract() -> None:
    canonical = {
        "EpisodeRecord",
        "EpisodeOutcome",
        "EpisodeStore",
        "EpisodeEntry",
        "EpisodeStoreLike",
        "EpisodeEntryLike",
        "ExperienceMemory",
        "NegativeExperienceRecord",
        "NegativeExperienceStore",
        "SQLiteDatabase",
    }
    defined = _class_names(_RETRIEVAL_MODULE)
    assert defined.isdisjoint(canonical)
    # The concrete owners still define their canonical contracts exactly once.
    assert _class_definitions("EpisodeStore") == [Path("agentx/infrastructure/episode_store.py")]
    assert _class_definitions("ExperienceMemory") == [Path("agentx/hive/experience_memory.py")]
    assert _class_definitions("EpisodeRecord") == [Path("agentx/core/episodes.py")]


def test_retrieval_never_instantiates_store_or_memory_owners() -> None:
    source = _executable_source(_RETRIEVAL_MODULE)
    for fragment in (
        "EpisodeStore(",
        "ExperienceMemory(",
        "NegativeExperienceStore(",
        "SQLiteDatabase(",
    ):
        assert fragment not in source, fragment


def test_retrieval_touches_only_the_store_read_contract() -> None:
    tree = _tree(_RETRIEVAL_MODULE)

    def has_store_base(node: ast.Attribute) -> bool:
        current: ast.expr | None = node
        while isinstance(current, ast.Attribute):
            if current.attr == "store":
                return True
            current = current.value
        return isinstance(current, ast.Name) and current.id == "store"

    store_attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and has_store_base(node)
    }
    assert store_attributes <= {"store", "get", "read", "append"}
    assert {"get", "read"} <= store_attributes


# ---------------------------------------------------------------------------
# Public API and read-only discipline
# ---------------------------------------------------------------------------


def test_retrieval_public_api_is_exactly_query_plus_facade() -> None:
    tree = _tree(_RETRIEVAL_MODULE)
    public_classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }
    assert public_classes == {
        "EpisodeRetrieval",
        "EpisodeRetrievalQuery",
        "EpisodeRetrievalValidationError",
    }
    public_functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    assert public_functions == set()
    assert _public_methods(_RETRIEVAL_MODULE, "EpisodeRetrieval") == {"get", "retrieve"}
    assert _public_methods(_RETRIEVAL_MODULE, "EpisodeRetrievalQuery") == set()


def test_retrieval_executable_code_performs_no_writes() -> None:
    source = _executable_source(_RETRIEVAL_MODULE)
    for fragment in ("INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE", "commit("):
        assert fragment not in source, fragment
    tree = _tree(_RETRIEVAL_MODULE)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in ("append", "update", "delete", "record", "grant", "promote", "activate"):
        assert name not in defined


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
    "fuzzy",
    "relevance",
    "tfidf",
)


@pytest.mark.parametrize("token", _FORBIDDEN_TOKENS)
def test_retrieval_has_no_semantic_scoring_or_generation_vocabulary(token: str) -> None:
    source = _executable_source(_RETRIEVAL_MODULE)
    pattern = re.compile(rf"\b{re.escape(token)}\w*", re.IGNORECASE)
    assert pattern.search(source) is None, f"episode_retrieval.py mentions {token!r}"


def test_retrieval_has_no_dynamic_execution_or_io_calls() -> None:
    tree = _tree(_RETRIEVAL_MODULE)
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called_names.isdisjoint(
        {"eval", "exec", "compile", "__import__", "open", "print", "input"}
    )


# ---------------------------------------------------------------------------
# Persistence and manifest untouched
# ---------------------------------------------------------------------------


def test_m1_04_adds_no_migrations() -> None:
    versions = tuple(migration.version for migration in _MIGRATIONS)
    names = tuple(migration.name for migration in _MIGRATIONS)
    assert versions == tuple(range(1, len(_MIGRATIONS) + 1))
    assert names == (
        "create_persistence_metadata",
        "create_event_journal",
        "create_knowledge_store",
        "create_episode_store",
        "create_procedure_store",
        "create_artifact_and_audit_stores",
        "create_knowledge_integrity",
        "create_negative_experience_store",
        "create_m12_scheduling_store",
    )
    assert not any("retrieval" in name or "episode_retrieval" in name for name in names)


def test_architecture_manifest_is_unchanged() -> None:
    manifest_source = _MANIFEST.read_text(encoding="utf-8")
    assert "episode_retrieval" not in manifest_source
    assert _architecture.SUBSYSTEMS == (
        "agentx.core",
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.hive",
        "agentx.procedures",
        "agentx.cognition",
        "agentx.learning",
        "agentx.infrastructure",
    )
    assert (
        frozenset(
            {
                (_architecture.KERNEL, _architecture.CORE),
                (_architecture.CAPABILITIES, _architecture.CORE),
                (_architecture.HIVE, _architecture.CORE),
                (_architecture.PROCEDURES, _architecture.CORE),
                (_architecture.COGNITION, _architecture.CORE),
                (_architecture.LEARNING, _architecture.CORE),
                (_architecture.INFRASTRUCTURE, _architecture.CORE),
                (_architecture.CAPABILITIES, _architecture.KERNEL),
                (_architecture.PROCEDURES, _architecture.KERNEL),
                (_architecture.COGNITION, _architecture.KERNEL),
            }
        )
        == _architecture.ALLOWED_ARCHITECTURE_EDGES
    )


def test_no_new_runtime_dependency_for_m1_04() -> None:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []


def test_store_and_memory_owner_files_are_untouched() -> None:
    for path in (_EPISODE_STORE, _PERSISTENCE, _EXPERIENCE_MEMORY, _CORE_EPISODES):
        source = path.read_text(encoding="utf-8")
        assert "episode_retrieval" not in source
        assert "EpisodeRetrieval" not in source
