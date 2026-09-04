"""A1.07 execution context, cooperative cancellation, and deadline contracts."""

from __future__ import annotations

from dataclasses import fields
from threading import Barrier, Lock, Thread
from typing import Any
from uuid import UUID, uuid4

import pytest

from agentx.core.execution import (
    CancellationSource,
    CancellationToken,
    Deadline,
    DeadlineStatus,
    ExecutionContext,
    ExecutionContextValidationError,
    ExecutionStopReason,
)
from agentx.core.ids import TaskId
from agentx.core.tasks import Task, TaskPriority


class FakeMonotonicClock:
    """Mutable deterministic monotonic clock; tests advance it without sleeping."""

    def __init__(self, value: float) -> None:
        self.value = value

    def monotonic(self) -> float:
        return self.value


class FailingClock:
    """Clock proving no-deadline observation does not read time at all."""

    def monotonic(self) -> float:
        raise AssertionError("clock must not be read when there is no deadline")


def _context(
    *,
    source: CancellationSource,
    deadline: Deadline | None = None,
    task_id: TaskId | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=source.token,
        task_id=task_id,
        deadline=deadline,
    )


def _token_snapshot(token: CancellationToken) -> tuple[bool, str | None]:
    return token.is_cancelled, token.reason


def test_execution_context_constructs_from_canonical_identity_and_contracts() -> None:
    source = CancellationSource()
    task_id = TaskId(uuid4())
    correlation_id = uuid4()
    deadline = Deadline(42.0)

    context = ExecutionContext(
        correlation_id=correlation_id,
        cancellation_token=source.token,
        task_id=task_id,
        deadline=deadline,
    )

    assert context.task_id == task_id
    assert context.correlation_id == correlation_id
    assert context.cancellation_token is source.token
    assert context.deadline == deadline


def test_execution_context_rejects_malformed_inputs() -> None:
    source = CancellationSource()
    bad_correlation: Any = "not-a-uuid"
    bad_task_id: Any = str(uuid4())
    bad_token: Any = object()
    bad_deadline: Any = "soon"

    with pytest.raises(TypeError, match="correlation_id"):
        ExecutionContext(correlation_id=bad_correlation, cancellation_token=source.token)
    with pytest.raises(ExecutionContextValidationError, match="nil UUID"):
        ExecutionContext(correlation_id=UUID(int=0), cancellation_token=source.token)
    with pytest.raises(TypeError, match="task_id"):
        ExecutionContext(
            correlation_id=uuid4(), cancellation_token=source.token, task_id=bad_task_id
        )
    with pytest.raises(TypeError, match="cancellation_token"):
        ExecutionContext(correlation_id=uuid4(), cancellation_token=bad_token)
    with pytest.raises(TypeError, match="deadline"):
        ExecutionContext(
            correlation_id=uuid4(), cancellation_token=source.token, deadline=bad_deadline
        )


def test_cancellation_is_initially_false_then_visible_to_shared_token() -> None:
    source = CancellationSource()
    token = source.token

    assert _token_snapshot(token) == (False, None)
    assert source.request_cancellation("operator requested stop") is True
    assert _token_snapshot(token) == (True, "operator requested stop")


def test_cancellation_is_monotonic_idempotent_and_first_reason_wins() -> None:
    source = CancellationSource()

    assert source.request_cancellation("first reason") is True
    assert source.request_cancellation("second reason") is False
    assert source.request_cancellation() is False
    assert source.token.is_cancelled is True
    assert source.token.reason == "first reason"


def test_repeated_cancellation_cannot_disturb_terminal_state_with_bad_reason() -> None:
    source = CancellationSource()
    malformed_later_reason: Any = "x" * 257

    assert source.request_cancellation("terminal reason") is True
    assert source.request_cancellation(malformed_later_reason) is False
    assert _token_snapshot(source.token) == (True, "terminal reason")


def test_cancellation_reason_is_bounded_and_validated() -> None:
    source = CancellationSource()
    bad_reason: Any = 123

    with pytest.raises(TypeError, match="cancellation reason"):
        source.request_cancellation(bad_reason)
    with pytest.raises(ExecutionContextValidationError, match="non-empty and trimmed"):
        source.request_cancellation(" padded ")
    with pytest.raises(ExecutionContextValidationError, match="must not exceed"):
        source.request_cancellation("x" * 257)
    with pytest.raises(ExecutionContextValidationError, match="control characters"):
        source.request_cancellation("line\nbreak")

    assert source.token.is_cancelled is False


def test_read_only_token_has_no_cancellation_or_reset_authority() -> None:
    source = CancellationSource()
    token = source.token
    source.request_cancellation("stop")
    public_state_attribute = "is_cancelled"
    private_state_attribute = "_CancellationToken__state"

    assert not hasattr(token, "request_cancellation")
    assert not hasattr(token, "reset")
    with pytest.raises(AttributeError, match="read-only"):
        setattr(token, public_state_attribute, False)
    with pytest.raises(AttributeError, match="read-only"):
        setattr(token, private_state_attribute, object())

    assert token.is_cancelled is True
    assert token.reason == "stop"


def test_concurrent_request_and_observation_are_safe_and_not_torn() -> None:
    source = CancellationSource()
    token = source.token
    worker_count = 12
    barrier = Barrier(worker_count)
    collection_lock = Lock()
    pre_observations: list[tuple[bool, str | None]] = []
    post_observations: list[tuple[bool, str | None]] = []
    request_results: list[bool] = []

    def worker(index: int) -> None:
        barrier.wait()
        if index == 0:
            result = source.request_cancellation("concurrent stop")
            with collection_lock:
                request_results.append(result)
        else:
            observation = token._snapshot()
            with collection_lock:
                pre_observations.append(observation)
        barrier.wait()
        observation = token._snapshot()
        with collection_lock:
            post_observations.append(observation)

    threads = [Thread(target=worker, args=(index,)) for index in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert request_results == [True]
    assert len(pre_observations) == worker_count - 1
    assert all(
        observation in ((False, None), (True, "concurrent stop"))
        for observation in pre_observations
    )
    assert post_observations == [(True, "concurrent stop")] * worker_count


def test_no_deadline_is_explicit_and_does_not_read_clock() -> None:
    source = CancellationSource()
    context = _context(source=source)
    clock = FailingClock()

    assert context.deadline is None
    assert context.deadline_status(clock=clock) is DeadlineStatus.NO_DEADLINE
    status = context.observe_stop(clock=clock)
    assert status.deadline_status is DeadlineStatus.NO_DEADLINE
    assert status.timed_out is False
    assert status.should_stop is False
    assert status.reasons == ()


def test_deadline_before_exact_and_after_boundary_are_distinct() -> None:
    deadline = Deadline(10.0)
    clock = FakeMonotonicClock(9.5)

    assert deadline.status(clock=clock) is DeadlineStatus.PENDING
    assert deadline.is_expired(clock=clock) is False

    clock.value = 10.0
    assert deadline.status(clock=clock) is DeadlineStatus.REACHED
    assert deadline.is_expired(clock=clock) is True

    clock.value = 10.5
    assert deadline.status(clock=clock) is DeadlineStatus.EXCEEDED
    assert deadline.is_expired(clock=clock) is True


def test_timeout_construction_uses_injected_monotonic_clock_without_sleep() -> None:
    clock = FakeMonotonicClock(100.0)
    deadline = Deadline.after(2.5, clock=clock)

    assert deadline.monotonic_at == 102.5
    assert deadline.status(clock=clock) is DeadlineStatus.PENDING

    clock.value = 102.5
    assert deadline.status(clock=clock) is DeadlineStatus.REACHED

    immediate = Deadline.after(0.0, clock=clock)
    assert immediate.status(clock=clock) is DeadlineStatus.REACHED


def test_deadline_rejects_malformed_values_and_timeout_durations() -> None:
    for invalid in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ExecutionContextValidationError, match="finite"):
            Deadline(invalid)

    clock = FakeMonotonicClock(1.0)
    with pytest.raises(ExecutionContextValidationError, match="greater than or equal to zero"):
        Deadline.after(-0.001, clock=clock)
    with pytest.raises(ExecutionContextValidationError, match="finite"):
        Deadline.after(float("inf"), clock=clock)

    bad_timeout: Any = "one second"
    with pytest.raises(TypeError, match="timeout_seconds"):
        Deadline.after(bad_timeout, clock=clock)


def test_deadline_rejects_non_finite_injected_clock_reading() -> None:
    clock = FakeMonotonicClock(float("nan"))

    with pytest.raises(ExecutionContextValidationError, match="monotonic clock reading"):
        Deadline.after(1.0, clock=clock)


def test_cancellation_and_timeout_remain_independent_stop_reasons() -> None:
    source = CancellationSource()
    clock = FakeMonotonicClock(5.0)
    context = _context(source=source, deadline=Deadline(5.0))

    timeout_only = context.observe_stop(clock=clock)
    assert timeout_only.cancellation_requested is False
    assert timeout_only.timed_out is True
    assert timeout_only.reasons == (ExecutionStopReason.TIMEOUT,)
    assert source.token.is_cancelled is False

    clock.value = 4.0
    source.request_cancellation("explicit cancellation")
    cancellation_only = context.observe_stop(clock=clock)
    assert cancellation_only.cancellation_requested is True
    assert cancellation_only.cancellation_reason == "explicit cancellation"
    assert cancellation_only.timed_out is False
    assert cancellation_only.reasons == (ExecutionStopReason.CANCELLED,)

    clock.value = 5.0
    both = context.observe_stop(clock=clock)
    assert both.should_stop is True
    assert both.reasons == (ExecutionStopReason.CANCELLED, ExecutionStopReason.TIMEOUT)
    assert source.token.reason == "explicit cancellation"


def test_task_priority_cannot_change_context_stop_semantics() -> None:
    task_id = TaskId(uuid4())
    low = Task(task_id=task_id, objective="low priority work", priority=TaskPriority.LOW)
    critical = Task(
        task_id=task_id,
        objective="critical priority work",
        priority=TaskPriority.CRITICAL,
    )
    source = CancellationSource()
    clock = FakeMonotonicClock(7.0)
    context = _context(source=source, task_id=task_id, deadline=Deadline(8.0))

    assert low.task_id == critical.task_id == context.task_id
    assert low.priority is TaskPriority.LOW
    assert critical.priority is TaskPriority.CRITICAL
    assert "priority" not in {field.name for field in fields(ExecutionContext)}
    assert context.observe_stop(clock=clock).should_stop is False

    source.request_cancellation("stop independent of priority")
    assert context.observe_stop(clock=clock).reasons == (ExecutionStopReason.CANCELLED,)


def test_execution_context_has_no_authority_or_metadata_channel() -> None:
    source = CancellationSource()
    context = _context(source=source)
    field_names = {field.name for field in fields(ExecutionContext)}

    assert field_names == {"correlation_id", "cancellation_token", "task_id", "deadline"}
    for forbidden in (
        "admin",
        "superuser",
        "bypass",
        "permission",
        "permissions",
        "risk_override",
        "budget_override",
        "capability",
        "priority",
        "metadata",
        "callback",
    ):
        assert not hasattr(context, forbidden)
