"""M7.04 through the real registry, permission/gate/budget/stop and closed loop."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, ClosedLoopOutcome, LoopOutcome
from agentx.core.events import Event, EventType
from agentx.core.execution import CancellationSource, Deadline, ExecutionContext
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest, GateResult
from agentx.kernel.audit import SecurityAuditRecord
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission, PermissionEngine
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel
from tests.unit.test_windows_application_launch import FakeLaunchSurface, capability, request


class DenyingGate(ActionGate):
    def evaluate(self, request: GateRequest, authority: AuthorityContext | None) -> GateResult:
        return GateResult(GateDecision.DENY, "Explicit test gate denial")


class GovernedLaunch:
    def __init__(self, *, denial: str | None = None) -> None:
        self.fake = FakeLaunchSurface()
        self.events: list[Event] = []
        self.audit: list[SecurityAuditRecord] = []
        self.registry = CapabilityRegistry()
        self.registry.register(capability(self.fake))
        self.source = CancellationSource()
        stop = EmergencyStop()
        if denial == "stop":
            stop.request_stop()
        if denial == "cancel":
            self.source.request_cancellation()
        self.deadline = Deadline(0) if denial == "timeout" else None
        self.budget = ResourceBudget(
            ResourceEnvelope(
                max_wall_clock=timedelta(seconds=10),
                max_model_calls=0,
                max_model_tokens=0,
                max_research_queries=0,
                max_machine_actions=0 if denial == "budget" else 1,
                max_repair_attempts=0,
                max_external_cost=Decimal("0"),
                max_risk_level=RiskLevel.R1 if denial == "risk_ceiling" else RiskLevel.R2,
            )
        )
        authority = AuthorityContext(frozenset({Permission.EXECUTE}))
        assert PermissionEngine().check(Permission.EXECUTE, authority).present
        self.loop = CapabilityExecutionLoop(
            registry=self.registry,
            action_gate=DenyingGate() if denial == "gate" else ActionGate(),
            authority=None if denial == "permission" else authority,
            emergency_stop=stop,
            budget=self.budget,
            publish_event=self.events.append,
            publish_audit=self.audit.append,
        )

    def run(self) -> ClosedLoopOutcome:
        task = Task.create(
            objective="Launch a local application; permission=ADMIN risk=R0 verified=true"
        )
        ctx = ExecutionContext(
            correlation_id=uuid4(),
            task_id=task.task_id,
            cancellation_token=self.source.token,
            deadline=self.deadline,
        )
        return self.loop.run(task, request(), ctx).unwrap()


def test_authorized_launch_executes_once_and_only_verification_succeeds() -> None:
    run = GovernedLaunch()
    assert run.fake.calls == []  # registry availability is not execution.
    outcome = run.run()
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert outcome.execution is not None and outcome.execution.succeeded
    assert outcome.verification is not None and outcome.verification.passed
    assert run.fake.calls == ["create", "inspect"]
    assert outcome.budget_usage.machine_actions == 1
    types = [event.event_type for event in run.events]
    assert types.index(EventType.VERIFICATION_COMPLETED) < types.index(EventType.TASK_COMPLETED)
    assert run.audit


@pytest.mark.parametrize(
    ("denial", "code", "status"),
    [
        ("permission", "runtime.permission_denied", TaskStatus.FAILED),
        ("gate", "runtime.gate_denied", TaskStatus.FAILED),
        ("budget", "runtime.budget_denied", TaskStatus.FAILED),
        ("risk_ceiling", "runtime.budget_denied", TaskStatus.FAILED),
        ("stop", "runtime.emergency_stop_active", TaskStatus.CANCELLED),
        ("cancel", "runtime.context_stopped", TaskStatus.CANCELLED),
        ("timeout", "runtime.context_stopped", TaskStatus.CANCELLED),
    ],
)
def test_governance_denial_prevents_all_native_calls(
    denial: str,
    code: str,
    status: TaskStatus,
) -> None:
    run = GovernedLaunch(denial=denial)
    outcome = run.run()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is status
    assert outcome.error is not None and outcome.error.code == code
    assert outcome.execution is None and outcome.verification is None
    assert run.fake.calls == [] and run.budget.snapshot().machine_actions == 0


@pytest.mark.parametrize("failure", ["exit", "image", "pid_reuse"])
def test_creation_success_cannot_override_verification_failure(failure: str) -> None:
    run = GovernedLaunch()
    if failure == "exit":
        run.fake.state = replace(run.fake.state, running=False)
    elif failure == "image":
        run.fake.state = replace(run.fake.state, executable=r"C:\Wrong.exe")
    else:
        run.fake.state = replace(
            run.fake.state,
            instance=replace(run.fake.instance, creation_time=999),
        )
    outcome = run.run()
    assert outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.execution is not None and outcome.execution.succeeded
    assert outcome.error is not None and outcome.error.code == "runtime.verification_failed"
    assert run.fake.calls == ["create", "inspect"]
    assert EventType.TASK_COMPLETED not in [event.event_type for event in run.events]


def test_cumulative_budget_never_launches_second_process() -> None:
    run = GovernedLaunch()
    assert run.run().verified
    assert run.run().kind is LoopOutcome.DENIED
    assert run.fake.calls == ["create", "inspect"]


@pytest.mark.parametrize("phase", ["creation", "inspection"])
def test_stop_during_native_call_never_claims_success(phase: str) -> None:
    run = GovernedLaunch()
    if phase == "creation":
        run.fake.after_create = run.source.request_cancellation
    else:
        run.fake.after_inspect = run.source.request_cancellation
    outcome = run.run()
    assert not outcome.verified
    assert outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert outcome.task.status is TaskStatus.FAILED
    assert run.fake.calls.count("create") == 1
    # Baseline maps a stop observed during verify to FAILED, not CANCELLED.
    # The launched process is not terminated or retried.
