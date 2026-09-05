"""A2.05 integration tests: the Verifier over the real A1.10 closed loop.

Every canonical outcome evaluated here is produced by the real canonical
execution path — the A1.09 registry, C1.07 PermissionEngine/ActionGate, C1.09
EmergencyStop, C1.08 ResourceBudget, the demo capability's ``execute`` and
``verify`` — with the real C1.03 EventBus as the evidence sink. The A2.05
Verifier then evaluates those outcomes against explicit requirements.

The tests prove, end to end, that the Verifier:

- can evaluate a canonically verified A1.10 outcome;
- can never promote unverified, failed, denied, or verification-failed
  outcomes to success;
- fails closed on missing evidence;
- never executes a capability, never calls ``Capability.verify``, never
  transitions a Task, and never mutates anything — including on repeated
  evaluations;
- is deterministic across instances and calls.

There is no model anywhere in these tests: the whole path is deterministic.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, ClosedLoopOutcome, LoopOutcome
from agentx.capabilities.verifier import (
    RequirementEvaluation,
    VerificationRequirement,
    Verifier,
    VerifierRequest,
)
from agentx.core.events import Event
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.event_bus import EventBus
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.audit import SecurityAuditRecord
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel
from tests.support.demo_capability import (
    DemoNoteCapability,
    NoteWriteParams,
    write_request,
)

# ---------------------------------------------------------------------------
# Harness: the A1.10 composition root, exactly as the A1.10 tests compose it.
# ---------------------------------------------------------------------------


def make_envelope() -> ResourceEnvelope:
    """A deterministic envelope with zero model resources by construction."""
    return ResourceEnvelope(
        max_wall_clock=timedelta(seconds=30),
        max_model_calls=0,
        max_model_tokens=0,
        max_research_queries=0,
        max_machine_actions=10,
        max_repair_attempts=0,
        max_external_cost=Decimal("0"),
        max_risk_level=RiskLevel.R2,
    )


class Harness:
    """Wires the canonical loop and records capability call evidence."""

    def __init__(
        self,
        *,
        authority: frozenset[Permission] | None,
        capability: DemoNoteCapability | None = None,
    ) -> None:
        self.capability = capability if capability is not None else DemoNoteCapability()
        self.registry = CapabilityRegistry()
        self.registry.register(self.capability)
        self.bus = EventBus()
        self.events: list[Event] = []
        self.audit_records: list[SecurityAuditRecord] = []
        self.bus.subscribe(self.events.append)
        self.budget = ResourceBudget(make_envelope())
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

    def run_note(self, *, key: str = "alpha", value: str = "v1") -> Result[ClosedLoopOutcome, Any]:
        task = self.make_task()
        context = self.make_context(task)
        request = write_request(NoteWriteParams(key=key, value=value))
        return self.loop.run(task, request, context)


# ---------------------------------------------------------------------------
# Tests.
# ---------------------------------------------------------------------------


def test_verified_outcome_can_be_evaluated_and_satisfied() -> None:
    harness = Harness(authority=frozenset({Permission.WRITE}))
    result = harness.run_note(key="alpha", value="v1")
    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.task.status is TaskStatus.SUCCEEDED

    verifier = Verifier()
    evaluation = verifier.evaluate(
        VerifierRequest(
            outcome=outcome,
            requirement=VerificationRequirement(
                expected_observation={"key": "alpha", "stored": True}
            ),
        )
    )
    assert evaluation.satisfied is True
    assert evaluation.unmet_conditions == ()


def test_verified_outcome_with_unmet_expectation_is_reported_not_satisfied() -> None:
    harness = Harness(authority=frozenset({Permission.WRITE}))
    outcome = harness.run_note(key="alpha", value="v1").unwrap()

    evaluation = Verifier().evaluate(
        VerifierRequest(
            outcome=outcome,
            requirement=VerificationRequirement(expected_observation={"key": "beta"}),
        )
    )
    assert evaluation.satisfied is False
    assert evaluation.unmet_conditions == (
        "observation evidence key 'key' does not match the required value",
    )


def test_denied_outcome_cannot_become_success_and_executes_nothing() -> None:
    harness = Harness(authority=frozenset())  # nothing granted
    result = harness.run_note()
    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert harness.capability.execute_calls == 0
    assert harness.capability.verify_calls == 0

    execute_calls = harness.capability.execute_calls
    verify_calls = harness.capability.verify_calls
    evaluation = Verifier().evaluate(
        VerifierRequest(
            outcome=outcome,
            requirement=VerificationRequirement(expected_observation={"stored": True}),
        )
    )
    assert evaluation.satisfied is False
    assert evaluation.unmet_conditions == (
        "canonical verification evidence missing",
        "canonical outcome kind is 'denied', not 'verified'",
        "observation evidence missing",
    )
    # Evaluation executed nothing: the capability was never reached.
    assert harness.capability.execute_calls == execute_calls
    assert harness.capability.verify_calls == verify_calls


def test_verification_failed_outcome_cannot_become_success() -> None:
    harness = Harness(
        authority=frozenset({Permission.WRITE}),
        capability=DemoNoteCapability(verification_mode="fail"),
    )
    outcome = harness.run_note(key="alpha", value="v1").unwrap()
    assert outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert outcome.verification is not None and outcome.verification.passed is False

    evaluation = Verifier().evaluate(
        VerifierRequest(
            outcome=outcome,
            requirement=VerificationRequirement(
                expected_observation={"key": "alpha", "stored": True}
            ),
        )
    )
    assert evaluation.satisfied is False
    assert "canonical verification did not pass" in evaluation.unmet_conditions
    assert "canonical outcome kind is 'verification_failed', not 'verified'" in (
        evaluation.unmet_conditions
    )


def test_execution_failed_outcome_cannot_become_success() -> None:
    harness = Harness(
        authority=frozenset({Permission.WRITE}),
        capability=DemoNoteCapability(execution_mode="fail"),
    )
    outcome = harness.run_note(key="alpha", value="v1").unwrap()
    assert outcome.kind is LoopOutcome.EXECUTION_FAILED

    evaluation = Verifier().evaluate(
        VerifierRequest(
            outcome=outcome,
            requirement=VerificationRequirement(expected_observation={"stored": False}),
        )
    )
    assert evaluation.satisfied is False
    assert "canonical verification evidence missing" in evaluation.unmet_conditions
    assert "canonical outcome kind is 'execution_failed', not 'verified'" in (
        evaluation.unmet_conditions
    )


def test_evaluation_never_executes_capabilities_or_transitions_tasks() -> None:
    harness = Harness(authority=frozenset({Permission.WRITE}))
    verified = harness.run_note(key="alpha", value="v1").unwrap()

    execute_calls = harness.capability.execute_calls
    verify_calls = harness.capability.verify_calls
    task_status = verified.task.status
    task_id = verified.task.task_id

    verifier = Verifier()
    for _ in range(3):
        evaluation = verifier.evaluate(
            VerifierRequest(
                outcome=verified,
                requirement=VerificationRequirement(expected_observation={"stored": True}),
            )
        )
        assert evaluation.satisfied is True

    assert harness.capability.execute_calls == execute_calls
    assert harness.capability.verify_calls == verify_calls
    assert verified.task.status is task_status
    assert verified.task.task_id == task_id
    assert verified.kind is LoopOutcome.VERIFIED


def test_evaluation_is_deterministic_across_instances_and_calls() -> None:
    harness = Harness(authority=frozenset({Permission.WRITE}))
    outcome = harness.run_note(key="alpha", value="v1").unwrap()
    requirement = VerificationRequirement(expected_observation={"key": "alpha"})

    first = Verifier().evaluate(VerifierRequest(outcome=outcome, requirement=requirement))
    second = Verifier().evaluate(VerifierRequest(outcome=outcome, requirement=requirement))
    assert isinstance(first, RequirementEvaluation)
    assert first == second
    assert first.satisfied is True
