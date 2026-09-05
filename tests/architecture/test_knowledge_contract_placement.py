"""Architecture tests for the canonical knowledge contract placement (C2.02).

Mirrors the Event-contract placement guard: contract inward in ``agentx.core``,
concrete storage outward in ``agentx.infrastructure``, no authority or
transport dependencies, and independence from concurrent C2.01 EpisodeStore
work (which merges separately).
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_KNOWLEDGE_CONTRACT = _AGENTX_SRC / "core" / "knowledge.py"
_KNOWLEDGE_STORE = _AGENTX_SRC / "infrastructure" / "knowledge_store.py"


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


def test_knowledge_contract_has_exactly_one_canonical_definition() -> None:
    """Only ``agentx.core.knowledge`` defines KnowledgeRecord/KnowledgeScope."""
    assert _class_definitions("KnowledgeRecord") == [Path("agentx/core/knowledge.py")]
    assert _class_definitions("KnowledgeScope") == [Path("agentx/core/knowledge.py")]
    assert _class_definitions("ProvenanceReference") == [Path("agentx/core/knowledge.py")]
    assert _class_definitions("KnowledgeStatus") == [Path("agentx/core/knowledge.py")]


def test_knowledge_store_has_exactly_one_canonical_definition() -> None:
    """Only ``agentx.infrastructure.knowledge_store`` defines KnowledgeStore."""
    assert _class_definitions("KnowledgeStore") == [
        Path("agentx/infrastructure/knowledge_store.py")
    ]


def test_knowledge_contract_module_imports_no_outer_subsystem() -> None:
    """``agentx.core`` stays inward: the contract imports only stdlib + core."""
    imported = _imported_modules(_KNOWLEDGE_CONTRACT)
    outer = [
        module
        for module in imported
        if module.startswith("agentx.") and not module.startswith("agentx.core")
    ]
    assert outer == []
    assert "sqlite3" not in imported  # the contract is storage-independent


def test_knowledge_store_avoids_kernel_authority_and_transports() -> None:
    """Storage is data-only: no kernel, no EventBus, no EventJournal writes."""
    imported = _imported_modules(_KNOWLEDGE_STORE)
    forbidden_prefixes = ("agentx.kernel", "agentx.hive", "agentx.capabilities")
    violations = [module for module in imported if module.startswith(forbidden_prefixes)]
    violations += [
        module
        for module in imported
        if module in ("agentx.infrastructure.event_bus", "agentx.infrastructure.event_journal")
    ]
    assert violations == []


def test_knowledge_store_depends_only_on_core_and_persistence() -> None:
    """The store's agentx imports are exactly core contracts + persistence."""
    imported = _imported_modules(_KNOWLEDGE_STORE)
    agentx_imports = {module for module in imported if module.startswith("agentx.")}
    assert agentx_imports <= {
        "agentx.core.ids",
        "agentx.core.knowledge",
        "agentx.core.knowledge_integrity",
        "agentx.infrastructure.persistence",
    }


def test_knowledge_modules_are_independent_of_episode_store() -> None:
    """C2.01 EpisodeStore merges separately; C2.02 must not import it."""
    for path in (_KNOWLEDGE_CONTRACT, _KNOWLEDGE_STORE):
        imported = _imported_modules(path)
        assert not any("episode" in module for module in imported), path


def test_historical_migrations_remain_immutable() -> None:
    """Migrations v1/v2 keep their canonical versions and names forever."""
    versions = tuple(migration.version for migration in _MIGRATIONS)
    names = tuple(migration.name for migration in _MIGRATIONS)

    assert versions == tuple(range(1, len(_MIGRATIONS) + 1))  # consecutive from 1
    assert names[:2] == ("create_persistence_metadata", "create_event_journal")


def test_knowledge_migration_is_registered_by_name() -> None:
    """The knowledge migration is identified by name so integration can
    resequence its version against concurrent C2.01 migration numbering."""
    knowledge_migrations = [
        migration for migration in _MIGRATIONS if migration.name == "create_knowledge_store"
    ]

    assert len(knowledge_migrations) == 1
    migration = knowledge_migrations[0]
    assert migration.statements
    assert any("agentx_knowledge" in statement for statement in migration.statements)
