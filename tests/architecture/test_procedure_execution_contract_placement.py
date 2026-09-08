"""Architecture-boundary tests for the M3.02 procedure-execution trace contract.

The contract is canonical execution *history*: it must live in the inward
``agentx.core`` boundary, depend only on the standard library and sibling
``agentx.core`` contracts, and never reach outward for authority
(``agentx.kernel``), execution (``agentx.capabilities``, ``agentx.procedures``,
``agentx.cognition``), learning, memory, or plumbing. It must not duplicate a
canonical Task / Procedure / Episode schema, must expose no persistence or
execution surface, must contain no dynamic code, and must not have touched the
canonical boundary manifest or the package exports.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture
from agentx.core.procedure_execution import (
    ProcedureExecutionEvidence,
    ProcedureRunRecord,
    ProcedureStepRecord,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_MODULE_PATH = _SRC_ROOT / "agentx" / "core" / "procedure_execution.py"
_CORE_INIT_PATH = _SRC_ROOT / "agentx" / "core" / "__init__.py"
_ARCHITECTURE_PATH = _SRC_ROOT / "agentx" / "_architecture.py"

_RECORD_CLASSES = ("ProcedureRunRecord", "ProcedureStepRecord", "ProcedureExecutionEvidence")


def _source() -> str:
    return _MODULE_PATH.read_text(encoding="utf-8")


def _tree() -> ast.Module:
    return ast.parse(_source())


def _imports() -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def _function_names() -> set[str]:
    return {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _class_names() -> set[str]:
    return {node.name for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)}


def test_contract_is_placed_in_the_core_boundary() -> None:
    assert _MODULE_PATH.is_file()
    assert _MODULE_PATH.parts[-3:] == ("agentx", "core", "procedure_execution.py")
    assert ProcedureRunRecord.__module__ == "agentx.core.procedure_execution"
    assert ProcedureStepRecord.__module__ == "agentx.core.procedure_execution"
    assert ProcedureExecutionEvidence.__module__ == "agentx.core.procedure_execution"


def test_contract_imports_core_only() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx")}

    assert agentx_imports
    assert all(name.startswith("agentx.core.") for name in agentx_imports)


def test_contract_never_imports_an_outward_subsystem() -> None:
    forbidden = tuple(
        package for package in _architecture.SUBSYSTEMS if package != _architecture.CORE
    )
    imports = _imports()

    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden)


def test_core_still_has_no_allowed_outward_edge() -> None:
    """Core is the foundation: no edge in the manifest starts at core."""
    outward = {
        edge for edge in _architecture.ALLOWED_ARCHITECTURE_EDGES if edge[0] == "agentx.core"
    }

    assert outward == set()


def test_architecture_manifest_was_not_amended_for_this_contract() -> None:
    manifest = _ARCHITECTURE_PATH.read_text(encoding="utf-8")

    assert "procedure_execution" not in manifest
    assert len(_architecture.SUBSYSTEMS) == 8
    assert _architecture.CORE == "agentx.core"


def test_package_exports_were_not_edited() -> None:
    """The contract is imported by module path; it edits no package export."""
    core_init = _CORE_INIT_PATH.read_text(encoding="utf-8")

    assert "procedure_execution" not in core_init
    assert "__all__" not in core_init


def test_contract_has_no_process_network_thread_or_import_machinery() -> None:
    forbidden_roots = {
        "asyncio",
        "concurrent",
        "ctypes",
        "http",
        "httpx",
        "importlib",
        "multiprocessing",
        "os",
        "pathlib",
        "pickle",
        "requests",
        "shutil",
        "socket",
        "sqlite3",
        "subprocess",
        "threading",
        "time",
        "urllib",
    }
    roots = {name.split(".", maxsplit=1)[0] for name in _imports()}

    assert not roots & forbidden_roots


def test_contract_exposes_no_execution_or_authority_operation() -> None:
    forbidden_prefixes = (
        "execute",
        "run_",
        "invoke",
        "dispatch",
        "schedule",
        "replay",
        "resume",
        "retry",
        "rollback",
        "grant",
        "authorize",
        "permit",
        "allow",
        "promote",
        "compile",
        "interpret",
        "traverse",
        "persist",
        "save",
        "store",
        "load",
        "publish",
        "emit",
        "verify",
        "succeed",
        "complete",
        "mark",
    )
    names = _function_names() | {
        name
        for record in (ProcedureRunRecord, ProcedureStepRecord, ProcedureExecutionEvidence)
        for name in dir(record)
        if not name.startswith("_") and callable(getattr(record, name, None))
    }

    assert not any(name.startswith(prefix) for name in names for prefix in forbidden_prefixes)


def test_contract_has_no_persistence_or_event_publication_machinery() -> None:
    source = _source().lower()

    for fragment in ("sqlite", "eventbus", "event_bus", "journal", "cursor", "commit(", "connect("):
        assert fragment not in source


def test_contract_contains_no_dynamic_code() -> None:
    called: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)

    assert not called & {"eval", "exec", "compile", "__import__", "import_module"}


def test_contract_defines_no_duplicate_task_procedure_or_episode_schema() -> None:
    duplicated = {
        "Task",
        "TaskId",
        "TaskStatus",
        "EpisodeRecord",
        "EpisodeId",
        "EpisodeOutcome",
        "ProcedureRecord",
        "ProcedureId",
        "ProcedureStatus",
        "ProcedurePayload",
        "ProcedureGraph",
        "ProcedureNode",
        "ProcedureNodeId",
        "ProcedureNodeKind",
        "ProcedureEdge",
        "ProcedureEdgeKind",
        "VerificationResult",
        "CapabilityObservation",
        "ExecutionResult",
        "Event",
        "AgentXError",
    }

    assert not _class_names() & duplicated


def test_canonical_identities_are_referenced_never_redefined() -> None:
    identity_owner = Path("agentx/core/ids.py")
    for identity in ("TaskId", "ProcedureId", "EpisodeId", "ArtifactId"):
        owners = sorted(
            path.relative_to(_SRC_ROOT)
            for path in _SRC_ROOT.rglob("*.py")
            if any(
                isinstance(node, ast.ClassDef) and node.name == identity
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            )
        )
        assert owners == [identity_owner], (identity, owners)


def test_records_carry_no_success_or_authority_field() -> None:
    forbidden_fields = {
        "success",
        "succeeded",
        "ok",
        "passed",
        "verified",
        "is_verified",
        "authorized",
        "permission",
        "permissions",
        "authority",
        "risk",
        "risk_level",
        "budget",
        "approved",
    }
    for node in _tree().body:
        if isinstance(node, ast.ClassDef) and node.name in _RECORD_CLASSES:
            fields = {
                child.target.id
                for child in node.body
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
            }
            assert not fields & forbidden_fields, (node.name, fields & forbidden_fields)


def test_contract_declares_no_module_level_record_instances() -> None:
    for node in _tree().body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            assert value.func.id not in _RECORD_CLASSES


def test_contract_owns_the_only_procedure_execution_trace_module() -> None:
    owners = sorted(
        path.relative_to(_SRC_ROOT)
        for path in _SRC_ROOT.rglob("*.py")
        if any(
            isinstance(node, ast.ClassDef) and node.name in _RECORD_CLASSES
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    )

    assert owners == [Path("agentx/core/procedure_execution.py")]
