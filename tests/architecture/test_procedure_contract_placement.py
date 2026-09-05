"""Architecture tests for the canonical procedure-record contract placement
(C2.03).

Mirrors the Event/Knowledge contract placement guards: contract inward in
``agentx.core``, concrete storage outward in ``agentx.infrastructure``, no
authority or transport dependencies, and independence from the C2.01
EpisodeStore and C2.02 KnowledgeStore production modules (which merge
separately). Also proves C2.03 did not steal the Day-3 A3.01 Procedure Graph
IR: ``agentx.procedures`` remains unimplemented and the record contract
carries no node/graph/interpreter semantics.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx.core.procedures import (
    ProcedurePayloadKind,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_PROCEDURE_CONTRACT = _AGENTX_SRC / "core" / "procedures.py"
_PROCEDURE_STORE = _AGENTX_SRC / "infrastructure" / "procedure_store.py"


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


def _top_level_class_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, ast.ClassDef)}


def test_procedure_contract_has_exactly_one_canonical_definition() -> None:
    """Only ``agentx.core.procedures`` defines the procedure-record contract."""
    assert _class_definitions("ProcedureRecord") == [Path("agentx/core/procedures.py")]
    assert _class_definitions("ProcedureScope") == [Path("agentx/core/procedures.py")]
    assert _class_definitions("ProcedureScopeDimension") == [Path("agentx/core/procedures.py")]
    assert _class_definitions("ProcedurePayload") == [Path("agentx/core/procedures.py")]
    assert _class_definitions("ProcedurePayloadKind") == [Path("agentx/core/procedures.py")]
    assert _class_definitions("ProcedureStatus") == [Path("agentx/core/procedures.py")]


def test_procedure_store_has_exactly_one_canonical_definition() -> None:
    """Only ``agentx.infrastructure.procedure_store`` defines ProcedureStore."""
    assert _class_definitions("ProcedureStore") == [
        Path("agentx/infrastructure/procedure_store.py")
    ]


def test_procedure_contract_module_imports_no_outer_subsystem() -> None:
    """``agentx.core`` stays inward: the contract imports only stdlib + core."""
    imported = _imported_modules(_PROCEDURE_CONTRACT)
    outer = [
        module
        for module in imported
        if module.startswith("agentx.") and not module.startswith("agentx.core")
    ]
    assert outer == []
    assert "sqlite3" not in imported  # the contract is storage-independent


def test_procedure_store_avoids_kernel_authority_and_transports() -> None:
    """Storage is data-only: no kernel, no EventBus, no EventJournal writes."""
    imported = _imported_modules(_PROCEDURE_STORE)
    forbidden_prefixes = ("agentx.kernel", "agentx.hive", "agentx.capabilities")
    violations = [module for module in imported if module.startswith(forbidden_prefixes)]
    violations += [
        module
        for module in imported
        if module in ("agentx.infrastructure.event_bus", "agentx.infrastructure.event_journal")
    ]
    assert violations == []


def test_procedure_store_depends_only_on_core_and_persistence() -> None:
    """The store's agentx imports are exactly core contracts + persistence."""
    imported = _imported_modules(_PROCEDURE_STORE)
    agentx_imports = {module for module in imported if module.startswith("agentx.")}
    assert agentx_imports <= {
        "agentx.core.ids",
        "agentx.core.procedures",
        "agentx.infrastructure.persistence",
    }


def test_procedure_modules_are_independent_of_other_stores() -> None:
    """C2.01 EpisodeStore and C2.02 KnowledgeStore merge separately; C2.03
    must not depend on either production module."""
    for path in (_PROCEDURE_CONTRACT, _PROCEDURE_STORE):
        imported = _imported_modules(path)
        assert not any("episode" in module for module in imported), path
        assert not any("knowledge" in module for module in imported), path


def test_procedures_subsystem_package_remains_unimplemented() -> None:
    """A3.01 owns the Procedure Graph IR in ``agentx.procedures``; C2.03 must
    not have planted anything in that package."""
    package = _AGENTX_SRC / "procedures"
    files = sorted(path.relative_to(_SRC_ROOT) for path in package.rglob("*.py") if path.is_file())
    assert files == [Path("agentx/procedures/__init__.py")]


def test_contract_defines_no_procedure_graph_ir() -> None:
    """The record contract carries no node/graph/interpreter semantics: no
    IR class names, and no enum member values reserved for future IR node
    vocabulary."""
    reserved_class_names = {
        "ProcedureGraph",
        "ProcedureNode",
        "Node",
        "ActionNode",
        "VerifyNode",
        "BranchNode",
        "ReasonNode",
        "Step",
        "Edge",
        "Interpreter",
        "Compiler",
        "Skill",
    }
    for path in (_PROCEDURE_CONTRACT, _PROCEDURE_STORE):
        assert _top_level_class_names(path).isdisjoint(reserved_class_names), path

    reserved_member_values = {"action", "verify", "branch", "reason", "node", "edge"}
    controlled_values = {
        member.value
        for enum_type in (ProcedureStatus, ProcedureScopeDimension, ProcedurePayloadKind)
        for member in enum_type
    }
    assert controlled_values.isdisjoint(reserved_member_values)


def test_historical_migrations_remain_immutable() -> None:
    """Migrations v1-v4 keep their canonical versions and names forever."""
    versions = tuple(migration.version for migration in _MIGRATIONS)
    names = tuple(migration.name for migration in _MIGRATIONS)

    assert versions == tuple(range(1, len(_MIGRATIONS) + 1))  # consecutive from 1
    assert names[:4] == (
        "create_persistence_metadata",
        "create_event_journal",
        "create_knowledge_store",
        "create_episode_store",
    )


def test_procedure_migration_is_registered_by_name() -> None:
    """The procedure migration is identified by name so integration can
    resequence its version against concurrent C2.01 migration numbering."""
    procedure_migrations = [
        migration for migration in _MIGRATIONS if migration.name == "create_procedure_store"
    ]

    assert len(procedure_migrations) == 1
    migration = procedure_migrations[0]
    assert migration.statements
    assert any("agentx_procedures" in statement for statement in migration.statements)
