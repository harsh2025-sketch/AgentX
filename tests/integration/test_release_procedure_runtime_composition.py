"""Release proof that procedure control composes above governed capabilities.

The interpreter never owns capability authority. A top-level composition layer
handles ACTION_REQUIRED by dispatching through the canonical governed
CapabilityExecutionLoop, supplies an explicit StepCompleted result back to the
interpreter, and records M3.02 historical evidence. Reaching END remains
separate from verification of the original task.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from agentx.capabilities.filesystem import FilesystemWriteTextCapability, write_text_request
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, LoopOutcome
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.ids import ProcedureId
from agentx.core.procedure_execution import (
    ExecutedEdgeKind,
    ExecutedNodeKind,
    ProcedureEvidenceKind,
    ProcedureExecutionEvidence,
    ProcedureRunDisposition,
    ProcedureRunRecord,
    ProcedureStepDisposition,
    ProcedureStepRecord,
    ProcedureStepTransition,
    ProcedureTaskVerification,
)
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel
from agentx.procedures.end import EndNodeSpec
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureEdgeKind,
    ProcedureGraph,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.procedures.interpreter import (
    InterpreterStatus,
    ProcedureInstructionKind,
    ProcedureInterpreter,
    StepCompleted,
)
from agentx.procedures.nodes import ActionNodeSpec

_T0 = datetime(2026, 9, 8, 15, 45, tzinfo=UTC)


def _governed_loop() -> CapabilityExecutionLoop:
    registry = CapabilityRegistry()
    registry.register(FilesystemWriteTextCapability())
    return CapabilityExecutionLoop(
        registry=registry,
        action_gate=ActionGate(),
        authority=AuthorityContext(permissions=frozenset({Permission.WRITE})),
        emergency_stop=EmergencyStop(),
        budget=ResourceBudget(
            ResourceEnvelope(
                max_wall_clock=timedelta(seconds=30),
                max_model_calls=0,
                max_model_tokens=0,
                max_research_queries=0,
                max_machine_actions=10,
                max_repair_attempts=0,
                max_external_cost=Decimal("0"),
                max_risk_level=RiskLevel.R2,
            )
        ),
        publish_event=lambda _event: None,
        publish_audit=lambda _record: None,
    )


def test_procedure_action_dispatch_is_governed_and_end_is_not_original_task_success(
    tmp_path: Path,
) -> None:
    target = tmp_path / "procedure-runtime.txt"
    original_task = Task.create("original user objective remains separately verified")
    action_task = Task.create("execute one governed procedure action")
    correlation_id = uuid4()
    context = ExecutionContext(
        correlation_id=correlation_id,
        cancellation_token=CancellationSource().token,
        task_id=action_task.task_id,
    )

    action = ActionNodeSpec(
        capability_name="filesystem.write_text",
        capability_version="1.0.0",
        description="top-level runtime dispatches this; interpreter does not execute it",
        params={"path": str(target), "content": "governed procedure action", "overwrite": False},
    )
    graph = ProcedureGraph(
        entry=ProcedureNodeId("write"),
        nodes=(action.to_node("write"), EndNodeSpec().to_node("done")),
        edges=(
            ProcedureEdge(
                source=ProcedureNodeId("write"),
                target=ProcedureNodeId("done"),
                kind=ProcedureEdgeKind.NEXT,
            ),
        ),
    )
    interpreter = ProcedureInterpreter(graph=graph)
    state = interpreter.start()
    instruction = interpreter.instruction(state)
    assert instruction.kind is ProcedureInstructionKind.ACTION_REQUIRED

    # Top-level composition resolves the known ACTION payload to the canonical
    # capability request. The interpreter itself still imports/calls no capability.
    spec = ActionNodeSpec.from_params(instruction.params)
    assert spec.capability_name == "filesystem.write_text"
    result = _governed_loop().run(
        action_task,
        write_text_request(
            str(target),
            content="governed procedure action",
            overwrite=False,
        ),
        context,
    )
    assert result.is_success, result.unwrap_error()
    capability_outcome = result.unwrap()
    assert capability_outcome.kind is LoopOutcome.VERIFIED
    assert capability_outcome.verification is not None
    assert capability_outcome.verification.passed is True
    assert target.read_text(encoding="utf-8") == "governed procedure action"

    state = interpreter.advance(state, StepCompleted(node_kind=ProcedureNodeKind.ACTION))
    assert state.status is InterpreterStatus.TERMINATED
    assert state.current_node == ProcedureNodeId("done")

    evidence = ProcedureExecutionEvidence(
        kind=ProcedureEvidenceKind.CHAIN_CORRELATION,
        correlation_id=correlation_id,
    )
    steps = (
        ProcedureStepRecord(
            step_index=0,
            node_id="write",
            node_kind=ExecutedNodeKind.ACTION,
            disposition=ProcedureStepDisposition.VERIFIED,
            started_at=_T0,
            ended_at=_T0 + timedelta(seconds=1),
            verification_evidence=(evidence,),
            transition=ProcedureStepTransition(
                edge_kind=ExecutedEdgeKind.NEXT,
                target_node_id="done",
            ),
        ),
        ProcedureStepRecord(
            step_index=1,
            node_id="done",
            node_kind=ExecutedNodeKind.END,
            disposition=ProcedureStepDisposition.EXECUTED,
            started_at=_T0 + timedelta(seconds=2),
            ended_at=_T0 + timedelta(seconds=2),
        ),
    )
    trace = ProcedureRunRecord(
        run_id=uuid4(),
        procedure_id=ProcedureId.create(),
        procedure_revision=1,
        task_id=original_task.task_id,
        correlation_id=correlation_id,
        started_at=_T0,
        ended_at=_T0 + timedelta(seconds=3),
        steps=steps,
        disposition=ProcedureRunDisposition.REACHED_END,
        control_evidence=(evidence,),
    )

    assert trace.disposition is ProcedureRunDisposition.REACHED_END
    assert trace.task_verification is ProcedureTaskVerification.NOT_ASSESSED
    assert original_task.status is TaskStatus.PENDING
