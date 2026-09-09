"""Architecture guardrails for the N2.25 Windows transition verification boundary.

These are import/structure guardrails, not security enforcement: authority is
owned exclusively by the Trusted Kernel, capability execution by A1.10, and
task lifecycle by the canonical task orchestration.

The boundary is a top-level composition module (alongside
``agentx.procedure_validation``): it reads canonical Windows observation and
execution contracts from ``agentx.capabilities`` and the canonical JSON value
contract from ``agentx.core``, without widening the boundary manifest,
performing any mutation, executing any capability, reaching any authority, or
touching Task state.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture
from agentx.capabilities.abi import VerificationResult
from agentx.core.tasks import TaskStatus
from agentx.windows_transition_verification import (
    REQUIRED_OBSERVATION_CONTRACT,
    SUPPORTED_TRANSITION_KINDS,
    UNVERIFIABLE_TRANSITION_KINDS,
    WindowsObservationContract,
    WindowsTransitionKind,
    WindowsTransitionVerdict,
    WindowsTransitionVerification,
    WindowsTransitionVerifier,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_MODULE_PATH = _AGENTX_SRC / "windows_transition_verification.py"


def _tree() -> ast.Module:
    return ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))


def _imports() -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(imported)


def _imported_names(module: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            names.update(alias.name for alias in node.names)
    return names


def _called_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _defined_functions() -> dict[str, ast.FunctionDef]:
    return {node.name: node for node in ast.walk(_tree()) if isinstance(node, ast.FunctionDef)}


# ---------------------------------------------------------------------------
# Placement: top-level composition, no new subsystem, manifest untouched.
# ---------------------------------------------------------------------------


def test_module_is_a_single_top_level_composition_module() -> None:
    assert _MODULE_PATH.is_file()
    assert _MODULE_PATH.parent == _AGENTX_SRC
    assert not (_AGENTX_SRC / "windows_transition_verification").exists()
    assert WindowsTransitionVerifier.__module__ == "agentx.windows_transition_verification"
    assert WindowsTransitionVerification.__module__ == "agentx.windows_transition_verification"


def test_module_is_not_inside_any_canonical_subsystem() -> None:
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        assert not _MODULE_PATH.is_relative_to(package)


def test_boundary_manifest_was_not_widened() -> None:
    assert "agentx.windows_transition_verification" not in _architecture.SUBSYSTEMS
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
    expected_edges = frozenset(
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
    assert expected_edges == _architecture.ALLOWED_ARCHITECTURE_EDGES


# ---------------------------------------------------------------------------
# Import boundary: canonical observation contracts only.
# ---------------------------------------------------------------------------


def test_module_imports_exactly_the_canonical_contracts_it_consumes() -> None:
    agentx_imports = {module for module in _imports() if module.startswith("agentx")}
    assert agentx_imports == {
        "agentx.capabilities.abi",
        "agentx.capabilities.windows.process_discovery",
        "agentx.capabilities.windows.uia_tree",
        "agentx.core.tasks",
    }


def test_module_reads_only_the_json_value_contract_from_core_tasks() -> None:
    assert _imported_names("agentx.core.tasks") == {"JsonValue"}


def test_module_imports_no_authority_or_governed_mechanism() -> None:
    imports = set(_imports())
    for forbidden in (
        "agentx.kernel",
        "agentx.kernel.action_gate",
        "agentx.kernel.permissions",
        "agentx.kernel.risk",
        "agentx.kernel.resource_budget",
        "agentx.kernel.emergency_stop",
        "agentx.kernel.audit",
        "agentx.kernel.secrets",
    ):
        assert forbidden not in imports, forbidden
    assert not any(module.startswith("agentx.kernel") for module in imports)


def test_module_imports_no_execution_loop_registry_or_second_verifier() -> None:
    imports = set(_imports())
    for forbidden in (
        "agentx.capabilities.runtime",
        "agentx.capabilities.registry",
        "agentx.capabilities.executor",
        "agentx.capabilities.verifier",
        "agentx.capabilities.windows.provider",
    ):
        assert forbidden not in imports, forbidden


def test_module_imports_no_native_seam_or_platform_surface() -> None:
    imports = set(_imports())
    for forbidden in (
        "agentx.capabilities.windows._native",
        "agentx.capabilities.windows._uia_native",
        "ctypes",
        "comtypes",
        "win32api",
        "win32gui",
        "pywinauto",
        "subprocess",
        "os",
        "socket",
        "urllib",
        "urllib.request",
        "sqlite3",
        "random",
        "time",
    ):
        assert forbidden not in imports, forbidden


def test_module_imports_no_model_research_or_learning_subsystem() -> None:
    imports = set(_imports())
    for subsystem in (
        "agentx.cognition",
        "agentx.hive",
        "agentx.learning",
        "agentx.procedures",
        "agentx.infrastructure",
    ):
        assert not any(module.startswith(subsystem) for module in imports), subsystem


def test_module_never_touches_task_lifecycle_contracts() -> None:
    imports = set(_imports())
    assert "agentx.core.task_state" not in imports
    assert "agentx.core.tasks" in imports
    forbidden_calls = {"transition_task", "try_transition_task", "update_status"}
    assert not (_called_names() & forbidden_calls)


# ---------------------------------------------------------------------------
# No actions, no side effects, no clock.
# ---------------------------------------------------------------------------


def test_module_performs_no_io_or_dynamic_execution() -> None:
    forbidden = {
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
        "system",
        "popen",
        "run",
        "spawn",
        "connect",
        "urlopen",
    }
    assert not (_called_names() & forbidden)


def test_module_reads_no_clock_and_no_randomness() -> None:
    forbidden = {"now", "utcnow", "today", "monotonic", "perf_counter", "random", "choice"}
    assert not (_called_names() & forbidden)


def test_module_invokes_no_mutation_execution_or_authority_operation() -> None:
    forbidden = {
        "execute",
        "invoke",
        "click",
        "type_text",
        "send_keys",
        "launch",
        "start",
        "terminate",
        "set_value",
        "set_focus",
        "move",
        "resize",
        "minimize",
        "maximize",
        "restore",
        "set_clipboard",
        "write_clipboard",
        "discover",
        "inspect",
        "inspect_window",
        "grant",
        "request_stop",
        "check_and_consume",
    }
    assert not (_called_names() & forbidden)


def test_module_exposes_no_mutating_or_persisting_public_api() -> None:
    public_methods = {
        node.name
        for class_node in ast.walk(_tree())
        if isinstance(class_node, ast.ClassDef)
        for node in ast.walk(class_node)
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }
    forbidden = {
        "execute",
        "verify",
        "apply",
        "mutate",
        "persist",
        "save",
        "store",
        "write",
        "grant",
        "authorize",
    }
    assert not (public_methods & forbidden), public_methods & forbidden


def test_predicate_functions_cannot_see_the_native_execution_result() -> None:
    # Structural proof of "native return is not verification": no predicate
    # helper takes or mentions native execution evidence at all.
    for name, node in _defined_functions().items():
        if not name.startswith("_predicate"):
            continue
        source = ast.dump(node)
        assert "native" not in source.lower(), name
        assert "succeeded" not in source, name


def test_envelope_check_cannot_see_the_native_execution_result() -> None:
    envelope = _defined_functions()["_check_envelope"]
    dumped = ast.dump(envelope)
    assert "succeeded" not in dumped
    assert "native_evidence" not in dumped


# ---------------------------------------------------------------------------
# Vocabulary: one truth system, three verdicts, declared observation contracts.
# ---------------------------------------------------------------------------


def test_verdict_vocabulary_is_disjoint_from_task_status() -> None:
    verdicts = {member.value for member in WindowsTransitionVerdict}
    assert verdicts.isdisjoint({member.value for member in TaskStatus})


def test_emitted_evidence_is_not_a_canonical_capability_verification_result() -> None:
    assert not issubclass(WindowsTransitionVerification, VerificationResult)
    fields = set(WindowsTransitionVerification.__dataclass_fields__)
    assert "verdict" in fields
    assert "reasons" in fields
    assert fields.isdisjoint({"passed", "task", "permission", "risk", "budget"})


def test_every_transition_kind_declares_a_canonical_observation_contract() -> None:
    assert set(REQUIRED_OBSERVATION_CONTRACT) == set(WindowsTransitionKind)
    for kind in SUPPORTED_TRANSITION_KINDS:
        assert REQUIRED_OBSERVATION_CONTRACT[kind] in {
            WindowsObservationContract.PROCESS_SNAPSHOT,
            WindowsObservationContract.UIA_TREE_SNAPSHOT,
        }
    for kind in UNVERIFIABLE_TRANSITION_KINDS:
        assert REQUIRED_OBSERVATION_CONTRACT[kind] is WindowsObservationContract.NONE


def test_no_visual_or_ocr_observation_contract_was_invented() -> None:
    contracts = {member.value for member in WindowsObservationContract}
    assert contracts == {"windows_process_snapshot", "uia_tree_snapshot", "none"}
    source = _MODULE_PATH.read_text(encoding="utf-8").lower()
    for invented in ("screenshot", "ocr", "pixel match", "template match"):
        assert invented not in source.replace("no ocr", "").replace("visual/ocr", "")


def test_the_boundary_is_a_single_stateless_class() -> None:
    assert WindowsTransitionVerifier.__slots__ == ()
    assert not hasattr(WindowsTransitionVerifier(), "__dict__")
