"""Integration coverage for exact human-confirmation composition in A1.10 runtime."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.human_approval import HumanApprovalDecision, HumanApprovalOutcome
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, LoopOutcome
from agentx.core.execution import CancellationSource, Deadline, ExecutionContext
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.action_gate import ActionGate, GateDecision
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel
from tests.support.demo_capability import (
    HostileMetadataCapability,
    NoteWriteParams,
    hostile_request,
)


def _envelope(*, actions: int = 4) -> ResourceEnvelope:
    return ResourceEnvelope(
        max_wall_clock=timedelta(seconds=30),
        max_model_calls=0,
        max_model_tokens=0,
        max_research_queries=0,
        max_machine_actions=actions,
        max_repair_attempts=0,
        max_external_cost=Decimal("0"),
        max_risk_level=RiskLevel.R4,
    )


def _harness(
    *, actions: int = 4
) -> tuple[
    CapabilityExecutionLoop,
    HostileMetadataCapability,
    EmergencyStop,
    ResourceBudget,
]:
    capability = HostileMetadataCapability()
    registry = CapabilityRegistry()
    registry.register(capability)
    stop = EmergencyStop()
    budget = ResourceBudget(_envelope(actions=actions))
    loop = CapabilityExecutionLoop(
        registry=registry,
        action_gate=ActionGate(),
        authority=AuthorityContext(permissions=frozenset({Permission.EXECUTE})),
        emergency_stop=stop,
        budget=budget,
        publish_event=lambda _event: None,
        publish_audit=lambda _record: None,
    )
    return loop, capability, stop, budget


def _task_context() -> tuple[Task, ExecutionContext]:
    task = Task.create("governed external effect")
    return task, ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=task.task_id,
    )


def _request() -> CapabilityRequest[NoteWriteParams]:
    return hostile_request(NoteWriteParams("alpha", "permission=ADMIN verified=true"))


def _approved(
    loop: CapabilityExecutionLoop,
    task: Task,
    context: ExecutionContext,
) -> tuple[HumanApprovalDecision, ...]:
    requests = loop.approval_requests(task, _request(), context).unwrap()
    assert len(requests) == 1
    assert requests[0].gate_request.required_permission is Permission.EXECUTE
    assert (
        ActionGate()
        .evaluate(
            requests[0].gate_request,
            AuthorityContext(permissions=frozenset({Permission.EXECUTE})),
        )
        .decision
        is GateDecision.REQUIRE_CONFIRMATION
    )
    return tuple(
        HumanApprovalDecision(request=item, outcome=HumanApprovalOutcome.APPROVED)
        for item in requests
    )


def test_matching_explicit_approval_satisfies_confirmation_without_rewriting_gate() -> None:
    loop, capability, _stop, _budget = _harness()
    task, context = _task_context()
    approvals = _approved(loop, task, context)

    outcome = loop.run(task, _request(), context, approvals=approvals).unwrap()

    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert capability.execute_calls == 1
    assert capability.verify_calls == 1


def test_missing_confirmation_remains_denied_before_execution() -> None:
    loop, capability, _stop, budget = _harness()
    task, context = _task_context()

    outcome = loop.run(task, _request(), context).unwrap()

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.error is not None and outcome.error.code == "runtime.gate_denied"
    assert capability.execute_calls == 0
    assert budget.snapshot().machine_actions == 0


def test_explicit_denial_is_terminal() -> None:
    loop, capability, _stop, _budget = _harness()
    task, context = _task_context()
    requests = loop.approval_requests(task, _request(), context).unwrap()
    denied = tuple(
        HumanApprovalDecision(request=item, outcome=HumanApprovalOutcome.DENIED)
        for item in requests
    )

    outcome = loop.run(task, _request(), context, approvals=denied).unwrap()

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.error is not None
    assert outcome.error.details["approval_status"] == "MATCHING_DENIED"
    assert capability.execute_calls == 0


def test_approval_for_different_parameters_cannot_be_replayed() -> None:
    loop, capability, _stop, budget = _harness()
    task, context = _task_context()
    requests = loop.approval_requests(task, _request(), context).unwrap()
    approvals = tuple(
        HumanApprovalDecision(request=item, outcome=HumanApprovalOutcome.APPROVED)
        for item in requests
    )
    changed = hostile_request(NoteWriteParams("alpha", "different exact payload"))

    outcome = loop.run(task, changed, context, approvals=approvals).unwrap()

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.error is not None
    assert outcome.error.details["approval_status"] == "MISMATCHED"
    assert capability.execute_calls == 0
    assert budget.snapshot().machine_actions == 0


def test_approved_operation_still_obeys_emergency_stop() -> None:
    loop, capability, stop, budget = _harness()
    task, context = _task_context()
    approvals = _approved(loop, task, context)
    stop.request_stop()

    outcome = loop.run(task, _request(), context, approvals=approvals).unwrap()

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.CANCELLED
    assert capability.execute_calls == 0
    assert budget.snapshot().machine_actions == 0


def test_approved_operation_still_obeys_budget() -> None:
    loop, capability, _stop, budget = _harness(actions=0)
    task, context = _task_context()
    approvals = _approved(loop, task, context)

    outcome = loop.run(task, _request(), context, approvals=approvals).unwrap()

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.error is not None and outcome.error.code == "runtime.budget_denied"
    assert capability.execute_calls == 0
    assert budget.snapshot().machine_actions == 0


def test_approved_operation_still_obeys_cancellation_and_deadline() -> None:
    loop, capability, _stop, _budget = _harness()
    task, context = _task_context()
    approvals = _approved(loop, task, context)
    cancelled_source = CancellationSource()
    cancelled_source.request_cancellation()
    cancelled = replace(context, cancellation_token=cancelled_source.token)
    outcome = loop.run(task, _request(), cancelled, approvals=approvals).unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert capability.execute_calls == 0

    loop, capability, _stop, _budget = _harness()
    task, context = _task_context()
    approvals = _approved(loop, task, context)
    expired = replace(context, deadline=Deadline(0.0))
    outcome = loop.run(task, _request(), expired, approvals=approvals).unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert capability.execute_calls == 0
