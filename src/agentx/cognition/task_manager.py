"""A2.06 runtime Task Manager built over canonical Task contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from threading import Lock
from types import MappingProxyType
from typing import Final

from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext, ExecutionStopStatus, MonotonicClock
from agentx.core.ids import TaskId
from agentx.core.result import Result
from agentx.core.task_state import try_transition_task
from agentx.core.tasks import Task, TaskPriority, TaskStatus

__all__ = [
    "TaskAlreadyRegisteredError",
    "TaskManager",
    "TaskManagerError",
    "TaskNotFoundError",
    "TaskRegistrationStateError",
]

_TASK_NOT_FOUND_CODE: Final[str] = "task_manager.task_not_found"
_CONTEXT_TASK_MISSING_CODE: Final[str] = "task_manager.context_task_missing"


class TaskManagerError(Exception):
    """Base exception for programming/registration contract violations."""


class TaskAlreadyRegisteredError(TaskManagerError):
    """Raised when a canonical TaskId is already registered."""

    def __init__(self, task_id: TaskId) -> None:
        self.task_id = task_id
        super().__init__(f"Task {task_id} is already registered")


class TaskNotFoundError(TaskManagerError):
    """Raised by TaskManager.require when a task is absent."""

    def __init__(self, task_id: TaskId) -> None:
        self.task_id = task_id
        super().__init__(f"Task {task_id} is not registered")


class TaskRegistrationStateError(TaskManagerError):
    """Raised when registration would bypass fresh-runtime lifecycle ownership."""

    def __init__(self, task: Task) -> None:
        self.task_id = task.task_id
        self.status = task.status
        super().__init__(
            f"Task {task.task_id} must be PENDING when first registered; got {task.status.value}"
        )


def _require_task_id(value: object) -> TaskId:
    if not isinstance(value, TaskId):
        raise TypeError(f"task_id must be a TaskId, got {type(value).__name__}")
    return value


def _require_task(value: object) -> Task:
    if not isinstance(value, Task):
        raise TypeError(f"task must be a Task, got {type(value).__name__}")
    return value


def _require_status(value: object) -> TaskStatus:
    if not isinstance(value, TaskStatus):
        raise TypeError(f"target must be a TaskStatus, got {type(value).__name__}")
    return value


def _require_context(value: object) -> ExecutionContext:
    if not isinstance(value, ExecutionContext):
        raise TypeError(f"context must be an ExecutionContext, got {type(value).__name__}")
    return value


def _task_sort_key(task: Task) -> int:
    return task.task_id.value.int


def _not_found_error(task_id: TaskId) -> AgentXError:
    return AgentXError(
        code=_TASK_NOT_FOUND_CODE,
        message=f"task {task_id} is not registered",
        category=ErrorCategory.NOT_FOUND,
        retryability=Retryability.NON_RETRYABLE,
        details={"task_id": task_id.to_str()},
    )


def _context_task_missing_error() -> AgentXError:
    return AgentXError(
        code=_CONTEXT_TASK_MISSING_CODE,
        message="execution context must carry a TaskId for Task Manager observation",
        category=ErrorCategory.PRECONDITION,
        retryability=Retryability.NON_RETRYABLE,
    )


class TaskManager:
    """Thread-safe in-memory runtime bookkeeping for canonical Tasks.

    Managed Tasks enter this fresh-runtime manager in PENDING state. Every
    later state update is derived exclusively through the canonical A1.06
    transition API. A1.07 cancellation/deadline observation stays read-only and
    does not implicitly change Task state.
    """

    __slots__ = ("_lock", "_tasks")

    def __init__(self) -> None:
        self._lock = Lock()
        self._tasks: dict[TaskId, Task] = {}

    def create(
        self,
        objective: str,
        *,
        task_id: TaskId | None = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        parent_task_id: TaskId | None = None,
        created_at: datetime | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Task:
        """Create and atomically register one fresh PENDING Task."""
        task = Task.create(
            objective,
            task_id=task_id,
            status=TaskStatus.PENDING,
            priority=priority,
            parent_task_id=parent_task_id,
            created_at=created_at,
            metadata=metadata,
        )
        self.register(task)
        return task

    def register(self, task: Task) -> TaskId:
        """Register an existing canonical PENDING Task without replacement."""
        validated = _require_task(task)
        if validated.status is not TaskStatus.PENDING:
            raise TaskRegistrationStateError(validated)
        with self._lock:
            if validated.task_id in self._tasks:
                raise TaskAlreadyRegisteredError(validated.task_id)
            self._tasks[validated.task_id] = validated
        return validated.task_id

    def get(self, task_id: TaskId) -> Task | None:
        """Return the immutable Task snapshot for task_id, or None."""
        key = _require_task_id(task_id)
        with self._lock:
            return self._tasks.get(key)

    def require(self, task_id: TaskId) -> Task:
        """Return a registered Task or raise TaskNotFoundError."""
        key = _require_task_id(task_id)
        task = self.get(key)
        if task is None:
            raise TaskNotFoundError(key)
        return task

    def list(self) -> tuple[Task, ...]:
        """Return current Task snapshots in deterministic TaskId order."""
        with self._lock:
            values = tuple(self._tasks.values())
        return tuple(sorted(values, key=_task_sort_key))

    def snapshot(self) -> Mapping[TaskId, Task]:
        """Return a read-only deterministic mapping over a private state copy."""
        with self._lock:
            items = tuple(self._tasks.items())
        ordered = sorted(items, key=lambda item: item[0].value.int)
        return MappingProxyType(dict(ordered))

    def transition(
        self,
        task_id: TaskId,
        target: TaskStatus,
    ) -> Result[Task, AgentXError]:
        """Atomically request one canonical A1.06 Task transition."""
        key = _require_task_id(task_id)
        validated_target = _require_status(target)
        with self._lock:
            current = self._tasks.get(key)
            if current is None:
                return Result[Task, AgentXError].failure(_not_found_error(key))
            outcome = try_transition_task(current, validated_target)
            if outcome.is_failure:
                return Result[Task, AgentXError].failure(outcome.unwrap_error())
            updated = outcome.unwrap()
            self._tasks[key] = updated
            return Result[Task, AgentXError].success(updated)

    def observe_stop(
        self,
        context: ExecutionContext,
        *,
        clock: MonotonicClock | None = None,
    ) -> Result[ExecutionStopStatus, AgentXError]:
        """Observe A1.07 stop state without mutating Task lifecycle."""
        validated = _require_context(context)
        task_id = validated.task_id
        if task_id is None:
            return Result[ExecutionStopStatus, AgentXError].failure(_context_task_missing_error())
        with self._lock:
            if task_id not in self._tasks:
                return Result[ExecutionStopStatus, AgentXError].failure(_not_found_error(task_id))
        return Result[ExecutionStopStatus, AgentXError].success(validated.observe_stop(clock=clock))

    def __contains__(self, task_id: object) -> bool:
        if not isinstance(task_id, TaskId):
            return False
        with self._lock:
            return task_id in self._tasks

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)

    def __repr__(self) -> str:
        return f"TaskManager(registered={len(self)})"
