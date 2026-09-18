"""Bounded plan-to-action composition through the canonical governed executor.

Planning remains inert. A trusted composition-time binder supplies typed
requests and verification requirements; model text never supplies authority.
Every terminal action and the separate goal check use the same Executor and
therefore the same kernel budget, stop, permission, audit and verification path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from agentx.agent_loop import StrategyResult
from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.executor import Executor, ExecutorRequest
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.capabilities.verifier import VerificationRequirement, Verifier, VerifierRequest
from agentx.cognition.router import ExecutionLevel
from agentx.core.decomposition_readiness import (
    DecompositionReadinessDisposition,
    DecompositionReadinessValidator,
)
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.execution import ExecutionContext, MonotonicClock
from agentx.core.ids import CapabilityId, TaskId
from agentx.core.result import Result
from agentx.core.task_decomposition import DecompositionNode, TaskDecomposition
from agentx.core.tasks import Task, TaskStatus
from agentx.planning_strategy import PlanningStrategy


@dataclass(frozen=True, slots=True)
class BoundPlanAction:
    """Trusted binding, separate from the untrusted decomposition metadata."""

    request: CapabilityRequest[Any]
    requirement: VerificationRequirement
    capability_id: CapabilityId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request, CapabilityRequest):
            raise TypeError("request must be a CapabilityRequest")
        if not isinstance(self.requirement, VerificationRequirement):
            raise TypeError("requirement must be a VerificationRequirement")
        if self.capability_id is not None and not isinstance(self.capability_id, CapabilityId):
            raise TypeError("capability_id must be a CapabilityId or None")


class PlanActionBinder(Protocol):
    """Application-owned typed binding; not a model-selected callable."""

    def bind(self, node: DecompositionNode) -> Result[BoundPlanAction, AgentXError]:
        """Resolve a supported typed capability leaf or fail without executing it."""
        ...


@dataclass(frozen=True, slots=True)
class BoundPlanProcedure:
    """Trusted binding of one procedure leaf to the canonical L2 runtime."""

    procedure_id: ProcedureId
    strategy: GovernedCompiledProcedureStrategy
    requirement: VerificationRequirement

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, ProcedureId):
            raise TypeError("procedure_id must be a ProcedureId")
        if not isinstance(self.strategy, GovernedCompiledProcedureStrategy):
            raise TypeError("strategy must be a GovernedCompiledProcedureStrategy")
        if not isinstance(self.requirement, VerificationRequirement):
            raise TypeError("requirement must be a VerificationRequirement")
        if self.strategy.binding.candidate.record.procedure_id != self.procedure_id:
            raise ValueError("bound strategy procedure identity does not match procedure_id")


class PlanProcedureBinder(Protocol):
    """Resolve exact procedure leaves through a canonical prepared runtime."""

    def bind(self, node: DecompositionNode) -> Result[BoundPlanProcedure, AgentXError]:
        """Resolve one typed procedure leaf or fail closed."""
        ...


def _failure(code: str) -> Result[ClosedLoopOutcome, AgentXError]:
    return Result.failure(
        AgentXError(
            code=f"plan_execution.{code}",
            message=f"governed plan execution refused: {code}",
            category=ErrorCategory.PRECONDITION,
        )
    )


def terminal_order(plan: TaskDecomposition) -> Result[tuple[DecompositionNode, ...], AgentXError]:
    """Expand ancestor/group dependencies and reject deadlocks before actions.

    Internal nodes are structural groups, never verified tasks. A dependency on
    a group waits for all its leaves; each leaf also inherits ancestor ordering
    constraints. Canonical pre-order breaks ties deterministically.
    """
    readiness = DecompositionReadinessValidator().assess(plan)
    if readiness.disposition is not DecompositionReadinessDisposition.READY:
        return Result.failure(
            AgentXError(
                code="plan_execution.not_ready",
                message="decomposition failed canonical readiness validation",
                category=ErrorCategory.VALIDATION,
            )
        )
    leaves = plan.leaves
    descendants: dict[TaskId, set[TaskId]] = {node.task_id: set() for node in plan.nodes}
    for leaf in leaves:
        current: DecompositionNode | None = leaf
        while current is not None:
            descendants[current.task_id].add(leaf.task_id)
            current = (
                None
                if current.parent_task_id is None
                else plan.require_node(current.parent_task_id)
            )
    dependencies: dict[TaskId, set[TaskId]] = {}
    for leaf in leaves:
        required: set[TaskId] = set()
        current = leaf
        while current is not None:
            for dependency in current.depends_on:
                required.update(descendants[dependency])
            current = (
                None
                if current.parent_task_id is None
                else plan.require_node(current.parent_task_id)
            )
        dependencies[leaf.task_id] = required
    ordered: list[DecompositionNode] = []
    completed: set[TaskId] = set()
    for _ in leaves:
        ready = next(
            (
                leaf
                for leaf in leaves
                if leaf.task_id not in completed and dependencies[leaf.task_id] <= completed
            ),
            None,
        )
        if ready is None:
            return Result.failure(
                AgentXError(
                    code="plan_execution.dependency_deadlock",
                    message="expanded group dependencies cannot be scheduled",
                    category=ErrorCategory.VALIDATION,
                )
            )
        ordered.append(ready)
        completed.add(ready.task_id)
    return Result.success(tuple(ordered))


class GovernedPlanExecutor:
    """Execute one accepted plan, with no retries or implicit replanning."""

    def __init__(
        self,
        *,
        executor: Executor,
        binder: PlanActionBinder,
        goal_check: BoundPlanAction,
        procedure_binder: PlanProcedureBinder | None = None,
        max_actions: int = 32,
        clock: MonotonicClock | None = None,
    ) -> None:
        if not isinstance(executor, Executor):
            raise TypeError("executor must be the canonical Executor")
        if not callable(getattr(binder, "bind", None)):
            raise TypeError("binder must expose bind")
        if not isinstance(goal_check, BoundPlanAction):
            raise TypeError("goal_check must be a BoundPlanAction")
        if procedure_binder is not None and not callable(getattr(procedure_binder, "bind", None)):
            raise TypeError("procedure_binder must expose bind or be None")
        if type(max_actions) is not int or not 1 <= max_actions <= 256:
            raise ValueError("max_actions must be an integer between 1 and 256")
        self._executor = executor
        self._binder = binder
        self._goal_check = goal_check
        self._procedure_binder = procedure_binder
        self._max_actions = max_actions
        self._clock = clock
        self._verifier = Verifier()

    def execute(
        self, plan: TaskDecomposition, task: Task, context: ExecutionContext
    ) -> Result[ClosedLoopOutcome, AgentXError]:
        """Preflight the entire plan, then fail closed at the first failed step."""
        if not isinstance(task, Task) or not isinstance(context, ExecutionContext):
            raise TypeError("task and context must be canonical contracts")
        ordered = terminal_order(plan)
        if ordered.is_failure:
            return Result.failure(ordered.unwrap_error())
        if plan.root_task_id != task.task_id:
            return _failure("root_mismatch")
        if context.task_id is not None and context.task_id != task.task_id:
            return _failure("context_mismatch")
        if context.observe_stop(clock=self._clock).should_stop:
            return _failure("context_stopped")
        # The independent goal check also consumes one governed machine action.
        if len(ordered.unwrap()) + 1 > self._max_actions:
            return _failure("action_limit")
        bindings: list[tuple[DecompositionNode, BoundPlanAction | BoundPlanProcedure]] = []
        for node in ordered.unwrap():
            if context.observe_stop(clock=self._clock).should_stop:
                return _failure("context_stopped")
            execution = node.metadata["execution"]
            # Readiness has validated this JSON object; no free-text parsing.
            if not isinstance(execution, Mapping):
                return _failure("invalid_execution")
            if execution["kind"] == "procedure":
                if self._procedure_binder is None:
                    return _failure("procedure_binding_unavailable")
                bound_procedure = self._procedure_binder.bind(node)
                if bound_procedure.is_failure:
                    return Result.failure(bound_procedure.unwrap_error())
                procedure = bound_procedure.unwrap()
                if not isinstance(procedure, BoundPlanProcedure):
                    raise TypeError("procedure binder must return a BoundPlanProcedure")
                if procedure.procedure_id.to_str() != execution["procedure_id"]:
                    return _failure("procedure_binding_mismatch")
                bindings.append((node, procedure))
                continue
            bound = self._binder.bind(node)
            if bound.is_failure:
                return Result.failure(bound.unwrap_error())
            action = bound.unwrap()
            if not isinstance(action, BoundPlanAction):
                raise TypeError("binder must return a BoundPlanAction")
            if execution["kind"] == "capability" and (
                action.capability_id is None
                or action.capability_id.to_str() != execution["capability_id"]
            ):
                return _failure("capability_binding_mismatch")
            bindings.append((node, action))
        for node, binding in bindings:
            if isinstance(binding, BoundPlanProcedure):
                result = self._run_procedure(binding, node.objective, task, context)
                requirement = binding.requirement
            else:
                result = self._run(binding, node.objective, task, context)
                requirement = binding.requirement
            if result.is_failure:
                return result
            if result.unwrap().kind is not LoopOutcome.VERIFIED:
                return result
            if not self._verified(result.unwrap(), requirement):
                return _failure("step_unverified")
        result = self._run(self._goal_check, task.objective, task, context)
        if result.is_failure:
            return result
        if result.unwrap().kind is not LoopOutcome.VERIFIED:
            return result
        if not self._verified(result.unwrap(), self._goal_check.requirement):
            return _failure("goal_unverified")
        # Return real goal-check evidence unchanged, never the last action's
        # evidence or a fabricated aggregate success outcome.
        return result

    def _run(
        self, action: BoundPlanAction, objective: str, task: Task, context: ExecutionContext
    ) -> Result[ClosedLoopOutcome, AgentXError]:
        if context.observe_stop(clock=self._clock).should_stop:
            return _failure("context_stopped")
        child = Task.create(objective=objective, parent_task_id=task.task_id)
        child_context = ExecutionContext(
            task_id=child.task_id,
            correlation_id=context.correlation_id,
            cancellation_token=context.cancellation_token,
            deadline=context.deadline,
        )
        result = self._executor.execute(
            ExecutorRequest(task=child, capability_request=action.request, context=child_context)
        )
        if context.observe_stop(clock=self._clock).should_stop:
            return _failure("context_stopped")
        return result

    def _run_procedure(
        self,
        procedure: BoundPlanProcedure,
        objective: str,
        task: Task,
        context: ExecutionContext,
    ) -> Result[ClosedLoopOutcome, AgentXError]:
        if context.observe_stop(clock=self._clock).should_stop:
            return _failure("context_stopped")
        child = Task.create(objective=objective, parent_task_id=task.task_id)
        child_context = ExecutionContext(
            task_id=child.task_id,
            correlation_id=context.correlation_id,
            cancellation_token=context.cancellation_token,
            deadline=context.deadline,
        )
        strategy_result = procedure.strategy.attempt(
            child,
            child_context,
            ExecutionLevel.L2_COMPILED,
        )
        if context.observe_stop(clock=self._clock).should_stop:
            return _failure("context_stopped")
        if strategy_result.outcome is None:
            return _failure("procedure_runtime_unavailable")
        return strategy_result.outcome

    def _verified(self, outcome: ClosedLoopOutcome, requirement: VerificationRequirement) -> bool:
        return (
            outcome.kind is LoopOutcome.VERIFIED
            and outcome.task.status is TaskStatus.SUCCEEDED
            and outcome.verification is not None
            and outcome.verification.passed is True
            and self._verifier.evaluate(
                VerifierRequest(outcome=outcome, requirement=requirement)
            ).satisfied
        )


class GovernedPlanningStrategy:
    """L4 adapter composing the inert planner with governed plan execution."""

    def __init__(self, *, planner: PlanningStrategy, executor: GovernedPlanExecutor) -> None:
        if not isinstance(planner, PlanningStrategy):
            raise TypeError("planner must be a PlanningStrategy")
        if not isinstance(executor, GovernedPlanExecutor):
            raise TypeError("executor must be a GovernedPlanExecutor")
        self._planner = planner
        self._executor = executor

    def attempt(
        self, task: Task, context: ExecutionContext, level: ExecutionLevel
    ) -> StrategyResult:
        if not isinstance(task, Task) or not isinstance(context, ExecutionContext):
            raise TypeError("task and context must be canonical contracts")
        if not isinstance(level, ExecutionLevel):
            raise TypeError("level must be an ExecutionLevel")
        if level is not ExecutionLevel.L4_PLANNED:
            return StrategyResult.unavailable("governed planning requires L4_PLANNED")
        if context.task_id is not None and context.task_id != task.task_id:
            return StrategyResult.executed(_failure("context_mismatch"))
        plan = self._planner.plan(task, context)
        if plan.is_failure:
            return StrategyResult.executed(Result.failure(plan.unwrap_error()))
        return StrategyResult.executed(self._executor.execute(plan.unwrap(), task, context))
