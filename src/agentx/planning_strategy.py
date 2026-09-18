"""Canonical L4_PLANNED bounded planning strategy boundary (N2.06).

This module is the concrete strategy for :data:`~agentx.cognition.router.ExecutionLevel.L4_PLANNED`:
novel tasks that require bounded planning. It produces **plan data only** —
an inert, strictly validated canonical
:class:`~agentx.core.task_decomposition.TaskDecomposition` candidate — by
delegating exactly one bounded invocation to the canonical A2.03
:class:`~agentx.cognition.reasoner.Reasoner` (the REASONING model role) and
accepting the model output exclusively through the canonical A6.01
:func:`~agentx.core.task_decomposition.accept_model_proposal` validation
boundary.

Planning is not execution
-------------------------

A successful plan result means exactly one thing:

    *"a bounded candidate plan was produced."*

It does **not** mean the Task was accepted, that permission was granted, that
a capability is executable, that a Procedure became active, that the Task
succeeded, or that verification passed. There is no direct L4 -> CAPABILITIES
edge and no direct L4 -> ProcedureStore activation: this module imports no
capability, kernel, procedure, store, persistence, event, or task-state
surface, calls nothing executable, performs no research/network I/O, persists
nothing, publishes no events, and never transitions a Task. The canonical
plan record never carries a status, success-claim, permission, risk, budget,
or authority field.

Strict typed plan output
------------------------

Free-form model text is never executable plan authority. The strategy:

* instructs the model to emit exactly the canonical proposal shape
  (``schema_version`` / ``root_task_id`` / ``nodes``) and nothing else;
* parses the response text as one JSON document (no fence stripping, no
  substring extraction — malformed text fails closed);
* enforces an explicit finite response-text ceiling tied to the configured
  ``max_output_tokens`` so a hostile provider cannot return unbounded text;
* feeds the parsed data to :func:`accept_model_proposal` with the caller
  Task's identity fixed as ``root_task_id``. Unknown fields, malformed
  hierarchies, unbounded node counts/depths, duplicate identities, dangling
  dependencies, cycles, and non-canonical ids all fail closed with structured
  ``task_decomposition.*`` codes. Record identity (``decomposition_id``,
  ``version``, ``created_at``) is owned by the accepting boundary, never by
  model text.

Hostile model payloads such as ``permission=ADMIN``, ``risk=R0``,
``execute_now=true``, ``task_success=true``, ``verified=true``,
``skip_action_gate=true``, or ``disable_stop=true`` are either rejected as
unknown fields or remain inert strings inside the canonical plan. They cannot
influence Trusted Kernel state.

Bounds
------

* **Model output** — ``PlanningStrategyLimits.max_output_tokens`` (default
  ``PLANNING_DEFAULT_MAX_OUTPUT_TOKENS``) is forwarded on every
  ``ReasonerRequest`` and additionally gates a fail-closed response-text
  ceiling.
* **Nodes** — canonical ``MAX_DECOMPOSITION_NODES`` (re-exported as
  ``PLANNING_MAX_NODES``), enforced by the A6.01 acceptance boundary.
* **Depth** — canonical ``MAX_DECOMPOSITION_DEPTH`` (``PLANNING_MAX_DEPTH``),
  enforced by the A6.01 acceptance boundary.
* **Instruction** — ``PlanningStrategyLimits.max_instruction_chars`` bounds
  the assembled planning input; an oversized caller Task fails closed before
  any model call.
* **Model calls** — exactly one ``Reasoner.reason(...)`` invocation per
  ``plan(...)`` call; no retry, fallback, second role, or second provider.

Stop semantics
--------------

The caller's canonical A1.07 ``ExecutionContext`` is observed before the model
call and again after it returns: a cancellation or expired deadline before the
call skips the model entirely, and a stop observed after the call discards the
produced plan. A stop is never converted into a plan or into success.
Provider/reasoner failures propagate unchanged — they are never converted
into fabricated plan output.

A2.10 execution port
--------------------

``attempt(...)`` satisfies the A2.10
:class:`~agentx.agent_loop.ExecutionStrategy` port so the strategy can be
registered in a :class:`~agentx.agent_loop.StrategyRegistry`, and it always
fails closed: planning produces no execution outcome, and the port vocabulary
has no plan channel, so no L4 attempt can ever claim ``executed``. The
planning boundary is :meth:`PlanningStrategy.plan`; the loop can never
consume a plan, and registering this strategy never turns an L4 attempt into
Task success.

Owner: N2.06. Top-level composition placement mirrors the M1.02
``agentx.capability_strategy`` adapter; the module adds no subsystem edge.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

from agentx.agent_loop import StrategyResult
from agentx.cognition.model_provider import TextContent
from agentx.cognition.reasoner import Reasoner, ReasonerRequest
from agentx.cognition.router import ExecutionLevel
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext, MonotonicClock
from agentx.core.result import Result
from agentx.core.task_decomposition import (
    DECOMPOSITION_SCHEMA_VERSION,
    MAX_DECOMPOSITION_DEPTH,
    MAX_DECOMPOSITION_NODES,
    MAX_SUCCESS_CRITERIA,
    TaskDecomposition,
    accept_model_proposal,
)
from agentx.core.tasks import Task

__all__ = [
    "PLANNING_DEFAULT_MAX_INSTRUCTION_CHARS",
    "PLANNING_DEFAULT_MAX_OUTPUT_TOKENS",
    "PLANNING_MAX_DEPTH",
    "PLANNING_MAX_NODES",
    "PLANNING_MAX_SUCCESS_CRITERIA",
    "PLANNING_STRATEGY_LEVEL",
    "PlanningStrategy",
    "PlanningStrategyLimits",
]

#: The only execution level this planning boundary may serve.
PLANNING_STRATEGY_LEVEL: Final[ExecutionLevel] = ExecutionLevel.L4_PLANNED

#: Enforced decomposition node bound (canonical A6.01 limit, re-exported so
#: the strategy's bounds are explicit at the L4 boundary).
PLANNING_MAX_NODES: Final[int] = MAX_DECOMPOSITION_NODES

#: Enforced decomposition depth bound (canonical A6.01 limit).
PLANNING_MAX_DEPTH: Final[int] = MAX_DECOMPOSITION_DEPTH

#: Enforced per-node success-criteria bound (canonical A6.01 limit).
PLANNING_MAX_SUCCESS_CRITERIA: Final[int] = MAX_SUCCESS_CRITERIA

#: Default finite bound on model output tokens forwarded to the Reasoner.
PLANNING_DEFAULT_MAX_OUTPUT_TOKENS: Final[int] = 4096

#: Default finite bound on the assembled planning instruction, in characters.
PLANNING_DEFAULT_MAX_INSTRUCTION_CHARS: Final[int] = 32768

#: Generous chars-per-token ceiling used to fail closed when a hostile
#: provider ignores ``max_output_tokens`` and returns unbounded text.
_MAX_CHARS_PER_OUTPUT_TOKEN: Final[int] = 16

#: Upper counter range shared with the canonical A2.01/A2.03 validators.
_MAX_COUNTER: Final[int] = (1 << 63) - 1

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


def _planning_error(
    *,
    code: str,
    message: str,
    category: ErrorCategory,
    retryability: Retryability = Retryability.NON_RETRYABLE,
    details: dict[str, object] | None = None,
) -> AgentXError:
    return AgentXError(
        code=code,
        message=message,
        category=category,
        retryability=retryability,
        details=details,
    )


def _stop_failure(
    *,
    cancelled: bool,
    correlation_id: object,
    task_id: object,
) -> AgentXError:
    """Build the structured fail-closed error for one observed stop state."""
    details: dict[str, object] = {"correlation_id": str(correlation_id)}
    if task_id is not None:
        details["task_id"] = str(task_id)
    if cancelled:
        return _planning_error(
            code="planning_strategy.cancelled",
            message="planning was cancelled before producing a plan candidate",
            category=ErrorCategory.CANCELLED,
            details=details,
        )
    return _planning_error(
        code="planning_strategy.timeout",
        message="planning deadline expired before producing a plan candidate",
        category=ErrorCategory.TIMEOUT,
        details=details,
    )


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanningStrategyLimits:
    """Immutable explicit finite bounds for one bounded planning invocation.

    The limits carry no authority, permission, risk, budget, retry, routing,
    escalation, or fallback configuration. Node count, depth, and
    success-criteria bounds are canonical A6.01 constants (re-exported above)
    and are enforced by the canonical acceptance boundary for every plan;
    these two fields bound the model-facing surface this strategy adds.
    """

    max_output_tokens: int = PLANNING_DEFAULT_MAX_OUTPUT_TOKENS
    max_instruction_chars: int = PLANNING_DEFAULT_MAX_INSTRUCTION_CHARS

    def __post_init__(self) -> None:
        if type(self.max_output_tokens) is not int:
            raise TypeError(
                f"max_output_tokens must be an int, got {type(self.max_output_tokens).__name__}"
            )
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be greater than zero")
        if self.max_output_tokens > _MAX_COUNTER:
            raise OverflowError("max_output_tokens exceeds the supported counter range")
        if type(self.max_instruction_chars) is not int:
            raise TypeError(
                "max_instruction_chars must be an int, "
                f"got {type(self.max_instruction_chars).__name__}"
            )
        if self.max_instruction_chars <= 0:
            raise ValueError("max_instruction_chars must be greater than zero")
        if self.max_instruction_chars > _MAX_COUNTER:
            raise OverflowError("max_instruction_chars exceeds the supported counter range")

    @property
    def max_response_chars(self) -> int:
        """Fail-closed ceiling on total model response text, in characters.

        Tied to ``max_output_tokens`` so the strategy stays bounded even when
        a hostile or broken provider ignores the forwarded token limit.
        """
        return self.max_output_tokens * _MAX_CHARS_PER_OUTPUT_TOKEN


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class PlanningStrategy:
    """Bounded L4 planning boundary producing inert plan candidates only.

    The strategy is stateless between calls. It owns exactly one canonical
    Reasoner, one immutable limits record, and an optional monotonic clock.
    There is no hidden retry, fallback, routing, second model role, research,
    persistence, procedure access, capability access, task transition, or
    direct model-provider bypass.
    """

    __slots__ = ("_clock", "_limits", "_reasoner")

    def __init__(
        self,
        *,
        reasoner: Reasoner,
        limits: PlanningStrategyLimits | None = None,
        clock: MonotonicClock | None = None,
    ) -> None:
        if not isinstance(reasoner, Reasoner):
            raise TypeError(f"reasoner must be a Reasoner, got {type(reasoner).__name__}")
        if limits is not None and not isinstance(limits, PlanningStrategyLimits):
            raise TypeError(
                f"limits must be a PlanningStrategyLimits or None, got {type(limits).__name__}"
            )
        if clock is not None and not callable(getattr(clock, "monotonic", None)):
            raise TypeError(f"clock must expose monotonic() or be None, got {type(clock).__name__}")
        self._reasoner = reasoner
        self._limits = limits if limits is not None else PlanningStrategyLimits()
        self._clock = clock

    @property
    def reasoner(self) -> Reasoner:
        """Return the canonical Reasoner used for bounded plan production."""
        return self._reasoner

    @property
    def limits(self) -> PlanningStrategyLimits:
        """Return the immutable planning bounds."""
        return self._limits

    def plan(self, task: Task, context: ExecutionContext) -> Result[TaskDecomposition, AgentXError]:
        """Produce one bounded, inert plan candidate for ``task``.

        The canonical Reasoner is invoked at most once; the produced plan is
        accepted exclusively through :func:`accept_model_proposal` with the
        caller Task fixed as the root. Success carries only an inert
        :class:`TaskDecomposition` candidate — never an execution outcome,
        permission, verification, or Task transition. Failures carry
        structured ``planning_strategy.*``, ``reasoner.*``, provider, or
        ``task_decomposition.*`` codes and never raise on model data.
        """
        if not isinstance(task, Task):
            raise TypeError(f"task must be a Task, got {type(task).__name__}")
        if not isinstance(context, ExecutionContext):
            raise TypeError(f"context must be an ExecutionContext, got {type(context).__name__}")

        stop = context.observe_stop(clock=self._clock)
        if stop.should_stop:
            return Result[TaskDecomposition, AgentXError].failure(
                _stop_failure(
                    cancelled=stop.cancellation_requested,
                    correlation_id=context.correlation_id,
                    task_id=context.task_id,
                )
            )

        instruction_result = self._assemble_instruction(task)
        if instruction_result.is_failure:
            return Result[TaskDecomposition, AgentXError].failure(instruction_result.unwrap_error())

        reasoner_result = self._reasoner.reason(
            ReasonerRequest(
                execution_context=context,
                instruction=instruction_result.unwrap(),
                max_output_tokens=self._limits.max_output_tokens,
            )
        )
        if reasoner_result.is_failure:
            # Provider/reasoner failures propagate unchanged: they are never
            # converted into fabricated plan output.
            return Result[TaskDecomposition, AgentXError].failure(reasoner_result.unwrap_error())

        # Re-observe stop state after the model call: a plan requested after
        # a stop must never be delivered, and a stop is never success.
        stop = context.observe_stop(clock=self._clock)
        if stop.should_stop:
            return Result[TaskDecomposition, AgentXError].failure(
                _stop_failure(
                    cancelled=stop.cancellation_requested,
                    correlation_id=context.correlation_id,
                    task_id=context.task_id,
                )
            )

        response = reasoner_result.unwrap()
        text = "".join(piece.text for piece in response.content)
        if len(text) > self._limits.max_response_chars:
            return Result[TaskDecomposition, AgentXError].failure(
                _planning_error(
                    code="planning_strategy.response_too_large",
                    message=(
                        "model response text exceeds the bounded planning ceiling "
                        f"({len(text)} characters)"
                    ),
                    category=ErrorCategory.VALIDATION,
                    details={
                        "max_response_chars": self._limits.max_response_chars,
                        "response_chars": len(text),
                    },
                )
            )

        try:
            parsed: object = json.loads(text)
        except ValueError:
            return Result[TaskDecomposition, AgentXError].failure(
                _planning_error(
                    code="planning_strategy.invalid_json",
                    message="model output is not a single valid JSON document",
                    category=ErrorCategory.VALIDATION,
                )
            )

        # Strict typed acceptance boundary: unknown fields, malformed
        # hierarchies, unbounded node counts, and every other canonical
        # invariant fail closed here. Model text never sets record identity.
        return accept_model_proposal(parsed, root_task_id=task.task_id, version=1)

    def attempt(
        self,
        task: Task,
        context: ExecutionContext,
        level: ExecutionLevel,
    ) -> StrategyResult:
        """A2.10 execution port: always fail closed, never execute.

        Planning is not execution. The port vocabulary carries either a
        governed execution outcome or an unavailability reason, and this
        strategy can honestly report neither an execution outcome nor an
        authorized execution path for L4 — plans are inert candidates
        returned by :meth:`plan`, which A2.10 never consumes. No model call
        and no execution happen here, for any level.
        """
        if not isinstance(task, Task):
            raise TypeError(f"task must be a Task, got {type(task).__name__}")
        if not isinstance(context, ExecutionContext):
            raise TypeError(f"context must be an ExecutionContext, got {type(context).__name__}")
        if not isinstance(level, ExecutionLevel):
            raise TypeError(f"level must be an ExecutionLevel, got {type(level).__name__}")

        if level is not PLANNING_STRATEGY_LEVEL:
            return StrategyResult.unavailable("planning strategy is available only for L4_PLANNED")
        return StrategyResult.unavailable(
            "L4_PLANNED planning never executes: the planning strategy produces only "
            "inert plan candidates via PlanningStrategy.plan(...) and reports no "
            "execution outcome"
        )

    # -- assembly ----------------------------------------------------------

    def _assemble_instruction(self, task: Task) -> Result[TextContent, AgentXError]:
        """Build the bounded deterministic planning instruction for ``task``.

        The instruction is assembled only from the caller-supplied canonical
        Task data (identity, objective, parent link, scheduling hint, inert
        metadata) plus the canonical proposal schema and bounds. It is a pure
        function of the Task and fails closed when the assembled input would
        exceed the configured instruction bound.
        """
        payload: dict[str, object] = {
            "task": {
                "task_id": task.task_id.to_str(),
                "objective": task.objective,
                "parent_task_id": (
                    None if task.parent_task_id is None else task.parent_task_id.to_str()
                ),
                "priority": task.priority.value,
                "metadata": dict(task.metadata),
            },
            "planning_bounds": {
                "schema_version": DECOMPOSITION_SCHEMA_VERSION,
                "max_nodes": PLANNING_MAX_NODES,
                "max_depth": PLANNING_MAX_DEPTH,
                "max_success_criteria_per_node": PLANNING_MAX_SUCCESS_CRITERIA,
            },
        }
        try:
            context_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            return Result[TextContent, AgentXError].failure(
                _planning_error(
                    code="planning_strategy.instruction_serialization",
                    message="task context could not be serialized for the planning instruction",
                    category=ErrorCategory.VALIDATION,
                )
            )

        instruction = (
            "Produce a bounded task decomposition plan for the root task described in the "
            "context below. Respond with exactly one JSON object and no other text. The "
            "object must contain exactly the fields: schema_version (the integer "
            f"{DECOMPOSITION_SCHEMA_VERSION}), root_task_id (the task_id of the root task), "
            "and nodes (a JSON list). nodes[0] must be the root node with parent_task_id "
            "null. Every node must contain exactly the fields: task_id (a valid UUID "
            "string, unique within the plan), objective (non-empty trimmed string), "
            "parent_task_id (a UUID string of another node in the plan, or null only for "
            "the root), success_criteria (a JSON list of strings), order_index "
            "(non-negative integer; indices within each sibling group must be exactly "
            "0..k-1), depends_on (a JSON list of task_id strings that must complete "
            "first), and metadata (a JSON object or null). Constraints: at most "
            f"{PLANNING_MAX_NODES} nodes including the root, at most {PLANNING_MAX_DEPTH} "
            f"levels including the root, at most {PLANNING_MAX_SUCCESS_CRITERIA} success "
            "criteria per node. Every terminal node must have at least one success "
            'criterion and metadata.execution equal to {"kind":"higher_level"}. '
            "This is an inert resolution requirement, not executable authority. "
            "Non-terminal nodes must not have metadata.execution. Do not include any "
            "permission, risk, budget, authority, verification, or success fields. "
            "Do not include any text outside "
            "the JSON object. Context: "
        ) + context_json
        if len(instruction) > self._limits.max_instruction_chars:
            return Result[TextContent, AgentXError].failure(
                _planning_error(
                    code="planning_strategy.input_too_large",
                    message=(
                        "assembled planning instruction exceeds the bounded input ceiling "
                        f"({len(instruction)} characters)"
                    ),
                    category=ErrorCategory.VALIDATION,
                    details={
                        "max_instruction_chars": self._limits.max_instruction_chars,
                        "instruction_chars": len(instruction),
                    },
                )
            )
        return Result[TextContent, AgentXError].success(TextContent(instruction))
