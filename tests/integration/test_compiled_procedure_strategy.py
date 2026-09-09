"""Integration tests for N2.04 composition over the real governed runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from agentx.agent_loop import OrchestrationStatus
from agentx.capabilities.runtime import LoopOutcome
from agentx.capabilities.verifier import VerificationRequirement
from agentx.compiled_procedure_strategy import (
    CompiledProcedureStrategyBinding,
    GovernedCompiledProcedureStrategy,
)
from agentx.cognition.router import ExecutionLevel, RoutingEvidence
from agentx.core.ids import ProcedureId
from agentx.core.procedure_execution import (
    ExecutedNodeKind,
    ProcedureRunDisposition,
    ProcedureRunRecord,
    ProcedureStepDisposition,
    ProcedureTaskVerification,
)
from agentx.core.procedure_matching import ProcedureCandidate, ProcedureRequirement
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.core.tasks import TaskStatus
from agentx.procedures.end import EndNodeSpec
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureEdgeKind,
    ProcedureGraph,
    ProcedureNodeId,
)
from agentx.procedures.nodes import ActionNodeSpec
from tests.support.demo_capability import DemoNoteCapability, NoteWriteParams, write_request
from tests.support.orchestration_harness import (
    OrchestrationHarness,
    default_limits,
)

_T0 = datetime(2026, 9, 8, 18, 30, tzinfo=UTC)
_RUN_ID = UUID("00000000-0000-0000-0000-000000000214")
_PROCEDURE_ID = ProcedureId(UUID("00000000-0000-0000-0000-000000000214"))


def _scope() -> ProcedureScope:
    return ProcedureScope({ProcedureScopeDimension.OPERATING_SYSTEM: "windows"})


def _graph() -> ProcedureGraph:
    return ProcedureGraph(
        entry=ProcedureNodeId("write"),
        nodes=(
            ActionNodeSpec(
                capability_name="demo.note.write",
                capability_version="1.0.0",
                description="governed compiled action",
                params={"key": "alpha", "value": "v1"},
            ).to_node("write"),
            EndNodeSpec().to_node("done"),
        ),
        edges=(
            ProcedureEdge(
                source=ProcedureNodeId("write"),
                target=ProcedureNodeId("done"),
                kind=ProcedureEdgeKind.NEXT,
            ),
        ),
    )


def _binding() -> CompiledProcedureStrategyBinding:
    graph = _graph()
    record = ProcedureRecord(
        procedure_id=_PROCEDURE_ID,
        revision=1,
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content=graph.to_json(),
        ),
        created_at=_T0,
        status=ProcedureStatus.ACTIVE,
        scope=_scope(),
    )
    return CompiledProcedureStrategyBinding(
        candidate=ProcedureCandidate(record=record),
        requirement=ProcedureRequirement(scope=_scope()),
        action_requests={
            "write": write_request(NoteWriteParams(key="alpha", value="v1")),
        },
        run_id=_RUN_ID,
        recorded_at=_T0,
    )


def _adapter(
    harness: OrchestrationHarness,
    records: list[ProcedureRunRecord] | None = None,
) -> GovernedCompiledProcedureStrategy:
    return GovernedCompiledProcedureStrategy(
        executor=harness.executor,
        binding=_binding(),
        run_sink=None if records is None else records.append,
    )


def test_action_required_is_dispatched_through_executor_and_end_is_not_task_success() -> None:
    harness = OrchestrationHarness()
    adapter = _adapter(harness)
    task = harness.make_task("original task must be verified separately")
    context = harness.make_context(task)

    attempt = adapter.attempt_with_evidence(task, context, ExecutionLevel.L2_COMPILED)

    assert attempt.strategy_result.outcome is not None
    assert attempt.strategy_result.outcome.is_success
    outcome = attempt.strategy_result.outcome.unwrap()
    assert outcome.kind is LoopOutcome.VERIFIED
    assert harness.capability.execute_calls == 1
    assert harness.capability.verify_calls == 1
    assert harness.capability.state == {"alpha": "v1"}

    trace = attempt.run_record
    assert trace is not None
    assert trace.disposition is ProcedureRunDisposition.REACHED_END
    assert trace.task_verification is ProcedureTaskVerification.NOT_ASSESSED
    assert trace.task_id == task.task_id
    assert tuple(step.node_kind for step in trace.steps) == (
        ExecutedNodeKind.ACTION,
        ExecutedNodeKind.END,
    )
    assert trace.steps[0].disposition is ProcedureStepDisposition.VERIFIED
    assert trace.steps[1].disposition is ProcedureStepDisposition.EXECUTED
    assert harness.task_status(task) is TaskStatus.PENDING


def test_action_gate_denial_stops_procedure_and_records_denial() -> None:
    capability = DemoNoteCapability(destructive=True)
    harness = OrchestrationHarness(capability=capability)
    task = harness.make_task()
    attempt = _adapter(harness).attempt_with_evidence(
        task,
        harness.make_context(task),
        ExecutionLevel.L2_COMPILED,
    )

    assert attempt.strategy_result.outcome is not None
    outcome = attempt.strategy_result.outcome.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.error is not None and outcome.error.code == "runtime.gate_denied"
    assert capability.execute_calls == 0
    assert capability.verify_calls == 0
    assert attempt.run_record is not None
    assert attempt.run_record.disposition is ProcedureRunDisposition.DENIED
    assert attempt.run_record.steps[-1].disposition is ProcedureStepDisposition.DENIED
    assert all(step.node_kind is not ExecutedNodeKind.END for step in attempt.run_record.steps)


def test_capability_verification_failure_does_not_advance_to_end() -> None:
    capability = DemoNoteCapability(verification_mode="fail")
    harness = OrchestrationHarness(capability=capability)
    task = harness.make_task()
    attempt = _adapter(harness).attempt_with_evidence(
        task,
        harness.make_context(task),
        ExecutionLevel.L2_COMPILED,
    )

    assert attempt.strategy_result.outcome is not None
    outcome = attempt.strategy_result.outcome.unwrap()
    assert outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert outcome.execution is not None and outcome.execution.succeeded is True
    assert outcome.verification is not None and outcome.verification.passed is False
    assert capability.execute_calls == 1
    assert capability.verify_calls == 1
    assert attempt.run_record is not None
    assert attempt.run_record.disposition is ProcedureRunDisposition.HALTED_ON_STEP_FAILURE
    assert attempt.run_record.steps[-1].disposition is ProcedureStepDisposition.VERIFICATION_FAILED
    assert all(step.node_kind is not ExecutedNodeKind.END for step in attempt.run_record.steps)


def test_successful_capability_and_procedure_end_do_not_bypass_original_task_verification() -> None:
    harness = OrchestrationHarness()
    records: list[ProcedureRunRecord] = []
    adapter = _adapter(harness, records)
    loop = harness.agent_loop({ExecutionLevel.L2_COMPILED: adapter})
    request = harness.make_request(
        routing_evidence=RoutingEvidence(verified_reasoning_free_procedure=True),
        requirement=VerificationRequirement({"stored": False}),
        limits=default_limits(max_total_attempts=1, escalation_permitted=False),
    )

    result = loop.run(request)
    assert result.is_success, result.unwrap_error()
    outcome = result.unwrap()

    assert outcome.status is not OrchestrationStatus.SUCCEEDED
    assert outcome.verified is False
    assert outcome.task.status is not TaskStatus.SUCCEEDED
    assert outcome.attempt_count == 1
    attempt = outcome.attempts[0]
    assert attempt.outcome is not None and attempt.outcome.kind is LoopOutcome.VERIFIED
    assert attempt.evaluation is not None and attempt.evaluation.satisfied is False
    assert len(records) == 1
    assert records[0].disposition is ProcedureRunDisposition.REACHED_END
    assert records[0].task_verification is ProcedureTaskVerification.NOT_ASSESSED


def test_separate_a2_10_task_verification_can_confirm_original_task() -> None:
    harness = OrchestrationHarness()
    records: list[ProcedureRunRecord] = []
    adapter = _adapter(harness, records)
    loop = harness.agent_loop({ExecutionLevel.L2_COMPILED: adapter})
    request = harness.make_request(
        routing_evidence=RoutingEvidence(verified_reasoning_free_procedure=True),
        requirement=VerificationRequirement({"stored": True}),
        limits=default_limits(max_total_attempts=1, escalation_permitted=False),
    )

    result = loop.run(request)
    assert result.is_success, result.unwrap_error()
    outcome = result.unwrap()

    assert outcome.status is OrchestrationStatus.SUCCEEDED
    assert outcome.verified is True
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert outcome.attempt_count == 1
    attempt = outcome.attempts[0]
    assert attempt.outcome is not None and attempt.outcome.kind is LoopOutcome.VERIFIED
    assert attempt.evaluation is not None and attempt.evaluation.satisfied is True
    assert len(records) == 1
    assert records[0].disposition is ProcedureRunDisposition.REACHED_END
    assert records[0].task_verification is ProcedureTaskVerification.NOT_ASSESSED
