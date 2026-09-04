"""Execution-scoped context, cooperative cancellation, and monotonic deadlines.

A1.07 defines the minimal inward contracts future execution layers need to
identify work and cooperatively decide when to stop. This module deliberately
does not execute capabilities, schedule work, mutate Task state, grant
authority, enforce budgets, or interrupt threads/processes.

Design:
    - :class:`ExecutionContext` carries only task/correlation identity, a
      read-only cancellation token, and an optional monotonic deadline.
    - :class:`CancellationSource` owns the one-way cancellation transition;
      consumers receive :class:`CancellationToken` for observation only.
    - :class:`Deadline` stores an absolute monotonic-clock reading. Wall-clock
      timestamps never decide elapsed-time expiry.
    - Clock observation is injected at check/construction boundaries rather
      than stored in ``ExecutionContext``, keeping executable callbacks out of
      the context and making tests deterministic without sleeping.

This module belongs to ``agentx.core`` and depends only on the standard library
and the canonical :class:`agentx.core.ids.TaskId` contract.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Final, Protocol
from uuid import UUID

from agentx.core.ids import TaskId

__all__ = [
    "CancellationSource",
    "CancellationToken",
    "Deadline",
    "DeadlineStatus",
    "ExecutionContext",
    "ExecutionContextValidationError",
    "ExecutionStopReason",
    "ExecutionStopStatus",
    "MonotonicClock",
]

_MAX_CANCELLATION_REASON_LENGTH: Final[int] = 256
_REASON_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class ExecutionContextValidationError(ValueError):
    """Raised when an A1.07 execution contract receives a malformed value."""


class MonotonicClock(Protocol):
    """Narrow injectable source of monotonic time.

    Implementations must return finite seconds from an arbitrary monotonic
    reference point. Only differences/order are meaningful; values are not
    wall-clock timestamps and are not serializable time-of-day information.
    """

    def monotonic(self) -> float:
        """Return the current monotonic clock reading in seconds."""
        ...


def _validate_finite_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} must be a finite number, got {type(value).__name__}")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ExecutionContextValidationError(f"{field_name} must be finite") from exc
    if not math.isfinite(number):
        raise ExecutionContextValidationError(f"{field_name} must be finite")
    return number


def _read_monotonic(clock: MonotonicClock | None) -> float:
    raw = time.monotonic() if clock is None else clock.monotonic()
    return _validate_finite_number(raw, field_name="monotonic clock reading")


def _validate_cancellation_reason(reason: object) -> str | None:
    if reason is None:
        return None
    if not isinstance(reason, str):
        raise TypeError(
            f"cancellation reason must be a string or None, got {type(reason).__name__}"
        )
    if not reason or reason != reason.strip():
        raise ExecutionContextValidationError(
            "cancellation reason must be non-empty and trimmed when provided"
        )
    if len(reason) > _MAX_CANCELLATION_REASON_LENGTH:
        raise ExecutionContextValidationError(
            f"cancellation reason must not exceed {_MAX_CANCELLATION_REASON_LENGTH} characters"
        )
    if any(character in reason for character in _REASON_CONTROL_CHARACTERS):
        raise ExecutionContextValidationError(
            "cancellation reason must not contain control characters"
        )
    return reason


class _CancellationState:
    """Shared mutable state hidden behind source/token ownership APIs."""

    __slots__ = ("_cancelled", "_lock", "_reason")

    def __init__(self) -> None:
        self._lock = Lock()
        self._cancelled = False
        self._reason: str | None = None

    def snapshot(self) -> tuple[bool, str | None]:
        with self._lock:
            return self._cancelled, self._reason

    def request(self, reason: object) -> bool:
        with self._lock:
            if self._cancelled:
                return False
            validated_reason = _validate_cancellation_reason(reason)
            # Reason is stored before the flag while holding the same lock, so
            # observers can never see cancelled=True with a partially written
            # reason. The state never transitions back to false.
            self._reason = validated_reason
            self._cancelled = True
            return True


class CancellationToken:
    """Read-only observer for a cooperative cancellation request.

    A token intentionally exposes no cancel/reset API. Cancellation ownership
    remains with its :class:`CancellationSource`; consumers can only observe the
    monotonic state and optional first-request reason.
    """

    __slots__ = ("__state",)

    __state: _CancellationState

    def __init__(self, state: _CancellationState) -> None:
        object.__setattr__(self, "_CancellationToken__state", state)

    @property
    def is_cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        cancelled, _reason = self.__state.snapshot()
        return cancelled

    @property
    def reason(self) -> str | None:
        """The bounded reason from the first cancellation request, if any."""
        _cancelled, reason = self.__state.snapshot()
        return reason

    def _snapshot(self) -> tuple[bool, str | None]:
        """Return an atomic internal snapshot for combined stop observation."""
        return self.__state.snapshot()

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("CancellationToken instances are read-only")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("CancellationToken instances are read-only")


class CancellationSource:
    """Owner/controller for a single cooperative cancellation signal.

    ``request_cancellation()`` performs the only public state transition. The
    first request wins, later requests are idempotent no-ops, and the shared
    token can never reset the state.
    """

    __slots__ = ("__state", "__token")

    __state: _CancellationState
    __token: CancellationToken

    def __init__(self) -> None:
        state = _CancellationState()
        object.__setattr__(self, "_CancellationSource__state", state)
        object.__setattr__(self, "_CancellationSource__token", CancellationToken(state))

    @property
    def token(self) -> CancellationToken:
        """Return the stable read-only token shared with execution consumers."""
        return self.__token

    def request_cancellation(self, reason: str | None = None) -> bool:
        """Request cancellation, returning ``True`` only for the first request.

        Cancellation is monotonic and idempotent. A later request never
        validates or replaces the first reason because the terminal state has
        already been established.
        """
        return self.__state.request(reason)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("CancellationSource ownership state cannot be replaced")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("CancellationSource ownership state cannot be replaced")


class DeadlineStatus(StrEnum):
    """Deterministic relation between current monotonic time and a deadline."""

    NO_DEADLINE = "no_deadline"
    PENDING = "pending"
    REACHED = "reached"
    EXCEEDED = "exceeded"


@dataclass(frozen=True, slots=True)
class Deadline:
    """Absolute deadline in monotonic-clock seconds.

    ``REACHED`` is the exact ``now == deadline`` boundary and already counts as
    expired. ``EXCEEDED`` means ``now > deadline``. This makes stop semantics
    explicitly ``now >= deadline`` without losing boundary observability.
    """

    monotonic_at: float

    def __post_init__(self) -> None:
        normalized = _validate_finite_number(self.monotonic_at, field_name="deadline.monotonic_at")
        object.__setattr__(self, "monotonic_at", normalized)

    @classmethod
    def after(
        cls,
        timeout_seconds: float,
        *,
        clock: MonotonicClock | None = None,
    ) -> Deadline:
        """Create a deadline ``timeout_seconds`` from an observed monotonic now.

        Zero is valid and yields a deadline reached immediately at the same
        clock reading. Negative or non-finite timeout durations are rejected.
        """
        timeout = _validate_finite_number(timeout_seconds, field_name="timeout_seconds")
        if timeout < 0:
            raise ExecutionContextValidationError(
                "timeout_seconds must be greater than or equal to zero"
            )
        now = _read_monotonic(clock)
        deadline_at = _validate_finite_number(now + timeout, field_name="computed deadline")
        return cls(deadline_at)

    def status(self, *, clock: MonotonicClock | None = None) -> DeadlineStatus:
        """Observe this deadline against monotonic time without blocking."""
        now = _read_monotonic(clock)
        if now < self.monotonic_at:
            return DeadlineStatus.PENDING
        if now == self.monotonic_at:
            return DeadlineStatus.REACHED
        return DeadlineStatus.EXCEEDED

    def is_expired(self, *, clock: MonotonicClock | None = None) -> bool:
        """Return ``True`` exactly when monotonic ``now >= deadline``."""
        status = self.status(clock=clock)
        return status in (DeadlineStatus.REACHED, DeadlineStatus.EXCEEDED)


class ExecutionStopReason(StrEnum):
    """Independent reasons an execution context says future work should stop."""

    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class ExecutionStopStatus:
    """Immutable observation of cancellation and deadline state.

    Cancellation and timeout are represented independently. If both conditions
    are true, ``reasons`` contains both in deterministic cancellation-then-
    timeout order; timeout never mutates the cancellation token.
    """

    cancellation_requested: bool
    cancellation_reason: str | None
    deadline_status: DeadlineStatus

    def __post_init__(self) -> None:
        if not isinstance(self.cancellation_requested, bool):
            raise TypeError("cancellation_requested must be a bool")
        if not isinstance(self.deadline_status, DeadlineStatus):
            raise TypeError("deadline_status must be a DeadlineStatus")
        reason = _validate_cancellation_reason(self.cancellation_reason)
        if not self.cancellation_requested and reason is not None:
            raise ExecutionContextValidationError(
                "cancellation_reason requires cancellation_requested=True"
            )

    @property
    def timed_out(self) -> bool:
        """Whether the deadline condition requires stopping (``now >= deadline``)."""
        return self.deadline_status in (DeadlineStatus.REACHED, DeadlineStatus.EXCEEDED)

    @property
    def should_stop(self) -> bool:
        """Whether cooperative execution should reject/stop future work."""
        return self.cancellation_requested or self.timed_out

    @property
    def reasons(self) -> tuple[ExecutionStopReason, ...]:
        """Return all active stop reasons in deterministic order."""
        reasons: list[ExecutionStopReason] = []
        if self.cancellation_requested:
            reasons.append(ExecutionStopReason.CANCELLED)
        if self.timed_out:
            reasons.append(ExecutionStopReason.TIMEOUT)
        return tuple(reasons)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionContext:
    """Minimal non-authoritative context shared by future execution layers.

    The context intentionally has no metadata bag, callbacks, permissions,
    risk/budget overrides, capability handles, priority, or kernel bypass.
    It duplicates no Task data beyond the canonical optional ``TaskId``.
    """

    correlation_id: UUID
    cancellation_token: CancellationToken
    task_id: TaskId | None = None
    deadline: Deadline | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.correlation_id, UUID):
            raise TypeError(
                f"correlation_id must be a UUID, got {type(self.correlation_id).__name__}"
            )
        if self.correlation_id.int == 0:
            raise ExecutionContextValidationError("correlation_id must not be the nil UUID")
        if self.task_id is not None and not isinstance(self.task_id, TaskId):
            raise TypeError(f"task_id must be a TaskId or None, got {type(self.task_id).__name__}")
        if not isinstance(self.cancellation_token, CancellationToken):
            raise TypeError(
                "cancellation_token must be a CancellationToken, got "
                f"{type(self.cancellation_token).__name__}"
            )
        if self.deadline is not None and not isinstance(self.deadline, Deadline):
            raise TypeError(
                f"deadline must be a Deadline or None, got {type(self.deadline).__name__}"
            )

    def deadline_status(self, *, clock: MonotonicClock | None = None) -> DeadlineStatus:
        """Observe deadline state; absence is explicit as ``NO_DEADLINE``."""
        if self.deadline is None:
            return DeadlineStatus.NO_DEADLINE
        return self.deadline.status(clock=clock)

    def observe_stop(self, *, clock: MonotonicClock | None = None) -> ExecutionStopStatus:
        """Observe all cooperative stop conditions without blocking or mutating state."""
        cancelled, reason = self.cancellation_token._snapshot()
        return ExecutionStopStatus(
            cancellation_requested=cancelled,
            cancellation_reason=reason,
            deadline_status=self.deadline_status(clock=clock),
        )
