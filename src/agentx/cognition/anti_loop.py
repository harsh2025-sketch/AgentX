"""Deterministic bounded anti-loop contracts for future AgentX orchestration.

A2.09 prevents future autonomous orchestration from treating repeated attempts
as an unbounded retry surface.  The guard consumes explicit, caller-supplied
stable fingerprints and returns inert evidence saying either that another
attempt is still within the configured limits or that the loop must stop.

The guard is deliberately narrow:

* fingerprints are opaque stable identifiers; this module never hashes,
  classifies, embeds, or interprets natural-language errors;
* progress is recognized only from an explicit previously unseen
  :class:`ProgressFingerprint`; correlation IDs, arbitrary text, and metadata
  are not progress inputs;
* every repetition/attempt limit is a finite positive integer; there is no
  ``None``, sentinel, zero, or implicit unlimited mode;
* evaluating history is pure and stateless.  The guard owns no retry counter,
  thread, timer, scheduler, persistence, EventBus worker, or background loop;
* the canonical C1.08 ``ResourceEnvelope`` / ``ResourceBudget`` remain the sole
  resource-accounting contracts.  A2.09 owns only narrow anti-loop ceilings and
  never imports, mutates, resets, widens, or consumes a resource budget.

A loop result is DATA, never authority.  It does not retry, escalate, execute,
research, repair, invoke models, transition Tasks, grant Permission, bypass the
ActionGate, alter risk/budgets, clear EmergencyStop, fabricate verification, or
activate procedures.  Later A2.10 orchestration may consume this result while
remaining responsible for all actual control-flow and authority decisions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

__all__ = [
    "AttemptEvidence",
    "AttemptFingerprint",
    "LoopGuard",
    "LoopGuardDecision",
    "LoopGuardLimits",
    "LoopGuardResult",
    "LoopGuardTrigger",
    "OutcomeFingerprint",
    "ProgressFingerprint",
]

_MAX_FINGERPRINT_LENGTH: Final[int] = 256
_MAX_LIMIT: Final[int] = (1 << 63) - 1
_FINGERPRINT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:/+=@-]{0,255}"
)


def _validate_fingerprint(value: object, *, field_name: str) -> str:
    """Validate one caller-supplied stable opaque fingerprint token.

    Fingerprints intentionally use a narrow token grammar instead of free
    natural language.  Callers that need to represent rich evidence must first
    supply their own stable ID/hash; this module never guesses equivalence.
    """
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty and trimmed")
    if len(value) > _MAX_FINGERPRINT_LENGTH:
        raise ValueError(
            f"{field_name} must not exceed {_MAX_FINGERPRINT_LENGTH} characters"
        )
    if _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must be an opaque stable token containing only "
            "ASCII letters, digits, '.', '_', ':', '/', '+', '=', '@', or '-'"
        )
    return value


def _validate_limit(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int")
    if value < 1:
        raise ValueError(f"{field_name} must be at least 1")
    if value > _MAX_LIMIT:
        raise OverflowError(f"{field_name} exceeds the supported counter range")
    return value


def _validate_count(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int")
    if value < 0:
        raise ValueError(f"{field_name} must not be negative")
    if value > _MAX_LIMIT:
        raise OverflowError(f"{field_name} exceeds the supported counter range")
    return value


@dataclass(frozen=True, slots=True)
class AttemptFingerprint:
    """Stable opaque identifier for what one attempt tried to do."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _validate_fingerprint(self.value, field_name="attempt fingerprint"),
        )


@dataclass(frozen=True, slots=True)
class OutcomeFingerprint:
    """Stable opaque identifier for one observed failure/outcome class.

    The value is supplied by the caller.  A2.09 does not parse errors or infer
    that two natural-language messages are equivalent.
    """

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _validate_fingerprint(self.value, field_name="outcome fingerprint"),
        )


@dataclass(frozen=True, slots=True)
class ProgressFingerprint:
    """Explicit stable marker for a meaningfully new progress state.

    Only the first occurrence of a previously unseen progress marker advances
    the guard's progress epoch.  Reusing an old marker is not progress.
    """

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "value",
            _validate_fingerprint(self.value, field_name="progress fingerprint"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class AttemptEvidence:
    """One completed attempt's explicit anti-loop evidence.

    There is deliberately no metadata bag, correlation ID, free-text error,
    retry instruction, authority, status mutation, or escalation field.  Those
    values cannot accidentally become progress or reset the guard.
    """

    attempt: AttemptFingerprint
    outcome: OutcomeFingerprint
    progress: ProgressFingerprint | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, AttemptFingerprint):
            raise TypeError("attempt must be an AttemptFingerprint")
        if not isinstance(self.outcome, OutcomeFingerprint):
            raise TypeError("outcome must be an OutcomeFingerprint")
        if self.progress is not None and not isinstance(self.progress, ProgressFingerprint):
            raise TypeError("progress must be a ProgressFingerprint or None")


@dataclass(frozen=True, slots=True, kw_only=True)
class LoopGuardLimits:
    """Explicit finite ceilings owned only by A2.09 anti-loop evaluation.

    ``max_total_attempts`` is global for the supplied history and never resets.
    ``max_same_attempts`` bounds exact occurrences of the latest attempt
    fingerprint since the most recent explicit progress advance.
    ``max_same_outcomes_without_progress`` bounds exact occurrences of the
    latest outcome fingerprint in that same progress epoch.

    Reaching a ceiling exactly is terminal.  All fields are required positive
    integers; there is no unlimited mode.
    """

    max_total_attempts: int
    max_same_attempts: int
    max_same_outcomes_without_progress: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_total_attempts",
            _validate_limit(self.max_total_attempts, field_name="max_total_attempts"),
        )
        object.__setattr__(
            self,
            "max_same_attempts",
            _validate_limit(self.max_same_attempts, field_name="max_same_attempts"),
        )
        object.__setattr__(
            self,
            "max_same_outcomes_without_progress",
            _validate_limit(
                self.max_same_outcomes_without_progress,
                field_name="max_same_outcomes_without_progress",
            ),
        )


class LoopGuardDecision(StrEnum):
    """Bounded anti-loop decision for future orchestration."""

    CONTINUE = "CONTINUE"
    STOP_LOOP = "STOP_LOOP"


class LoopGuardTrigger(StrEnum):
    """Deterministic reason a loop evaluation reached its decision."""

    NONE = "NONE"
    TOTAL_ATTEMPTS = "TOTAL_ATTEMPTS"
    REPEATED_ATTEMPT = "REPEATED_ATTEMPT"
    STALLED_OUTCOME = "STALLED_OUTCOME"


@dataclass(frozen=True, slots=True, kw_only=True)
class LoopGuardResult:
    """Inert anti-loop decision plus the exact counters that produced it."""

    decision: LoopGuardDecision
    trigger: LoopGuardTrigger
    total_attempts: int
    same_attempt_count: int
    same_outcome_count: int
    distinct_progress_markers: int

    def __post_init__(self) -> None:
        if not isinstance(self.decision, LoopGuardDecision):
            raise TypeError("decision must be a LoopGuardDecision")
        if not isinstance(self.trigger, LoopGuardTrigger):
            raise TypeError("trigger must be a LoopGuardTrigger")
        object.__setattr__(
            self,
            "total_attempts",
            _validate_count(self.total_attempts, field_name="total_attempts"),
        )
        object.__setattr__(
            self,
            "same_attempt_count",
            _validate_count(self.same_attempt_count, field_name="same_attempt_count"),
        )
        object.__setattr__(
            self,
            "same_outcome_count",
            _validate_count(self.same_outcome_count, field_name="same_outcome_count"),
        )
        object.__setattr__(
            self,
            "distinct_progress_markers",
            _validate_count(
                self.distinct_progress_markers,
                field_name="distinct_progress_markers",
            ),
        )
        if self.decision is LoopGuardDecision.CONTINUE and self.trigger is not LoopGuardTrigger.NONE:
            raise ValueError("CONTINUE requires trigger NONE")
        if self.decision is LoopGuardDecision.STOP_LOOP and self.trigger is LoopGuardTrigger.NONE:
            raise ValueError("STOP_LOOP requires a terminal trigger")


class LoopGuard:
    """Stateless deterministic evaluator over explicit completed-attempt history.

    Trigger priority is deterministic when multiple ceilings are reached by the
    same history: total-attempt ceiling first, then repeated-attempt ceiling,
    then stalled-outcome ceiling.
    """

    __slots__ = ()

    def evaluate(
        self,
        *,
        history: tuple[AttemptEvidence, ...],
        limits: LoopGuardLimits,
    ) -> LoopGuardResult:
        """Evaluate one immutable history without executing or mutating anything."""
        if not isinstance(history, tuple):
            raise TypeError("history must be a tuple of AttemptEvidence")
        if not isinstance(limits, LoopGuardLimits):
            raise TypeError("limits must be LoopGuardLimits")
        for evidence in history:
            if not isinstance(evidence, AttemptEvidence):
                raise TypeError("history must contain only AttemptEvidence")

        total_attempts = len(history)
        if total_attempts > _MAX_LIMIT:
            raise OverflowError("history exceeds the supported counter range")
        if not history:
            return LoopGuardResult(
                decision=LoopGuardDecision.CONTINUE,
                trigger=LoopGuardTrigger.NONE,
                total_attempts=0,
                same_attempt_count=0,
                same_outcome_count=0,
                distinct_progress_markers=0,
            )

        epoch_start, distinct_progress_markers = self._progress_epoch(history)
        current_epoch = history[epoch_start:]
        latest = history[-1]
        same_attempt_count = sum(item.attempt == latest.attempt for item in current_epoch)
        same_outcome_count = sum(item.outcome == latest.outcome for item in current_epoch)

        if total_attempts >= limits.max_total_attempts:
            decision = LoopGuardDecision.STOP_LOOP
            trigger = LoopGuardTrigger.TOTAL_ATTEMPTS
        elif same_attempt_count >= limits.max_same_attempts:
            decision = LoopGuardDecision.STOP_LOOP
            trigger = LoopGuardTrigger.REPEATED_ATTEMPT
        elif same_outcome_count >= limits.max_same_outcomes_without_progress:
            decision = LoopGuardDecision.STOP_LOOP
            trigger = LoopGuardTrigger.STALLED_OUTCOME
        else:
            decision = LoopGuardDecision.CONTINUE
            trigger = LoopGuardTrigger.NONE

        return LoopGuardResult(
            decision=decision,
            trigger=trigger,
            total_attempts=total_attempts,
            same_attempt_count=same_attempt_count,
            same_outcome_count=same_outcome_count,
            distinct_progress_markers=distinct_progress_markers,
        )

    @staticmethod
    def _progress_epoch(history: tuple[AttemptEvidence, ...]) -> tuple[int, int]:
        """Return start index of the latest explicit-progress epoch and marker count.

        A progress marker advances the epoch exactly once: only its first
        occurrence is meaningful.  Replaying an old marker cannot reset
        repetition counters.
        """
        seen: set[ProgressFingerprint] = set()
        epoch_start = 0
        for index, evidence in enumerate(history):
            progress = evidence.progress
            if progress is None or progress in seen:
                continue
            seen.add(progress)
            epoch_start = index
        return epoch_start, len(seen)
