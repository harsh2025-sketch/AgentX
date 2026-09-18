"""M12 process/browser/device event-source behavior tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from agentx.capabilities.windows.process_discovery import (
    MetadataStatus,
    WindowsProcessDiscovery,
    WindowsProcessIdentity,
    WindowsProcessSnapshot,
)
from agentx.core.execution import CancellationSource
from agentx.core.knowledge import ProvenanceKind, ProvenanceReference
from agentx.core.result import Result
from agentx.event_triggers import (
    FileChangeWatcherSource,
    ProcessStateWatcherSource,
    browser_state_watcher,
    device_state_watcher,
)
from agentx.world_model import (
    BrowserPageState,
    DeviceState,
    ObservationMetadata,
    WorldAvailability,
    WorldEntityId,
    WorldEntityKind,
    WorldModel,
)

_T0 = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.current = _T0

    def now(self) -> datetime:
        return self.current


class _Discovery:
    def __init__(self, snapshots: tuple[WindowsProcessSnapshot, ...]) -> None:
        self._snapshots: Iterator[WindowsProcessSnapshot] = iter(snapshots)

    def discover(self) -> Result[WindowsProcessSnapshot, object]:
        return Result.success(next(self._snapshots))


def _process(process_id: int, name: str) -> WindowsProcessIdentity:
    return WindowsProcessIdentity(
        process_id=process_id,
        parent_process_id=None,
        executable_name=name,
        executable_name_status=MetadataStatus.AVAILABLE,
        executable_path=None,
        executable_path_status=MetadataStatus.UNSUPPORTED,
        window_handles=(),
        visible_window_count=0,
    )


def _snapshot(*processes: WindowsProcessIdentity) -> WindowsProcessSnapshot:
    return WindowsProcessSnapshot(
        processes=tuple(sorted(processes, key=lambda item: item.sort_key)),
        windows=(),
        dropped_invalid_entries=0,
        merged_duplicate_entries=0,
    )


def _metadata(observation_id: str, at: datetime) -> ObservationMetadata:
    return ObservationMetadata(
        observation_id=observation_id,
        source=ProvenanceReference(
            kind=ProvenanceKind.SYSTEM,
            reference="m12.trigger.test",
        ),
        observed_at=at,
        ttl=timedelta(minutes=5),
        environment_id="local",
    )


def test_process_state_trigger_reports_appeared_and_exited() -> None:
    clock = _Clock()
    fake = _Discovery(
        (
            _snapshot(),
            _snapshot(_process(42, "demo.exe")),
            _snapshot(),
        )
    )
    source = ProcessStateWatcherSource(
        cast(WindowsProcessDiscovery, fake),
        clock=clock,
    )
    cancellation = CancellationSource().token

    assert source.poll(cancellation=cancellation, max_items=8) == ()
    appeared = source.poll(cancellation=cancellation, max_items=8)
    exited = source.poll(cancellation=cancellation, max_items=8)

    assert len(appeared) == 1
    assert appeared[0].event_key == "process.appeared"
    assert appeared[0].payload["process_id"] == 42
    assert len(exited) == 1
    assert exited[0].event_key == "process.exited"


def test_browser_world_state_trigger_reports_navigation_change() -> None:
    clock = _Clock()
    model = WorldModel()
    session = WorldEntityId("local", WorldEntityKind.BROWSER_SESSION, "session-1")
    page = WorldEntityId("local", WorldEntityKind.BROWSER_PAGE, "page-1")
    model.cache.put(
        BrowserPageState(
            entity_id=page,
            session_id=session,
            target_id="target-1",
            title="Before",
            url="https://example.invalid/before",
            origin="https://example.invalid",
            document_version="1",
            dom_observation_ref=None,
            active=True,
            availability=WorldAvailability.AVAILABLE,
            metadata=_metadata("browser-before", _T0),
        )
    )
    source = browser_state_watcher(model, (page,))
    source._clock = clock
    cancellation = CancellationSource().token

    assert source.poll(cancellation=cancellation, max_items=8) == ()
    clock.current += timedelta(seconds=1)
    model.cache.put(
        BrowserPageState(
            entity_id=page,
            session_id=session,
            target_id="target-1",
            title="After",
            url="https://example.invalid/after",
            origin="https://example.invalid",
            document_version="2",
            dom_observation_ref=None,
            active=True,
            availability=WorldAvailability.AVAILABLE,
            metadata=_metadata("browser-after", clock.current),
        )
    )

    events = source.poll(cancellation=cancellation, max_items=8)
    assert len(events) == 1
    assert events[0].event_key == "browser.state.changed"
    assert "permission" not in events[0].payload


def test_device_world_state_trigger_reports_state_change() -> None:
    clock = _Clock()
    model = WorldModel()
    device = WorldEntityId("local", WorldEntityKind.DEVICE, "device-1")
    model.cache.put(
        DeviceState(
            entity_id=device,
            platform="windows",
            role="local_host",
            availability=WorldAvailability.AVAILABLE,
            capability_health=(),
            last_seen=_T0,
            metadata=_metadata("device-before", _T0),
        )
    )
    source = device_state_watcher(model, (device,))
    source._clock = clock
    cancellation = CancellationSource().token

    assert source.poll(cancellation=cancellation, max_items=8) == ()
    clock.current += timedelta(seconds=1)
    model.cache.put(
        DeviceState(
            entity_id=device,
            platform="windows",
            role="local_host",
            availability=WorldAvailability.UNAVAILABLE,
            capability_health=(),
            last_seen=_T0,
            metadata=_metadata("device-after", clock.current),
        )
    )

    events = source.poll(cancellation=cancellation, max_items=8)
    assert len(events) == 1
    assert events[0].event_key == "device.state.changed"


def test_process_state_trigger_reports_identity_change() -> None:
    fake = _Discovery(
        (
            _snapshot(_process(42, "before.exe")),
            _snapshot(_process(42, "after.exe")),
        )
    )
    source = ProcessStateWatcherSource(
        cast(WindowsProcessDiscovery, fake),
        clock=_Clock(),
    )
    cancellation = CancellationSource().token

    assert source.poll(cancellation=cancellation, max_items=8) == ()
    changed = source.poll(cancellation=cancellation, max_items=8)

    assert len(changed) == 1
    assert changed[0].event_key == "process.changed"
    assert changed[0].payload["process_id"] == 42
    assert changed[0].payload["executable_name"] == "after.exe"


def test_real_file_watcher_create_modify_rename_delete_and_cancellation(
    tmp_path: Path,
) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    source = FileChangeWatcherSource(watched)
    cancellation_source = CancellationSource()

    assert source.poll(cancellation=cancellation_source.token, max_items=16) == ()

    first = watched / "first.txt"
    first.write_text("one", encoding="utf-8")
    created = source.poll(cancellation=cancellation_source.token, max_items=16)
    assert len(created) == 1
    assert created[0].event_key == "filesystem.created"

    first.write_text("two-two", encoding="utf-8")
    modified = source.poll(cancellation=cancellation_source.token, max_items=16)
    assert len(modified) == 1
    assert modified[0].event_key == "filesystem.modified"

    renamed = watched / "renamed.txt"
    first.rename(renamed)
    moved = source.poll(cancellation=cancellation_source.token, max_items=16)
    assert len(moved) == 1
    assert moved[0].event_key == "filesystem.renamed"
    assert moved[0].payload["previous_path"] == "first.txt"
    assert moved[0].payload["path"] == "renamed.txt"

    renamed.unlink()
    deleted = source.poll(cancellation=cancellation_source.token, max_items=16)
    assert len(deleted) == 1
    assert deleted[0].event_key == "filesystem.deleted"

    cancellation_source.request_cancellation("watch cancelled")
    ignored = watched / "ignored.txt"
    ignored.write_text("ignored", encoding="utf-8")
    assert source.poll(cancellation=cancellation_source.token, max_items=16) == ()
