"""Architecture guardrails for C2.05 semantic-memory placement.

Semantic memory is a Hive memory service: it composes the canonical C2.02
KnowledgeStore instead of adding storage, reuses the canonical C2.02/C2.07
contracts instead of redefining them, and stays inside the declared
``agentx.hive -> agentx.core`` boundary edge.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

from agentx import _architecture
from agentx.hive import semantic_memory
from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_SEMANTIC_MEMORY = _AGENTX_SRC / "hive" / "semantic_memory.py"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

# Contracts C2.05 must reuse rather than duplicate, and where they live.
_CANONICAL_DEFINITIONS = {
    "KnowledgeRecord": Path("agentx/core/knowledge.py"),
    "KnowledgeScope": Path("agentx/core/knowledge.py"),
    "KnowledgeStatus": Path("agentx/core/knowledge.py"),
    "KnowledgeType": Path("agentx/core/knowledge.py"),
    "ScopeDimension": Path("agentx/core/knowledge.py"),
    "ProvenanceKind": Path("agentx/core/knowledge.py"),
    "ProvenanceReference": Path("agentx/core/knowledge.py"),
    "ProvenanceRecord": Path("agentx/core/provenance.py"),
    "EvidenceReference": Path("agentx/core/provenance.py"),
    "KnowledgeEvidence": Path("agentx/core/provenance.py"),
    "KnowledgeStore": Path("agentx/infrastructure/knowledge_store.py"),
}


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in sorted(_AGENTX_SRC.rglob("*.py")):
        tree = _module_tree(path)
        if any(isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree)):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


def _identifiers(path: Path) -> frozenset[str]:
    """Return every lowercased executable identifier in a module.

    Docstrings and comments are excluded on purpose: a module is allowed to
    document the machinery it refuses to implement.
    """
    names: set[str] = set()
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.arg | ast.keyword) and node.arg is not None:
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add(node.name)
            names.add(node.asname or "")
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and len(node.value) < 60  # short literals only; docstrings are prose
        ):
            names.add(node.value)
    return frozenset(name.lower() for name in names if name)


def _imported_modules(path: Path) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def test_semantic_memory_lives_in_the_hive_subsystem() -> None:
    assert _SEMANTIC_MEMORY.is_file()
    assert semantic_memory.__name__ == "agentx.hive.semantic_memory"


def test_semantic_memory_service_has_exactly_one_definition() -> None:
    for name in ("SemanticMemory", "SemanticMemoryQuery", "KnowledgeStorePort"):
        assert _class_definitions(name) == [Path("agentx/hive/semantic_memory.py")], name


def test_canonical_contracts_are_reused_not_duplicated() -> None:
    """C2.02/C2.07 contracts keep exactly one definition site each."""
    for name, owner in _CANONICAL_DEFINITIONS.items():
        assert _class_definitions(name) == [owner], name


def test_hive_package_initializer_stays_declarative() -> None:
    tree = _module_tree(_AGENTX_SRC / "hive" / "__init__.py")

    assert len(tree.body) == 1
    assert isinstance(tree.body[0], ast.Expr)


# ---------------------------------------------------------------------------
# Dependency boundary
# ---------------------------------------------------------------------------


def test_semantic_memory_only_imports_core_contracts() -> None:
    """``agentx.hive -> agentx.core`` is the only AgentX edge this module uses."""
    agentx_imports = [
        module for module in _imported_modules(_SEMANTIC_MEMORY) if module.startswith("agentx")
    ]

    assert agentx_imports
    assert all(module.startswith("agentx.core.") for module in agentx_imports)


def test_hive_still_has_no_infrastructure_edge() -> None:
    """Storage is injected structurally; no new architecture edge was declared."""
    assert (
        _architecture.HIVE,
        _architecture.INFRASTRUCTURE,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES
    assert (_architecture.HIVE, _architecture.CORE) in _architecture.ALLOWED_ARCHITECTURE_EDGES
    assert (_architecture.HIVE, _architecture.KERNEL) not in (
        _architecture.ALLOWED_ARCHITECTURE_EDGES
    )


def test_semantic_memory_uses_only_stdlib_and_agentx() -> None:
    """Zero new runtime dependencies: every import is stdlib or ``agentx.core``."""
    for module in _imported_modules(_SEMANTIC_MEMORY):
        root = module.split(".")[0]
        assert root in sys.stdlib_module_names or root == "agentx", module


def test_runtime_distribution_declares_no_dependencies() -> None:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]

    assert project["dependencies"] == []
    assert sorted(project["optional-dependencies"]) == ["dev"]
    assert [
        requirement.split(">=")[0] for requirement in project["optional-dependencies"]["dev"]
    ] == ["pytest", "ruff", "mypy"]


# ---------------------------------------------------------------------------
# Storage composition: one place where knowledge lives
# ---------------------------------------------------------------------------


def test_semantic_memory_adds_no_table_and_no_migration() -> None:
    """Knowledge keeps exactly one home: the C2.02 ``agentx_knowledge`` table."""
    migration_names = tuple(migration.name for migration in _MIGRATIONS)
    persistence_source = (_AGENTX_SRC / "infrastructure" / "persistence.py").read_text(
        encoding="utf-8"
    )
    created_tables = set(re.findall(r"CREATE TABLE (\w+)", persistence_source))

    assert "create_knowledge_store" in migration_names
    assert [name for name in migration_names if "semantic" in name or "memory" in name] == []
    assert "agentx_knowledge" in created_tables
    assert [table for table in created_tables if "semantic" in table or "memory" in table] == []


def test_semantic_memory_contains_no_sql_and_no_schema() -> None:
    identifiers = _identifiers(_SEMANTIC_MEMORY)

    for token in ("sql", "sqlite", "table", "cursor", "connection", "commit", "migration"):
        assert not any(token in identifier for identifier in identifiers), token


def test_only_the_canonical_store_persists_knowledge() -> None:
    """No second knowledge table or store class was introduced anywhere."""
    knowledge_store_source = (_AGENTX_SRC / "infrastructure" / "knowledge_store.py").read_text(
        encoding="utf-8"
    )

    assert knowledge_store_source.count("CREATE TABLE") == 0  # owned by the migration list
    assert "agentx_knowledge" in knowledge_store_source
    assert _class_definitions("SemanticMemoryStore") == []
    assert _class_definitions("SemanticKnowledgeStore") == []


# ---------------------------------------------------------------------------
# Forbidden neighbouring scope
# ---------------------------------------------------------------------------


def test_semantic_memory_implements_no_retrieval_machinery() -> None:
    """C2.09 owns retrieval: no embeddings, vectors, similarity, or ranking.

    The check inspects executable identifiers rather than prose, so the
    module's own documentation may name what it deliberately does not do.
    """
    identifiers = _identifiers(_SEMANTIC_MEMORY)

    for token in (
        "embed",
        "vector",
        "cosine",
        "similar",
        "faiss",
        "numpy",
        "tfidf",
        "bm25",
        "rank",
        "score",
        "relevan",
        "nearest",
        "traversal",
        "consolidat",
    ):
        assert not any(token in identifier for identifier in identifiers), token


def test_semantic_memory_implements_no_lifecycle_transition() -> None:
    """C2.08 owns lifecycle: the service exposes no transition operation."""
    public_operations = {
        node.name
        for node in ast.walk(_module_tree(_SEMANTIC_MEMORY))
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }

    assert public_operations == {
        "insert",
        "get",
        "list_records",
        "matches",
        "remember",
        "recall",
        "recall_all",
        "provenance_of",
    }
    for forbidden in ("update_status", "promote", "supersede", "contradict", "resolve", "expire"):
        assert forbidden not in public_operations


def test_semantic_memory_owns_no_other_memory_class() -> None:
    identifiers = _identifiers(_SEMANTIC_MEMORY)

    for token in ("episode", "procedure", "causal", "trajectory", "artifact", "audit"):
        assert not any(token in identifier for identifier in identifiers), token
    assert _class_definitions("EpisodeStore") == [Path("agentx/infrastructure/episode_store.py")]
    assert _class_definitions("ProcedureStore") == [
        Path("agentx/infrastructure/procedure_store.py")
    ]
