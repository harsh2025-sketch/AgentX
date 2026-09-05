"""Unit tests for the A2.06 runtime Task Manager."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from uuid import UUID

import pytest

from agentx.cognition.task_manager import (
    TaskAlreadyRegisteredError,
    TaskManager,
    TaskNotFoundError,
    TaskRegistrationStateError,
)
from agentx.core.errors import ErrorCategory, Retryability
from agentx.core.execution import CancellationSource, Deadline, ExecutionContext
from agentx.core.ids import TaskId
from agentx.core.task_state import TASK_TRANSITION_ERROR_CODE
from agentx.core.tasks import Task, TaskPriority, TaskStatus

_ID_LOW = TaskId(UUID("00000000-0000-4000-8000-000000000001"))
_ID_MID = TaskId(UUID("00000000-0000-4000-8000-000000000010"))
_ID_HIGH = TaskId(UUID("00000000-0000-4000-8000-000000000100"))
_CORRELATION_ID = UUID("11111111-1111-4111-8111-111111111111")


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


def _context(
    task_id: TaskId | None,
    *,
    source: CancellationSource | None = None,
    deadline: Deadline | None = None,
) -> ExecutionContext:
    cancellation = source or CancellationSource()
    return ExecutionContext(
        correlation_id=_CORRELATION_ID,
        cancellation_token=cancellation.token,
        task_id=task_id,
        deadline=deadline,
    )


def test_create_registers_canonical_pending_task() -> None:
    manager = TaskManager()
    task = manager.create("Index local notes")
    assert isinstance(task.task_id, TaskId)
    assert task.status is TaskStatus.PENDING
    assert manager.get(task.task_id) is task
    assert len(manager) == 1


def test_create_preserves_explicit_task_fields() -> None:
    manager = TaskManager()
    created_at = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    parent = TaskId.create()
    task = manager.create(
        "Prepare deterministic summary",
        task_id=_ID_MID,
        priority=TaskPriority.HIGH,
        parent_task_id=parent,
        created_at=created_at,
        metadata={"source": "local"},
    )
    assert task.task_id == _ID_MID
    assert task.priority is TaskPriority.HIGH
    assert task.parent_task_id == parent
    assert task.created_at == created_at
    assert task.metadata["source"] == "local"


def test_register_returns_exact_task_id() -> None:
    manager = TaskManager()
    task = Task.create("Register me", task_id=_ID_LOW)
    registered = manager.register(task)
    assert registered is _ID_LOW
    assert manager.require(_ID_LOW) is task


def test_duplicate_identity_is_rejected_without_replacement() -> None:
    manager = TaskManager()
    incumbent = Task.create("First", task_id=_ID_LOW)
    replacement = Task.create("Second", task_id=_ID_LOW)
    manager.register(incumbent)
    with pytest.raises(TaskAlreadyRegisteredError) as exc_info:
        manager.register(replacement)
    assert exc_info.value.task_id == _ID_LOW
    assert manager.require(_ID_LOW) is incumbent
    assert len(manager) == 1


def test_create_with_duplicate_explicit_identity_is_rejected() -> None:
    manager = TaskManager()
    manager.create("First", task_id=_ID_LOW)
    with pytest.raises(TaskAlreadyRegisteredError):
        manager.create("Second", task_id=_ID_LOW)
    assert manager.require(_ID_LOW).objective == "First"


@pytest.mark.parametrize(
    "status",
    [TaskStatus.RUNNING, TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED],
)
def test_register_rejects_non_pending_snapshots(status: TaskStatus) -> None:
    manager = TaskManager()
    task = Task.create("Already advanced", status=status)
    with pytest.raises(TaskRegistrationStateError) as exc_info:
        manager.register(task)
    assert exc_info.value.status is status
    assert len(manager) == 0


def test_register_rejects_non_task() -> None:
    manager = TaskManager()
    with pytest.raises(TypeError, match="task must be a Task"):
        manager.register(object())  # type: ignore[arg-type]


def test_get_absence_is_explicit() -> None:
    assert TaskManager().get(_ID_LOW) is None


def test_require_absence_raises_specific_error() -> None:
    manager = TaskManager()
    with pytest.raises(TaskNotFoundError) as exc_info:
        manager.require(_ID_LOW)
    assert exc_info.value.task_id == _ID_LOW


def test_get_and_require_reject_non_task_id() -> None:
    manager = TaskManager()
    with pytest.raises(TypeError, match="task_id must be a TaskId"):
        manager.get("not-an-id")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="task_id must be a TaskId"):
        manager.require(UUID(int=1))  # type: ignore[arg-type]


def test_list_is_deterministic_by_task_id_not_insertion_order() -> None:
    manager = TaskManager()
    manager.create("High", task_id=_ID_HIGH)
    manager.create("Low", task_id=_ID_LOW)
    manager.create("Mid", task_id=_ID_MID)
    assert tuple(task.task_id for task in manager.list()) == (_ID_LOW, _ID_MID, _ID_HIGH)


def test_list_returns_immutable_tuple() -> None:
    manager = TaskManager()
    manager.create("One")
    assert isinstance(manager.list(), tuple)


def test_snapshot_is_read_only_and_deterministic() -> None:
    manager = TaskManager()
    manager.create("High", task_id=_ID_HIGH)
    manager.create("Low", task_id=_ID_LOW)
    snapshot = manager.snapshot()
    assert isinstance(snapshot, MappingProxyType)
    assert tuple(snapshot) == (_ID_LOW, _ID_HIGH)
    mutable: Any = snapshot
    with pytest.raises(TypeError):
        mutable[_ID_MID] = Task.create("No")


def test_snapshot_is_stable_after_later_manager_changes() -> None:
    manager = TaskManager()
    first = manager.create("First", task_id=_ID_LOW)
    snapshot = manager.snapshot()
    manager.create("Second", task_id=_ID_HIGH)
    assert tuple(snapshot) == (_ID_LOW,)
    assert snapshot[_ID_LOW] is first


def test_task_snapshots_are_canonical_immutable_tasks() -> None:
    manager = TaskManager()
    task = manager.create("Immutable")
    returned = manager.require(task.task_id)
    mutable: Any = returned
    with pytest.raises(FrozenInstanceError):
        mutable.status = TaskStatus.RUNNING
    assert manager.require(task.task_id).status is TaskStatus.PENDING


def test_pending_to_running_uses_canonical_transition() -> None:
    manager = TaskManager()
    task = manager.create("Run")
    result = manager.transition(task.task_id, TaskStatus.RUNNING)
    assert result.is_success
    updated = result.unwrap()
    assert updated.status is TaskStatus.RUNNING
    assert updated.task_id == task.task_id
    assert manager.require(task.task_id) is updated
    assert task.status is TaskStatus.PENDING


def test_running_to_succeeded_is_allowed_only_after_running() -> None:
    manager = TaskManager()
    task = manager.create("Complete")
    premature = manager.transition(task.task_id, TaskStatus.SUCCEEDED)
    assert premature.is_failure
    assert premature.unwrap_error().code == TASK_TRANSITION_ERROR_CODE
    assert manager.require(task.task_id).status is TaskStatus.PENDING
    assert manager.transition(task.task_id, TaskStatus.RUNNING).is_success
    completed = manager.transition(task.task_id, TaskStatus.SUCCEEDED)
    assert completed.is_success
    assert completed.unwrap().status is TaskStatus.SUCCEEDED


def test_illegal_transition_preserves_canonical_error_and_state() -> None:
    manager = TaskManager()
    task = manager.create("No shortcut")
    result = manager.transition(task.task_id, TaskStatus.FAILED)
    assert result.is_failure
    error = result.unwrap_error()
    assert error.code == TASK_TRANSITION_ERROR_CODE
    assert error.category is ErrorCategory.CONFLICT
    assert error.retryability is Retryability.NON_RETRYABLE
    assert error.details["current_status"] == TaskStatus.PENDING.value
    assert error.details["target_status"] == TaskStatus.FAILED.value
    assert manager.require(task.task_id).status is TaskStatus.PENDING


def test_terminal_state_rejects_further_transition() -> None:
    manager = TaskManager()
    task = manager.create("Terminal")
    manager.transition(task.task_id, TaskStatus.RUNNING)
    manager.transition(task.task_id, TaskStatus.FAILED)
    result = manager.transition(task.task_id, TaskStatus.RUNNING)
    assert result.is_failure
    assert result.unwrap_error().code == TASK_TRANSITION_ERROR_CODE
    assert manager.require(task.task_id).status is TaskStatus.FAILED


def test_transition_rejects_raw_status_string() -> None:
    manager = TaskManager()
    task = manager.create("Typed status only")
    with pytest.raises(TypeError, match="target must be a TaskStatus"):
        manager.transition(task.task_id, "succeeded")  # type: ignore[arg-type]
    assert manager.require(task.task_id).status is TaskStatus.PENDING


def test_transition_unknown_task_returns_structured_not_found() -> None:
    result = TaskManager().transition(_ID_LOW, TaskStatus.RUNNING)
    assert result.is_failure
    error = result.unwrap_error()
    assert error.code == "task_manager.task_not_found"
    assert error.category is ErrorCategory.NOT_FOUND
    assert error.retryability is Retryability.NON_RETRYABLE
    assert error.details["task_id"] == _ID_LOW.to_str()


def test_observe_stop_requires_context_task_identity() -> None:
    result = TaskManager().observe_stop(_context(None))
    assert result.is_failure
    assert result.unwrap_error().code == "task_manager.context_task_missing"


def test_observe_stop_requires_registered_context_task() -> None:
    result = TaskManager().observe_stop(_context(_ID_LOW))
    assert result.is_failure
    assert result.unwrap_error().code == "task_manager.task_not_found"


def test_observe_stop_rejects_non_execution_context() -> None:
    with pytest.raises(TypeError, match="context must be an ExecutionContext"):
        TaskManager().observe_stop(object())  # type: ignore[arg-type]


def test_cancellation_observation_is_canonical_and_read_only() -> None:
    manager = TaskManager()
    task = manager.create("Observe cancellation")
    source = CancellationSource()
    context = _context(task.task_id, source=source)
    source.request_cancellation("user cancelled")
    stop = manager.observe_stop(context).unwrap()
    assert stop.cancellation_requested
    assert stop.cancellation_reason == "user cancelled"
    assert stop.should_stop
    assert manager.require(task.task_id).status is TaskStatus.PENDING


def test_timeout_observation_does_not_invent_task_transition() -> None:
    manager = TaskManager()
    task = manager.create("Observe deadline")
    context = _context(task.task_id, deadline=Deadline(10.0))
    stop = manager.observe_stop(context, clock=FakeClock(10.0)).unwrap()
    assert stop.timed_out
    assert manager.require(task.task_id).status is TaskStatus.PENDING


def test_explicit_cancel_after_stop_observation_uses_state_machine() -> None:
    manager = TaskManager()
    task = manager.create("Cancel explicitly")
    source = CancellationSource()
    context = _context(task.task_id, source=source)
    source.request_cancellation()
    assert manager.observe_stop(context).unwrap().cancellation_requested
    cancelled = manager.transition(task.task_id, TaskStatus.CANCELLED)
    assert cancelled.is_success
    assert cancelled.unwrap().status is TaskStatus.CANCELLED


def test_execution_correlation_is_not_rewritten_or_stored_on_task() -> None:
    manager = TaskManager()
    task = manager.create("Correlation")
    context = _context(task.task_id)
    assert manager.observe_stop(context).is_success
    assert context.correlation_id == _CORRELATION_ID
    assert not hasattr(manager.require(task.task_id), "correlation_id")


def test_contains_len_and_repr_are_instance_local() -> None:
    first = TaskManager()
    second = TaskManager()
    task = first.create("Only first")
    assert task.task_id in first
    assert task.task_id not in second
    assert "registered=1" in repr(first)
    assert "registered=0" in repr(second)


def test_concurrent_duplicate_registration_has_exactly_one_winner() -> None:
    manager = TaskManager()
    task = Task.create("Concurrent registration", task_id=_ID_LOW)

    def attempt() -> bool:
        try:
            manager.register(task)
        except TaskAlreadyRegisteredError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = tuple(pool.map(lambda _index: attempt(), range(32)))
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 31
    assert len(manager) == 1


def test_concurrent_same_transition_has_exactly_one_success() -> None:
    manager = TaskManager()
    task = manager.create("Concurrent transition", task_id=_ID_LOW)

    def attempt() -> bool:
        return manager.transition(task.task_id, TaskStatus.RUNNING).is_success

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = tuple(pool.map(lambda _index: attempt(), range(32)))
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 31
    assert manager.require(task.task_id).status is TaskStatus.RUNNING
