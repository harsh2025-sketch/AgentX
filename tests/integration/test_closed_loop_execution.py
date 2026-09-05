"""A1.10 integration tests: the first closed-loop deterministic execution path.

Every test here runs the real canonical contracts end to end: the A1.09
registry resolves the capability, the C1.07 PermissionEngine/ActionGate decide
authority, the C1.09 EmergencyStop and A1.07 ExecutionContext are observed,
the C1.08 ResourceBudget is consumed atomically, and the evidence flows
through the real C1.03 EventBus as canonical C1.02 events plus canonical
C1.09 audit records.

There is no model anywhere in these tests: the loop is fully deterministic.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from agentx.capabilities.abi import (
    CapabilityDescriptor,
    CapabilityIdentity,
    CapabilityName,
    CapabilityObservation,
    CapabilityPlatform,
    CapabilityRequest,
    CapabilityScope,
    CapabilityVersion,
    ExecutionResult,
    ResourceEstimate,
    RollbackDeclaration,
    RollbackSupport,
    VerificationResult,
)
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import (
    CapabilityExecutionLoop,
    ClosedLoopOutcome,
    LoopOutcome,
)
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.events import (
    ActionPayload,
    DecisionPayload,
    Event,
    EventCategory,
    EventType,
    SelectionPayload,
    VerificationPayload,
)
from agentx.core.execution import CancellationSource, Deadline, ExecutionContext
from agentx.core.ids import TaskId
from agentx.core.result import Result
from agentx.core.task_state import can_transition
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.event_bus import EventBus
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.audit import AuditOutcome, SecurityAuditRecord
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope, ResourceUsage
from agentx.kernel.risk import RiskLevel, assess_risk
from tests.support.demo_capability import (
    DemoNoteCapability,
    HostileMetadataCapability,
    NoteWriteParams,
    hostile_identity,
    hostile_request,
    write_identity,
    write_request,
)

_MAX_SHORT_DETERMINISTIC_WAIT = timedelta(seconds=30)


# ---------------------------------------------------------------------------
# Fixtures and helpers.
# ---------------------------------------------------------------------------


def make_envelope(**overrides: Any) -> ResourceEnvelope:
    """A deterministic envelope with zero model resources by construction."""
    values: dict[str, Any] = {
        "max_wall_clock": _MAX_SHORT_DETERMINISTIC_WAIT,
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


class Harness:
    """Wires the closed loop to the real canonical collaborators.

    This is the A1.10 composition root exactly as production will compose it:
    canonical registry, canonical gate, one externally granted authority,
    canonical emergency stop, canonical budget, and the canonical EventBus as
    the event sink plus a canonical audit-record sink.
    """

    def __init__(
        self,
        *,
        authority: frozenset[Permission] | None = frozenset({Permission.WRITE}),
        envelope: ResourceEnvelope | None = None,
    ) -> None:
        self.registry = CapabilityRegistry()
        self.bus = EventBus()
        self.events: list[Event] = []
        self.audit_records: list[SecurityAuditRecord] = []
        self.bus.subscribe(self.events.append)
        self.budget = ResourceBudget(envelope if envelope is not None else make_envelope())
        self.emergency_stop = EmergencyStop()
        self.authority = None if authority is None else AuthorityContext(authority)
        self.loop = CapabilityExecutionLoop(
            registry=self.registry,
            action_gate=ActionGate(),
            authority=self.authority,
            emergency_stop=self.emergency_stop,
            budget=self.budget,
            publish_event=self.bus.publish,
            publish_audit=self.audit_records.append,
        )

    def make_task(self, objective: str = "write the demo note for key alpha") -> Task:
        return Task.create(objective=objective)

    def make_context(self, task: Task) -> ExecutionContext:
        return ExecutionContext(
            correlation_id=uuid4(),
            cancellation_token=CancellationSource().token,
            task_id=task.task_id,
        )

    def run(
        self,
        capability: DemoNoteCapability | HostileMetadataCapability,
        *,
        task: Task | None = None,
        request: CapabilityRequest[NoteWriteParams] | None = None,
        context: ExecutionContext | None = None,
        params: NoteWriteParams | None = None,
    ) -> Result[ClosedLoopOutcome, AgentXError]:
        """Register ``capability`` and run one closed-loop invocation."""
        if capability.descriptor.identity not in self.registry:
            self.registry.register(capability)
        resolved_task = task if task is not None else self.make_task()
        resolved_context = context if context is not None else self.make_context(resolved_task)
        resolved_request = (
            request
            if request is not None
            else write_request(
                params if params is not None else NoteWriteParams(key="alpha", value="beta")
            )
        )
        return self.loop.run(resolved_task, resolved_request, resolved_context)

    def run_any(
        self,
        capability: DemoNoteCapability | HostileMetadataCapability,
    ) -> Result[ClosedLoopOutcome, AgentXError]:
        """Register ``capability`` and run it with its own canonical request."""
        if capability.descriptor.identity not in self.registry:
            self.registry.register(capability)
        if isinstance(capability, DemoNoteCapability):
            request: CapabilityRequest[NoteWriteParams] = write_request(
                NoteWriteParams(key="alpha", value="beta")
            )
        else:
            request = hostile_request(NoteWriteParams(key="alpha", value="beta"))
        task = self.make_task()
        return self.loop.run(task, request, self.make_context(task))


def outcome_of(result: Result[ClosedLoopOutcome, AgentXError]) -> ClosedLoopOutcome:
    """Unwrap a loop result, failing the test on a refusal."""
    assert result.is_success, f"loop refused the run: {result.unwrap_error()!r}"
    return result.unwrap()


def event_types(events: list[Event]) -> tuple[EventType, ...]:
    return tuple(event.event_type for event in events)


def find_event(events: list[Event], event_type: EventType) -> Event | None:
    for event in events:
        if event.event_type is event_type:
            return event
    return None


def decision_payload_of(event: Event) -> DecisionPayload:
    """Narrow a policy-decision event to its canonical payload type."""
    assert isinstance(event.payload, DecisionPayload)
    return event.payload


def selection_payload_of(event: Event) -> SelectionPayload:
    """Narrow a capability-selected event to its canonical payload type."""
    assert isinstance(event.payload, SelectionPayload)
    return event.payload


def action_payload_of(event: Event) -> ActionPayload:
    """Narrow an action event to its canonical payload type."""
    assert isinstance(event.payload, ActionPayload)
    return event.payload


def verification_payload_of(event: Event) -> VerificationPayload:
    """Narrow a verification event to its canonical payload type."""
    assert isinstance(event.payload, VerificationPayload)
    return event.payload


def deny_decisions(events: list[Event]) -> list[Event]:
    """Return every published DENY policy-decision event, in order."""
    return [
        event
        for event in events
        if event.event_type is EventType.POLICY_DECISION
        and decision_payload_of(event).decision == "DENY"
    ]


# ---------------------------------------------------------------------------
# Happy path: the required end-to-end verified-success demonstration.
# ---------------------------------------------------------------------------


def test_end_to_end_verified_success() -> None:
    """Task -> Registry -> Gate -> Budget -> Execute -> Observation -> Verify."""
    harness = Harness()
    capability = DemoNoteCapability()
    result = harness.run(capability)
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.verified is True
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert outcome.verification is not None and outcome.verification.passed is True
    assert outcome.observation is not None
    assert outcome.execution is not None
    assert capability.state == {"alpha": "beta"}
    # The legal canonical path: PENDING -> RUNNING -> SUCCEEDED.
    assert can_transition(TaskStatus.RUNNING, TaskStatus.SUCCEEDED)
    assert event_types(harness.events) == (
        EventType.TASK_STARTED,
        EventType.CAPABILITY_SELECTED,
        EventType.POLICY_DECISION,
        EventType.POLICY_DECISION,
        EventType.ACTION_REQUESTED,
        EventType.ACTION_COMPLETED,
        EventType.OBSERVATION_RECORDED,
        EventType.VERIFICATION_COMPLETED,
        EventType.TASK_COMPLETED,
    )


def test_exact_capability_resolution_uses_the_registry() -> None:
    """Resolution is exact-identity and produces canonical descriptor data."""
    harness = Harness()
    capability = DemoNoteCapability()
    harness.registry.register(capability)

    # A different version of the same name is a different identity: unknown.
    other_version = CapabilityIdentity(
        name=write_identity().name,
        version=CapabilityVersion(2, 0, 0),
    )
    assert harness.registry.get(other_version) is None

    result = harness.run(
        capability,
        task=harness.make_task(),
        request=CapabilityRequest(identity=write_identity(), params=NoteWriteParams("k", "v")),
        context=None,
        params=NoteWriteParams("k", "v"),
    )
    outcome = outcome_of(result)
    assert outcome.kind is LoopOutcome.VERIFIED
    selected = find_event(harness.events, EventType.CAPABILITY_SELECTED)
    assert selected is not None
    assert selection_payload_of(selected).selection == "demo.note.write@1.0.0"


# ---------------------------------------------------------------------------
# Fail-closed: pre-action gates.
# ---------------------------------------------------------------------------


class _UnauditedRiskCapability:
    """Descriptor declares no permissions while effective risk is R2.

    A risk-bearing capability that declares no required permission can never
    be checked against authority, so the loop must fail closed.
    """

    def __init__(self) -> None:
        self.execute_calls = 0
        self._descriptor = CapabilityDescriptor(
            identity=CapabilityIdentity(
                name=CapabilityName("demo.unchecked.risk"),
                version=CapabilityVersion(1, 0, 0),
            ),
            description="Deterministic fixture with an unchecked risk declaration.",
            scope=CapabilityScope(platform=CapabilityPlatform.ANY),
            required_permissions=frozenset(),
            risk_assessment=assess_risk(
                read_only=False,
                modifies_state=True,
                reversible=False,
                external_effect=False,
            ),
            preconditions=(),
            rollback=RollbackDeclaration(
                support=RollbackSupport.UNSUPPORTED,
                detail="The fixture performs no effect.",
            ),
            estimate=ResourceEstimate(
                wall_clock=timedelta(0),
                machine_actions=1,
                external_cost=Decimal("0"),
            ),
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[NoteWriteParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        self.execute_calls += 1
        return ExecutionResult(
            succeeded=True,
            message="unchecked fixture executed",
            observation=CapabilityObservation(summary="unchecked fixture executed"),
        )

    def verify(
        self,
        request: CapabilityRequest[NoteWriteParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        return VerificationResult(passed=True, detail="unchecked fixture self-certifies")


def test_risk_without_declared_permissions_fails_closed() -> None:
    """No declared permission + risk above R0 can never be checked: deny."""
    harness = Harness(authority=frozenset({Permission.WRITE}))
    capability = _UnauditedRiskCapability()
    request = CapabilityRequest(
        identity=capability.descriptor.identity,
        params=NoteWriteParams(key="alpha", value="beta"),
    )
    result = harness.run(capability, request=request)  # type: ignore[arg-type]
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.code == "runtime.permission_denied"
    assert capability.execute_calls == 0
    denial = find_event(harness.events, EventType.POLICY_DECISION)
    assert denial is not None
    assert decision_payload_of(denial).decision == "DENY"


def test_unknown_capability_fails_closed() -> None:
    harness = Harness()
    capability = DemoNoteCapability()
    # Register nothing: the identity cannot resolve.
    task = harness.make_task()
    request = write_request(NoteWriteParams(key="alpha", value="beta"))
    context = harness.make_context(task)
    result = harness.loop.run(task, request, context)

    assert result.is_success  # the loop ran to a deterministic terminal state
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.execution is None and outcome.verification is None
    assert capability.execute_calls == 0 and capability.verify_calls == 0
    assert harness.events == [event for event in harness.events]  # evidence exists
    assert event_types(harness.events) == (
        EventType.TASK_STARTED,
        EventType.TASK_FAILED,
    )
    assert outcome.error is not None
    assert outcome.error.category is ErrorCategory.NOT_FOUND


def test_invalid_request_is_refused_without_side_effects() -> None:
    """Malformed/invalid execution requests fail closed and touch nothing."""
    harness = Harness()
    capability = DemoNoteCapability()
    harness.registry.register(capability)
    task = harness.make_task()
    context = harness.make_context(task)

    # A non-PENDING task is an invalid request: refused before any state
    # change, event, or audit record.
    already_running = Task.create(
        objective="another objective",
        status=TaskStatus.RUNNING,
    )
    refusal = harness.loop.run(already_running, write_request(NoteWriteParams("a", "b")), context)
    assert refusal.is_failure
    assert refusal.unwrap_error().category is ErrorCategory.VALIDATION

    # A context bound to a different Task is refused as well.
    other_context = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=TaskId.create(),
    )
    mismatch = harness.loop.run(task, write_request(NoteWriteParams("a", "b")), other_context)
    assert mismatch.is_failure
    assert mismatch.unwrap_error().category is ErrorCategory.VALIDATION

    # Wrong argument types are programming errors.
    with pytest.raises(TypeError):
        harness.loop.run("not a task", write_request(NoteWriteParams("a", "b")), context)  # type: ignore[arg-type]

    assert capability.execute_calls == 0
    assert harness.events == [] and harness.audit_records == []
    assert task.status is TaskStatus.PENDING


def test_registry_lookup_performs_no_execution() -> None:
    """Discovery is not authority and never invokes capability code."""
    harness = Harness()
    capability = DemoNoteCapability()
    harness.registry.register(capability)
    assert write_identity() in harness.registry
    assert harness.registry.require(write_identity()) is capability
    assert harness.registry.describe(write_identity()) is capability.descriptor
    assert capability.execute_calls == 0
    assert capability.verify_calls == 0
    assert harness.events == [] and harness.audit_records == []


def test_permission_denial_prevents_execute() -> None:
    harness = Harness(authority=None)  # nothing is granted
    capability = DemoNoteCapability()
    result = harness.run(capability)
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.category is ErrorCategory.PERMISSION
    assert outcome.error.code == "runtime.permission_denied"
    assert capability.execute_calls == 0 and capability.verify_calls == 0
    decision = find_event(harness.events, EventType.POLICY_DECISION)
    assert decision is not None and decision_payload_of(decision).decision == "DENY"


def test_action_gate_denial_prevents_execute() -> None:
    """R4 without DESTRUCTIVE authority is gate-denied even with permission."""
    harness = Harness(authority=frozenset({Permission.WRITE}))
    capability = DemoNoteCapability(destructive=True)
    result = harness.run(capability)
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.code == "runtime.gate_denied"
    assert capability.execute_calls == 0 and capability.verify_calls == 0
    denied = find_event(harness.events, EventType.POLICY_DECISION)
    assert denied is not None and decision_payload_of(denied).decision == "DENY"


def test_effective_risk_floor_cannot_be_bypassed() -> None:
    """A forged low-risk claim cannot lower the characteristic-derived risk."""
    harness = Harness(authority=frozenset({Permission.EXECUTE}))
    capability = HostileMetadataCapability()
    result = harness.run(
        capability,
        request=hostile_request(NoteWriteParams(key="alpha", value="beta")),
    )
    outcome = outcome_of(result)

    # The metadata text claims risk=R0; the canonical characteristic floor is
    # R3, which requires confirmation that no deterministic loop can grant.
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.code == "runtime.gate_denied"
    assert "REQUIRE_CONFIRMATION" in outcome.error.message
    assert capability.execute_calls == 0 and capability.verify_calls == 0


def test_emergency_stop_prevents_execute() -> None:
    harness = Harness()
    capability = DemoNoteCapability()
    harness.emergency_stop.request_stop()
    result = harness.run(capability)
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.DENIED
    # Safety stops take the canonical CANCELLED path, never success.
    assert outcome.task.status is TaskStatus.CANCELLED
    assert capability.execute_calls == 0 and capability.verify_calls == 0
    assert any(record.outcome is AuditOutcome.STOP_REQUESTED for record in harness.audit_records)
    denied = deny_decisions(harness.events)
    assert denied
    deny_reason = decision_payload_of(denied[-1]).reason
    assert deny_reason is not None and "emergency stop" in deny_reason


def test_insufficient_budget_prevents_execute() -> None:
    harness = Harness(envelope=make_envelope(max_machine_actions=0))
    capability = DemoNoteCapability()
    before = harness.budget.snapshot()
    result = harness.run(capability)
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.category is ErrorCategory.RESOURCE
    assert capability.execute_calls == 0 and capability.verify_calls == 0
    # A rejected execution consumes nothing.
    assert harness.budget.snapshot() == before


def test_budget_risk_ceiling_denies_execution() -> None:
    """The envelope risk ceiling is enforced independently of the gate."""
    harness = Harness(envelope=make_envelope(max_risk_level=RiskLevel.R0))
    capability = DemoNoteCapability()  # effective risk R1 > ceiling R0
    result = harness.run(capability)
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.error is not None
    assert outcome.error.category is ErrorCategory.RESOURCE
    assert capability.execute_calls == 0


def test_cancelled_context_prevents_execute() -> None:
    harness = Harness()
    capability = DemoNoteCapability()
    task = harness.make_task()
    source = CancellationSource()
    context = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=source.token,
        task_id=task.task_id,
    )
    source.request_cancellation("operator requested stop")
    result = harness.run(capability, task=task, context=context)
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.CANCELLED
    assert outcome.error is not None
    assert outcome.error.code == "runtime.context_stopped"
    assert capability.execute_calls == 0 and capability.verify_calls == 0


def test_expired_context_prevents_execute() -> None:
    """A deadline already reached counts as expired: the run never executes."""
    harness = Harness()
    capability = DemoNoteCapability()
    task = harness.make_task()
    context = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=task.task_id,
        deadline=Deadline.after(0.0),
    )
    assert context.observe_stop().timed_out
    result = harness.run(capability, task=task, context=context)
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.CANCELLED
    assert outcome.error is not None
    assert "timeout" in outcome.error.details["stop_reasons"]
    assert capability.execute_calls == 0 and capability.verify_calls == 0


# ---------------------------------------------------------------------------
# The verified-success invariant.
# ---------------------------------------------------------------------------


def test_execution_result_alone_does_not_mark_success() -> None:
    """A returned succeeded=True result is not success without verification."""
    harness = Harness()
    capability = DemoNoteCapability(verification_mode="raise")
    result = harness.run(capability)
    outcome = outcome_of(result)

    assert capability.execute_calls == 1
    assert outcome.execution is not None and outcome.execution.succeeded is True
    assert outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.verification is None  # never fabricated


def test_observation_alone_does_not_mark_success() -> None:
    """Recorded observation evidence is not success without verification."""
    harness = Harness()
    capability = DemoNoteCapability(verification_mode="fail")
    result = harness.run(capability)
    outcome = outcome_of(result)

    recorded = find_event(harness.events, EventType.OBSERVATION_RECORDED)
    assert recorded is not None  # observation evidence exists
    assert outcome.observation is not None
    assert outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert outcome.task.status is TaskStatus.FAILED
    completed = find_event(harness.events, EventType.TASK_COMPLETED)
    assert completed is None


def test_verify_is_required_on_the_successful_path() -> None:
    harness = Harness()
    capability = DemoNoteCapability()
    result = harness.run(capability)
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.VERIFIED
    assert capability.verify_calls == 1
    verification_event = find_event(harness.events, EventType.VERIFICATION_COMPLETED)
    assert verification_event is not None
    assert verification_payload_of(verification_event).passed is True


def test_verification_failure_prevents_task_success() -> None:
    """End-to-end: Task -> Registry -> Gate -> Execute -> Observation -> VERIFY FAIL."""
    harness = Harness()
    capability = DemoNoteCapability(verification_mode="fail")
    result = harness.run(capability)
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert outcome.verification is not None and outcome.verification.passed is False
    assert outcome.task.status is TaskStatus.FAILED
    assert capability.execute_calls == 1 and capability.verify_calls == 1
    assert event_types(harness.events) == (
        EventType.TASK_STARTED,
        EventType.CAPABILITY_SELECTED,
        EventType.POLICY_DECISION,
        EventType.POLICY_DECISION,
        EventType.ACTION_REQUESTED,
        EventType.ACTION_COMPLETED,
        EventType.OBSERVATION_RECORDED,
        EventType.VERIFICATION_COMPLETED,
        EventType.TASK_FAILED,
    )
    # The machine action really happened, so it is represented in usage...
    assert outcome.budget_usage.machine_actions == 1
    assert outcome.budget_usage.model_calls == 0 and outcome.budget_usage.model_tokens == 0


def test_verification_exception_fails_closed() -> None:
    harness = Harness()
    capability = DemoNoteCapability(verification_mode="raise")
    result = harness.run(capability)
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.category is ErrorCategory.VERIFICATION
    assert outcome.error.code == "runtime.verification_raised"
    assert outcome.verification is None  # the loop never fabricates a verdict
    verification_event = find_event(harness.events, EventType.VERIFICATION_COMPLETED)
    assert verification_event is not None
    assert verification_payload_of(verification_event).passed is False


def test_execution_exception_fails_closed() -> None:
    harness = Harness()
    capability = DemoNoteCapability(execution_mode="raise")
    result = harness.run(capability)
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.EXECUTION_FAILED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.category is ErrorCategory.EXECUTION
    assert outcome.error.code == "runtime.capability_execution_raised"
    assert outcome.execution is None and outcome.verification is None
    assert capability.execute_calls == 1 and capability.verify_calls == 0
    failed_action = find_event(harness.events, EventType.ACTION_FAILED)
    assert failed_action is not None
    assert action_payload_of(failed_action).data["failure"] == "exception"


def test_execution_failure_result_fails_closed() -> None:
    """An explicit failed execution result short-circuits before verify."""
    harness = Harness()
    capability = DemoNoteCapability(execution_mode="fail")
    result = harness.run(capability)
    outcome = outcome_of(result)

    assert outcome.kind is LoopOutcome.EXECUTION_FAILED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.code == "runtime.capability_execution_failed"
    assert capability.execute_calls == 1 and capability.verify_calls == 0
    assert outcome.verification is None
    assert find_event(harness.events, EventType.VERIFICATION_COMPLETED) is None


# ---------------------------------------------------------------------------
# Invocation-count and path invariants.
# ---------------------------------------------------------------------------


def test_successful_path_invokes_execute_and_verify_exactly_once() -> None:
    harness = Harness()
    capability = DemoNoteCapability()
    result = harness.run(capability)
    outcome_of(result)
    assert capability.execute_calls == 1
    assert capability.verify_calls == 1


def test_denied_paths_invoke_execute_zero_times() -> None:
    scenarios: list[tuple[str, Harness, DemoNoteCapability | HostileMetadataCapability]] = [
        ("unknown capability", Harness(), DemoNoteCapability()),
        ("no authority", Harness(authority=None), DemoNoteCapability()),
        (
            "gate denial",
            Harness(authority=frozenset({Permission.WRITE})),
            DemoNoteCapability(destructive=True),
        ),
        (
            "risk floor",
            Harness(authority=frozenset({Permission.EXECUTE})),
            HostileMetadataCapability(),
        ),
        (
            "emergency stop",
            _stopped_harness(),
            DemoNoteCapability(),
        ),
        (
            "no budget",
            Harness(envelope=make_envelope(max_machine_actions=0)),
            DemoNoteCapability(),
        ),
    ]
    for name, harness, capability in scenarios:
        if name == "unknown capability":
            # Deliberately do not register: resolution must fail.
            task = harness.make_task()
            context = harness.make_context(task)
            result = harness.loop.run(task, write_request(NoteWriteParams("a", "b")), context)
        else:
            result = harness.run_any(capability)
        assert result.is_success, name
        outcome = result.unwrap()
        assert outcome.kind is LoopOutcome.DENIED, name
        assert outcome.task.status in (TaskStatus.FAILED, TaskStatus.CANCELLED), name
        assert outcome.execution is None and outcome.verification is None, name
        if isinstance(capability, DemoNoteCapability):
            assert capability.execute_calls == 0 and capability.verify_calls == 0, name
        else:
            assert capability.execute_calls == 0 and capability.verify_calls == 0, name
        assert find_event(harness.events, EventType.ACTION_REQUESTED) is None, name
        assert find_event(harness.events, EventType.TASK_COMPLETED) is None, name


def _stopped_harness() -> Harness:
    harness = Harness()
    harness.emergency_stop.request_stop()
    return harness


def test_failure_paths_never_fabricate_verification_results() -> None:
    capabilities = [
        DemoNoteCapability(),
        DemoNoteCapability(execution_mode="fail"),
        DemoNoteCapability(execution_mode="raise"),
        DemoNoteCapability(verification_mode="fail"),
        DemoNoteCapability(verification_mode="raise"),
    ]
    for capability in capabilities:
        harness = Harness()
        result = harness.run(capability)
        outcome = result.unwrap()
        if outcome.kind is LoopOutcome.VERIFIED:
            assert outcome.verification is not None
            assert outcome.verification.passed is True
            assert outcome.task.status is TaskStatus.SUCCEEDED
        else:
            assert outcome.task.status is not TaskStatus.SUCCEEDED
            if outcome.kind is LoopOutcome.VERIFICATION_FAILED:
                # Either the capability's own returned failed verdict, or None
                # when verify raised — never a fabricated one.
                if outcome.verification is not None:
                    assert outcome.verification.passed is False
            else:
                assert outcome.verification is None


# ---------------------------------------------------------------------------
# Budget, events, audit, ordering.
# ---------------------------------------------------------------------------


def test_budget_accounting_is_deterministic() -> None:
    usages: list[ResourceUsage] = []
    for _ in range(2):
        harness = Harness()
        capability = DemoNoteCapability()
        result = harness.run(capability)
        outcome = outcome_of(result)
        usages.append(outcome.budget_usage)
        assert harness.budget.snapshot() == outcome.budget_usage

    assert usages[0] == usages[1]
    usage = usages[0]
    assert usage.machine_actions == 1  # the descriptor's declared estimate
    assert usage.model_calls == 0
    assert usage.model_tokens == 0
    assert usage.research_queries == 0
    assert usage.repair_attempts == 0
    assert usage.external_cost == Decimal("0")
    assert usage.wall_clock == timedelta(0)


def test_events_use_the_canonical_event_contract() -> None:
    harness = Harness()
    capability = DemoNoteCapability()
    task = harness.make_task()
    context = harness.make_context(task)
    result = harness.run(capability, task=task, context=context)
    outcome_of(result)

    assert harness.events  # events arrived through the real EventBus
    previous_id: UUID | None = None
    for event in harness.events:
        assert isinstance(event, Event)
        assert event.correlation_id == context.correlation_id
        assert event.task_id == task.task_id_str
        assert event.source == "agentx.capabilities.runtime"
        assert event.causation_id == previous_id
        assert event.causation_id != event.event_id
        # The canonical serialization round-trips.
        decoded = Event.from_dict(event.to_dict())
        assert decoded.to_dict() == event.to_dict()
        previous_id = event.event_id


def test_audit_evidence_uses_the_canonical_mechanism() -> None:
    harness = Harness()
    capability = DemoNoteCapability()
    task = harness.make_task()
    context = harness.make_context(task)
    result = harness.run(capability, task=task, context=context)
    outcome_of(result)

    outcomes = [record.outcome for record in harness.audit_records]
    assert outcomes == [
        AuditOutcome.ALLOW,  # permission + gate decision
        AuditOutcome.ALLOW,  # budget check-and-consume
        AuditOutcome.SUCCEEDED,  # verified run outcome
    ]
    for record in harness.audit_records:
        assert isinstance(record, SecurityAuditRecord)
        assert record.task_id == task.task_id
        assert record.correlation_id == context.correlation_id
        assert record.reason


def test_causal_ordering_is_deterministic() -> None:
    capability = DemoNoteCapability()
    sequences: list[tuple[EventType, ...]] = []
    for _ in range(3):
        harness = Harness()
        result = harness.run(capability)
        outcome_of(result)
        sequences.append(event_types(harness.events))
    assert sequences[0] == sequences[1] == sequences[2]

    failed_harness = Harness()
    failed_capability = DemoNoteCapability(verification_mode="fail")
    failed_result = failed_harness.run(failed_capability)
    outcome_of(failed_result)
    assert event_types(failed_harness.events)[-2:] == (
        EventType.VERIFICATION_COMPLETED,
        EventType.TASK_FAILED,
    )


def test_no_model_events_or_reasoning_appear_anywhere() -> None:
    harness = Harness()
    capability = DemoNoteCapability()
    result = harness.run(capability)
    outcome_of(result)
    for event in harness.events:
        assert event.category is not EventCategory.REASONING
        assert event.category is not EventCategory.LEARNING
        assert event.category is not EventCategory.RESEARCH


# ---------------------------------------------------------------------------
# Authority containment.
# ---------------------------------------------------------------------------


def test_capability_cannot_self_grant_authority() -> None:
    """Capabilities receive no authority and cannot widen the loop's grant."""
    harness = Harness(authority=frozenset({Permission.WRITE}))
    capability = DemoNoteCapability()
    result = harness.run(capability)
    outcome_of(result)

    request_arg, context_arg = capability.last_execute_args or (None, None)
    assert isinstance(request_arg, CapabilityRequest)
    assert isinstance(context_arg, ExecutionContext)
    verify_request, verify_observation, verify_context = capability.last_verify_args or (
        None,
        None,
        None,
    )
    assert isinstance(verify_request, CapabilityRequest)
    assert verify_observation is not None
    assert isinstance(verify_context, ExecutionContext)
    # No authority object (or any kernel object) is ever handed to
    # capability code.
    kernel_objects: tuple[object, ...] = (
        harness.authority,
        harness.loop,
        harness.budget,
        harness.emergency_stop,
    )
    received_arguments: tuple[object, ...] = (
        request_arg,
        context_arg,
        verify_request,
        verify_observation,
        verify_context,
    )
    for kernel_object in kernel_objects:
        for argument in received_arguments:
            assert kernel_object is not argument


def test_capability_without_authority_cannot_run_at_all() -> None:
    """A willing capability still cannot run when authority denies it."""
    harness = Harness(authority=None)
    capability = DemoNoteCapability()
    result = harness.run(capability)
    outcome = outcome_of(result)
    assert outcome.kind is LoopOutcome.DENIED
    assert capability.execute_calls == 0
    assert capability.state == {}


def test_hostile_capability_metadata_remains_inert() -> None:
    """Hostile text is stored verbatim and widens nothing."""
    harness = Harness(authority=None)
    capability = HostileMetadataCapability()
    harness.registry.register(capability)
    stored = harness.registry.describe(hostile_identity())
    assert stored is not None
    assert stored.description == capability.hostile_description  # stored verbatim

    # The text changes nothing: authority is still required...
    denied = harness.run(
        capability,
        request=hostile_request(NoteWriteParams(key="alpha", value="beta")),
    )
    assert denied.unwrap().kind is LoopOutcome.DENIED
    assert capability.execute_calls == 0

    # ...and even with the permission granted, the forged "risk=R0" text does
    # not lower the canonical effective risk floor.
    granted = Harness(authority=frozenset({Permission.EXECUTE}))
    granted_capability = HostileMetadataCapability()
    still_denied = granted.run(
        granted_capability,
        request=hostile_request(NoteWriteParams(key="alpha", value="beta")),
    )
    assert still_denied.unwrap().kind is LoopOutcome.DENIED
    assert granted_capability.execute_calls == 0


def test_loop_refuses_to_grant_anything() -> None:
    """The loop exposes no grant surface: it can only deny or verify."""
    harness = Harness()
    for name in ("grant", "allow", "authorize", "permit", "escalate", "bypass"):
        assert not hasattr(harness.loop, name)
    assert isinstance(harness.loop, CapabilityExecutionLoop)
