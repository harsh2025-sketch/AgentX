"""Top-level governed capability strategy adapter for A2.10.

This module is composition wiring only. It binds one explicit canonical
:class:`~agentx.capabilities.abi.CapabilityRequest` to the deterministic
``L1_DIRECT`` strategy level and delegates each attempt exactly once through
the canonical A2.04 :class:`~agentx.capabilities.executor.Executor`.

The adapter owns no authority and makes no execution decision itself. Registry
resolution, permission checks, ActionGate decisions, EmergencyStop observation,
ExecutionContext stop handling, resource-budget consumption, capability
execution, capability verification, canonical Task transitions, events, audit,
and A1.10 outcome production all remain inside the injected Executor's
:class:`~agentx.capabilities.runtime.CapabilityExecutionLoop`.

A2.10 retains orchestration ownership. The adapter never routes, retries,
escalates, falls back, evaluates verification requirements, or marks success.
It returns only the canonical governed-path ``Result`` wrapped in A2.10's
``StrategyResult.executed(...)`` vocabulary, or explicit strategy
unavailability when called for a level other than its immutable L1 binding.

Task lifecycle separation
-------------------------

A2.10 transitions its Task through the canonical TaskManager before invoking a
strategy, while A1.10 requires a PENDING Task at its own execution boundary.
The baseline orchestration harness solves this by creating a fresh PENDING
sibling Task for the governed capability attempt. This production adapter uses
the same composition pattern: the sibling copies only the objective as inert
text, and a sibling ExecutionContext reuses the caller's correlation identity,
cancellation token, and deadline while binding the sibling Task identity.

The pre-bound CapabilityRequest is never derived from the Task objective or
metadata. Hostile task text therefore cannot rewrite capability identity,
parameters, permissions, risk, budget, or verification requirements.

Owner: M1.02. Top-level composition only; adds no subsystem edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from agentx.agent_loop import StrategyResult
from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.executor import Executor, ExecutorRequest
from agentx.cognition.router import ExecutionLevel
from agentx.core.execution import ExecutionContext
from agentx.core.tasks import Task

__all__ = [
    "CAPABILITY_STRATEGY_LEVEL",
    "CapabilityStrategyBinding",
    "CapabilityStrategyBindingError",
    "GovernedCapabilityStrategy",
]

#: The only execution level this deterministic adapter may serve.
CAPABILITY_STRATEGY_LEVEL: Final[ExecutionLevel] = ExecutionLevel.L1_DIRECT


class CapabilityStrategyBindingError(ValueError):
    """Raised when a capability-strategy binding is internally inconsistent."""


@dataclass(frozen=True, slots=True)
class CapabilityStrategyBinding:
    """Immutable explicit binding for one deterministic capability strategy.

    ``request`` is the exact canonical request that will be handed to A2.04 on
    every attempt. ``level`` is explicit so callers cannot accidentally register
    an L1 capability dispatch as a cache, compiled-procedure, guided, planned,
    or exploratory strategy.

    The binding contains no authority, permission, risk, budget, verification,
    retry, routing, escalation, or fallback field.
    """

    request: CapabilityRequest[Any]
    level: ExecutionLevel = CAPABILITY_STRATEGY_LEVEL

    def __post_init__(self) -> None:
        if not isinstance(self.request, CapabilityRequest):
            raise TypeError(
                "request must be a CapabilityRequest, got "
                f"{type(self.request).__name__}"
            )
        if not isinstance(self.level, ExecutionLevel):
            raise TypeError(
                f"level must be an ExecutionLevel, got {type(self.level).__name__}"
            )
        if self.level is not CAPABILITY_STRATEGY_LEVEL:
            raise CapabilityStrategyBindingError(
                "governed capability strategy bindings must use L1_DIRECT"
            )


class GovernedCapabilityStrategy:
    """A2.10 strategy adapter for one pre-bound canonical capability request.

    The adapter is deliberately stateless between attempts. It owns only the
    injected canonical Executor and an immutable request binding. There is no
    hidden retry, fallback, routing, planning, model call, research, persistence,
    or direct Capability access.
    """

    __slots__ = ("_binding", "_executor")

    def __init__(
        self,
        *,
        executor: Executor,
        binding: CapabilityStrategyBinding,
    ) -> None:
        if not isinstance(executor, Executor):
            raise TypeError(f"executor must be an Executor, got {type(executor).__name__}")
        if not isinstance(binding, CapabilityStrategyBinding):
            raise TypeError(
                "binding must be a CapabilityStrategyBinding, got "
                f"{type(binding).__name__}"
            )
        self._executor = executor
        self._binding = binding

    @property
    def binding(self) -> CapabilityStrategyBinding:
        """Return the immutable explicit request binding."""
        return self._binding

    @property
    def executor(self) -> Executor:
        """Return the canonical A2.04 Executor used for governed dispatch."""
        return self._executor

    def attempt(
        self,
        task: Task,
        context: ExecutionContext,
        level: ExecutionLevel,
    ) -> StrategyResult:
        """Attempt the pre-bound capability once through A2.04/A1.10.

        A level mismatch fails closed as canonical A2.10 strategy
        unavailability. For the bound L1 level, the exact CapabilityRequest is
        delegated once to the injected Executor. The returned governed result
        is neither unwrapped nor rewritten.
        """
        if not isinstance(task, Task):
            raise TypeError(f"task must be a Task, got {type(task).__name__}")
        if not isinstance(context, ExecutionContext):
            raise TypeError(
                f"context must be an ExecutionContext, got {type(context).__name__}"
            )
        if not isinstance(level, ExecutionLevel):
            raise TypeError(f"level must be an ExecutionLevel, got {type(level).__name__}")

        if level is not self._binding.level:
            return StrategyResult.unavailable(
                "governed capability strategy is available only for L1_DIRECT"
            )

        governed_task = Task.create(objective=task.objective)
        governed_context = ExecutionContext(
            correlation_id=context.correlation_id,
            cancellation_token=context.cancellation_token,
            task_id=governed_task.task_id,
            deadline=context.deadline,
        )
        executor_request = ExecutorRequest(
            task=governed_task,
            capability_request=self._binding.request,
            context=governed_context,
        )
        return StrategyResult.executed(self._executor.execute(executor_request))
