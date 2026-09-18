"""Concrete bounded event sources and event-to-task rules for M12."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from agentx.capabilities.windows.process_discovery import WindowsProcessDiscovery
from agentx.core.execution import CancellationToken
from agentx.core.knowledge import ProvenanceKind, ProvenanceReference
from agentx.core.scheduling import ScheduledTaskIntent
from agentx.infrastructure.event_watcher import (
    WatcherEmission,
    WatcherObservation,
    WatcherSourceId,
)
from agentx.infrastructure.schedule_store import DuplicateScheduleError
from agentx.scheduling import Scheduler, SchedulerTickReport
from agentx.world_model import WorldEntityId, WorldEntityKind, WorldModel

_MAX_ENTRIES = 4096
_MAX_EVENTS = 256


class ObservationClock(Protocol):
    def now(self) -> datetime: ...


class SystemObservationClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class _FileEntry:
    path: str
    inode: tuple[int, int] | None
    size: int
    modified_ns: int


class FileChangeWatcherSource:
    """Real metadata snapshot differ; content remains untrusted DATA."""

    def __init__(
        self,
        root: Path,
        *,
        recursive: bool = True,
        clock: ObservationClock | None = None,
    ) -> None:
        if not root.is_absolute():
            raise ValueError("watched path must be absolute")
        self._root = root
        self._recursive = recursive
        self._clock = SystemObservationClock() if clock is None else clock
        self._source_id = WatcherSourceId("filesystem.change")
        self._baseline: dict[str, _FileEntry] | None = None

    @property
    def source_id(self) -> WatcherSourceId:
        return self._source_id

    def poll(
        self,
        *,
        cancellation: CancellationToken,
        max_items: int,
    ) -> tuple[WatcherObservation, ...]:
        if cancellation.is_cancelled:
            return ()
        current = self._snapshot()
        previous = self._baseline
        self._baseline = current
        if previous is None:
            return ()
        now = self._clock.now()
        events: list[WatcherObservation] = []
        old_inode = {item.inode: item for item in previous.values() if item.inode is not None}
        new_inode = {item.inode: item for item in current.values() if item.inode is not None}
        moved_old: set[str] = set()
        moved_new: set[str] = set()
        for inode, old in old_inode.items():
            new = new_inode.get(inode)
            if new is not None and new.path != old.path:
                events.append(
                    self._event(
                        "filesystem.renamed",
                        now,
                        {
                            "change": "rename",
                            "path": new.path,
                            "previous_path": old.path,
                        },
                    )
                )
                moved_old.add(old.path)
                moved_new.add(new.path)
        for path, old in previous.items():
            if path in moved_old:
                continue
            new = current.get(path)
            if new is None:
                events.append(
                    self._event(
                        "filesystem.deleted",
                        now,
                        {"change": "delete", "path": path},
                    )
                )
            elif (new.size, new.modified_ns) != (old.size, old.modified_ns):
                events.append(
                    self._event(
                        "filesystem.modified",
                        now,
                        {"change": "modify", "path": path},
                    )
                )
        for path in current.keys() - previous.keys():
            if path not in moved_new:
                events.append(
                    self._event(
                        "filesystem.created",
                        now,
                        {"change": "create", "path": path},
                    )
                )
        events.sort(key=lambda item: (item.event_key, str(item.payload)))
        return tuple(events[: min(max_items, _MAX_EVENTS)])

    def _snapshot(self) -> dict[str, _FileEntry]:
        if not self._root.exists():
            return {}
        if self._root.is_dir():
            iterator = self._root.rglob("*") if self._recursive else self._root.glob("*")
            paths = list(iterator)
        else:
            paths = [self._root]
        result: dict[str, _FileEntry] = {}
        for path in paths:
            if len(result) >= _MAX_ENTRIES:
                raise RuntimeError("filesystem watcher entry bound exceeded")
            try:
                stat = path.stat()
            except OSError:
                continue
            relative = path.name if self._root.is_file() else str(path.relative_to(self._root))
            inode_number = int(getattr(stat, "st_ino", 0))
            inode = None if inode_number == 0 else (int(getattr(stat, "st_dev", 0)), inode_number)
            result[relative] = _FileEntry(
                path=relative,
                inode=inode,
                size=int(stat.st_size),
                modified_ns=int(stat.st_mtime_ns),
            )
        return result

    def _event(
        self,
        event_key: str,
        observed_at: datetime,
        payload: dict[str, object],
    ) -> WatcherObservation:
        return WatcherObservation(
            source_id=self._source_id,
            event_key=event_key,
            observed_at=observed_at,
            provenance=ProvenanceReference(
                kind=ProvenanceKind.SYSTEM,
                reference="m12.filesystem",
            ),
            payload=payload,
        )


class ProcessStateWatcherSource:
    """Read-only process state differ over canonical Windows discovery."""

    def __init__(
        self,
        discovery: WindowsProcessDiscovery,
        *,
        clock: ObservationClock | None = None,
    ) -> None:
        self._discovery = discovery
        self._clock = SystemObservationClock() if clock is None else clock
        self._source_id = WatcherSourceId("windows.process.state")
        self._baseline: dict[int, tuple[str | None, str | None]] | None = None

    @property
    def source_id(self) -> WatcherSourceId:
        return self._source_id

    def poll(
        self,
        *,
        cancellation: CancellationToken,
        max_items: int,
    ) -> tuple[WatcherObservation, ...]:
        if cancellation.is_cancelled:
            return ()
        result = self._discovery.discover()
        if result.is_failure:
            raise RuntimeError(result.unwrap_error().code)
        current = {
            process.process_id: (
                process.executable_name,
                process.executable_path,
            )
            for process in result.unwrap().processes
        }
        previous = self._baseline
        self._baseline = current
        if previous is None:
            return ()
        now = self._clock.now()
        events: list[WatcherObservation] = []
        for process_id in sorted(previous.keys() - current.keys()):
            events.append(self._event("process.exited", process_id, previous[process_id], now))
        for process_id in sorted(current.keys() - previous.keys()):
            events.append(self._event("process.appeared", process_id, current[process_id], now))
        for process_id in sorted(current.keys() & previous.keys()):
            if current[process_id] != previous[process_id]:
                events.append(
                    self._event(
                        "process.changed",
                        process_id,
                        current[process_id],
                        now,
                    )
                )
        return tuple(events[: min(max_items, _MAX_EVENTS)])

    def _event(
        self,
        key: str,
        process_id: int,
        identity: tuple[str | None, str | None],
        at: datetime,
    ) -> WatcherObservation:
        return WatcherObservation(
            source_id=self._source_id,
            event_key=key,
            observed_at=at,
            provenance=ProvenanceReference(
                kind=ProvenanceKind.SYSTEM,
                reference="m12.process.discovery",
            ),
            payload={
                "process_id": process_id,
                "executable_name": identity[0],
                "executable_path": identity[1],
            },
        )


class WorldStateWatcherSource:
    """Browser/device state watcher that emits digest and freshness, not authority."""

    def __init__(
        self,
        model: WorldModel,
        entity_ids: tuple[WorldEntityId, ...],
        *,
        required_kind: WorldEntityKind,
        event_key: str,
        source_id: str,
        clock: ObservationClock | None = None,
    ) -> None:
        if not entity_ids or len(entity_ids) > 256:
            raise ValueError("entity_ids must be bounded and non-empty")
        if any(item.kind is not required_kind for item in entity_ids):
            raise ValueError("entity kind mismatch")
        self._model = model
        self._ids = entity_ids
        self._event_key = event_key
        self._source_id = WatcherSourceId(source_id)
        self._clock = SystemObservationClock() if clock is None else clock
        self._baseline: dict[WorldEntityId, str] | None = None

    @property
    def source_id(self) -> WatcherSourceId:
        return self._source_id

    def poll(
        self,
        *,
        cancellation: CancellationToken,
        max_items: int,
    ) -> tuple[WatcherObservation, ...]:
        if cancellation.is_cancelled:
            return ()
        at = self._clock.now()
        current: dict[WorldEntityId, str] = {}
        freshness: dict[WorldEntityId, str] = {}
        for entity_id in self._ids:
            lookup = self._model.cache.lookup(entity_id, at=at, refresh=False)
            freshness[entity_id] = lookup.freshness.value
            current[entity_id] = hashlib.sha256(
                repr((lookup.freshness, lookup.value)).encode()
            ).hexdigest()
        previous = self._baseline
        self._baseline = current
        if previous is None:
            return ()
        events = [
            WatcherObservation(
                source_id=self._source_id,
                event_key=self._event_key,
                observed_at=at,
                provenance=ProvenanceReference(
                    kind=ProvenanceKind.SYSTEM,
                    reference="m12.world_model",
                ),
                payload={
                    "entity_id": entity_id.to_str(),
                    "freshness": freshness[entity_id],
                    "state_digest": current[entity_id],
                },
            )
            for entity_id in self._ids
            if previous.get(entity_id) != current[entity_id]
        ]
        return tuple(events[: min(max_items, _MAX_EVENTS)])


def browser_state_watcher(
    model: WorldModel,
    page_ids: tuple[WorldEntityId, ...],
) -> WorldStateWatcherSource:
    return WorldStateWatcherSource(
        model,
        page_ids,
        required_kind=WorldEntityKind.BROWSER_PAGE,
        event_key="browser.state.changed",
        source_id="browser.world.state",
    )


def device_state_watcher(
    model: WorldModel,
    device_ids: tuple[WorldEntityId, ...],
) -> WorldStateWatcherSource:
    return WorldStateWatcherSource(
        model,
        device_ids,
        required_kind=WorldEntityKind.DEVICE,
        event_key="device.state.changed",
        source_id="device.world.state",
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class EventTriggerRule:
    rule_id: str
    source_id: WatcherSourceId
    event_key: str
    intent: ScheduledTaskIntent


class EventTriggeredTaskLauncher:
    """Only pre-registered rules map event DATA to governed scheduled work."""

    def __init__(
        self,
        *,
        scheduler: Scheduler,
        rules: tuple[EventTriggerRule, ...],
    ) -> None:
        self._scheduler = scheduler
        self._rules = {(rule.source_id, rule.event_key): rule for rule in rules}

    def handle(
        self,
        emission: WatcherEmission,
    ) -> SchedulerTickReport | None:
        observation = emission.observation
        rule = self._rules.get((observation.source_id, observation.event_key))
        if rule is None:
            return None
        payload = json.dumps(
            _jsonable(observation.payload),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        schedule_id = uuid5(
            NAMESPACE_URL,
            "|".join(
                (
                    "agentx:m12:event",
                    rule.rule_id,
                    str(emission.watcher_id),
                    observation.event_key,
                    observation.observed_at.isoformat(),
                    hashlib.sha256(payload.encode()).hexdigest(),
                )
            ),
        )
        try:
            self._scheduler.register_immediate(
                intent=rule.intent,
                schedule_id=schedule_id,
            )
        except DuplicateScheduleError:
            if self._scheduler.store.get(schedule_id) is None:
                raise
        return self._scheduler.dispatch_schedule(schedule_id)


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "EventTriggerRule",
    "EventTriggeredTaskLauncher",
    "FileChangeWatcherSource",
    "ProcessStateWatcherSource",
    "WorldStateWatcherSource",
    "browser_state_watcher",
    "device_state_watcher",
]
