"""Executor transport for exact human confirmation evidence."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from agentx.capabilities.executor import Executor, ExecutorRequest
from agentx.capabilities.human_approval import HumanApprovalDecision, HumanApprovalOutcome
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, LoopOutcome
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.tasks import Task
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel
from tests.support.demo_capability import (
    HostileMetadataCapability,
    NoteWriteParams,
    hostile_request,
)


def test_executor_transports_typed_approval_without_becoming_an_authority() -> None:
    capability = HostileMetadataCapability()
    registry = CapabilityRegistry()
    registry.register(capability)
    loop = CapabilityExecutionLoop(
        registry=registry,
        action_gate=ActionGate(),
        authority=AuthorityContext(frozenset({Permission.EXECUTE})),
        emergency_stop=EmergencyStop(),
        budget=ResourceBudget(
            ResourceEnvelope(
                max_wall_clock=timedelta(seconds=30),
                max_model_calls=0,
                max_model_tokens=0,
                max_research_queries=0,
                max_machine_actions=1,
                max_repair_attempts=0,
                max_external_cost=Decimal("0"),
                max_risk_level=RiskLevel.R4,
            )
        ),
        publish_event=lambda _event: None,
        publish_audit=lambda _record: None,
    )
    task = Task.create("external effect")
    context = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=task.task_id,
    )
    request = hostile_request(NoteWriteParams("alpha", "data"))
    approval_request = loop.approval_requests(task, request, context).unwrap()[0]
    decision = HumanApprovalDecision(
        request=approval_request,
        outcome=HumanApprovalOutcome.APPROVED,
    )

    outcome = (
        Executor(execution_loop=loop)
        .execute(
            ExecutorRequest(
                task=task,
                capability_request=request,
                context=context,
            ),
            approvals=(decision,),
        )
        .unwrap()
    )

    assert outcome.kind is LoopOutcome.VERIFIED
    assert capability.execute_calls == 1
