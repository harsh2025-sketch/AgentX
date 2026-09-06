"""A2.10 minimal end-to-end bounded agent execution loop (composition only).

This module owns **composition and control flow**. It is the smallest
production-quality orchestration layer that proves the already-canonical
AgentX runtime contracts participate in one bounded task-attempt loop::

    Task (A1.05/A1.06 via A2.06 TaskManager)
     -> observe A1.07 cancellation/deadline
     -> route initial execution level (A2.07)
     -> attempt the caller-supplied strategy for that level
     -> obtain explicit outcome / canonical verification evidence
     -> success ONLY if canonical verification says success (A1.10 + A2.05)
     -> otherwise record attempt evidence
     -> anti-loop decision (A2.09)
     -> bounded escalation decision (A2.08)
     -> next attempt OR terminal failure/exhaustion

Nothing here is a new planner, executor, verifier, router, escalation table,
anti-loop engine, budget system, or task-state machine. Every decision is
delegated to the canonical contract that owns it, and this module only
sequences those decisions and carries explicit evidence between them.

The governing invariant (I1)
----------------------------

    NO ACTION == SUCCESS WITHOUT VERIFICATION.

There is exactly one statement in this module that can produce
:data:`~agentx.cognition.agent_loop.OrchestrationStatus.SUCCEEDED`, and it is
guarded by :func:`_verified_success`, which requires all of:

* the strategy returned a canonical A1.10
  :class:`~agentx.capabilities.runtime.ClosedLoopOutcome`;
* ``outcome.kind is LoopOutcome.VERIFIED`` (the only A1.10 kind that can
  accompany ``TaskStatus.SUCCEEDED``);
* ``outcome.verification`` is a canonical
  :class:`~agentx.capabilities.abi.VerificationResult` with ``passed is True``;
* ``outcome.task.status is TaskStatus.SUCCEEDED``;
* the A2.05 :class:`~agentx.capabilities.verifier.Verifier` evaluated that same
  outcome against the caller's explicit
  :class:`~agentx.capabilities.verifier.VerificationRequirement` and returned
  ``satisfied=True``.

A capability returning normally, an observation existing, an END state being
reached, a strategy reporting "done", a model claiming success, a procedure
claiming success, or simply the absence of an exception can never satisfy that
guard: none of those are canonical verification evidence, and the guard reads
only typed canonical fields (never text).

Strategy ports (the L0-L5 limitation)
-------------------------------------

The repository has no concrete L0/L2/L3/L4/L5 strategy implementations, and
A2.10 must not fabricate them. Levels are therefore served by explicit
caller-supplied :class:`ExecutionStrategy` adapters registered in a
:class:`StrategyRegistry`. An adapter is subordinate, not an authority:

* it returns a canonical A1.10 ``Result[ClosedLoopOutcome, AgentXError]``
  produced by the governed path (A2.04 Executor over the A1.10
  ``CapabilityExecutionLoop``) or an explicit unavailability/failure error;
* it cannot mark success — the outcome it returns is re-checked here against
  canonical evidence and the A2.05 Verifier;
* a level with no registered adapter fails closed as
  :data:`AttemptDisposition.STRATEGY_UNAVAILABLE`. That includes
  ``L5_EXPLORATORY``: reaching L5 authorizes nothing, and this module never
  browses, researches, plans, or invokes a model because escalation got there.

Bounds
------

* **Anti-loop** — A2.09 :class:`~agentx.cognition.anti_loop.LoopGuard` bounds
  repeated attempts. A2.10 constructs the explicit history it requires and
  never reinterprets its semantics: ``STOP_LOOP`` stops orchestration, and it
  neither implies success nor authorizes escalation.
* **Total attempts** — an explicit ``max_total_attempts`` ceiling in
  :class:`OrchestrationLimits` bounds the loop independently of A2.09, so the
  `for` loop over attempts is finite by construction.
* **Resources** — C1.08 remains the sole accounting authority. This module
  never constructs, resets, widens, or consumes a
  :class:`~agentx.kernel.resource_budget.ResourceEnvelope`; it only *observes*
  canonical resource exhaustion reported by the governed path and fails closed.
* **Cancellation/deadline** — A1.07 stop state is observed deterministically
  before the first attempt, before every later attempt, and after each attempt
  completes. There is no sleeping, polling, thread, or background loop, and a
  stop is never converted into success.

Authority
---------

A2.10 holds no authority and can manufacture none. It never calls
``Capability.execute()`` directly, never constructs a
:class:`~agentx.kernel.permissions.Permission` or ``AuthorityContext``, never
touches the ``ActionGate``, never lowers risk, never clears an
``EmergencyStop``, and never widens a budget. It imports no kernel module at
all: the governed path is reached only through caller-supplied strategy
adapters bound to the canonical A1.10 loop at composition time.

Deliberate non-scope
--------------------

No A3 interpreter, research engine, skill compiler, repair engine, model
router, EventBus redesign, persistence schema, background daemon, scheduler,
or UI. No Hive writes, no procedure synthesis, no learning, no self-
modification, no prompts, and no model-text inspection.

Owner: A2.10. Belongs to ``agentx.cognition`` (which already owns A2.06-A2.09)
and widens no boundary edge: it imports only the standard library, canonical
``agentx.core`` contracts, canonical ``agentx.capabilities`` runtime/verifier
contracts through a caller-injected port, and its canonical cognition
siblings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol

from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.capabilities.verifier import (
    RequirementEvaluation,
    VerificationRequirement,
    Verifier,
    VerifierRequest,
)
from agentx.cognition.anti_loop import (
    AttemptEvidence,
    AttemptFingerprint,
    LoopGuard,
    LoopGuardDecision,
    LoopGuardLimits,
    LoopGuardResult,
    OutcomeFingerprint,
)
from agentx.cognition.escalation import (
    EscalationAction,
    EscalationDecision,
    EscalationEvidence,
    ExecutionLevelEscalator,
)
from agentx.cognition.router import (
    ExecutionLevel,
    ExecutionLevelRouter,
    RoutingDecision,
    RoutingEvidence,
)
from agentx.cognition.task_manager import TaskManager
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import (
    ExecutionContext,
    ExecutionStopReason,
    ExecutionStopStatus,
    MonotonicClock,
)
from agentx.core.result import Result
from agentx.core.task_state import is_terminal
from agentx.core.tasks import Task, TaskStatus

__all__ = [
    "AGENT_LOOP_SOURCE",
    "AgentLoop",
    "AttemptDisposition",
    "AttemptRecord",
    "ExecutionStrategy",
    "OrchestrationLimits",
    "OrchestrationOutcome",
    "OrchestrationRequest",
    "OrchestrationRequestError",
    "OrchestrationStatus",
    "OrchestrationStopReason",
    "StrategyRegistry",
    "StrategyResult",
]

#: Stable identifier of this orchestration boundary, for reporting only. The
#: loop publishes no events of its own; canonical evidence is emitted by the
#: A1.10 path under its own source.
AGENT_LOOP_SOURCE: Final[str] = "agentx.cognition.agent_loop"

#: Error-code prefix for every structured orchestration error.
_ERROR_PREFIX: Final[str] = "agent_loop"

#: Hard ceiling on the configurable total-attempt bound. Orchestration must be
#: finite by construction; there is no unlimited mode.
MAX_CONFIGURABLE_TOTAL_ATTEMPTS: Final[int] = 64

#: Canonical A1.10 error codes that mean the governed path refused the run for
#: resource reasons. Observing one is terminal: A2.10 fails closed and never
#: retries, escalates, resets, or widens a C1.08 budget.
_RESOURCE_EXHAUSTION_CODES: Final[frozenset[str]] = frozenset({"runtime.budget_denied"})

#: Canonical A1.10 error codes that mean the governed path refused the run for
#: safety/stop reasons (emergency stop, cancellation, deadline).
_SAFETY_STOP_CODES: Final[frozenset[str]] = frozenset(
    {"runtime.emergency_stop_active", "runtime.context_stopped"}
)


class OrchestrationRequestError(ValueError):
    """Raised when an orchestration request is malformed or inconsistent.

    This is a request-shape error only. It never carries, encodes, or implies
    an authority decision, a verification verdict, or a Task transition.
    """


class AttemptDisposition(StrEnum):
    """Controlled classification of one completed attempt.

    Exactly one value — :attr:`VERIFIED_SUCCESS` — represents success, and it
    is produced only by :func:`_verified_success`. Every other value is a
    non-success classification of explicit evidence.
    """

    #: Canonical A1.10 verification confirmed the postcondition *and* the A2.05
    #: Verifier confirmed the caller's explicit requirement.
    VERIFIED_SUCCESS = "verified_success"
    #: The strategy produced a canonical outcome, but it carried no canonical
    #: verification evidence (denied, execution failed, verification failed) or
    #: the explicit A2.05 requirement was not satisfied.
    UNVERIFIED = "unverified"
    #: The governed path refused the run for canonical resource reasons.
    RESOURCE_EXHAUSTED = "resource_exhausted"
    #: The governed path refused the run for canonical safety/stop reasons.
    SAFETY_STOPPED = "safety_stopped"
    #: The strategy itself reported an explicit refusal/failure error.
    STRATEGY_ERROR = "strategy_error"
    #: No authorized concrete strategy exists for the routed level.
    STRATEGY_UNAVAILABLE = "strategy_unavailable"


class OrchestrationStatus(StrEnum):
    """Controlled terminal status of one bounded orchestration run.

    ``SUCCEEDED`` is reachable only through verified success. Every other value
    is an explicit non-success terminal state; none of them is a hidden Task
    status, and each maps only through canonical A1.06 transitions.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    EXHAUSTED = "exhausted"


class OrchestrationStopReason(StrEnum):
    """Deterministic reason the bounded loop stopped attempting."""

    #: Verified success (the only success reason).
    VERIFIED = "verified"
    #: A1.07 cancellation was observed at an orchestration boundary.
    CANCELLED = "cancelled"
    #: A1.07 deadline expiry was observed at an orchestration boundary.
    DEADLINE_EXPIRED = "deadline_expired"
    #: The governed path reported canonical resource exhaustion.
    RESOURCE_EXHAUSTED = "resource_exhausted"
    #: The governed path refused for canonical safety/stop reasons.
    SAFETY_STOP = "safety_stop"
    #: A2.09 returned ``STOP_LOOP``.
    ANTI_LOOP = "anti_loop"
    #: A2.08 returned ``EXHAUSTED`` (including at L5 with no successor).
    ESCALATION_EXHAUSTED = "escalation_exhausted"
    #: The explicit total-attempt ceiling was reached.
    ATTEMPT_CEILING = "attempt_ceiling"
    #: No authorized concrete strategy exists for the current level.
    STRATEGY_UNAVAILABLE = "strategy_unavailable"


def _require_type[T](value: object, expected: type[T], *, field_name: str) -> T:
    """Reject anything that is not an instance of the canonical contract type."""
    if not isinstance(value, expected):
        raise TypeError(
            f"{field_name} must be a {expected.__name__}, got {type(value).__name__}"
        )
    return value


def _require_positive_int(value: object, *, field_name: str, maximum: int) -> int:
    """Reject non-``int``, non-positive, and over-ceiling bounds."""
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if value < 1:
        raise OrchestrationRequestError(f"{field_name} must be at least 1")
    if value > maximum:
        raise OrchestrationRequestError(f"{field_name} must not exceed {maximum}")
    return value


def _error(
    *,
    code: str,
    message: str,
    category: ErrorCategory,
    details: dict[str, object] | None = None,
    retryability: Retryability = Retryability.NON_RETRYABLE,
) -> AgentXError:
    """Build one structured orchestration error value."""
    return AgentXError(
        code=f"{_ERROR_PREFIX}.{code}",
        message=message,
        category=category,
        retryability=retryability,
        details=details,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyResult:
    """What one caller-supplied strategy adapter reports for one attempt.

    A strategy reports exactly one of two things and can assert neither
    success nor authority:

    ``outcome``
        The canonical A1.10 ``Result[ClosedLoopOutcome, AgentXError]`` produced
        by the governed path. Success/failure of that ``Result`` is canonical
        A1.10 semantics, re-checked here; the strategy cannot pre-decide it.

    ``unavailable_reason``
        An explicit statement that no authorized concrete strategy exists for
        this level right now. It fails closed and is never a success.

    Exactly one of the two must be supplied. There is deliberately no
    ``succeeded`` flag, no verification field, no permission field, no risk or
    budget field, no next-level field, and no free-text status: any of those
    would let an adapter fabricate an orchestration decision.
    """

    outcome: Result[ClosedLoopOutcome, AgentXError] | None = None
    unavailable_reason: str | None = None

    def __post_init__(self) -> None:
        if self.outcome is not None and not isinstance(self.outcome, Result):
            raise TypeError(
                "outcome must be a Result[ClosedLoopOutcome, AgentXError] or None, "
                f"got {type(self.outcome).__name__}"
            )
        if self.unavailable_reason is not None:
            if not isinstance(self.unavailable_reason, str):
                raise TypeError(
                    "unavailable_reason must be a string or None, "
                    f"got {type(self.unavailable_reason).__name__}"
                )
            reason = self.unavailable_reason
            if not reason or reason != reason.strip():
                raise OrchestrationRequestError(
                    "unavailable_reason must be non-empty and trimmed when provided"
                )
        if (self.outcome is None) == (self.unavailable_reason is None):
            raise OrchestrationRequestError(
                "a strategy result must carry exactly one of outcome or unavailable_reason"
            )

    @classmethod
    def executed(cls, outcome: Result[ClosedLoopOutcome, AgentXError]) -> StrategyResult:
        """Report the canonical governed-path result for this attempt."""
        return cls(outcome=outcome)

    @classmethod
    def unavailable(cls, reason: str) -> StrategyResult:
        """Report that no authorized concrete strategy exists for this level."""
        return cls(unavailable_reason=reason)


class ExecutionStrategy(Protocol):
    """Caller-supplied adapter that attempts one execution level, once.

    An adapter is a *port*, not an authority. It is expected to delegate to the
    canonical governed path (A2.04 Executor over the A1.10
    ``CapabilityExecutionLoop``) and return exactly what that path produced. It
    must not verify, transition Tasks, retry, escalate, or widen anything —
    A2.10 re-checks canonical evidence for every attempt regardless of what an
    adapter reports.
    """

    def attempt(
        self,
        task: Task,
        context: ExecutionContext,
        level: ExecutionLevel,
    ) -> StrategyResult:
        """Attempt the task once at ``level`` through the governed path."""
        ...


class StrategyRegistry:
    """Explicit immutable level -> adapter binding supplied by the caller.

    The registry is a plain lookup with no defaults, no fallbacks, and no
    synthesis: a level with no registered adapter is simply absent, and A2.10
    fails closed on it. Registration happens once, at construction, so the
    binding cannot drift between attempts of the same run.
    """

    __slots__ = ("_strategies",)

    def __init__(self, strategies: dict[ExecutionLevel, ExecutionStrategy] | None = None) -> None:
        """Bind zero or more explicit level adapters.

        Raises:
            TypeError: if a key is not a canonical :class:`ExecutionLevel` or a
                value does not expose the :class:`ExecutionStrategy` protocol.
        """
        bound: dict[ExecutionLevel, ExecutionStrategy] = {}
        if strategies is not None:
            if not isinstance(strategies, dict):
                raise TypeError(
                    "strategies must be a mapping of ExecutionLevel to ExecutionStrategy, "
                    f"got {type(strategies).__name__}"
                )
            for level, strategy in strategies.items():
                if not isinstance(level, ExecutionLevel):
                    raise TypeError(
                        f"strategy keys must be ExecutionLevel, got {type(level).__name__}"
                    )
                attempt = getattr(strategy, "attempt", None)
                if attempt is None or not callable(attempt):
                    raise TypeError(
                        f"strategy for {level.value} must expose a callable attempt(...)"
                    )
                bound[level] = strategy
        self._strategies = MappingProxyType(bound)

    def get(self, level: ExecutionLevel) -> ExecutionStrategy | None:
        """Return the adapter bound to ``level``, or ``None`` when unbound."""
        if not isinstance(level, ExecutionLevel):
            raise TypeError(f"level must be an ExecutionLevel, got {type(level).__name__}")
        return self._strategies.get(level)

    def levels(self) -> tuple[ExecutionLevel, ...]:
        """Return the bound levels in canonical hierarchy order."""
        return tuple(level for level in ExecutionLevel if level in self._strategies)

    def __contains__(self, level: object) -> bool:
        if not isinstance(level, ExecutionLevel):
            return False
        return level in self._strategies

    def __len__(self) -> int:
        return len(self._strategies)

    def __repr__(self) -> str:
        return f"StrategyRegistry(levels={[level.value for level in self.levels()]})"


@dataclass(frozen=True, slots=True, kw_only=True)
class OrchestrationLimits:
    """Explicit finite bounds owned by A2.10 composition.

    These bound *orchestration control flow only*. They are not a second
    resource-accounting system: C1.08 remains the sole authority for resource
    consumption, and nothing here consumes, resets, or widens a budget. They
    are also not a second anti-loop engine: A2.09's own
    :class:`~agentx.cognition.anti_loop.LoopGuardLimits` are supplied
    separately and evaluated by A2.09 itself.

    ``max_total_attempts`` is the hard ceiling on attempts in one run; the
    attempt loop is written as a bounded iteration over it, so no input can
    produce an unbounded loop. ``escalation_permitted`` is the explicit A2.08
    ``escalation_explicitly_permitted`` input: when ``False``, an unsuccessful
    attempt that cannot continue is EXHAUSTED rather than escalated.
    """

    max_total_attempts: int
    loop_guard_limits: LoopGuardLimits
    escalation_permitted: bool = True

    def __post_init__(self) -> None:
        _require_positive_int(
            self.max_total_attempts,
            field_name="max_total_attempts",
            maximum=MAX_CONFIGURABLE_TOTAL_ATTEMPTS,
        )
        _require_type(
            self.loop_guard_limits, LoopGuardLimits, field_name="loop_guard_limits"
        )
        if not isinstance(self.escalation_permitted, bool):
            raise TypeError("escalation_permitted must be a bool")


@dataclass(frozen=True, slots=True, kw_only=True)
class OrchestrationRequest:
    """The smallest typed request the bounded loop accepts.

    It carries exactly the explicit values composition needs:

    * ``task`` — the canonical A1.05 Task, registered PENDING with the A2.06
      TaskManager by the caller (A2.10 never invents Task identity);
    * ``context`` — the canonical A1.07 execution context whose cancellation
      token and deadline are observed at every boundary;
    * ``routing_evidence`` — the explicit A2.07 facts that select the *initial*
      level. A2.10 never guesses them and never re-routes mid-run;
    * ``requirement`` — the explicit A2.05 verification requirement every
      attempt is evaluated against;
    * ``limits`` — the explicit A2.10 control-flow bounds plus the A2.09
      LoopGuard limits.

    There is deliberately no permission field, no authority context, no risk
    or budget override, no envelope, no model/provider field, no prompt, no
    level override, and no metadata bag.
    """

    task: Task
    context: ExecutionContext
    routing_evidence: RoutingEvidence
    requirement: VerificationRequirement
    limits: OrchestrationLimits

    def __post_init__(self) -> None:
        task = _require_type(self.task, Task, field_name="task")
        context = _require_type(self.context, ExecutionContext, field_name="context")
        _require_type(self.routing_evidence, RoutingEvidence, field_name="routing_evidence")
        _require_type(self.requirement, VerificationRequirement, field_name="requirement")
        _require_type(self.limits, OrchestrationLimits, field_name="limits")

        if context.task_id is None:
            raise OrchestrationRequestError(
                "execution context must carry the orchestrated Task identity"
            )
        if context.task_id != task.task_id:
            raise OrchestrationRequestError(
                "execution context task identity does not match the orchestrated Task"
            )
        if task.status is not TaskStatus.PENDING:
            raise OrchestrationRequestError(
                "orchestration requires a Task in PENDING status; "
                f"got {task.status.value}"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class AttemptRecord:
    """Immutable evidence for exactly one completed attempt.

    The record is a *report*, never an authority: it carries the canonical
    values that were produced (the A1.10 outcome, the A2.05 evaluation, the
    structured error) plus the deterministic classifications A2.10 derived
    from them. It grants nothing and re-decides nothing.
    """

    attempt_index: int
    level: ExecutionLevel
    disposition: AttemptDisposition
    outcome: ClosedLoopOutcome | None
    evaluation: RequirementEvaluation | None
    error: AgentXError | None
    loop_guard: LoopGuardResult
    escalation: EscalationDecision | None

    def __post_init__(self) -> None:
        if type(self.attempt_index) is not int or self.attempt_index < 1:
            raise OrchestrationRequestError("attempt_index must be an int of at least 1")
        _require_type(self.level, ExecutionLevel, field_name="level")
        _require_type(self.disposition, AttemptDisposition, field_name="disposition")
        if self.outcome is not None:
            _require_type(self.outcome, ClosedLoopOutcome, field_name="outcome")
        if self.evaluation is not None:
            _require_type(self.evaluation, RequirementEvaluation, field_name="evaluation")
        if self.error is not None and not isinstance(self.error, AgentXError):
            raise TypeError("error must be an AgentXError or None")
        _require_type(self.loop_guard, LoopGuardResult, field_name="loop_guard")
        if self.escalation is not None:
            _require_type(self.escalation, EscalationDecision, field_name="escalation")

    @property
    def verified(self) -> bool:
        """True only for the one disposition that represents verified success."""
        return self.disposition is AttemptDisposition.VERIFIED_SUCCESS


@dataclass(frozen=True, slots=True, kw_only=True)
class OrchestrationOutcome:
    """Immutable terminal state of one bounded orchestration run.

    ``task`` is the final canonical Task exactly as the A2.06 TaskManager holds
    it after canonical A1.06 transitions. ``status`` is
    :attr:`OrchestrationStatus.SUCCEEDED` if and only if the run reached
    verified success, which the loop guarantees is equivalent to
    ``task.status is TaskStatus.SUCCEEDED`` and to a final attempt whose
    disposition is :attr:`AttemptDisposition.VERIFIED_SUCCESS`.

    Semantics the canonical Task state machine cannot represent (exhaustion,
    anti-loop stop, strategy unavailability) are reported here as explicit
    orchestration data — ``stop_reason`` and ``error`` — never as a mutated or
    invented Task status.
    """

    task: Task
    status: OrchestrationStatus
    stop_reason: OrchestrationStopReason
    initial_level: ExecutionLevel
    final_level: ExecutionLevel
    attempts: tuple[AttemptRecord, ...]
    error: AgentXError | None

    def __post_init__(self) -> None:
        _require_type(self.task, Task, field_name="task")
        _require_type(self.status, OrchestrationStatus, field_name="status")
        _require_type(self.stop_reason, OrchestrationStopReason, field_name="stop_reason")
        _require_type(self.initial_level, ExecutionLevel, field_name="initial_level")
        _require_type(self.final_level, ExecutionLevel, field_name="final_level")
        if not isinstance(self.attempts, tuple) or any(
            not isinstance(record, AttemptRecord) for record in self.attempts
        ):
            raise TypeError("attempts must be a tuple of AttemptRecord")
        if self.error is not None and not isinstance(self.error, AgentXError):
            raise TypeError("error must be an AgentXError or None")

        succeeded = self.status is OrchestrationStatus.SUCCEEDED
        # I1, enforced structurally on the outcome value itself: a SUCCEEDED
        # orchestration outcome cannot exist without a verified final attempt,
        # the VERIFIED stop reason, a canonically SUCCEEDED Task, and no error.
        if succeeded:
            if self.stop_reason is not OrchestrationStopReason.VERIFIED:
                raise OrchestrationRequestError(
                    "SUCCEEDED requires the VERIFIED stop reason"
                )
            if not self.attempts or not self.attempts[-1].verified:
                raise OrchestrationRequestError(
                    "SUCCEEDED requires a final attempt with verified success"
                )
            if self.task.status is not TaskStatus.SUCCEEDED:
                raise OrchestrationRequestError(
                    "SUCCEEDED requires the canonical Task to be SUCCEEDED"
                )
            if self.error is not None:
                raise OrchestrationRequestError("SUCCEEDED must carry no error")
        else:
            if self.stop_reason is OrchestrationStopReason.VERIFIED:
                raise OrchestrationRequestError(
                    "the VERIFIED stop reason requires SUCCEEDED"
                )
            if any(record.verified for record in self.attempts):
                raise OrchestrationRequestError(
                    "a verified attempt cannot end in a non-success status"
                )
            if self.task.status is TaskStatus.SUCCEEDED:
                raise OrchestrationRequestError(
                    "a non-success orchestration cannot leave the Task SUCCEEDED"
                )
        if not is_terminal(self.task.status):
            raise OrchestrationRequestError(
                "orchestration must leave the Task in a canonical terminal status"
            )

    @property
    def verified(self) -> bool:
        """True only when the run reached canonical verified success."""
        return self.status is OrchestrationStatus.SUCCEEDED

    @property
    def attempt_count(self) -> int:
        """Number of attempts actually made in this run."""
        return len(self.attempts)


def _verified_success(
    outcome: ClosedLoopOutcome,
    evaluation: RequirementEvaluation,
) -> bool:
    """Return True only for canonical, explicitly verified success.

    This is the single gate for I1 (``NO ACTION == SUCCESS WITHOUT
    VERIFICATION``). It reads only typed canonical fields — the A1.10
    :class:`~agentx.capabilities.runtime.LoopOutcome` enum, the canonical
    :class:`~agentx.capabilities.abi.VerificationResult` ``passed`` bool, the
    canonical :class:`~agentx.core.tasks.TaskStatus` enum, and the A2.05
    ``satisfied`` bool. No message, summary, detail, observation value, model
    text, or exception string is consulted, so hostile text cannot fabricate
    success. Both the A1.10 verdict and the A2.05 evaluation must agree.
    """
    if not isinstance(outcome, ClosedLoopOutcome):
        return False
    if not isinstance(evaluation, RequirementEvaluation):
        return False
    if outcome.kind is not LoopOutcome.VERIFIED:
        return False
    verification = outcome.verification
    if verification is None or verification.passed is not True:
        return False
    if outcome.task.status is not TaskStatus.SUCCEEDED:
        return False
    return evaluation.satisfied is True


def _attempt_fingerprint(level: ExecutionLevel, attempt_index: int) -> AttemptFingerprint:
    """Build the A2.09 attempt fingerprint for one attempt.

    The fingerprint identifies *what was tried*: the execution level. It is
    deliberately independent of the attempt index so that repeating the same
    level is visible to A2.09 as a repetition rather than as novelty.
    """
    del attempt_index
    return AttemptFingerprint(f"level:{level.value}")


def _outcome_fingerprint(
    disposition: AttemptDisposition,
    error: AgentXError | None,
) -> OutcomeFingerprint:
    """Build the A2.09 outcome fingerprint from typed classification only.

    The token is derived from the controlled :class:`AttemptDisposition`
    vocabulary plus, when present, the canonical structured error *code* (a
    controlled dot-separated identifier). Free-text messages, model output, and
    exception strings never reach A2.09.
    """
    if error is None:
        return OutcomeFingerprint(f"disposition:{disposition.value}")
    return OutcomeFingerprint(f"disposition:{disposition.value}/code:{error.code}")


class AgentLoop:
    """The A2.10 bounded task-attempt orchestration loop.

    The loop owns composition only. Its collaborators are the canonical
    contracts that own each decision, injected at construction:

    * A2.06 :class:`~agentx.cognition.task_manager.TaskManager` — the only
      Task lifecycle authority used here;
    * A2.07 :class:`~agentx.cognition.router.ExecutionLevelRouter` — the only
      source of the initial level;
    * A2.08 :class:`~agentx.cognition.escalation.ExecutionLevelEscalator` — the
      only source of a next level;
    * A2.09 :class:`~agentx.cognition.anti_loop.LoopGuard` — the only anti-loop
      authority;
    * A2.05 :class:`~agentx.capabilities.verifier.Verifier` — the only
      requirement-evaluation authority;
    * a caller-supplied :class:`StrategyRegistry` of level adapters bound to
      the canonical governed path.

    The object holds no mutable state between runs: every counter, history, and
    level lives in the local frame of :meth:`run`. Two runs with identical
    explicit inputs and identical strategy outcomes therefore make identical
    decisions.
    """

    __slots__ = (
        "_escalator",
        "_loop_guard",
        "_router",
        "_strategies",
        "_task_manager",
        "_verifier",
    )

    def __init__(
        self,
        *,
        task_manager: TaskManager,
        strategies: StrategyRegistry,
        router: ExecutionLevelRouter | None = None,
        escalator: ExecutionLevelEscalator | None = None,
        loop_guard: LoopGuard | None = None,
        verifier: Verifier | None = None,
    ) -> None:
        """Bind canonical collaborators and the explicit strategy registry.

        The stateless canonical engines (router, escalator, loop guard,
        verifier) default to fresh canonical instances; they are injectable so
        a composition root can supply the exact canonical objects it already
        owns. Only canonical types are accepted, so the loop cannot be pointed
        at a substitute decision engine.
        """
        self._task_manager = _require_type(task_manager, TaskManager, field_name="task_manager")
        self._strategies = _require_type(strategies, StrategyRegistry, field_name="strategies")
        self._router = (
            ExecutionLevelRouter()
            if router is None
            else _require_type(router, ExecutionLevelRouter, field_name="router")
        )
        self._escalator = (
            ExecutionLevelEscalator()
            if escalator is None
            else _require_type(escalator, ExecutionLevelEscalator, field_name="escalator")
        )
        self._loop_guard = (
            LoopGuard()
            if loop_guard is None
            else _require_type(loop_guard, LoopGuard, field_name="loop_guard")
        )
        self._verifier = (
            Verifier()
            if verifier is None
            else _require_type(verifier, Verifier, field_name="verifier")
        )

    # ------------------------------------------------------------------
    # Public entry point.
    # ------------------------------------------------------------------

    def run(
        self,
        request: OrchestrationRequest,
        *,
        clock: MonotonicClock | None = None,
    ) -> Result[OrchestrationOutcome, AgentXError]:
        """Run one bounded orchestration for ``request``.

        Returns ``Result.success(OrchestrationOutcome)`` for every deterministic
        terminal state of the loop. The outcome's ``status`` carries the truth;
        :attr:`OrchestrationStatus.SUCCEEDED` is the only value that ever
        accompanies ``TaskStatus.SUCCEEDED``, and it requires canonical
        verification evidence.

        Returns ``Result.failure(AgentXError)`` — without attempting anything —
        when the request cannot be orchestrated at all (the Task is not
        registered with the injected TaskManager, or the canonical
        ``PENDING -> RUNNING`` transition is refused). Wrong argument types are
        programming errors and raise ``TypeError``.

        ``clock`` is the injectable A1.07 monotonic clock used for deadline
        observation; it makes deadline tests deterministic without sleeping.
        The loop never sleeps, polls, or starts a background worker.
        """
        _require_type(request, OrchestrationRequest, field_name="request")

        task_id = request.task.task_id
        registered = self._task_manager.get(task_id)
        if registered is None:
            return Result[OrchestrationOutcome, AgentXError].failure(
                _error(
                    code="task_not_registered",
                    message="orchestrated Task is not registered with the Task Manager",
                    category=ErrorCategory.NOT_FOUND,
                    details={"task_id": task_id.to_str()},
                )
            )
        if registered.status is not TaskStatus.PENDING:
            return Result[OrchestrationOutcome, AgentXError].failure(
                _error(
                    code="task_not_pending",
                    message=(
                        "orchestration requires a registered Task in PENDING status; "
                        f"got {registered.status.value}"
                    ),
                    category=ErrorCategory.PRECONDITION,
                    details={"task_id": task_id.to_str()},
                )
            )

        # A2.07 owns the initial level. It is computed once, from explicit
        # evidence, before any Task transition or attempt.
        routing: RoutingDecision = self._router.route(request.routing_evidence)
        initial_level = routing.level

        # Boundary 1: observe A1.07 stop state BEFORE starting. A cancelled or
        # timed-out request never transitions to RUNNING and never attempts.
        stop = request.context.observe_stop(clock=clock)
        if stop.should_stop:
            return self._stop_before_start(
                request,
                stop=stop,
                initial_level=initial_level,
            )

        # Canonical A1.06 PENDING -> RUNNING through the A2.06 TaskManager.
        started = self._task_manager.transition(task_id, TaskStatus.RUNNING)
        if started.is_failure:
            return Result[OrchestrationOutcome, AgentXError].failure(started.unwrap_error())

        return self._attempt_loop(request, initial_level=initial_level, clock=clock)

    # ------------------------------------------------------------------
    # The bounded attempt loop.
    # ------------------------------------------------------------------

    def _attempt_loop(
        self,
        request: OrchestrationRequest,
        *,
        initial_level: ExecutionLevel,
        clock: MonotonicClock | None,
    ) -> Result[OrchestrationOutcome, AgentXError]:
        """Iterate bounded attempts, delegating every decision to its owner.

        The iteration is a ``for`` over ``range(max_total_attempts)``: the loop
        is finite by construction, so no strategy behaviour, evidence shape, or
        decision combination can produce an infinite retry.
        """
        limits = request.limits
        level = initial_level
        history: list[AttemptEvidence] = []
        records: list[AttemptRecord] = []

        for index in range(limits.max_total_attempts):
            attempt_index = index + 1

            # Boundary 2: observe A1.07 stop state before every attempt,
            # including between attempts. Cancellation/timeout stops future
            # attempts and is never converted into success.
            stop = request.context.observe_stop(clock=clock)
            if stop.should_stop:
                return self._stop_during_run(
                    request,
                    stop=stop,
                    initial_level=initial_level,
                    final_level=level,
                    records=records,
                )

            strategy = self._strategies.get(level)
            if strategy is None:
                # Fail closed: no authorized concrete strategy for this level.
                # At L5 this is exactly the required semantic — reaching
                # L5_EXPLORATORY authorizes nothing and triggers no research.
                return self._terminate_unavailable(
                    request,
                    initial_level=initial_level,
                    level=level,
                    attempt_index=attempt_index,
                    history=history,
                    records=records,
                )

            result = strategy.attempt(request.task, request.context, level)
            _require_type(result, StrategyResult, field_name="strategy result")

            disposition, outcome, evaluation, error = self._classify(result, request.requirement)

            # I1: the single success path in this module.
            if disposition is AttemptDisposition.VERIFIED_SUCCESS:
                return self._terminate_verified(
                    request,
                    initial_level=initial_level,
                    level=level,
                    attempt_index=attempt_index,
                    outcome=outcome,
                    evaluation=evaluation,
                    history=history,
                    records=records,
                )

            # Record explicit attempt evidence, then let A2.09 evaluate it.
            history.append(
                AttemptEvidence(
                    attempt=_attempt_fingerprint(level, attempt_index),
                    outcome=_outcome_fingerprint(disposition, error),
                )
            )
            loop_guard_result = self._loop_guard.evaluate(
                history=tuple(history),
                limits=limits.loop_guard_limits,
            )

            # Canonical resource exhaustion and canonical safety stops are
            # terminal: fail closed, never retry, never escalate, never widen.
            if disposition in (
                AttemptDisposition.RESOURCE_EXHAUSTED,
                AttemptDisposition.SAFETY_STOPPED,
            ):
                records.append(
                    AttemptRecord(
                        attempt_index=attempt_index,
                        level=level,
                        disposition=disposition,
                        outcome=outcome,
                        evaluation=evaluation,
                        error=error,
                        loop_guard=loop_guard_result,
                        escalation=None,
                    )
                )
                return self._terminate_hard_stop(
                    request,
                    initial_level=initial_level,
                    final_level=level,
                    disposition=disposition,
                    error=error,
                    records=records,
                )

            # A2.09 owns the anti-loop decision. STOP_LOOP stops orchestration;
            # it implies no success and authorizes no escalation.
            if loop_guard_result.decision is LoopGuardDecision.STOP_LOOP:
                records.append(
                    AttemptRecord(
                        attempt_index=attempt_index,
                        level=level,
                        disposition=disposition,
                        outcome=outcome,
                        evaluation=evaluation,
                        error=error,
                        loop_guard=loop_guard_result,
                        escalation=None,
                    )
                )
                return self._terminate_anti_loop(
                    request,
                    initial_level=initial_level,
                    final_level=level,
                    loop_guard_result=loop_guard_result,
                    records=records,
                )

            # A2.08 owns the next-level decision. A2.10 supplies explicit typed
            # evidence and never consults a second escalation table.
            escalation = self._escalator.decide(
                EscalationEvidence(
                    current_level=level,
                    # A2.10 attempts each level once per attempt; the strategy
                    # port exposes no "can continue" signal, so this is False.
                    current_strategy_can_continue=False,
                    # Verified success never reaches this point (it returns
                    # above), so this is always False here.
                    attempt_verified_successful=False,
                    escalation_explicitly_permitted=limits.escalation_permitted,
                )
            )
            records.append(
                AttemptRecord(
                    attempt_index=attempt_index,
                    level=level,
                    disposition=disposition,
                    outcome=outcome,
                    evaluation=evaluation,
                    error=error,
                    loop_guard=loop_guard_result,
                    escalation=escalation,
                )
            )

            if escalation.action is EscalationAction.ESCALATE:
                next_level = escalation.next_level
                if next_level is None:  # pragma: no cover - A2.08 guarantees this
                    return self._terminate_escalation_exhausted(
                        request,
                        initial_level=initial_level,
                        final_level=level,
                        records=records,
                    )
                # Monotonic by A2.08 construction: never cheaper, never skipped.
                level = next_level
                continue

            if escalation.action is EscalationAction.STAY:  # pragma: no cover - see note
                # Unreachable with the evidence A2.10 supplies (STAY requires a
                # verified success or a continuable strategy, neither of which
                # can hold here). Kept explicit so a future A2.08 change cannot
                # silently fall through to EXHAUSTED.
                continue

            return self._terminate_escalation_exhausted(
                request,
                initial_level=initial_level,
                final_level=level,
                records=records,
            )

        # The explicit total-attempt ceiling was reached without success.
        return self._terminate_attempt_ceiling(
            request,
            initial_level=initial_level,
            final_level=level,
            records=records,
        )

    # ------------------------------------------------------------------
    # Attempt classification. Reads canonical typed evidence only.
    # ------------------------------------------------------------------

    def _classify(
        self,
        result: StrategyResult,
        requirement: VerificationRequirement,
    ) -> tuple[
        AttemptDisposition,
        ClosedLoopOutcome | None,
        RequirementEvaluation | None,
        AgentXError | None,
    ]:
        """Classify one strategy result from canonical evidence alone.

        No free text is consulted. An outcome is promoted to
        :attr:`AttemptDisposition.VERIFIED_SUCCESS` only when
        :func:`_verified_success` agrees, which requires both the canonical
        A1.10 verdict and the A2.05 requirement evaluation.
        """
        if result.unavailable_reason is not None:
            return (
                AttemptDisposition.STRATEGY_UNAVAILABLE,
                None,
                None,
                _error(
                    code="strategy_unavailable",
                    message="no authorized concrete strategy is available for this level",
                    category=ErrorCategory.PRECONDITION,
                ),
            )

        outcome_result = result.outcome
        if outcome_result is None:  # pragma: no cover - StrategyResult enforces this
            raise OrchestrationRequestError("strategy result carried no outcome")

        if outcome_result.is_failure:
            error = outcome_result.unwrap_error()
            return (self._classify_error(error), None, None, error)

        outcome = outcome_result.unwrap()
        if not isinstance(outcome, ClosedLoopOutcome):
            # Fail closed on a malformed outcome rather than trusting it.
            return (
                AttemptDisposition.STRATEGY_ERROR,
                None,
                None,
                _error(
                    code="strategy_outcome_malformed",
                    message="strategy returned a non-canonical execution outcome",
                    category=ErrorCategory.INTERNAL,
                ),
            )

        # A2.05 evaluates the canonical outcome against the explicit
        # requirement. It never manufactures a verdict and never mutates
        # anything; it is the second half of the I1 gate.
        evaluation = self._verifier.evaluate(
            VerifierRequest(outcome=outcome, requirement=requirement)
        )
        if _verified_success(outcome, evaluation):
            return (AttemptDisposition.VERIFIED_SUCCESS, outcome, evaluation, None)

        error = outcome.error
        if error is not None:
            classified = self._classify_error(error)
            if classified in (
                AttemptDisposition.RESOURCE_EXHAUSTED,
                AttemptDisposition.SAFETY_STOPPED,
            ):
                return (classified, outcome, evaluation, error)
        return (AttemptDisposition.UNVERIFIED, outcome, evaluation, error)

    @staticmethod
    def _classify_error(error: AgentXError) -> AttemptDisposition:
        """Classify a canonical structured error by code/category only.

        Codes are controlled canonical identifiers, never free text, and the
        category is a controlled enum. Resource exhaustion and safety stops are
        recognized so the loop can fail closed on them rather than retrying.
        """
        if error.code in _RESOURCE_EXHAUSTION_CODES or error.category is ErrorCategory.RESOURCE:
            return AttemptDisposition.RESOURCE_EXHAUSTED
        if error.code in _SAFETY_STOP_CODES or error.category is ErrorCategory.CANCELLED:
            return AttemptDisposition.SAFETY_STOPPED
        return AttemptDisposition.STRATEGY_ERROR

    # ------------------------------------------------------------------
    # Terminal helpers. Every one closes the run through canonical A1.06
    # transitions requested from the A2.06 TaskManager.
    # ------------------------------------------------------------------

    def _finish(
        self,
        request: OrchestrationRequest,
        *,
        target: TaskStatus,
        status: OrchestrationStatus,
        stop_reason: OrchestrationStopReason,
        initial_level: ExecutionLevel,
        final_level: ExecutionLevel,
        records: list[AttemptRecord],
        error: AgentXError | None,
    ) -> Result[OrchestrationOutcome, AgentXError]:
        """Apply the canonical terminal transition and build the outcome value."""
        transitioned = self._task_manager.transition(request.task.task_id, target)
        if transitioned.is_failure:  # pragma: no cover - RUNNING -> terminal is legal
            return Result[OrchestrationOutcome, AgentXError].failure(
                transitioned.unwrap_error()
            )
        return Result[OrchestrationOutcome, AgentXError].success(
            OrchestrationOutcome(
                task=transitioned.unwrap(),
                status=status,
                stop_reason=stop_reason,
                initial_level=initial_level,
                final_level=final_level,
                attempts=tuple(records),
                error=error,
            )
        )

    def _stop_before_start(
        self,
        request: OrchestrationRequest,
        *,
        stop: ExecutionStopStatus,
        initial_level: ExecutionLevel,
    ) -> Result[OrchestrationOutcome, AgentXError]:
        """Close a run stopped before it began: canonical PENDING -> CANCELLED."""
        status, stop_reason, error = self._stop_terminal(stop, attempted=False)
        return self._finish(
            request,
            target=TaskStatus.CANCELLED,
            status=status,
            stop_reason=stop_reason,
            initial_level=initial_level,
            final_level=initial_level,
            records=[],
            error=error,
        )

    def _stop_during_run(
        self,
        request: OrchestrationRequest,
        *,
        stop: ExecutionStopStatus,
        initial_level: ExecutionLevel,
        final_level: ExecutionLevel,
        records: list[AttemptRecord],
    ) -> Result[OrchestrationOutcome, AgentXError]:
        """Close a run stopped between attempts: canonical RUNNING -> CANCELLED."""
        status, stop_reason, error = self._stop_terminal(stop, attempted=bool(records))
        return self._finish(
            request,
            target=TaskStatus.CANCELLED,
            status=status,
            stop_reason=stop_reason,
            initial_level=initial_level,
            final_level=final_level,
            records=records,
            error=error,
        )

    @staticmethod
    def _stop_terminal(
        stop: ExecutionStopStatus,
        *,
        attempted: bool,
    ) -> tuple[OrchestrationStatus, OrchestrationStopReason, AgentXError]:
        """Map an A1.07 stop observation onto explicit terminal orchestration data.

        Cancellation and timeout are distinct and are reported distinctly.
        When both hold, A1.07 lists cancellation first and this mapping follows
        that canonical order. Neither is ever reported as success.
        """
        reasons = ", ".join(reason.value for reason in stop.reasons)
        details: dict[str, object] = {"stop_reasons": reasons, "attempted": attempted}
        if ExecutionStopReason.CANCELLED in stop.reasons:
            return (
                OrchestrationStatus.CANCELLED,
                OrchestrationStopReason.CANCELLED,
                _error(
                    code="cancelled",
                    message=f"orchestration stopped: {reasons}",
                    category=ErrorCategory.CANCELLED,
                    details=details,
                ),
            )
        return (
            OrchestrationStatus.TIMED_OUT,
            OrchestrationStopReason.DEADLINE_EXPIRED,
            _error(
                code="deadline_expired",
                message=f"orchestration stopped: {reasons}",
                category=ErrorCategory.TIMEOUT,
                details=details,
            ),
        )

    def _terminate_verified(
        self,
        request: OrchestrationRequest,
        *,
        initial_level: ExecutionLevel,
        level: ExecutionLevel,
        attempt_index: int,
        outcome: ClosedLoopOutcome | None,
        evaluation: RequirementEvaluation | None,
        history: list[AttemptEvidence],
        records: list[AttemptRecord],
    ) -> Result[OrchestrationOutcome, AgentXError]:
        """Close the one successful path: canonical RUNNING -> SUCCEEDED.

        Reached only from the :func:`_verified_success` branch, so the Task can
        only become SUCCEEDED after canonical verification evidence exists.
        """
        loop_guard_result = self._loop_guard.evaluate(
            history=tuple(history),
            limits=request.limits.loop_guard_limits,
        )
        records.append(
            AttemptRecord(
                attempt_index=attempt_index,
                level=level,
                disposition=AttemptDisposition.VERIFIED_SUCCESS,
                outcome=outcome,
                evaluation=evaluation,
                error=None,
                loop_guard=loop_guard_result,
                escalation=None,
            )
        )
        return self._finish(
            request,
            target=TaskStatus.SUCCEEDED,
            status=OrchestrationStatus.SUCCEEDED,
            stop_reason=OrchestrationStopReason.VERIFIED,
            initial_level=initial_level,
            final_level=level,
            records=records,
            error=None,
        )

    def _terminate_unavailable(
        self,
        request: OrchestrationRequest,
        *,
        initial_level: ExecutionLevel,
        level: ExecutionLevel,
        attempt_index: int,
        history: list[AttemptEvidence],
        records: list[AttemptRecord],
    ) -> Result[OrchestrationOutcome, AgentXError]:
        """Fail closed when no authorized concrete strategy exists for ``level``."""
        error = _error(
            code="strategy_unavailable",
            message=(
                "no authorized concrete strategy is registered for execution level "
                f"{level.value}"
            ),
            category=ErrorCategory.PRECONDITION,
            details={"level": level.value},
        )
        history.append(
            AttemptEvidence(
                attempt=_attempt_fingerprint(level, attempt_index),
                outcome=_outcome_fingerprint(AttemptDisposition.STRATEGY_UNAVAILABLE, error),
            )
        )
        loop_guard_result = self._loop_guard.evaluate(
            history=tuple(history),
            limits=request.limits.loop_guard_limits,
        )
        records.append(
            AttemptRecord(
                attempt_index=attempt_index,
                level=level,
                disposition=AttemptDisposition.STRATEGY_UNAVAILABLE,
                outcome=None,
                evaluation=None,
                error=error,
                loop_guard=loop_guard_result,
                escalation=None,
            )
        )
        return self._finish(
            request,
            target=TaskStatus.FAILED,
            status=OrchestrationStatus.FAILED,
            stop_reason=OrchestrationStopReason.STRATEGY_UNAVAILABLE,
            initial_level=initial_level,
            final_level=level,
            records=records,
            error=error,
        )

    def _terminate_hard_stop(
        self,
        request: OrchestrationRequest,
        *,
        initial_level: ExecutionLevel,
        final_level: ExecutionLevel,
        disposition: AttemptDisposition,
        error: AgentXError | None,
        records: list[AttemptRecord],
    ) -> Result[OrchestrationOutcome, AgentXError]:
        """Fail closed on canonical resource exhaustion or a canonical safety stop."""
        if disposition is AttemptDisposition.RESOURCE_EXHAUSTED:
            stop_reason = OrchestrationStopReason.RESOURCE_EXHAUSTED
            reported = error or _error(
                code="resource_exhausted",
                message="canonical resource accounting refused the governed run",
                category=ErrorCategory.RESOURCE,
            )
            target = TaskStatus.FAILED
            status = OrchestrationStatus.FAILED
        else:
            stop_reason = OrchestrationStopReason.SAFETY_STOP
            reported = error or _error(
                code="safety_stop",
                message="the governed path refused the run for safety reasons",
                category=ErrorCategory.PRECONDITION,
            )
            target = TaskStatus.CANCELLED
            status = OrchestrationStatus.CANCELLED
        return self._finish(
            request,
            target=target,
            status=status,
            stop_reason=stop_reason,
            initial_level=initial_level,
            final_level=final_level,
            records=records,
            error=reported,
        )

    def _terminate_anti_loop(
        self,
        request: OrchestrationRequest,
        *,
        initial_level: ExecutionLevel,
        final_level: ExecutionLevel,
        loop_guard_result: LoopGuardResult,
        records: list[AttemptRecord],
    ) -> Result[OrchestrationOutcome, AgentXError]:
        """Stop because A2.09 said STOP_LOOP; no success, no escalation."""
        error = _error(
            code="anti_loop_stop",
            message="anti-loop evaluation stopped further attempts",
            category=ErrorCategory.PRECONDITION,
            details={
                "trigger": loop_guard_result.trigger.value,
                "total_attempts": loop_guard_result.total_attempts,
            },
        )
        return self._finish(
            request,
            target=TaskStatus.FAILED,
            status=OrchestrationStatus.EXHAUSTED,
            stop_reason=OrchestrationStopReason.ANTI_LOOP,
            initial_level=initial_level,
            final_level=final_level,
            records=records,
            error=error,
        )

    def _terminate_escalation_exhausted(
        self,
        request: OrchestrationRequest,
        *,
        initial_level: ExecutionLevel,
        final_level: ExecutionLevel,
        records: list[AttemptRecord],
    ) -> Result[OrchestrationOutcome, AgentXError]:
        """Stop because A2.08 said EXHAUSTED (no justified next level)."""
        error = _error(
            code="escalation_exhausted",
            message=(
                "escalation is exhausted: no justified next execution level above "
                f"{final_level.value}"
            ),
            category=ErrorCategory.PRECONDITION,
            details={"final_level": final_level.value},
        )
        return self._finish(
            request,
            target=TaskStatus.FAILED,
            status=OrchestrationStatus.EXHAUSTED,
            stop_reason=OrchestrationStopReason.ESCALATION_EXHAUSTED,
            initial_level=initial_level,
            final_level=final_level,
            records=records,
            error=error,
        )

    def _terminate_attempt_ceiling(
        self,
        request: OrchestrationRequest,
        *,
        initial_level: ExecutionLevel,
        final_level: ExecutionLevel,
        records: list[AttemptRecord],
    ) -> Result[OrchestrationOutcome, AgentXError]:
        """Stop because the explicit total-attempt ceiling was reached."""
        error = _error(
            code="attempt_ceiling_reached",
            message=(
                "orchestration reached its explicit total-attempt ceiling of "
                f"{request.limits.max_total_attempts}"
            ),
            category=ErrorCategory.PRECONDITION,
            details={"max_total_attempts": request.limits.max_total_attempts},
        )
        return self._finish(
            request,
            target=TaskStatus.FAILED,
            status=OrchestrationStatus.EXHAUSTED,
            stop_reason=OrchestrationStopReason.ATTEMPT_CEILING,
            initial_level=initial_level,
            final_level=final_level,
            records=records,
            error=error,
        )

    def __repr__(self) -> str:
        return f"AgentLoop(levels={[level.value for level in self._strategies.levels()]})"
