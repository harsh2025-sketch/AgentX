"""Architecture guards for the N2.15 repair-patch materialization composition root."""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_ROOT = _SRC_ROOT / "agentx"
_MODULE = _AGENTX_ROOT / "repair_patch_materializer.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")

_EXPECTED_AGENTX_IMPORTS = {
    "agentx.core.ids",
    "agentx.core.procedures",
    "agentx.core.repair_patch",
    "agentx.procedures.graph",
}
_EXPECTED_STDLIB_IMPORTS = {"__future__", "collections", "dataclasses", "json", "typing"}


def _tree() -> ast.Module:
    return ast.parse(_SOURCE)


def _imports() -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def _classes() -> set[str]:
    return {node.name for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)}


def _module_functions() -> set[str]:
    return {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }


def _called_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _dataclass_fields(class_name: str) -> set[str]:
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        return {
            statement.target.id
            for statement in node.body
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
        }
    raise AssertionError(f"missing class {class_name}")


def test_materializer_is_one_top_level_composition_module_not_a_new_subsystem() -> None:
    assert _MODULE.is_file()
    assert _MODULE.parent == _AGENTX_ROOT
    assert "agentx.repair_patch_materializer" not in _architecture.SUBSYSTEMS
    assert not (_AGENTX_ROOT / "repair_patch_materializer").exists()
    for subsystem in _architecture.SUBSYSTEMS:
        assert not _MODULE.is_relative_to(_SRC_ROOT / Path(*subsystem.split(".")))


def test_materializer_composes_only_canonical_procedure_and_repair_contracts() -> None:
    imports = _imports()
    assert {name for name in imports if name.startswith("agentx.")} == _EXPECTED_AGENTX_IMPORTS
    assert {name.split(".", 1)[0] for name in imports if not name.startswith("agentx.")} == {
        name.split(".", 1)[0] for name in _EXPECTED_STDLIB_IMPORTS
    }


def test_materializer_never_reaches_authority_execution_model_or_storage_layers() -> None:
    imports = _imports()
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.hive",
        "agentx.learning",
        "agentx.infrastructure",
        "agentx.agent_loop",
        "agentx.procedure_validation",
        "agentx.procedure_degradation",
    )
    assert not any(imported.startswith(forbidden_prefixes) for imported in imports)

    forbidden_calls = {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "execute",
        "run",
        "validate",
        "verify",
        "activate",
        "update_status",
        "insert",
        "persist",
        "save",
        "write",
        "publish",
        "transition",
        "grant",
        "revoke",
        "now",
        "utcnow",
        "today",
        "uuid4",
        "uuid5",
        "random",
        "choice",
        "sleep",
    }
    assert _called_names().isdisjoint(forbidden_calls)


def test_materializer_surface_is_a_data_transform_not_a_lifecycle_engine() -> None:
    assert _classes() == {"RepairPatchMaterializationError", "MaterializedProcedureCandidate"}
    assert _module_functions() == {"materialize_repair_patch"}
    assert _dataclass_fields("MaterializedProcedureCandidate") == {
        "source_procedure_id",
        "source_revision",
        "repair_patch",
        "candidate",
        "schema_version",
    }

    forbidden_fields = {
        "permission",
        "authority",
        "risk",
        "action_gate",
        "budget",
        "stop",
        "task",
        "validated",
        "shadow_safe",
        "active",
        "accepted",
        "persisted",
    }
    assert _dataclass_fields("MaterializedProcedureCandidate").isdisjoint(forbidden_fields)


def test_materializer_reuses_the_canonical_graph_and_record_contracts_without_new_schema() -> None:
    assert "class ProcedureGraph" not in _SOURCE
    assert "class ProcedureNode" not in _SOURCE
    assert "class ProcedureRecord" not in _SOURCE
    assert "ProcedureStore" not in _SOURCE
    assert "ProcedureNode.from_dict" in _SOURCE
    assert "ProcedureGraph.from_json" in _SOURCE
    assert "ProcedureRecord.create" in _SOURCE
    assert "ProcedureStatus.CANDIDATE" in _SOURCE
    assert "ProcedureStore" not in {
        node.id for node in ast.walk(_tree()) if isinstance(node, ast.Name)
    }


def test_materializer_adds_no_persistence_or_migration_surface() -> None:
    names = {node.id for node in ast.walk(_tree()) if isinstance(node, ast.Name)}
    for forbidden in (
        "SQLiteDatabase",
        "ProcedureStore",
        "update_status",
        "insert",
        "connection",
        "Path",
    ):
        assert forbidden not in names
    for forbidden in ("CREATE TABLE", "_Migration", "agentx_schema_migrations"):
        assert forbidden not in _SOURCE


def test_docs_describe_the_source_binding_candidate_boundary_and_non_goals() -> None:
    docs = _REPO_ROOT / "docs" / "repair_patch_materializer.md"
    assert docs.is_file()
    text = docs.read_text(encoding="utf-8")
    for token in (
        "N2.15",
        "NODE_DEFINITION_REPLACEMENT",
        "source ProcedureId",
        "source revision",
        "CANDIDATE",
        "ProcedureStore",
        "No persistence",
        "No activation",
        "Zero new runtime dependencies",
    ):
        assert token in text
