"""Real filesystem and kernel coverage for governed L4 composition.

The model is scripted; these are integration regressions, not a live-model
learning benchmark. unittest also permits running them without dev packages.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from agentx.capabilities.filesystem import (
    FilesystemReadTextCapability,
    FilesystemWriteTextCapability,
    read_text_request,
    write_text_request,
)
from agentx.capabilities.runtime import LoopOutcome
from agentx.capabilities.verifier import VerificationRequirement
from agentx.cognition.model_provider import (
    ModelCapability,
    ModelDescriptor,
    ModelId,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderDescriptor,
    ProviderId,
    TextContent,
)
from agentx.cognition.model_roles import ModelRole, ModelRoleBinding, ModelRoleBindings
from agentx.cognition.reasoner import Reasoner
from agentx.cognition.router import ExecutionLevel, RoutingEvidence
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.execution import CancellationSource, Deadline
from agentx.core.ids import TaskId
from agentx.core.result import Result
from agentx.core.task_decomposition import DecompositionNode, TaskDecomposition
from agentx.core.tasks import Task
from agentx.kernel.permissions import Permission
from agentx.plan_execution import (
    BoundPlanAction,
    GovernedPlanExecutor,
    GovernedPlanningStrategy,
    terminal_order,
)
from agentx.planning_strategy import PlanningStrategy
from tests.support.orchestration_harness import (
    FixedClock,
    OrchestrationHarness,
    default_limits,
    make_envelope,
)


class FileBinder:
    def __init__(self, path: Path, *, reject: str = "") -> None:
        self.path = path
        self.reject = reject
        self.calls: list[str] = []

    def bind(self, node: DecompositionNode) -> Result[BoundPlanAction, AgentXError]:
        self.calls.append(node.objective)
        values = {"first": "draft", "second": "finished"}
        if node.objective not in values or node.objective == self.reject:
            return Result.failure(
                AgentXError(
                    code="test.unsupported_objective",
                    message="unsupported typed binding",
                    category=ErrorCategory.VALIDATION,
                )
            )
        return Result.success(
            BoundPlanAction(
                request=write_text_request(
                    str(self.path), content=values[node.objective], overwrite=True
                ),
                requirement=VerificationRequirement({}),
            )
        )


class ScriptedProvider:
    def __init__(self, plan: TaskDecomposition) -> None:
        provider_id = ProviderId("plan-test")
        caps = frozenset(
            {ModelCapability("content.text.input"), ModelCapability("content.text.output")}
        )
        model = ModelDescriptor(model_id=ModelId(provider_id, "scripted"), capabilities=caps)
        self.descriptor = ProviderDescriptor(
            provider_id=provider_id, capabilities=caps, models=(model,)
        )
        self.calls = 0
        self.output = json.dumps(
            {
                "schema_version": 1,
                "root_task_id": plan.root_task_id.to_str(),
                "nodes": [node.to_dict() for node in plan.nodes],
            }
        )

    def invoke(self, request: ModelRequest) -> Result[ModelResponse, AgentXError]:
        self.calls += 1
        return Result.success(
            ModelResponse(
                model_id=request.model_id,
                content=(TextContent(self.output),),
                usage=ModelUsage(),
            )
        )


def plan_for(task: Task) -> TaskDecomposition:
    first, second = TaskId.create(), TaskId.create()
    # Reverse the dependency order relative to pre-order to exercise scheduling.
    return TaskDecomposition.create(
        root_task_id=task.task_id,
        nodes=(
            DecompositionNode(task_id=task.task_id, objective=task.objective),
            DecompositionNode(
                task_id=second,
                objective="second",
                parent_task_id=task.task_id,
                order_index=0,
                depends_on=(first,),
                success_criteria=("final value",),
                metadata={"execution": {"kind": "higher_level"}},
            ),
            DecompositionNode(
                task_id=first,
                objective="first",
                parent_task_id=task.task_id,
                order_index=1,
                success_criteria=("draft value",),
                metadata={"execution": {"kind": "higher_level"}},
            ),
        ),
    )


class PlanExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "result.txt"
        self.harness = self.make_harness()
        self.task = self.harness.make_task("prepare a final document")
        self.plan = plan_for(self.task)
        self.binder = FileBinder(self.path)
        self.source = CancellationSource()
        self.context = self.harness.make_context(self.task, cancellation=self.source)

    def make_harness(self, *, allowed: bool = True, actions: int = 12) -> OrchestrationHarness:
        harness = OrchestrationHarness(
            authority=frozenset({Permission.READ, Permission.WRITE}) if allowed else None,
            envelope=make_envelope(max_machine_actions=actions),
            register_capability=False,
        )
        harness.registry.register(FilesystemReadTextCapability())
        harness.registry.register(FilesystemWriteTextCapability())
        return harness

    def executor(self, *, expected: str = "finished", max_actions: int = 3) -> GovernedPlanExecutor:
        return GovernedPlanExecutor(
            executor=self.harness.executor,
            binder=self.binder,
            goal_check=BoundPlanAction(
                request=read_text_request(str(self.path)),
                requirement=VerificationRequirement({"text": expected}),
            ),
            max_actions=max_actions,
        )

    def test_dependency_order_and_independent_goal_read(self) -> None:
        result = self.executor().execute(self.plan, self.task, self.context).unwrap()
        self.assertEqual(self.binder.calls, ["first", "second"])
        self.assertEqual(self.path.read_text(), "finished")
        self.assertIs(result.kind, LoopOutcome.VERIFIED)
        assert result.observation is not None
        self.assertEqual(result.observation.data["text"], "finished")
        self.assertEqual(self.harness.budget.snapshot().machine_actions, 6)
        self.assertTrue(self.harness.audit_records)

    def test_all_bindings_preflight_before_first_mutation(self) -> None:
        self.binder.reject = "second"
        result = self.executor().execute(self.plan, self.task, self.context)
        self.assertEqual(result.unwrap_error().code, "test.unsupported_objective")
        self.assertFalse(self.path.exists())
        self.assertEqual(self.harness.budget.snapshot().machine_actions, 0)

    def test_successful_steps_do_not_prove_the_goal(self) -> None:
        result = self.executor(expected="wrong").execute(self.plan, self.task, self.context)
        self.assertEqual(result.unwrap_error().code, "plan_execution.goal_unverified")
        self.assertEqual(self.path.read_text(), "finished")

    def test_permission_denial_is_preserved(self) -> None:
        self.harness = self.make_harness(allowed=False)
        result = self.executor().execute(self.plan, self.task, self.context).unwrap()
        self.assertIs(result.kind, LoopOutcome.DENIED)
        assert result.error is not None
        self.assertEqual(result.error.code, "runtime.permission_denied")
        self.assertFalse(self.path.exists())

    def test_shared_budget_stops_mid_plan_without_goal_check(self) -> None:
        self.harness = self.make_harness(actions=2)
        result = self.executor().execute(self.plan, self.task, self.context)
        error = result.unwrap().error
        assert error is not None
        self.assertEqual(error.code, "runtime.budget_denied")
        self.assertEqual(self.path.read_text(), "draft")
        self.assertEqual(self.harness.budget.snapshot().machine_actions, 2)

    def test_emergency_stop_reaches_kernel(self) -> None:
        self.harness.emergency_stop.request_stop()
        result = self.executor().execute(self.plan, self.task, self.context)
        error = result.unwrap().error
        assert error is not None
        self.assertEqual(error.code, "runtime.emergency_stop_active")
        self.assertFalse(self.path.exists())

    def test_cancel_before_binding(self) -> None:
        self.source.request_cancellation()
        result = self.executor().execute(self.plan, self.task, self.context)
        self.assertEqual(result.unwrap_error().code, "plan_execution.context_stopped")
        self.assertEqual(self.binder.calls, [])

    def test_expired_deadline(self) -> None:
        context = replace(self.context, deadline=Deadline(0.0))
        result = self.executor().execute(self.plan, self.task, context)
        self.assertEqual(result.unwrap_error().code, "plan_execution.context_stopped")
        self.assertFalse(self.path.exists())

    def test_action_ceiling_includes_goal_check(self) -> None:
        result = self.executor(max_actions=2).execute(self.plan, self.task, self.context)
        self.assertEqual(result.unwrap_error().code, "plan_execution.action_limit")
        self.assertFalse(self.path.exists())

    def test_root_and_context_identity_cannot_be_substituted(self) -> None:
        other = Task.create("another task")
        result = self.executor().execute(self.plan, other, self.context)
        self.assertEqual(result.unwrap_error().code, "plan_execution.root_mismatch")
        result = self.executor().execute(
            self.plan, self.task, replace(self.context, task_id=other.task_id)
        )
        self.assertEqual(result.unwrap_error().code, "plan_execution.context_mismatch")
        self.assertFalse(self.path.exists())

    def test_not_ready_plan_never_binds(self) -> None:
        nodes = tuple(replace(n, metadata={}) for n in self.plan.nodes)
        plan = replace(self.plan, nodes=nodes)
        result = self.executor().execute(plan, self.task, self.context)
        self.assertEqual(result.unwrap_error().code, "plan_execution.not_ready")
        self.assertEqual(self.binder.calls, [])

    def test_group_dependency_deadlock_rejected_before_execution(self) -> None:
        root, second, first = self.plan.nodes
        # No syntactic cycle: first depends on root, but completing the root
        # itself requires first. Expansion must catch this semantic deadlock.
        plan = replace(self.plan, nodes=(root, second, replace(first, depends_on=(root.task_id,))))
        result = terminal_order(plan)
        self.assertEqual(result.unwrap_error().code, "plan_execution.dependency_deadlock")

    def test_l4_runs_real_agent_loop_with_scripted_model_and_real_filesystem(self) -> None:
        provider = ScriptedProvider(self.plan)
        clock = FixedClock()
        planner = PlanningStrategy(
            reasoner=Reasoner(
                bindings=ModelRoleBindings(
                    bindings=(ModelRoleBinding(ModelRole.REASONING, provider.descriptor.models[0]),)
                ),
                provider=provider,
                clock=clock,
            ),
            clock=clock,
        )
        strategy = GovernedPlanningStrategy(planner=planner, executor=self.executor())
        loop = self.harness.agent_loop({ExecutionLevel.L4_PLANNED: strategy})
        result = loop.run(
            self.harness.make_request(
                task=self.task,
                context=self.context,
                routing_evidence=RoutingEvidence(known_composition_required=True),
                requirement=VerificationRequirement({"text": "finished"}),
                limits=default_limits(max_total_attempts=1, escalation_permitted=False),
            )
        ).unwrap()
        self.assertTrue(result.verified)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(self.path.read_text(), "finished")


if __name__ == "__main__":
    unittest.main()
