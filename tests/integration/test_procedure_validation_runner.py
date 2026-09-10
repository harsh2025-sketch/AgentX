"""Integration tests for the varied-parameter validation runner (N2.09).

These tests run the runner over REAL canonical machinery — the A1.10
``CapabilityExecutionLoop`` (registry, ActionGate, EmergencyStop,
ResourceBudget, canonical events and audit records), the A2.05 ``Verifier``,
the M3.01 ``ProcedureInterpreter``, and the C2.03 ``ProcedureStore`` — so the
runner is proven to compose over the canonical evidence path rather than over a
test-only evidence shim.

No model, no research, no network, and no wall-clock dependency anywhere: the
capabilities are the deterministic in-memory fixtures from
``tests.support.demo_capability``.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentx.capabilities.abi import (
    CapabilityObservation,
    CapabilityRequest,
    VerificationResult,
)
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import (
    CapabilityExecutionLoop,
    ClosedLoopOutcome,
    LoopOutcome,
)
from agentx.capabilities.verifier import VerificationRequirement
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.events import Event
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.ids import TaskId
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
)
from agentx.core.result import Result
from agentx.core.task_state import try_transition_task
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.event_bus import EventBus
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.audit import SecurityAuditRecord
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope, ResourceUsage
from agentx.kernel.risk import RiskLevel
from agentx.procedure_validation import ValidationDecision, ValidationRunKind
from agentx.procedure_validation_runner import (
    ProcedureValidationCase,
    ProcedureValidationHarness,
    ProcedureValidationRunner,
    ProcedureValidationRunRequest,
)
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureEdgeKind,
    ProcedureGraph,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.procedures.interpreter import (
    InterpreterStatus,
    ProcedureInterpreter,
    StepCompleted,
)
from tests.support.demo_capability import (
    DemoNoteCapability,
    NoteWriteParams,
    write_request,
)

_T0 = Task.create(objective="seed").created_at


def _envelope(**overrides: Any) -> ResourceEnvelope:
    values: dict[str, Any] = {
        "max_wall_clock": timedelta(seconds=30),
        "max_model_calls": 0,
        "max_model_tokens": 0,
        "max_research_queries": 0,
        "max_machine_actions": 10,
        "max_repair_attempts": 0,
        "max_external_cost": Decimal("0"),
        "max_risk_level": RiskLevel.R2,
    }
    values.update(overrides)
    return ResourceEnvelope(**values)


def _context(task: Task) -> ExecutionContext:
    return ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=task.task_id,
    )


def _failed_task(objective: str) -> Task:
    pending = Task.create(objective=objective)
    running = try_transition_task(pending, TaskStatus.RUNNING).unwrap()
    return try_transition_task(running, TaskStatus.FAILED).unwrap()


class GovernedLoopHarness:
    """Adapter from the runner port to the REAL canonical A1.10 closed loop.

    It builds a canonical ``CapabilityRequest`` from the case's parameter
    binding, creates a fresh PENDING Task for the governed run (the A1.10 loop
    owns the Task lifecycle of the run it performs), and returns exactly the
    canonical ``Result`` the governed path produced. It verifies nothing,
    retries nothing, and decides nothing.
    """

    def __init__(
        self,
        *,
        capability: DemoNoteCapability,
        authority: frozenset[Permission] = frozenset({Permission.WRITE}),
        envelope: ResourceEnvelope | None = None,
    ) -> None:
        self.capability = capability
        self.registry = CapabilityRegistry()
        self.registry.register(capability)
        self.bus = EventBus()
        self.events: list[Event] = []
        self.audit_records: list[SecurityAuditRecord] = []
        self.bus.subscribe(self.events.append)
        self.budget = ResourceBudget(envelope if envelope is not None else _envelope())
        self.emergency_stop = EmergencyStop()
        self.loop = CapabilityExecutionLoop(
            registry=self.registry,
            action_gate=ActionGate(),
            authority=AuthorityContext(authority) if authority is not None else None,
            emergency_stop=self.emergency_stop,
            budget=self.budget,
            publish_event=self.bus.publish,
            publish_audit=self.audit_records.append,
        )
        self.requests: list[ProcedureValidationRunRequest] = []

    def run(self, request: ProcedureValidationRunRequest) -> Result[ClosedLoopOutcome, AgentXError]:
        self.requests.append(request)
        binding = request.case.parameter_binding
        task = Task.create(objective=f"validate {request.case.case_id}")
        params = NoteWriteParams(
            key=str(binding.get("key", "alpha")), value=str(binding.get("value", "v1"))
        )
        capability_request: CapabilityRequest[NoteWriteParams] = write_request(params)
        return self.loop.run(task, capability_request, _context(task))


def _candidate() -> ProcedureRecord:
    return ProcedureRecord.create(
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content='{"schema_version":1,"entry":"start","nodes":[],"edges":[]}',
        ),
        revision=1,
    )


def _case(
    case_id: str,
    *,
    key: str,
    value: str,
    verification: VerificationRequirement | None = None,
) -> ProcedureValidationCase:
    return ProcedureValidationCase(
        case_id=case_id,
        run_id=TaskId.create(),
        parameter_binding={"key": key, "value": value},
        verification=verification,
    )


def _runner(harness: ProcedureValidationHarness) -> ProcedureValidationRunner:
    return ProcedureValidationRunner(harness=harness)


# ---------------------------------------------------------------------------
# Real governed execution under parameter variation.
# ---------------------------------------------------------------------------


def test_real_closed_loop_validation_of_two_varied_cases() -> None:
    capability = DemoNoteCapability()
    harness = GovernedLoopHarness(capability=capability)
    cases = (
        _case("case-alpha", key="alpha", value="v1"),
        _case("case-beta", key="beta", value="v2"),
    )
    result = _runner(harness).run(_candidate(), cases)

    assert result.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION
    assert result.all_cases_passed is True
    assert result.complete is True
    assert result.report.verified_successes == 2
    assert result.report.distinct_parameter_bindings == 2
    # The real governed loop executed each case exactly once.
    assert capability.execute_calls == 2
    assert capability.verify_calls == 2
    # Real canonical evidence was produced and published by A1.10.
    assert harness.events
    assert harness.audit_records
    assert harness.budget.snapshot().machine_actions == 2
    # The variation really reached the governed capability.
    assert capability.state == {"alpha": "v1", "beta": "v2"}


def test_real_closed_loop_verification_failure_blocks_eligibility() -> None:
    capability = DemoNoteCapability(verification_mode="fail")
    harness = GovernedLoopHarness(capability=capability)
    cases = (
        _case("case-alpha", key="alpha", value="v1"),
        _case("case-beta", key="beta", value="v2"),
    )
    result = _runner(harness).run(_candidate(), cases)

    assert result.decision is ValidationDecision.REJECTED
    assert result.eligible is False
    assert all(case.kind is ValidationRunKind.VERIFICATION_FAILURE for case in result.cases)
    assert capability.execute_calls == 2
    assert capability.verify_calls == 2
    for case_result in result.cases:
        assert case_result.passed is False


def test_real_closed_loop_execution_failure_is_preserved() -> None:
    capability = DemoNoteCapability(execution_mode="fail")
    harness = GovernedLoopHarness(capability=capability)
    cases = (
        _case("case-alpha", key="alpha", value="v1"),
        _case("case-beta", key="beta", value="v2"),
    )
    result = _runner(harness).run(_candidate(), cases)
    assert result.decision is ValidationDecision.REJECTED
    assert result.cases[0].kind is ValidationRunKind.EXECUTION_FAILURE


def test_real_closed_loop_denial_is_preserved() -> None:
    """A destructive (R4) capability is denied by the canonical gate."""
    capability = DemoNoteCapability(destructive=True)
    harness = GovernedLoopHarness(capability=capability)
    cases = (
        _case("case-alpha", key="alpha", value="v1"),
        _case("case-beta", key="beta", value="v2"),
    )
    result = _runner(harness).run(_candidate(), cases)
    assert result.decision is ValidationDecision.REJECTED
    assert all(case.kind is ValidationRunKind.DENIED for case in result.cases)
    # Nothing was executed: a denial is never a success and never a bypass.
    assert capability.execute_calls == 0
    assert harness.budget.snapshot().machine_actions == 0


def test_real_closed_loop_without_authority_is_denied() -> None:
    capability = DemoNoteCapability()
    harness = GovernedLoopHarness(capability=capability, authority=frozenset())
    cases = (
        _case("case-alpha", key="alpha", value="v1"),
        _case("case-beta", key="beta", value="v2"),
    )
    result = _runner(harness).run(_candidate(), cases)
    assert result.decision is ValidationDecision.REJECTED
    assert result.cases[0].kind is ValidationRunKind.DENIED


def test_canonical_verifier_requirement_over_real_evidence() -> None:
    """A per-case A2.05 requirement is checked against real observation data."""
    capability = DemoNoteCapability()
    harness = GovernedLoopHarness(capability=capability)
    cases = (
        _case(
            "case-alpha",
            key="alpha",
            value="v1",
            verification=VerificationRequirement(
                expected_observation={"key": "alpha", "value": "v1", "stored": True}
            ),
        ),
        _case(
            "case-beta",
            key="beta",
            value="v2",
            verification=VerificationRequirement(
                expected_observation={"key": "beta", "value": "v2", "stored": True}
            ),
        ),
    )
    result = _runner(harness).run(_candidate(), cases)
    assert result.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION
    assert result.cases[0].unmet_conditions == ()
    assert result.cases[1].unmet_conditions == ()


def test_canonical_verifier_rejects_a_wrong_expected_value_over_real_evidence() -> None:
    capability = DemoNoteCapability()
    harness = GovernedLoopHarness(capability=capability)
    cases = (
        _case(
            "case-alpha",
            key="alpha",
            value="v1",
            verification=VerificationRequirement(
                expected_observation={"key": "alpha", "value": "v1", "stored": True}
            ),
        ),
        _case(
            "case-beta",
            key="beta",
            value="v2",
            verification=VerificationRequirement(
                expected_observation={"key": "beta", "value": "WRONG", "stored": True}
            ),
        ),
    )
    result = _runner(harness).run(_candidate(), cases)
    assert result.cases[0].passed is True
    assert result.cases[1].kind is ValidationRunKind.VERIFICATION_FAILURE
    assert result.cases[1].evidence_considered is False
    assert result.decision is ValidationDecision.REJECTED


def test_real_run_preserves_case_identity_and_revision_binding() -> None:
    capability = DemoNoteCapability()
    harness = GovernedLoopHarness(capability=capability)
    candidate = _candidate()
    cases = (
        _case("case-alpha", key="alpha", value="v1"),
        _case("case-beta", key="beta", value="v2"),
    )
    result = _runner(harness).run(candidate, cases)
    assert [case.case_id for case in result.cases] == ["case-alpha", "case-beta"]
    assert result.candidate.procedure_id == candidate.procedure_id
    assert result.candidate.revision == candidate.revision
    assert [request.case.case_id for request in harness.requests] == ["case-alpha", "case-beta"]
    assert all(
        request.candidate.procedure_id == candidate.procedure_id for request in harness.requests
    )


# ---------------------------------------------------------------------------
# Procedure END is not validation success (real M3.01 interpreter).
# ---------------------------------------------------------------------------


class EndOnlyInterpreterHarness:
    """Runs the REAL M3.01 interpreter for each case and reports honestly.

    The interpreter executes nothing and verifies nothing: reaching its
    ``PROCEDURE_CONTROL_TERMINATED`` state means only that graph control flow
    reached a canonical END node. This adapter therefore returns a canonical
    closed-loop outcome describing an unverified, failed run — it can never
    manufacture a passing verdict from END.
    """

    def __init__(self, graph: ProcedureGraph) -> None:
        self._graph = graph
        self.terminal_states: list[InterpreterStatus] = []
        self.requests: list[ProcedureValidationRunRequest] = []

    def run(self, request: ProcedureValidationRunRequest) -> Result[ClosedLoopOutcome, AgentXError]:
        self.requests.append(request)
        interpreter = ProcedureInterpreter(graph=self._graph)
        state = interpreter.start()
        steps = 0
        while not state.is_terminal and steps < 8:
            instruction = interpreter.instruction(state)
            if instruction.node_kind is not ProcedureNodeKind.ACTION:
                break
            # No capability is executed here: the interpreter only asked for
            # one. Control completion is still not verification.
            state = interpreter.advance(state, StepCompleted(node_kind=instruction.node_kind))
            steps += 1
        self.terminal_states.append(state.status)
        task = _failed_task(f"procedure control ended at {state.current_node.to_str()}")
        return Result[ClosedLoopOutcome, AgentXError].success(
            ClosedLoopOutcome(
                task=task,
                kind=LoopOutcome.EXECUTION_FAILED,
                error=AgentXError(
                    code="validation.end_without_verification",
                    message=(
                        "procedure control reached a canonical END node with no canonical "
                        "verification evidence"
                    ),
                    category=ErrorCategory.VERIFICATION,
                ),
                execution=None,
                observation=CapabilityObservation(
                    summary="procedure control terminated at END",
                    data={"reached_end": state.is_terminated, "steps": state.steps_taken},
                ),
                verification=None,
                budget_usage=ResourceUsage.zero(),
            )
        )


def test_procedure_end_alone_is_never_validation_success() -> None:
    graph = ProcedureGraph(
        entry=ProcedureNodeId("start"),
        nodes=(
            ProcedureNode(id=ProcedureNodeId("start"), kind=ProcedureNodeKind.ACTION),
            ProcedureNode(id=ProcedureNodeId("end"), kind=ProcedureNodeKind.END),
        ),
        edges=(
            ProcedureEdge(
                source=ProcedureNodeId("start"),
                target=ProcedureNodeId("end"),
                kind=ProcedureEdgeKind.NEXT,
            ),
        ),
    )
    harness = EndOnlyInterpreterHarness(graph)
    cases = (
        _case("case-alpha", key="alpha", value="v1"),
        _case("case-beta", key="beta", value="v2"),
    )
    result = _runner(harness).run(_candidate(), cases)

    # The real interpreter really did reach the canonical END node.
    assert harness.terminal_states == [
        InterpreterStatus.TERMINATED,
        InterpreterStatus.TERMINATED,
    ]
    # ...and END is still not validation success.
    assert result.decision is ValidationDecision.REJECTED
    assert result.eligible is False
    assert all(case.kind is ValidationRunKind.EXECUTION_FAILURE for case in result.cases)
    assert all(case.passed is False for case in result.cases)


# ---------------------------------------------------------------------------
# No lifecycle mutation through the real canonical store.
# ---------------------------------------------------------------------------


class ScriptedHarness:
    """Returns a caller-supplied canonical outcome per case (no I/O)."""

    def __init__(
        self,
        kind: LoopOutcome,
        *,
        status: TaskStatus,
        verification: VerificationResult | None = None,
    ) -> None:
        self._kind = kind
        self._status = status
        self._verification = verification
        self.requests: list[ProcedureValidationRunRequest] = []

    def run(self, request: ProcedureValidationRunRequest) -> Result[ClosedLoopOutcome, AgentXError]:
        self.requests.append(request)
        task = Task.create(objective=f"validate {request.case.case_id}")
        running = try_transition_task(task, TaskStatus.RUNNING).unwrap()
        terminal = try_transition_task(running, self._status).unwrap()
        outcome = ClosedLoopOutcome(
            task=terminal,
            kind=self._kind,
            error=None,
            execution=None,
            observation=CapabilityObservation(summary="evidence", data={"match": True}),
            verification=self._verification,
            budget_usage=ResourceUsage.zero(),
        )
        return Result[ClosedLoopOutcome, AgentXError].success(outcome)


def test_validation_evidence_never_activates_a_stored_procedure(tmp_path: Path) -> None:
    store = ProcedureStore(SQLiteDatabase(tmp_path / "procedures.sqlite3"))
    candidate = _candidate()
    store.insert(candidate)
    before = store.history(candidate.procedure_id)

    cases = (
        _case("case-alpha", key="alpha", value="v1"),
        _case("case-beta", key="beta", value="v2"),
    )
    result = _runner(
        ScriptedHarness(
            LoopOutcome.VERIFIED,
            status=TaskStatus.SUCCEEDED,
            verification=VerificationResult(passed=True, detail="postcondition confirmed"),
        )
    ).run(candidate, cases)

    assert result.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION
    after = store.history(candidate.procedure_id)
    assert after == before
    assert len(after) == 1
    stored = store.get(candidate.procedure_id, candidate.revision)
    assert stored is not None
    assert stored.status is ProcedureStatus.CANDIDATE
    assert stored.updated_at is None
    assert store.list_records() == before


def test_runner_touches_no_persistence_of_its_own(tmp_path: Path) -> None:
    """With a scripted harness the runner opens and writes nothing."""
    database = tmp_path / "untouched.sqlite3"
    store = ProcedureStore(SQLiteDatabase(database))
    candidate = _candidate()
    store.insert(candidate)
    before = database.read_bytes()

    cases = (
        _case("case-alpha", key="alpha", value="v1"),
        _case("case-beta", key="beta", value="v2"),
    )
    _runner(
        ScriptedHarness(
            LoopOutcome.VERIFIED,
            status=TaskStatus.SUCCEEDED,
            verification=VerificationResult(passed=True, detail="postcondition confirmed"),
        )
    ).run(candidate, cases)

    assert database.read_bytes() == before


def test_eligible_evidence_is_still_not_active_status() -> None:
    """ELIGIBLE_FOR_PROMOTION is evidence, never a lifecycle transition."""
    harness = GovernedLoopHarness(capability=DemoNoteCapability())
    cases = (
        _case("case-alpha", key="alpha", value="v1"),
        _case("case-beta", key="beta", value="v2"),
    )
    candidate = _candidate()
    result = _runner(harness).run(candidate, cases)
    assert result.eligible is True
    assert candidate.status is ProcedureStatus.CANDIDATE
    assert result.decision.value not in {member.value for member in ProcedureStatus}
    assert {member.value for member in ValidationDecision}.isdisjoint(
        {member.value for member in ProcedureStatus}
    )
