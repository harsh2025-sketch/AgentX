"""Canonical AgentX Task state machine (A1.06).

This module owns **Task status transitions only**. It is the single authority
for which ``TaskStatus -> TaskStatus`` moves are legal in AgentX v1, and for
what happens when a caller asks for an illegal one.

Canonical v1 transitions
------------------------

::

    PENDING   -> RUNNING
    PENDING   -> CANCELLED

    RUNNING   -> SUCCEEDED
    RUNNING   -> FAILED
    RUNNING   -> CANCELLED

Terminal states (no outgoing transitions)::

    SUCCEEDED  FAILED  CANCELLED

Every other combination — including every self-transition such as
``RUNNING -> RUNNING`` — is invalid. The table in
:data:`LEGAL_TASK_TRANSITIONS` is *exhaustive over the controlled vocabulary*:
a status that is not listed as a legal target is rejected, never inferred and
never defaulted to "allowed". Terminality is derived from that same table (a
status is terminal exactly when it has no outgoing transitions), so the matrix
has a single source of truth.

Contract shape
--------------

The API is a small set of pure, stateless functions. There is no state-machine
object to construct, no hidden mutation, and no module-level mutable state:

    - :func:`can_transition` — ask whether a move is legal (predicate).
    - :func:`validate_transition` — reject an illegal move by raising.
    - :func:`transition_error` — the structured :class:`AgentXError` for an
      illegal move, as a value.
    - :func:`transition_task` — derive the resulting Task (new object) or raise.
    - :func:`try_transition_task` — derive the resulting Task as a
      ``Result[Task, AgentXError]``, so expected failure crosses a subsystem
      boundary without an exception.
    - :func:`legal_transitions` / :func:`is_terminal` — read the contract.

Error semantics
---------------

An illegal transition is never ignored and never silently coerced: it either
raises :class:`InvalidTaskTransitionError` (a narrow
:class:`~agentx.core.errors.AgentXException` subclass) or comes back as
``Result.failure(...)`` carrying one structured
:class:`~agentx.core.errors.AgentXError` with code
:data:`TASK_TRANSITION_ERROR_CODE`. Following the A1.04 error policy, the
``Result`` form is the expected cross-boundary outcome and the exception form
is for callers that treat an illegal request as a violated contract. Because
the machine is deterministic, an illegal transition is reported as
``Retryability.NON_RETRYABLE``: repeating the identical request against the
identical state fails identically.

Immutability
------------

:func:`transition_task` and :func:`try_transition_task` return a **new**
:class:`~agentx.core.tasks.Task`. The input Task is never modified — ``Task``
is frozen — and every unrelated field (``task_id``, ``objective``,
``priority``, ``parent_task_id``, ``created_at``, ``metadata``) is carried over
unchanged. A transition happens only because a caller explicitly asked for it.

Not authority
-------------

Task state is data. It grants nothing: no permission, no capability, no kernel
trust, and no exemption from policy. ``TaskPriority`` is not an input to
transition legality, so a CRITICAL task is treated exactly like a LOW one. This
module deliberately does **not** import ``agentx.kernel``; lifecycle vocabulary
needs no authority boundary, and reaching for one here would only blur it.

Deliberate non-scope
--------------------

This module is not a Task Manager, TaskStore, executor, planner, scheduler,
retry engine, event publisher, or persistence adapter, and it implements no
cancellation tokens or timeouts (A1.07 owns execution context, cancellation,
and timeouts; Day 2 owns the Task Manager). It performs no I/O, no event
publication, and no storage. It reuses the A1.05 :class:`TaskStatus` vocabulary
and the A1.05 ``Task`` schema rather than restating either.

Owner: A1.06. Belongs to ``agentx.core``; imports only the standard library and
sibling ``agentx.core`` contracts, so ``agentx.core`` remains a dependency leaf
with respect to the other canonical subsystems.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from agentx.core.errors import AgentXError, AgentXException, ErrorCategory, Retryability
from agentx.core.result import Result
from agentx.core.tasks import Task, TaskStatus

__all__ = [
    "LEGAL_TASK_TRANSITIONS",
    "TASK_TRANSITION_ERROR_CODE",
    "TERMINAL_TASK_STATUSES",
    "InvalidTaskTransitionError",
    "can_transition",
    "is_terminal",
    "legal_transitions",
    "transition_error",
    "transition_task",
    "try_transition_task",
    "validate_transition",
]

#: Error code carried by every rejected transition.
TASK_TRANSITION_ERROR_CODE: Final[str] = "task.transition_invalid"

#: The canonical v1 transition matrix: every status in the controlled
#: vocabulary maps to the exact set of statuses it may move to. An empty set
#: means terminal. This mapping is the single source of truth for the state
#: machine and is immutable at runtime.
LEGAL_TASK_TRANSITIONS: Final[Mapping[TaskStatus, frozenset[TaskStatus]]] = MappingProxyType(
    {
        TaskStatus.PENDING: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
        TaskStatus.RUNNING: frozenset(
            {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
        ),
        TaskStatus.SUCCEEDED: frozenset(),
        TaskStatus.FAILED: frozenset(),
        TaskStatus.CANCELLED: frozenset(),
    }
)

#: Statuses with no outgoing transitions. Derived from
#: :data:`LEGAL_TASK_TRANSITIONS` so terminality can never drift from the
#: matrix.
TERMINAL_TASK_STATUSES: Final[frozenset[TaskStatus]] = frozenset(
    status for status in TaskStatus if not LEGAL_TASK_TRANSITIONS[status]
)


class InvalidTaskTransitionError(AgentXException):
    """Raised when a caller requests a transition the state machine forbids.

    This is the only exception type this module raises for an illegal
    transition. It is an :class:`~agentx.core.errors.AgentXException`, so the
    structured :class:`~agentx.core.errors.AgentXError` is always available as
    ``error``. Callers that would rather not raise should use
    :func:`try_transition_task`.
    """


def _validate_status(value: object, *, field_name: str) -> TaskStatus:
    """Reject anything that is not a member of the controlled status vocabulary.

    Raw strings are never coerced: ``"running"`` is a programming error, not a
    status. This mirrors the A1.05 Task schema's constructor behaviour.
    """
    if not isinstance(value, TaskStatus):
        raise TypeError(f"{field_name} must be a TaskStatus, got {type(value).__name__}")
    return value


def _validate_task(task: object) -> Task:
    """Reject anything that is not a canonical :class:`Task`."""
    if not isinstance(task, Task):
        raise TypeError(f"task must be a Task, got {type(task).__name__}")
    return task


def legal_transitions(status: TaskStatus) -> frozenset[TaskStatus]:
    """Return the statuses ``status`` may legally move to.

    An empty set means ``status`` is terminal. The returned set is a
    ``frozenset``, so callers cannot widen the contract.
    """
    return LEGAL_TASK_TRANSITIONS[_validate_status(status, field_name="status")]


def is_terminal(status: TaskStatus) -> bool:
    """Return True when ``status`` has no outgoing transitions."""
    return _validate_status(status, field_name="status") in TERMINAL_TASK_STATUSES


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    """Return True when ``current -> target`` is a legal v1 transition.

    Pure and deterministic: the answer depends only on the two arguments.
    Self-transitions are always False, transitions out of terminal states are
    always False, and no other factor (priority, identity, time, caller) is
    consulted.
    """
    validated_current = _validate_status(current, field_name="current")
    validated_target = _validate_status(target, field_name="target")
    return validated_target in LEGAL_TASK_TRANSITIONS[validated_current]


def transition_error(current: TaskStatus, target: TaskStatus) -> AgentXError:
    """Return the structured error describing the rejected ``current -> target``.

    The error is a value, not an exception, so it can be returned inside a
    ``Result``, logged, or wrapped by :func:`validate_transition`. It is
    deterministic: the same status pair always produces an equal error.
    """
    validated_current = _validate_status(current, field_name="current")
    validated_target = _validate_status(target, field_name="target")
    return AgentXError(
        code=TASK_TRANSITION_ERROR_CODE,
        message=(
            f"task status transition {validated_current.value} -> "
            f"{validated_target.value} is not allowed"
        ),
        category=ErrorCategory.CONFLICT,
        retryability=Retryability.NON_RETRYABLE,
        details={
            "current_status": validated_current.value,
            "target_status": validated_target.value,
            "allowed_targets": sorted(
                status.value for status in legal_transitions(validated_current)
            ),
        },
    )


def validate_transition(current: TaskStatus, target: TaskStatus) -> None:
    """Return None when ``current -> target`` is legal; otherwise raise.

    Raises:
        InvalidTaskTransitionError: if the transition is not in the canonical
            matrix. The Task is never touched by this function.
        TypeError: if either argument is not a :class:`TaskStatus`.
    """
    if not can_transition(current, target):
        raise InvalidTaskTransitionError(transition_error(current, target))


def transition_task(task: Task, target: TaskStatus) -> Task:
    """Return a new Task in ``target`` status, or raise if the move is illegal.

    The input Task is returned unchanged and untouched; only ``status`` differs
    on the result, and every other field is carried over exactly.

    Raises:
        InvalidTaskTransitionError: if ``task.status -> target`` is not legal.
        TypeError: if ``task`` is not a Task or ``target`` is not a TaskStatus.
    """
    validated_task = _validate_task(task)
    validated_target = _validate_status(target, field_name="target")
    validate_transition(validated_task.status, validated_target)
    return dataclasses.replace(validated_task, status=validated_target)


def try_transition_task(task: Task, target: TaskStatus) -> Result[Task, AgentXError]:
    """Return a new Task in ``target`` status, or a failure describing why not.

    This is the non-raising form intended for callers that cross a subsystem
    boundary, where an illegal transition request is an expected operational
    outcome rather than a violated contract.

    Raises:
        TypeError: if ``task`` is not a Task or ``target`` is not a TaskStatus.
            Those are programming errors and are never reported as a failure.
    """
    validated_task = _validate_task(task)
    validated_target = _validate_status(target, field_name="target")
    if not can_transition(validated_task.status, validated_target):
        return Result[Task, AgentXError].failure(
            transition_error(validated_task.status, validated_target)
        )
    return Result[Task, AgentXError].success(
        dataclasses.replace(validated_task, status=validated_target)
    )
