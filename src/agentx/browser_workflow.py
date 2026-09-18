"""Bounded governed browser workflow composition.

This is a sequencing helper over the canonical Executor, not a second browser
runtime. Callers supply already-selected browser ExecutorRequests and any exact
human-approval evidence. Every step still executes through Executor ->
CapabilityExecutionLoop -> ActionGate -> ResourceBudget -> capability ->
independent verification.

The workflow:
* accepts only browser.* capability identities;
* requires one shared correlation id across all steps;
* executes each step once in declared order;
* stops immediately on the first non-VERIFIED canonical outcome or error;
* never creates approval, permission, budget, retry, fallback, or success
  evidence.

It is suitable for explicit multi-field form and multi-page browser workflows
while preserving the existing authority boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentx.capabilities.executor import Executor, ExecutorRequest
from agentx.capabilities.runtime import ApprovalDecisions, ClosedLoopOutcome, LoopOutcome
from agentx.core.errors import AgentXError
from agentx.core.result import Result

__all__ = [
    "BrowserWorkflow",
    "BrowserWorkflowResult",
    "BrowserWorkflowStep",
]

_MAX_STEPS = 64


@dataclass(frozen=True, slots=True)
class BrowserWorkflowStep:
    request: ExecutorRequest
    approvals: ApprovalDecisions = ()

    def __post_init__(self) -> None:
        if not isinstance(self.request, ExecutorRequest):
            raise TypeError("request must be an ExecutorRequest")
        if type(self.approvals) is not tuple:
            raise TypeError("approvals must be a tuple")
        identity = self.request.capability_request.identity.name.value
        if not identity.startswith("browser."):
            raise ValueError("browser workflow accepts browser.* capabilities only")


@dataclass(frozen=True, slots=True)
class BrowserWorkflowResult:
    outcomes: tuple[ClosedLoopOutcome, ...]
    completed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.outcomes, tuple) or any(
            not isinstance(item, ClosedLoopOutcome) for item in self.outcomes
        ):
            raise TypeError("outcomes must be a tuple of ClosedLoopOutcome values")
        if type(self.completed) is not bool:
            raise TypeError("completed must be bool")
        all_verified = bool(self.outcomes) and all(
            item.kind is LoopOutcome.VERIFIED for item in self.outcomes
        )
        if self.completed != all_verified:
            raise ValueError("completed must exactly reflect canonical VERIFIED outcomes")


class BrowserWorkflow:
    __slots__ = ("_executor",)

    def __init__(self, executor: Executor) -> None:
        if not isinstance(executor, Executor):
            raise TypeError("executor must be an Executor")
        self._executor = executor

    def run(
        self,
        steps: tuple[BrowserWorkflowStep, ...],
    ) -> Result[BrowserWorkflowResult, AgentXError]:
        if not isinstance(steps, tuple):
            raise TypeError("steps must be a tuple")
        if not steps or len(steps) > _MAX_STEPS:
            raise ValueError(f"steps must contain 1..{_MAX_STEPS} entries")
        if any(not isinstance(step, BrowserWorkflowStep) for step in steps):
            raise TypeError("steps must contain BrowserWorkflowStep values")

        correlation_id = steps[0].request.context.correlation_id
        if any(step.request.context.correlation_id != correlation_id for step in steps):
            raise ValueError("all browser workflow steps must share one correlation id")

        outcomes: list[ClosedLoopOutcome] = []
        for step in steps:
            result = self._executor.execute(
                step.request,
                approvals=step.approvals,
            )
            if result.is_failure:
                return Result.failure(result.unwrap_error())
            outcome = result.unwrap()
            outcomes.append(outcome)
            if outcome.kind is not LoopOutcome.VERIFIED:
                return Result.success(
                    BrowserWorkflowResult(
                        outcomes=tuple(outcomes),
                        completed=False,
                    )
                )

        return Result.success(
            BrowserWorkflowResult(
                outcomes=tuple(outcomes),
                completed=True,
            )
        )
