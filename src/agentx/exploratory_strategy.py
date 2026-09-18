"""Clean bounded L5 exploratory strategy composition.

This adapter composes the existing Hive-first research decision with one
pre-bound governed research capability request.  It does not generate research
objectives, choose providers, parse task text for authority, retry, or promote
external evidence.

The strategy is available only for L5_EXPLORATORY.  It first consults the
canonical Hive lookup using explicit typed knowledge requirements.  If Hive
already satisfies them, the L5 attempt fails closed as unavailable (the caller
should have used a cheaper level).  If research is required, exactly one
pre-bound capability request is delegated through the canonical Executor.

Research-query budget is consumed through the same injected canonical
ResourceBudget used by the governed runtime.  The separate research dimension
is accounted before provider invocation; the capability execution path then
accounts its ordinary machine-action/wall-clock estimate.  No budget can be
reset or widened here.

A successful acquisition proves only that the provider returned one canonical
response for the explicit request.  Provider content remains untrusted data,
and this strategy performs no KnowledgeStore promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Final

from agentx.agent_loop import StrategyResult
from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.executor import Executor, ExecutorRequest
from agentx.cognition.gap_detector import KnowledgeGapRequirement
from agentx.cognition.router import ExecutionLevel
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.execution import ExecutionContext
from agentx.core.knowledge import KnowledgeScope
from agentx.core.result import Result
from agentx.core.tasks import Task
from agentx.governed_research import ResearchAcquireParams
from agentx.hive_first_research import HiveFirstResearchLookup
from agentx.infrastructure.knowledge_retrieval import KnowledgeRetrievalQuery
from agentx.kernel.resource_budget import (
    BudgetDecision,
    ResourceBudget,
    ResourceDelta,
    ResourceRequest,
)
from agentx.kernel.risk import RiskLevel

__all__ = [
    "EXPLORATORY_STRATEGY_LEVEL",
    "ExploratoryStrategyBinding",
    "GovernedExploratoryStrategy",
]

EXPLORATORY_STRATEGY_LEVEL: Final[ExecutionLevel] = ExecutionLevel.L5_EXPLORATORY


@dataclass(frozen=True, slots=True, kw_only=True)
class ExploratoryStrategyBinding:
    """Immutable typed L5 inputs selected by trusted composition."""

    requirements: tuple[KnowledgeGapRequirement, ...]
    query: KnowledgeRetrievalQuery
    acquisition_request: CapabilityRequest[ResearchAcquireParams]
    level: ExecutionLevel = EXPLORATORY_STRATEGY_LEVEL

    def __post_init__(self) -> None:
        if not isinstance(self.requirements, tuple) or not self.requirements:
            raise ValueError("requirements must be a non-empty tuple")
        if any(not isinstance(item, KnowledgeGapRequirement) for item in self.requirements):
            raise TypeError("requirements must contain KnowledgeGapRequirement values")
        if not isinstance(self.query, KnowledgeRetrievalQuery):
            raise TypeError("query must be a KnowledgeRetrievalQuery")
        if self.query.scope is None or not isinstance(self.query.scope, KnowledgeScope):
            raise ValueError("exploratory lookup requires an explicit KnowledgeScope")
        if not isinstance(self.acquisition_request, CapabilityRequest):
            raise TypeError("acquisition_request must be a CapabilityRequest")
        if not isinstance(self.acquisition_request.params, ResearchAcquireParams):
            raise TypeError("acquisition_request must carry ResearchAcquireParams")
        if not isinstance(self.level, ExecutionLevel):
            raise TypeError("level must be an ExecutionLevel")
        if self.level is not EXPLORATORY_STRATEGY_LEVEL:
            raise ValueError("exploratory strategy binding must use L5_EXPLORATORY")


def _budget_failure() -> StrategyResult:
    return StrategyResult.executed(
        Result.failure(
            AgentXError(
                code="exploratory_strategy.research_budget_denied",
                message="research query would exceed the canonical resource budget",
                category=ErrorCategory.RESOURCE,
            )
        )
    )


class GovernedExploratoryStrategy:
    """A2.10-compatible clean L5 strategy with one bounded research attempt."""

    __slots__ = ("_binding", "_budget", "_executor", "_lookup")

    def __init__(
        self,
        *,
        lookup: HiveFirstResearchLookup,
        executor: Executor,
        budget: ResourceBudget,
        binding: ExploratoryStrategyBinding,
    ) -> None:
        if not isinstance(lookup, HiveFirstResearchLookup):
            raise TypeError("lookup must be a HiveFirstResearchLookup")
        if not isinstance(executor, Executor):
            raise TypeError("executor must be the canonical Executor")
        if not isinstance(budget, ResourceBudget):
            raise TypeError("budget must be a ResourceBudget")
        if not isinstance(binding, ExploratoryStrategyBinding):
            raise TypeError("binding must be an ExploratoryStrategyBinding")
        self._lookup = lookup
        self._executor = executor
        self._budget = budget
        self._binding = binding

    @property
    def binding(self) -> ExploratoryStrategyBinding:
        return self._binding

    def attempt(
        self,
        task: Task,
        context: ExecutionContext,
        level: ExecutionLevel,
    ) -> StrategyResult:
        if not isinstance(task, Task):
            raise TypeError("task must be a Task")
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        if not isinstance(level, ExecutionLevel):
            raise TypeError("level must be an ExecutionLevel")
        if level is not self._binding.level:
            return StrategyResult.unavailable(
                "governed exploratory strategy is available only for L5_EXPLORATORY"
            )
        if context.task_id is not None and context.task_id != task.task_id:
            return StrategyResult.unavailable("execution context does not match the Task")
        if context.observe_stop().should_stop:
            return StrategyResult.unavailable("execution context is stopped")

        decision = self._lookup.assess(
            requirements=self._binding.requirements,
            query=self._binding.query,
        )
        if not decision.external_research_required:
            return StrategyResult.unavailable(
                "Hive already satisfies the explicit knowledge requirements"
            )

        research_budget = self._budget.check_and_consume(
            ResourceRequest(
                delta=ResourceDelta(
                    wall_clock=timedelta(0),
                    model_calls=0,
                    model_tokens=0,
                    research_queries=1,
                    machine_actions=0,
                    repair_attempts=0,
                    external_cost=Decimal("0"),
                ),
                risk_level=RiskLevel.R0,
            )
        )
        if research_budget.decision is not BudgetDecision.ALLOW:
            return _budget_failure()
        if context.observe_stop().should_stop:
            return StrategyResult.unavailable("execution context stopped before acquisition")

        child = Task.create(objective=task.objective, parent_task_id=task.task_id)
        child_context = ExecutionContext(
            correlation_id=context.correlation_id,
            cancellation_token=context.cancellation_token,
            task_id=child.task_id,
            deadline=context.deadline,
        )
        return StrategyResult.executed(
            self._executor.execute(
                ExecutorRequest(
                    task=child,
                    capability_request=self._binding.acquisition_request,
                    context=child_context,
                )
            )
        )
