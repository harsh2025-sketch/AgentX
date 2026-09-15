"""AX-465 bounded watcher registration/runtime coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from agentx.core.execution import CancellationToken
from agentx.core.knowledge import ProvenanceKind, ProvenanceReference
from agentx.infrastructure.event_watcher import (
    EventWatcherFramework,
    WatcherLifecycle,
    WatcherObservation,
    WatcherRegistration,
    WatcherSourceId,
)

_T0 = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
_PROVENANCE = ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="watcher.test")


class _Source:
    def __init__(
        self,
        source_id: WatcherSourceId,
        batches: list[tuple[WatcherObservation, ...]],
        *,
        fail: bool = False,
    ) -> None:
        self._source_id = source_id
        self._batches = list(batches)
        self._fail = fail
        self.last_token: CancellationToken | None = None

    @property
    def source_id(self) -> WatcherSourceId:
        return self._source_id

    def poll(
        self,
        *,
        cancellation: CancellationToken,
        max_items: int,
    ) -> tuple[WatcherObservation, ...]:
        self.last_token = cancellation
        if self._fail:
            raise RuntimeError("source failed")
        if not self._batches:
            return ()
        return self._batches.pop(0)


def _observation(source_id: WatcherSourceId, *, key: str, value: object) -> WatcherObservation:
    return WatcherObservation(
        source_id=source_id,
        event_key=key,
        observed_at=_T0,
        provenance=_PROVENANCE,
        payload={"value": value},
    )


def _registration(
    source_id: WatcherSourceId,
    *,
    dedupe: timedelta = timedelta(seconds=5),
    debounce: timedelta = timedelta(seconds=1),
) -> WatcherRegistration:
    return WatcherRegistration.create(
        source_id=source_id,
        correlation_id=uuid4(),
        task_id="task-1",
        max_batch=8,
        dedupe_window=dedupe,
        debounce_window=debounce,
    )


def test_registration_start_poll_preserves_provenance_and_task_correlation() -> None:
    source_id = WatcherSourceId("windows.events")
    registration = _registration(source_id)
    observation = _observation(source_id, key="window.changed", value="Notepad")
    source = _Source(source_id, [(observation,)])
    framework = EventWatcherFramework()
    framework.register(registration, source)
    assert framework.lifecycle(registration.watcher_id) is WatcherLifecycle.REGISTERED

    framework.start(registration.watcher_id)
    report = framework.poll_once(registration.watcher_id, polled_at=_T0)

    assert not report.failures
    assert len(report.emissions) == 1
    emission = report.emissions[0]
    assert emission.correlation_id == registration.correlation_id
    assert emission.task_id == "task-1"
    assert emission.observation.provenance == _PROVENANCE
    assert emission.observation.payload["value"] == "Notepad"


def test_duplicate_and_debounce_suppression_are_bounded_by_trusted_poll_time() -> None:
    source_id = WatcherSourceId("filesystem.events")
    registration = _registration(source_id)
    first = _observation(source_id, key="file:a", value=1)
    exact_duplicate = _observation(source_id, key="file:a", value=1)
    changed_same_key = _observation(source_id, key="file:a", value=2)
    source = _Source(source_id, [(first,), (exact_duplicate,), (changed_same_key,)])
    framework = EventWatcherFramework()
    framework.register(registration, source)
    framework.start(registration.watcher_id)

    assert len(framework.poll_once(registration.watcher_id, polled_at=_T0).emissions) == 1
    duplicate = framework.poll_once(
        registration.watcher_id,
        polled_at=_T0 + timedelta(milliseconds=100),
    )
    assert duplicate.duplicate_suppressed == 1
    changed = framework.poll_once(
        registration.watcher_id,
        polled_at=_T0 + timedelta(milliseconds=200),
    )
    assert changed.debounce_suppressed == 1


def test_poll_all_isolates_one_source_failure_from_other_watchers() -> None:
    good_id = WatcherSourceId("good")
    bad_id = WatcherSourceId("bad")
    good_registration = _registration(good_id)
    bad_registration = _registration(bad_id)
    framework = EventWatcherFramework()
    framework.register(
        good_registration,
        _Source(good_id, [(_observation(good_id, key="ok", value=True),)]),
    )
    framework.register(bad_registration, _Source(bad_id, [], fail=True))
    framework.start(good_registration.watcher_id)
    framework.start(bad_registration.watcher_id)

    report = framework.poll_all_once(polled_at=_T0)

    assert len(report.emissions) == 1
    assert len(report.failures) == 1
    assert report.failures[0].watcher_id == bad_registration.watcher_id
    assert report.failures[0].error_type == "RuntimeError"


def test_stop_cancels_source_and_shutdown_is_idempotent() -> None:
    source_id = WatcherSourceId("device.events")
    registration = _registration(source_id)
    source = _Source(source_id, [()])
    framework = EventWatcherFramework()
    framework.register(registration, source)
    framework.start(registration.watcher_id)
    framework.poll_once(registration.watcher_id, polled_at=_T0)
    assert source.last_token is not None and not source.last_token.is_cancelled

    framework.stop(registration.watcher_id, reason="test stop")
    assert framework.lifecycle(registration.watcher_id) is WatcherLifecycle.STOPPED
    assert source.last_token.is_cancelled
    assert source.last_token.reason == "test stop"

    framework.shutdown()
    framework.shutdown()
    assert framework.is_shutdown
