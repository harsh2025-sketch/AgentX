"""Integration proof that L4 procedure leaves use the canonical L2 runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from agentx.application_binding import ApplicationActionBinder
from agentx.capabilities.verifier import VerificationRequirement
from agentx.compiled_procedure_strategy import (
    CompiledProcedureStrategyBinding,
    GovernedCompiledProcedureStrategy,
)
from agentx.core.ids import ProcedureId, TaskId
from agentx.core.procedure_matching import ProcedureCandidate, ProcedureRequirement
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.core.task_decomposition import DecompositionNode, TaskDecomposition
from agentx.plan_execution import BoundPlanAction, BoundPlanProcedure, GovernedPlanExecutor
from agentx.procedure_binding import PreparedProcedureBinder
from agentx.procedures.end import EndNodeSpec
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureEdgeKind,
    ProcedureGraph,
    ProcedureNodeId,
)
from agentx.procedures.nodes import ActionNodeSpec
from tests.support.demo_capability import NoteWriteParams, write_request
from tests.support.orchestration_harness import OrchestrationHarness

_T0 = datetime(2026, 9, 18, 4, 30, tzinfo=UTC)
_PROCEDURE_ID = ProcedureId(UUID("00000000-0000-0000-0000-000000000301"))


def _scope() -> ProcedureScope:
    return ProcedureScope({ProcedureScopeDimension.OPERATING_SYSTEM: "windows"})


def _compiled_strategy(harness: OrchestrationHarness) -> GovernedCompiledProcedureStrategy:
    graph = ProcedureGraph(
        entry=ProcedureNodeId("write"),
        nodes=(
            ActionNodeSpec(
                capability_name="demo.note.write",
                capability_version="1.0.0",
                description="write through canonical compiled procedure runtime",
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
    binding = CompiledProcedureStrategyBinding(
        candidate=ProcedureCandidate(record=record),
        requirement=ProcedureRequirement(scope=_scope()),
        action_requests={
            "write": write_request(NoteWriteParams(key="alpha", value="v1")),
        },
        run_id=UUID("00000000-0000-0000-0000-000000000302"),
        recorded_at=_T0,
    )
    return GovernedCompiledProcedureStrategy(executor=harness.executor, binding=binding)


def test_l4_procedure_leaf_dispatches_through_compiled_runtime_and_executor() -> None:
    harness = OrchestrationHarness()
    task = harness.make_task("compose a verified procedure step")
    leaf_id = TaskId.create()
    plan = TaskDecomposition.create(
        root_task_id=task.task_id,
        nodes=(
            DecompositionNode(task_id=task.task_id, objective=task.objective),
            DecompositionNode(
                task_id=leaf_id,
                objective="run pre-bound active procedure",
                parent_task_id=task.task_id,
                success_criteria=("stored note is verified",),
                metadata={
                    "execution": {
                        "kind": "procedure",
                        "procedure_id": _PROCEDURE_ID.to_str(),
                    }
                },
            ),
        ),
    )
    procedure = BoundPlanProcedure(
        procedure_id=_PROCEDURE_ID,
        strategy=_compiled_strategy(harness),
        requirement=VerificationRequirement({"stored": True}),
    )
    goal_check = BoundPlanAction(
        request=write_request(NoteWriteParams(key="alpha", value="v1")),
        requirement=VerificationRequirement({"stored": True}),
    )
    executor = GovernedPlanExecutor(
        executor=harness.executor,
        binder=ApplicationActionBinder({}),
        procedure_binder=PreparedProcedureBinder({_PROCEDURE_ID: procedure}),
        goal_check=goal_check,
        max_actions=2,
    )

    outcome = executor.execute(plan, task, harness.make_context(task)).unwrap()

    assert outcome.verified is True
    assert harness.capability.execute_calls == 2
    assert harness.capability.verify_calls == 2
    assert harness.capability.state == {"alpha": "v1"}


def test_unknown_procedure_leaf_fails_before_any_governed_action() -> None:
    harness = OrchestrationHarness()
    task = harness.make_task("unknown procedure must fail closed")
    unknown = ProcedureId.create()
    plan = TaskDecomposition.create(
        root_task_id=task.task_id,
        nodes=(
            DecompositionNode(task_id=task.task_id, objective=task.objective),
            DecompositionNode(
                task_id=TaskId.create(),
                objective="unknown procedure",
                parent_task_id=task.task_id,
                success_criteria=("never executed",),
                metadata={
                    "execution": {
                        "kind": "procedure",
                        "procedure_id": unknown.to_str(),
                    }
                },
            ),
        ),
    )
    executor = GovernedPlanExecutor(
        executor=harness.executor,
        binder=ApplicationActionBinder({}),
        procedure_binder=PreparedProcedureBinder({}),
        goal_check=BoundPlanAction(
            request=write_request(NoteWriteParams(key="alpha", value="v1")),
            requirement=VerificationRequirement({"stored": True}),
        ),
        max_actions=2,
    )

    error = executor.execute(plan, task, harness.make_context(task)).unwrap_error()

    assert error.code == "procedure_binding.unavailable"
    assert harness.capability.execute_calls == 0
    assert harness.capability.verify_calls == 0
