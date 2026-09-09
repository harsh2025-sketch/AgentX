"""Architecture guardrails for the N2.09 procedure validation runner.

These are import/structure guardrails, not security enforcement. Authority is
owned exclusively by the Trusted Kernel; procedure lifecycle transitions are
owned by C3.09 through the canonical ``ProcedureStore``.

The runner is a top-level composition module (alongside ``agent_loop`` and
``procedure_validation``): it must read canonical procedure identity from
``agentx.core``, canonical execution/verification evidence from
``agentx.capabilities``, and the canonical M4.02 policy from the top-level
``agentx.procedure_validation`` module — without widening the boundary
manifest, duplicating M4.02, executing a capability, or reaching authority.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture
from agentx.capabilities.runtime import ClosedLoopOutcome
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.ids import TaskId
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
)
from agentx.core.result import Result
from agentx.procedure_validation import (
    ValidationDecision,
    ValidationReasonCode,
    ValidationRunKind,
)
from agentx.procedure_validation_runner import (
    ProcedureValidationCase,
    ProcedureValidationCaseResult,
    ProcedureValidationRunner,
    ProcedureValidationRunRequest,
    ProcedureValidationRunResult,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_MODULE_PATH = _AGENTX_SRC / "procedure_validation_runner.py"


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


def _defined_classes() -> dict[str, ast.ClassDef]:
    return {node.name: node for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)}


def _methods(class_name: str) -> set[str]:
    classes = _defined_classes()
    assert class_name in classes
    return {
        node.name
        for node in ast.walk(classes[class_name])
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _dataclass_fields(class_name: str) -> set[str]:
    classes = _defined_classes()
    assert class_name in classes
    fields: set[str] = set()
    for node in ast.walk(classes[class_name]):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            fields.add(node.target.id)
    return fields


def _call_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


# ---------------------------------------------------------------------------
# Placement: top-level composition, no subsystem, no manifest widening.
# ---------------------------------------------------------------------------


def test_runner_is_a_single_top_level_composition_module() -> None:
    assert _MODULE_PATH.is_file()
    assert _MODULE_PATH.parent == _AGENTX_SRC


def test_runner_is_not_inside_any_canonical_subsystem() -> None:
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        assert not _MODULE_PATH.is_relative_to(package)


def test_runner_did_not_create_a_new_subsystem() -> None:
    assert "agentx.procedure_validation_runner" not in _architecture.SUBSYSTEMS
    assert not (_AGENTX_SRC / "procedure_validation_runner").exists()
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


def test_boundary_manifest_was_not_widened() -> None:
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


# ---------------------------------------------------------------------------
# Import boundary: core + capabilities + the M4.02 policy only.
# ---------------------------------------------------------------------------


def test_runner_imports_only_core_capabilities_and_the_m4_02_policy() -> None:
    agentx_imports = [module for module in _imports() if module.startswith("agentx")]
    assert agentx_imports, "the runner must consume canonical contracts"
    for module in agentx_imports:
        allowed = (
            module.startswith("agentx.core.")
            or module.startswith("agentx.capabilities.")
            or module == "agentx.procedure_validation"
        )
        assert allowed, f"unexpected import: {module}"


def test_runner_imports_no_authority_or_governed_mechanism() -> None:
    imports = set(_imports())
    for forbidden in (
        "agentx.kernel",
        "agentx.kernel.permissions",
        "agentx.kernel.action_gate",
        "agentx.kernel.risk",
        "agentx.kernel.resource_budget",
        "agentx.kernel.emergency_stop",
        "agentx.kernel.audit",
    ):
        assert forbidden not in imports, forbidden


def test_runner_imports_no_persistence_or_lifecycle_store() -> None:
    imports = set(_imports())
    for forbidden in (
        "agentx.infrastructure",
        "agentx.infrastructure.persistence",
        "agentx.infrastructure.procedure_store",
        "agentx.infrastructure.knowledge_store",
        "agentx.infrastructure.episode_store",
        "sqlite3",
    ):
        assert forbidden not in imports, forbidden


def test_runner_imports_no_model_research_hive_or_learning() -> None:
    imports = set(_imports())
    for subsystem in ("agentx.cognition", "agentx.hive", "agentx.learning", "agentx.procedures"):
        assert not any(module.startswith(f"{subsystem}.") for module in imports), subsystem


def test_runner_imports_no_clock_identity_or_concurrency_source() -> None:
    imports = set(_imports())
    for forbidden in ("datetime", "time", "random", "uuid", "threading", "asyncio", "os", "socket"):
        assert forbidden not in imports, forbidden


def test_runner_composes_the_m4_02_policy_instead_of_duplicating_it() -> None:
    classes = _defined_classes()
    for duplicated in (
        "ValidationDecision",
        "ValidationPolicy",
        "ValidationReport",
        "ValidationRunEvidence",
        "ValidationRunKind",
        "ValidationReasonCode",
        "ProcedureCandidateIdentity",
    ):
        assert duplicated not in classes, f"the runner must not redefine {duplicated}"


# ---------------------------------------------------------------------------
# No execution, no mutation, no persistence surface.
# ---------------------------------------------------------------------------


def test_runner_never_invokes_capability_execute_or_verify() -> None:
    names = _call_names()
    assert "execute" not in names
    assert "verify" not in names
    # The canonical A2.05 Verifier is a read-only evidence evaluator; the
    # runner calls only its evaluation entry point.
    assert "evaluate" in names


def test_runner_never_transitions_a_task_or_a_status() -> None:
    imports = set(_imports())
    assert "agentx.core.task_state" not in imports
    names = _call_names()
    for forbidden in (
        "transition_task",
        "try_transition_task",
        "update_status",
        "insert",
        "commit",
        "activate",
        "promote",
    ):
        assert forbidden not in names, forbidden


def test_runner_exposes_no_mutating_or_persisting_api() -> None:
    forbidden_methods = {
        "persist",
        "promote",
        "activate",
        "execute",
        "write",
        "store",
        "save",
        "update_status",
        "insert",
    }
    for class_name in (
        "ProcedureValidationRunner",
        "ProcedureValidationCase",
        "ProcedureValidationCaseResult",
        "ProcedureValidationRunResult",
    ):
        assert not (_methods(class_name) & forbidden_methods), (
            f"{class_name} exposes {_methods(class_name) & forbidden_methods}"
        )


def test_runner_has_no_module_level_side_effects() -> None:
    forbidden_calls = {"open", "eval", "exec", "compile", "__import__", "system", "popen"}
    assert not (_call_names() & forbidden_calls)
    allowed = (
        ast.Import | ast.ImportFrom | ast.Assign | ast.AnnAssign | ast.ClassDef | ast.FunctionDef
    )
    for node in _tree().body:
        if isinstance(node, ast.Expr):
            # The module docstring is the only expression statement allowed.
            assert isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            continue
        assert isinstance(node, allowed), node


def test_runner_defines_no_procedure_graph_schema() -> None:
    classes = _defined_classes()
    for forbidden in ("ProcedureGraph", "ProcedureNode", "ProcedureEdge", "ProcedureNodeKind"):
        assert forbidden not in classes, forbidden


def test_runner_defines_no_authority_field() -> None:
    authority_words = (
        "permission",
        "permissions",
        "authority",
        "risk",
        "budget",
        "gate",
        "approval",
        "status",
        "active",
        "verified",
        "succeeded",
    )
    for class_name in (
        "ProcedureValidationCase",
        "ProcedureValidationCaseResult",
        "ProcedureValidationRunResult",
        "ProcedureValidationRunner",
        "ProcedureValidationRunRequest",
    ):
        fields = {field.lower() for field in _dataclass_fields(class_name)}
        assert not (fields & set(authority_words)), (
            f"{class_name} declares {fields & set(authority_words)}"
        )


# ---------------------------------------------------------------------------
# Contract surface: evidence vocabulary comes from M4.02, not from here.
# ---------------------------------------------------------------------------


def test_case_carries_only_minimum_variation_data() -> None:
    fields = _dataclass_fields("ProcedureValidationCase")
    assert fields == {
        "case_id",
        "run_id",
        "parameter_binding",
        "environment",
        "verification",
    }


def _sample_run_id() -> TaskId:
    return TaskId.create()


def _sample_candidate() -> ProcedureRecord:
    return ProcedureRecord.create(
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content='{"schema_version":1,"entry":"start","nodes":[],"edges":[]}',
        )
    )


class _RecordingHarness:
    """Minimal port implementation that records requests and fails closed."""

    def __init__(self) -> None:
        self.requests: list[ProcedureValidationRunRequest] = []

    def run(self, request: ProcedureValidationRunRequest) -> Result[ClosedLoopOutcome, AgentXError]:
        self.requests.append(request)
        return Result[ClosedLoopOutcome, AgentXError].failure(
            AgentXError(
                code="architecture.no_execution",
                message="the architecture guardrail harness executes nothing",
                category=ErrorCategory.PRECONDITION,
            )
        )


def test_per_case_result_reuses_the_m4_02_vocabularies() -> None:
    fields = _dataclass_fields("ProcedureValidationCaseResult")
    assert "kind" in fields
    assert "reason" in fields
    assert "passed" not in fields  # success is a canonical kind, never a bool field
    result = ProcedureValidationCaseResult(
        case_id="case-a",
        run_id=ProcedureValidationCase(
            case_id="case-a",
            run_id=_sample_run_id(),
            parameter_binding={},
        ).run_id,
        kind=ValidationRunKind.VERIFIED_SUCCESS,
        reason=None,
        evidence_considered=True,
        error_code=None,
        unmet_conditions=(),
        parameter_binding={},
        environment=None,
    )
    assert result.kind in set(ValidationRunKind)
    assert result.passed is True


def test_aggregate_result_is_a_structured_value_not_a_bool() -> None:
    fields = _dataclass_fields("ProcedureValidationRunResult")
    for expected in ("candidate", "cases", "report", "decision", "reasons", "complete"):
        assert expected in fields, expected
    assert isinstance(ProcedureValidationRunResult.eligible, property)


def test_runner_binds_the_policy_and_the_harness_at_construction() -> None:
    runner_methods = _methods("ProcedureValidationRunner")
    assert "run" in runner_methods
    assert {"harness", "policy", "max_cases"} <= runner_methods
    assert not ({"__init__", "run"} & {"persist", "promote", "activate"})


def test_eligible_for_promotion_is_not_active() -> None:
    decision_values = {member.value for member in ValidationDecision}
    status_values = {member.value for member in ProcedureStatus}
    assert decision_values.isdisjoint(status_values)


def test_reason_codes_stay_in_the_m4_02_vocabulary() -> None:
    runner = ProcedureValidationRunner(
        harness=_RecordingHarness(),
    )
    result = runner.run(
        _sample_candidate(),
        (
            ProcedureValidationCase(
                case_id="case-a", run_id=_sample_run_id(), parameter_binding={"target": "a"}
            ),
        ),
    )
    for reason in result.reasons:
        assert reason in set(ValidationReasonCode)
    assert result.case_count == 1
