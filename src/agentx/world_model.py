"""M10 lazy, evidence-backed world-model composition for AgentX.

The existing :mod:`agentx.core.world_state` module remains the inert M6.01
snapshot contract.  This outer composition layer owns current-state models,
lazy caching, Task relevance, targeted invalidation and the transient/durable
Hive boundary.  World state is descriptive data only: it never grants
permission, lowers risk, bypasses the Action Gate, clears the Emergency Stop,
executes a capability, verifies an outcome, or marks a Task successful.
"""

from __future__ import annotations

import json
import ntpath
import os
import platform
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Final, Protocol, TypeAlias
from urllib.parse import urlsplit
from uuid import UUID

from agentx.capabilities.browser_connection import BrowserTargetRef
from agentx.capabilities.browser_dom import BrowserDomObservation, BrowserDomObservationState
from agentx.capabilities.device import DeviceAvailability, DeviceDescriptor, DevicePlatform
from agentx.capabilities.windows.process_discovery import WindowsProcessSnapshot
from agentx.core.events import ActionPayload, Event, EventType
from agentx.core.execution import ExecutionContext
from agentx.core.ids import KnowledgeId, TaskId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeType,
    ProvenanceKind,
    ProvenanceReference,
    ScopeDimension,
)
from agentx.core.provenance import EvidenceReference
from agentx.infrastructure.knowledge_store import KnowledgeStore

_WORLD_LINK_PROVENANCE: Final = "agentx.world_model.link/v1"
_MAX_CACHE_ENTRIES: Final = 1024
_MAX_APPLICATIONS: Final = 256
_MAX_TASK_BINDINGS: Final = 1024
_MAX_TASK_ENTITIES: Final = 256
_MAX_LINKS: Final = 4096
ACTIVE_WINDOW_KEY: Final = "__active_window__"


class WorldModelError(ValueError):
    """Base world-model error."""


class WorldModelValidationError(WorldModelError):
    """Typed world data violated a production invariant."""


class ApplicationRegistryConflictError(WorldModelError):
    """A stable application identity was registered incompatibly."""


class ApplicationRegistryLimitError(WorldModelError):
    """A bounded registry limit would be exceeded."""


class WorldFreshness(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class WorldEntityKind(StrEnum):
    PERCEPTION = "perception"
    APPLICATION = "application"
    PROCESS = "process"
    WINDOW = "window"
    BROWSER_SESSION = "browser_session"
    BROWSER_PAGE = "browser_page"
    FILESYSTEM = "filesystem"
    DEVICE = "device"


class WorldAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class WindowShowState(StrEnum):
    RESTORED = "restored"
    MINIMIZED = "minimized"
    MAXIMIZED = "maximized"
    UNKNOWN = "unknown"


class FilesystemExistence(StrEnum):
    EXISTS = "exists"
    MISSING = "missing"
    INACCESSIBLE = "inaccessible"
    UNKNOWN = "unknown"


class FilesystemEntityType(StrEnum):
    FILE = "file"
    DIRECTORY = "directory"
    OTHER = "other"
    UNKNOWN = "unknown"


class CacheRefreshState(StrEnum):
    CACHE_MISS = "cache_miss"
    FRESH_HIT = "fresh_hit"
    STALE_HIT = "stale_hit"
    INVALIDATED = "invalidated"
    REFRESH_SUCCESS = "refresh_success"
    REFRESH_FAILURE = "refresh_failure"
    SOURCE_UNAVAILABLE = "source_unavailable"


class LinkVerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    SUPERSEDED = "superseded"


def _text(value: object, name: str, *, max_length: int = 16_384) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value or value != value.strip() or len(value) > max_length:
        raise WorldModelValidationError(f"{name} is malformed")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise WorldModelValidationError(f"{name} contains control characters")
    return value


def _untrusted(value: object, name: str, *, max_length: int = 16_384) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string or None")
    if len(value) > max_length:
        raise WorldModelValidationError(f"{name} exceeds its defensive bound")
    return value


def _time(value: object, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise WorldModelValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise WorldModelValidationError(f"{name} must be ISO-8601 text")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        return _time(datetime.fromisoformat(normalized), name)
    except ValueError as exc:
        raise WorldModelValidationError(f"{name} is malformed") from exc


@dataclass(frozen=True, slots=True, order=True)
class WorldEntityId:
    environment_id: str
    kind: WorldEntityKind
    value: str

    def __post_init__(self) -> None:
        _text(self.environment_id, "environment_id", max_length=1024)
        if not isinstance(self.kind, WorldEntityKind):
            raise TypeError("kind must be WorldEntityKind")
        _text(self.value, "entity value", max_length=4096)

    def to_str(self) -> str:
        return f"{self.environment_id}:{self.kind.value}:{self.value}"

    def to_dict(self) -> dict[str, str]:
        return {"environment_id": self.environment_id, "kind": self.kind.value, "value": self.value}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> WorldEntityId:
        if set(raw) != {"environment_id", "kind", "value"}:
            raise WorldModelValidationError("world entity identity fields are malformed")
        environment_id, kind, value = raw["environment_id"], raw["kind"], raw["value"]
        if not isinstance(environment_id, str) or not isinstance(kind, str) or not isinstance(value, str):
            raise WorldModelValidationError("world entity identity fields must be strings")
        try:
            entity_kind = WorldEntityKind(kind)
        except ValueError as exc:
            raise WorldModelValidationError("unknown world entity kind") from exc
        return cls(environment_id, entity_kind, value)


@dataclass(frozen=True, slots=True)
class ObservationMetadata:
    observation_id: str
    source: ProvenanceReference
    observed_at: datetime
    ttl: timedelta
    environment_id: str
    device_id: WorldEntityId | None = None
    task_id: TaskId | None = None
    correlation_id: UUID | None = None

    def __post_init__(self) -> None:
        _text(self.observation_id, "observation_id", max_length=2048)
        if not isinstance(self.source, ProvenanceReference):
            raise TypeError("source must be ProvenanceReference")
        object.__setattr__(self, "observed_at", _time(self.observed_at, "observed_at"))
        if not isinstance(self.ttl, timedelta) or self.ttl <= timedelta(0):
            raise WorldModelValidationError("ttl must be a positive timedelta")
        _text(self.environment_id, "environment_id", max_length=1024)
        if self.device_id is not None:
            if self.device_id.kind is not WorldEntityKind.DEVICE:
                raise WorldModelValidationError("device_id must identify a device")
            if self.device_id.environment_id != self.environment_id:
                raise WorldModelValidationError("device_id crosses environment scope")
        if self.task_id is not None and not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be TaskId or None")
        if self.correlation_id is not None and (
            not isinstance(self.correlation_id, UUID) or self.correlation_id.int == 0
        ):
            raise WorldModelValidationError("correlation_id must be a non-nil UUID")

    def freshness(self, at: datetime, *, invalidated: bool = False) -> WorldFreshness:
        moment = _time(at, "at")
        if invalidated:
            return WorldFreshness.STALE
        return WorldFreshness.FRESH if moment < self.observed_at + self.ttl else WorldFreshness.STALE

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "source": self.source.to_dict(),
            "observed_at": _iso(self.observed_at),
            "ttl_us": int(self.ttl / timedelta(microseconds=1)),
            "environment_id": self.environment_id,
            "device_id": None if self.device_id is None else self.device_id.to_dict(),
            "task_id": None if self.task_id is None else self.task_id.to_str(),
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ObservationMetadata:
        source_raw = raw.get("source")
        if not isinstance(source_raw, Mapping):
            raise WorldModelValidationError("metadata source is malformed")
        source = ProvenanceReference.from_dict(dict(source_raw))
        ttl_us = raw.get("ttl_us")
        if type(ttl_us) is not int or ttl_us <= 0:
            raise WorldModelValidationError("metadata ttl_us is malformed")
        observation_id = raw.get("observation_id")
        environment_id = raw.get("environment_id")
        if not isinstance(observation_id, str) or not isinstance(environment_id, str):
            raise WorldModelValidationError("metadata identity is malformed")
        device_raw = raw.get("device_id")
        device_id = None
        if device_raw is not None:
            if not isinstance(device_raw, Mapping):
                raise WorldModelValidationError("metadata device_id is malformed")
            device_id = WorldEntityId.from_dict(dict(device_raw))
        task_raw = raw.get("task_id")
        task_id = None
        if task_raw is not None:
            if not isinstance(task_raw, str):
                raise WorldModelValidationError("metadata task_id is malformed")
            try:
                task_id = TaskId.parse(task_raw)
            except ValueError as exc:
                raise WorldModelValidationError("metadata task_id is malformed") from exc
        correlation_raw = raw.get("correlation_id")
        correlation_id = None
        if correlation_raw is not None:
            if not isinstance(correlation_raw, str):
                raise WorldModelValidationError("metadata correlation_id is malformed")
            try:
                correlation_id = UUID(correlation_raw)
            except ValueError as exc:
                raise WorldModelValidationError("metadata correlation_id is malformed") from exc
        return cls(
            observation_id=observation_id,
            source=source,
            observed_at=_parse_time(raw.get("observed_at"), "observed_at"),
            ttl=timedelta(microseconds=ttl_us),
            environment_id=environment_id,
            device_id=device_id,
            task_id=task_id,
            correlation_id=correlation_id,
        )


@dataclass(frozen=True, slots=True, order=True)
class ScreenBounds:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if any(type(item) is not int for item in (self.x, self.y, self.width, self.height)):
            raise TypeError("screen bounds must be integers")
        if self.width <= 0 or self.height <= 0:
            raise WorldModelValidationError("bounds width and height must be positive")
        if self.width > 1_000_000 or self.height > 1_000_000:
            raise WorldModelValidationError("bounds exceed the defensive limit")

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True, slots=True)
class PerceptionRegion:
    region_id: str
    bounds: ScreenBounds
    text: str | None = None
    role: str | None = None
    confidence: float | None = None
    structured_observation_ref: str | None = None

    def __post_init__(self) -> None:
        _text(self.region_id, "region_id", max_length=1024)
        if not isinstance(self.bounds, ScreenBounds):
            raise TypeError("bounds must be ScreenBounds")
        _untrusted(self.text, "region text")
        _untrusted(self.role, "region role", max_length=4096)
        if self.confidence is not None:
            if isinstance(self.confidence, bool) or not isinstance(self.confidence, int | float):
                raise TypeError("confidence must be numeric or None")
            confidence = float(self.confidence)
            if not 0.0 <= confidence <= 1.0:
                raise WorldModelValidationError("confidence must be within [0, 1]")
            object.__setattr__(self, "confidence", confidence)
        if self.structured_observation_ref is not None:
            _text(self.structured_observation_ref, "structured_observation_ref", max_length=2048)

    def to_dict(self) -> dict[str, object]:
        return {
            "region_id": self.region_id,
            "bounds": self.bounds.to_dict(),
            "text": self.text,
            "role": self.role,
            "confidence": self.confidence,
            "structured_observation_ref": self.structured_observation_ref,
        }


@dataclass(frozen=True, slots=True)
class PerceptionObservation:
    """AX-409 representation only; it performs no capture or visual grounding."""

    entity_id: WorldEntityId
    metadata: ObservationMetadata
    surface_id: str
    bounds: ScreenBounds
    regions: tuple[PerceptionRegion, ...] = ()
    structured_observation_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.entity_id.kind is not WorldEntityKind.PERCEPTION:
            raise WorldModelValidationError("perception requires PERCEPTION identity")
        if self.entity_id.environment_id != self.metadata.environment_id:
            raise WorldModelValidationError("perception crosses environment scope")
        _text(self.surface_id, "surface_id", max_length=2048)
        if not isinstance(self.bounds, ScreenBounds):
            raise TypeError("bounds must be ScreenBounds")
        if len(self.regions) > 512 or len(self.structured_observation_refs) > 128:
            raise WorldModelValidationError("perception representation is unbounded")
        if len({region.region_id for region in self.regions}) != len(self.regions):
            raise WorldModelValidationError("perception region identities must be unique")
        for region in self.regions:
            if not isinstance(region, PerceptionRegion):
                raise TypeError("regions must contain PerceptionRegion values")
        for reference in self.structured_observation_refs:
            _text(reference, "structured observation reference", max_length=2048)
        if len(set(self.structured_observation_refs)) != len(self.structured_observation_refs):
            raise WorldModelValidationError("structured observation references must be unique")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "entity": self.entity_id.to_dict(),
            "metadata": self.metadata.to_dict(),
            "surface_id": self.surface_id,
            "bounds": self.bounds.to_dict(),
            "regions": [region.to_dict() for region in self.regions],
            "structured_observation_refs": list(self.structured_observation_refs),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> PerceptionObservation:
        try:
            raw: object = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise WorldModelValidationError("perception JSON is malformed") from exc
        if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
            raise WorldModelValidationError("perception JSON schema is unsupported")
        entity_raw, metadata_raw, bounds_raw = raw.get("entity"), raw.get("metadata"), raw.get("bounds")
        regions_raw, refs_raw = raw.get("regions"), raw.get("structured_observation_refs")
        surface_id = raw.get("surface_id")
        if not isinstance(entity_raw, Mapping) or not isinstance(metadata_raw, Mapping) or not isinstance(bounds_raw, Mapping):
            raise WorldModelValidationError("perception JSON identity/bounds are malformed")
        if not isinstance(regions_raw, list) or not isinstance(refs_raw, list) or not isinstance(surface_id, str):
            raise WorldModelValidationError("perception JSON collections are malformed")

        def bounds_of(value: Mapping[object, object]) -> ScreenBounds:
            try:
                x, y, width, height = value["x"], value["y"], value["width"], value["height"]
            except KeyError as exc:
                raise WorldModelValidationError("perception bounds are malformed") from exc
            if any(type(item) is not int for item in (x, y, width, height)):
                raise WorldModelValidationError("perception bounds are malformed")
            return ScreenBounds(x=x, y=y, width=width, height=height)

        regions: list[PerceptionRegion] = []
        for item in regions_raw:
            if not isinstance(item, Mapping) or not isinstance(item.get("bounds"), Mapping):
                raise WorldModelValidationError("perception region is malformed")
            region_id = item.get("region_id")
            text, role = item.get("text"), item.get("role")
            confidence, structured_ref = item.get("confidence"), item.get("structured_observation_ref")
            if not isinstance(region_id, str):
                raise WorldModelValidationError("perception region identity is malformed")
            if text is not None and not isinstance(text, str):
                raise WorldModelValidationError("perception region text is malformed")
            if role is not None and not isinstance(role, str):
                raise WorldModelValidationError("perception region role is malformed")
            if structured_ref is not None and not isinstance(structured_ref, str):
                raise WorldModelValidationError("perception structured reference is malformed")
            if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, int | float)):
                raise WorldModelValidationError("perception confidence is malformed")
            regions.append(
                PerceptionRegion(
                    region_id=region_id,
                    bounds=bounds_of(item["bounds"]),
                    text=text,
                    role=role,
                    confidence=None if confidence is None else float(confidence),
                    structured_observation_ref=structured_ref,
                )
            )
        if not all(isinstance(item, str) for item in refs_raw):
            raise WorldModelValidationError("perception references are malformed")
        return cls(
            entity_id=WorldEntityId.from_dict(dict(entity_raw)),
            metadata=ObservationMetadata.from_dict(dict(metadata_raw)),
            surface_id=surface_id,
            bounds=bounds_of(bounds_raw),
            regions=tuple(regions),
            structured_observation_refs=tuple(refs_raw),
        )


@dataclass(frozen=True, slots=True, order=True)
class ApplicationIdentity:
    environment_id: str
    platform: str
    stable_id: str

    def __post_init__(self) -> None:
        _text(self.environment_id, "environment_id", max_length=1024)
        _text(self.platform, "platform", max_length=128)
        _text(self.stable_id, "stable_id", max_length=2048)

    @property
    def entity_id(self) -> WorldEntityId:
        return WorldEntityId(self.environment_id, WorldEntityKind.APPLICATION, self.stable_id)


@dataclass(frozen=True, slots=True)
class ApplicationRecord:
    identity: ApplicationIdentity
    canonical_name: str
    display_name: str
    executable_ids: tuple[str, ...]
    package_ids: tuple[str, ...]
    launch_targets: tuple[str, ...]
    aliases: tuple[str, ...]
    availability: WorldAvailability
    process_ids: tuple[WorldEntityId, ...]
    window_ids: tuple[WorldEntityId, ...]
    metadata: ObservationMetadata

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ApplicationIdentity):
            raise TypeError("identity must be ApplicationIdentity")
        _text(self.canonical_name, "canonical_name", max_length=4096)
        _untrusted(self.display_name, "display_name", max_length=4096)
        for field_name in ("executable_ids", "package_ids", "launch_targets", "aliases"):
            values = getattr(self, field_name)
            if not isinstance(values, tuple) or len(values) > 64 or len(set(values)) != len(values):
                raise WorldModelValidationError(f"{field_name} is malformed or unbounded")
            for item in values:
                _text(item, field_name, max_length=4096)
        for entity_id in self.process_ids:
            if entity_id.kind is not WorldEntityKind.PROCESS:
                raise WorldModelValidationError("process_ids contains non-process identity")
        for entity_id in self.window_ids:
            if entity_id.kind is not WorldEntityKind.WINDOW:
                raise WorldModelValidationError("window_ids contains non-window identity")
        if len(self.process_ids) > 256 or len(self.window_ids) > 256:
            raise WorldModelValidationError("application relationships are unbounded")
        if self.metadata.environment_id != self.identity.environment_id:
            raise WorldModelValidationError("application metadata crosses environment scope")


@dataclass(frozen=True, slots=True)
class ProcessState:
    entity_id: WorldEntityId
    pid: int
    executable_name: str | None
    executable_path: str | None
    parent_pid: int | None
    application_id: WorldEntityId | None
    window_ids: tuple[WorldEntityId, ...]
    availability: WorldAvailability
    metadata: ObservationMetadata

    def __post_init__(self) -> None:
        if self.entity_id.kind is not WorldEntityKind.PROCESS or type(self.pid) is not int or self.pid < 0:
            raise WorldModelValidationError("process identity/PID is malformed")
        _untrusted(self.executable_name, "executable_name", max_length=512)
        _untrusted(self.executable_path, "executable_path", max_length=4096)
        if self.parent_pid is not None and (type(self.parent_pid) is not int or self.parent_pid < 0):
            raise WorldModelValidationError("parent_pid is malformed")
        if self.application_id is not None and self.application_id.kind is not WorldEntityKind.APPLICATION:
            raise WorldModelValidationError("application_id must identify an application")
        if any(item.kind is not WorldEntityKind.WINDOW for item in self.window_ids):
            raise WorldModelValidationError("window_ids must identify windows")


@dataclass(frozen=True, slots=True)
class WindowState:
    entity_id: WorldEntityId
    handle: int | None
    process_id: WorldEntityId | None
    application_id: WorldEntityId | None
    title: str | None
    bounds: ScreenBounds | None
    visible: bool | None
    show_state: WindowShowState
    is_foreground: bool | None
    availability: WorldAvailability
    metadata: ObservationMetadata

    def __post_init__(self) -> None:
        if self.entity_id.kind is not WorldEntityKind.WINDOW:
            raise WorldModelValidationError("window identity is malformed")
        if self.handle is not None and (type(self.handle) is not int or self.handle < 1):
            raise WorldModelValidationError("window handle is malformed")
        if self.process_id is not None and self.process_id.kind is not WorldEntityKind.PROCESS:
            raise WorldModelValidationError("process_id must identify a process")
        if self.application_id is not None and self.application_id.kind is not WorldEntityKind.APPLICATION:
            raise WorldModelValidationError("application_id must identify an application")
        _untrusted(self.title, "window title", max_length=4096)


@dataclass(frozen=True, slots=True)
class BrowserSessionState:
    entity_id: WorldEntityId
    provider_id: str
    session_id: str
    availability: WorldAvailability
    active_page_id: WorldEntityId | None
    application_id: WorldEntityId | None
    process_id: WorldEntityId | None
    metadata: ObservationMetadata

    def __post_init__(self) -> None:
        if self.entity_id.kind is not WorldEntityKind.BROWSER_SESSION:
            raise WorldModelValidationError("browser session identity is malformed")


@dataclass(frozen=True, slots=True)
class BrowserPageState:
    entity_id: WorldEntityId
    session_id: WorldEntityId
    target_id: str
    title: str | None
    url: str | None
    origin: str | None
    document_version: str | None
    dom_observation_ref: str | None
    active: bool
    availability: WorldAvailability
    metadata: ObservationMetadata

    def __post_init__(self) -> None:
        if self.entity_id.kind is not WorldEntityKind.BROWSER_PAGE or self.session_id.kind is not WorldEntityKind.BROWSER_SESSION:
            raise WorldModelValidationError("browser page/session identity is malformed")
        _text(self.target_id, "target_id", max_length=2048)
        _untrusted(self.title, "browser title", max_length=4096)
        _untrusted(self.url, "browser url", max_length=16_384)


@dataclass(frozen=True, slots=True)
class FilesystemState:
    entity_id: WorldEntityId
    normalized_path: str
    original_path: str
    existence: FilesystemExistence
    entity_type: FilesystemEntityType
    size_bytes: int | None
    modified_ns: int | None
    task_relevant: bool
    metadata: ObservationMetadata

    def __post_init__(self) -> None:
        if self.entity_id.kind is not WorldEntityKind.FILESYSTEM:
            raise WorldModelValidationError("filesystem identity is malformed")
        _text(self.normalized_path, "normalized_path", max_length=4096)
        _text(self.original_path, "original_path", max_length=4096)
        if self.size_bytes is not None and (type(self.size_bytes) is not int or self.size_bytes < 0):
            raise WorldModelValidationError("size_bytes is malformed")


@dataclass(frozen=True, slots=True)
class DeviceState:
    entity_id: WorldEntityId
    platform: str
    role: str
    availability: WorldAvailability
    capability_health: tuple[tuple[str, str], ...]
    last_seen: datetime
    metadata: ObservationMetadata

    def __post_init__(self) -> None:
        if self.entity_id.kind is not WorldEntityKind.DEVICE:
            raise WorldModelValidationError("device identity is malformed")
        _text(self.platform, "platform", max_length=128)
        _text(self.role, "role", max_length=128)
        object.__setattr__(self, "last_seen", _time(self.last_seen, "last_seen"))
        if tuple(sorted(self.capability_health)) != self.capability_health:
            raise WorldModelValidationError("capability_health must be sorted")


WorldStateValue: TypeAlias = PerceptionObservation | ApplicationRecord | ProcessState | WindowState | BrowserSessionState | BrowserPageState | FilesystemState | DeviceState


def _metadata(value: WorldStateValue) -> ObservationMetadata:
    return value.metadata


class WorldStateProvider(Protocol):
    def observe(self, entity_id: WorldEntityId) -> WorldStateValue | None: ...


@dataclass(frozen=True, slots=True)
class CacheLookup:
    entity_id: WorldEntityId
    freshness: WorldFreshness
    state: CacheRefreshState
    value: WorldStateValue | None
    detail: str | None = None


@dataclass(slots=True)
class _CacheEntry:
    value: WorldStateValue
    invalidated: bool = False
    reason: str | None = None


class LazyWorldStateCache:
    """Bounded on-demand cache.  Refresh races are rejected by per-key epochs."""

    def __init__(self, *, max_entries: int = _MAX_CACHE_ENTRIES) -> None:
        if type(max_entries) is not int or max_entries < 1:
            raise WorldModelValidationError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: dict[WorldEntityId, _CacheEntry] = {}
        self._providers: dict[WorldEntityKind, WorldStateProvider] = {}
        self._epochs: dict[WorldEntityId, int] = {}
        self._lock = RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def register_provider(self, kind: WorldEntityKind, provider: WorldStateProvider) -> None:
        if not hasattr(provider, "observe"):
            raise TypeError("provider must expose observe")
        with self._lock:
            self._providers[kind] = provider

    def put(self, value: WorldStateValue) -> None:
        entity_id = value.entity_id
        if entity_id.environment_id != _metadata(value).environment_id:
            raise WorldModelValidationError("cache value crosses environment scope")
        with self._lock:
            if entity_id not in self._entries and len(self._entries) >= self._max_entries:
                victim = min(self._entries, key=lambda key: (_metadata(self._entries[key].value).observed_at, key))
                del self._entries[victim]
                self._epochs[victim] = self._epochs.get(victim, 0) + 1
            self._entries[entity_id] = _CacheEntry(value)
            self._epochs[entity_id] = self._epochs.get(entity_id, 0) + 1

    def lookup(self, entity_id: WorldEntityId, *, at: datetime, refresh: bool = True) -> CacheLookup:
        moment = _time(at, "at")
        with self._lock:
            entry = self._entries.get(entity_id)
            provider = self._providers.get(entity_id.kind)
            if entry is not None:
                freshness = _metadata(entry.value).freshness(moment, invalidated=entry.invalidated)
                if freshness is WorldFreshness.FRESH:
                    return CacheLookup(entity_id, freshness, CacheRefreshState.FRESH_HIT, entry.value)
                stale_value = entry.value
                stale_state = CacheRefreshState.INVALIDATED if entry.invalidated else CacheRefreshState.STALE_HIT
                if not refresh:
                    return CacheLookup(entity_id, WorldFreshness.STALE, stale_state, stale_value, entry.reason)
            else:
                stale_value = None
                if not refresh:
                    return CacheLookup(entity_id, WorldFreshness.UNKNOWN, CacheRefreshState.CACHE_MISS, None)
            if provider is None:
                freshness = WorldFreshness.STALE if stale_value is not None else WorldFreshness.UNKNOWN
                return CacheLookup(entity_id, freshness, CacheRefreshState.SOURCE_UNAVAILABLE, stale_value)
            epoch = self._epochs.get(entity_id, 0)
        try:
            observed = provider.observe(entity_id)
        except Exception as exc:
            freshness = WorldFreshness.STALE if stale_value is not None else WorldFreshness.UNKNOWN
            return CacheLookup(entity_id, freshness, CacheRefreshState.REFRESH_FAILURE, stale_value, f"{type(exc).__name__}: {exc}")
        if observed is None:
            freshness = WorldFreshness.STALE if stale_value is not None else WorldFreshness.UNKNOWN
            return CacheLookup(entity_id, freshness, CacheRefreshState.SOURCE_UNAVAILABLE, stale_value)
        if observed.entity_id != entity_id:
            freshness = WorldFreshness.STALE if stale_value is not None else WorldFreshness.UNKNOWN
            return CacheLookup(entity_id, freshness, CacheRefreshState.REFRESH_FAILURE, stale_value, "provider returned wrong identity")
        with self._lock:
            if self._epochs.get(entity_id, 0) != epoch:
                current = self._entries.get(entity_id)
                current_value = None if current is None else current.value
                freshness = WorldFreshness.STALE if current_value is not None else WorldFreshness.UNKNOWN
                return CacheLookup(entity_id, freshness, CacheRefreshState.REFRESH_FAILURE, current_value, "refresh raced with invalidation/update")
            self._entries[entity_id] = _CacheEntry(observed)
            self._epochs[entity_id] = epoch + 1
        return CacheLookup(entity_id, _metadata(observed).freshness(moment), CacheRefreshState.REFRESH_SUCCESS, observed)

    def invalidate(self, entity_id: WorldEntityId, *, reason: str) -> bool:
        _text(reason, "invalidation reason", max_length=512)
        with self._lock:
            self._epochs[entity_id] = self._epochs.get(entity_id, 0) + 1
            entry = self._entries.get(entity_id)
            if entry is None:
                return False
            entry.invalidated, entry.reason = True, reason
            return True

    def invalidate_where(self, predicate: Callable[[WorldEntityId, WorldStateValue], bool], *, reason: str) -> tuple[WorldEntityId, ...]:
        _text(reason, "invalidation reason", max_length=512)
        changed: list[WorldEntityId] = []
        with self._lock:
            for entity_id in sorted(self._entries):
                entry = self._entries[entity_id]
                if predicate(entity_id, entry.value):
                    entry.invalidated, entry.reason = True, reason
                    self._epochs[entity_id] = self._epochs.get(entity_id, 0) + 1
                    changed.append(entity_id)
        return tuple(changed)

    def cleanup_stale(self, *, at: datetime) -> int:
        moment = _time(at, "at")
        with self._lock:
            stale = [key for key, entry in self._entries.items() if _metadata(entry.value).freshness(moment, invalidated=entry.invalidated) is WorldFreshness.STALE]
            for key in stale:
                del self._entries[key]
                self._epochs[key] = self._epochs.get(key, 0) + 1
            return len(stale)

    def snapshot_ids(self) -> tuple[WorldEntityId, ...]:
        with self._lock:
            return tuple(sorted(self._entries))


class ApplicationRegistry:
    def __init__(self, *, max_applications: int = _MAX_APPLICATIONS) -> None:
        self._max = max_applications
        self._records: dict[ApplicationIdentity, ApplicationRecord] = {}
        self._lock = RLock()

    def register(self, record: ApplicationRecord) -> ApplicationRecord:
        with self._lock:
            current = self._records.get(record.identity)
            if current is None and len(self._records) >= self._max:
                raise ApplicationRegistryLimitError("application registry is full")
            if current is not None and current.canonical_name != record.canonical_name:
                raise ApplicationRegistryConflictError("stable application identity conflicts")
            self._records[record.identity] = record
        return record

    def get(self, identity: ApplicationIdentity) -> ApplicationRecord | None:
        with self._lock:
            return self._records.get(identity)

    def resolve_alias(self, *, environment_id: str, alias: str) -> tuple[ApplicationRecord, ...]:
        needle = _text(alias, "alias", max_length=4096).casefold()
        with self._lock:
            matches = [record for record in self._records.values() if record.identity.environment_id == environment_id and needle in {record.canonical_name.casefold(), record.display_name.casefold(), *(item.casefold() for item in record.aliases)}]
        return tuple(sorted(matches, key=lambda record: record.identity))

    def update_relationships(self, identity: ApplicationIdentity, *, process_ids: tuple[WorldEntityId, ...], window_ids: tuple[WorldEntityId, ...], metadata: ObservationMetadata) -> ApplicationRecord:
        with self._lock:
            current = self._records.get(identity)
            if current is None:
                raise KeyError(identity)
            updated = replace(current, process_ids=process_ids, window_ids=window_ids, metadata=metadata)
            self._records[identity] = updated
            return updated

    def stable_snapshot_json(self) -> str:
        with self._lock:
            payload = [
                {
                    "environment_id": r.identity.environment_id,
                    "platform": r.identity.platform,
                    "stable_id": r.identity.stable_id,
                    "canonical_name": r.canonical_name,
                    "display_name": r.display_name,
                    "executable_ids": list(r.executable_ids),
                    "package_ids": list(r.package_ids),
                    "launch_targets": list(r.launch_targets),
                    "aliases": list(r.aliases),
                }
                for r in sorted(self._records.values(), key=lambda item: item.identity)
            ]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_stable_snapshot_json(cls, payload: str, *, metadata: ObservationMetadata, max_applications: int = _MAX_APPLICATIONS) -> ApplicationRegistry:
        try:
            raw: object = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise WorldModelValidationError("application registry JSON is malformed") from exc
        if not isinstance(raw, list):
            raise WorldModelValidationError("application registry JSON must be an array")
        registry = cls(max_applications=max_applications)
        for item in raw:
            if not isinstance(item, Mapping):
                raise WorldModelValidationError("application registry entry is malformed")
            strings = ("environment_id", "platform", "stable_id", "canonical_name", "display_name")
            if not all(isinstance(item.get(name), str) for name in strings):
                raise WorldModelValidationError("application registry identity is malformed")
            identity = ApplicationIdentity(str(item["environment_id"]), str(item["platform"]), str(item["stable_id"]))
            if identity.environment_id != metadata.environment_id:
                raise WorldModelValidationError("restored application crosses environment scope")
            tuple_values: dict[str, tuple[str, ...]] = {}
            for name in ("executable_ids", "package_ids", "launch_targets", "aliases"):
                value = item.get(name)
                if not isinstance(value, list) or not all(isinstance(part, str) for part in value):
                    raise WorldModelValidationError("application registry collection is malformed")
                tuple_values[name] = tuple(value)
            registry.register(
                ApplicationRecord(
                    identity=identity,
                    canonical_name=str(item["canonical_name"]),
                    display_name=str(item["display_name"]),
                    executable_ids=tuple_values["executable_ids"],
                    package_ids=tuple_values["package_ids"],
                    launch_targets=tuple_values["launch_targets"],
                    aliases=tuple_values["aliases"],
                    availability=WorldAvailability.UNKNOWN,
                    process_ids=(),
                    window_ids=(),
                    metadata=metadata,
                )
            )
        return registry


@dataclass(frozen=True, slots=True)
class TaskWorldBinding:
    task_id: TaskId
    correlation_id: UUID
    entity_ids: tuple[WorldEntityId, ...]
    evidence: tuple[EvidenceReference, ...]
    updated_at: datetime

    def __post_init__(self) -> None:
        if len(self.entity_ids) > _MAX_TASK_ENTITIES or len(set(self.entity_ids)) != len(self.entity_ids):
            raise WorldModelValidationError("task binding entity set is malformed or unbounded")
        if not self.evidence or not all(isinstance(item, EvidenceReference) for item in self.evidence):
            raise WorldModelValidationError("task binding requires canonical evidence")
        object.__setattr__(self, "entity_ids", tuple(sorted(self.entity_ids)))
        object.__setattr__(self, "updated_at", _time(self.updated_at, "updated_at"))


class TaskWorldBinder:
    def __init__(self, *, max_bindings: int = _MAX_TASK_BINDINGS) -> None:
        self._max = max_bindings
        self._bindings: dict[TaskId, TaskWorldBinding] = {}
        self._lock = RLock()

    def bind(self, context: ExecutionContext, *, entity_ids: tuple[WorldEntityId, ...], evidence: tuple[EvidenceReference, ...], updated_at: datetime) -> TaskWorldBinding:
        if context.task_id is None:
            raise WorldModelValidationError("task/world binding requires context.task_id")
        binding = TaskWorldBinding(context.task_id, context.correlation_id, entity_ids, evidence, updated_at)
        with self._lock:
            if context.task_id not in self._bindings and len(self._bindings) >= self._max:
                raise WorldModelValidationError("task binding registry is full")
            self._bindings[context.task_id] = binding
        return binding

    def get(self, task_id: TaskId) -> TaskWorldBinding | None:
        with self._lock:
            return self._bindings.get(task_id)

    def invalidate_entities(self, entity_ids: tuple[WorldEntityId, ...]) -> tuple[TaskId, ...]:
        affected = set(entity_ids)
        changed: list[TaskId] = []
        with self._lock:
            for task_id, binding in tuple(self._bindings.items()):
                remaining = tuple(item for item in binding.entity_ids if item not in affected)
                if remaining == binding.entity_ids:
                    continue
                changed.append(task_id)
                if remaining:
                    self._bindings[task_id] = replace(binding, entity_ids=remaining)
                else:
                    del self._bindings[task_id]
        return tuple(sorted(changed, key=lambda item: item.to_str()))

    def complete_task(self, task_id: TaskId) -> bool:
        with self._lock:
            return self._bindings.pop(task_id, None) is not None

    def assemble(self, task_id: TaskId, *, cache: LazyWorldStateCache, at: datetime) -> tuple[CacheLookup, ...]:
        binding = self.get(task_id)
        if binding is None:
            return ()
        return tuple(cache.lookup(entity_id, at=at, refresh=False) for entity_id in binding.entity_ids)


@dataclass(frozen=True, slots=True)
class WorldHiveLink:
    world_entity_id: WorldEntityId
    knowledge_id: KnowledgeId
    evidence: tuple[EvidenceReference, ...]
    observed_at: datetime
    environment_id: str
    verification_status: LinkVerificationStatus = LinkVerificationStatus.UNVERIFIED
    contradiction_ids: tuple[KnowledgeId, ...] = ()
    supersedes_ids: tuple[KnowledgeId, ...] = ()
    durable: bool = False

    def __post_init__(self) -> None:
        if not self.evidence or not all(isinstance(item, EvidenceReference) for item in self.evidence):
            raise WorldModelValidationError("world/Hive link requires evidence")
        object.__setattr__(self, "observed_at", _time(self.observed_at, "observed_at"))
        if self.environment_id != self.world_entity_id.environment_id:
            raise WorldModelValidationError("world/Hive link crosses environment scope")
        object.__setattr__(self, "contradiction_ids", tuple(sorted(self.contradiction_ids, key=lambda item: item.to_str())))
        object.__setattr__(self, "supersedes_ids", tuple(sorted(self.supersedes_ids, key=lambda item: item.to_str())))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "world_entity": self.world_entity_id.to_dict(),
            "knowledge_id": self.knowledge_id.to_str(),
            "evidence": [item.to_dict() for item in self.evidence],
            "observed_at": _iso(self.observed_at),
            "environment_id": self.environment_id,
            "verification_status": self.verification_status.value,
            "contradiction_ids": [item.to_str() for item in self.contradiction_ids],
            "supersedes_ids": [item.to_str() for item in self.supersedes_ids],
            "durable": self.durable,
        }


class WorldHiveLinkage:
    """Durable links reuse KnowledgeStore; live world state remains separate."""

    def __init__(self, *, knowledge_store: KnowledgeStore | None = None, max_links: int = _MAX_LINKS) -> None:
        self._store, self._max = knowledge_store, max_links
        self._links: list[WorldHiveLink] = []
        self._lock = RLock()

    def add(self, link: WorldHiveLink) -> KnowledgeId | None:
        with self._lock:
            if link in self._links:
                return None
            if len(self._links) >= self._max:
                raise WorldModelValidationError("world/Hive link set is full")
        if not link.durable:
            with self._lock:
                self._links.append(link)
            return None
        if self._store is None:
            raise WorldModelValidationError("durable link requires canonical KnowledgeStore")
        record = KnowledgeRecord.create(
            knowledge_type=KnowledgeType.OBSERVATION,
            content=json.dumps(link.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            scope=KnowledgeScope(dimensions={ScopeDimension.ENVIRONMENT: link.environment_id}),
            provenance=ProvenanceReference(kind=ProvenanceKind.DERIVED, reference=_WORLD_LINK_PROVENANCE),
            created_at=link.observed_at,
        )
        self._store.insert(record)
        with self._lock:
            self._links.append(link)
        return record.knowledge_id

    def load_durable(self) -> tuple[WorldHiveLink, ...]:
        if self._store is None:
            return ()
        loaded: list[WorldHiveLink] = []
        for record in self._store.list_records():
            provenance = record.provenance
            if provenance is None or provenance.kind is not ProvenanceKind.DERIVED or provenance.reference != _WORLD_LINK_PROVENANCE:
                continue
            loaded.append(self._decode(record.content))
        with self._lock:
            for link in loaded:
                if link not in self._links:
                    if len(self._links) >= self._max:
                        raise WorldModelValidationError("world/Hive link set is full")
                    self._links.append(link)
        return tuple(sorted(loaded, key=lambda link: (link.world_entity_id, link.observed_at, link.knowledge_id.to_str())))

    def links_for(self, entity_id: WorldEntityId) -> tuple[WorldHiveLink, ...]:
        with self._lock:
            values = [link for link in self._links if link.world_entity_id == entity_id]
        return tuple(sorted(values, key=lambda link: (link.observed_at, link.knowledge_id.to_str())))

    @staticmethod
    def _decode(content: str) -> WorldHiveLink:
        try:
            raw: object = json.loads(content)
        except json.JSONDecodeError as exc:
            raise WorldModelValidationError("stored world/Hive link is malformed") from exc
        if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
            raise WorldModelValidationError("stored world/Hive link schema is malformed")
        entity_raw, evidence_raw = raw.get("world_entity"), raw.get("evidence")
        if not isinstance(entity_raw, Mapping) or not isinstance(evidence_raw, list):
            raise WorldModelValidationError("stored world/Hive link is malformed")
        evidence: list[EvidenceReference] = []
        for item in evidence_raw:
            if not isinstance(item, Mapping):
                raise WorldModelValidationError("stored link evidence is malformed")
            evidence.append(EvidenceReference.from_dict(dict(item)))
        knowledge_raw, status_raw, environment_id = raw.get("knowledge_id"), raw.get("verification_status"), raw.get("environment_id")
        if not isinstance(knowledge_raw, str) or not isinstance(status_raw, str) or not isinstance(environment_id, str):
            raise WorldModelValidationError("stored world/Hive identity is malformed")
        contradictions, supersedes = raw.get("contradiction_ids", []), raw.get("supersedes_ids", [])
        if not isinstance(contradictions, list) or not isinstance(supersedes, list):
            raise WorldModelValidationError("stored world/Hive relationships are malformed")
        durable = raw.get("durable")
        if type(durable) is not bool:
            raise WorldModelValidationError("stored world/Hive durable flag is malformed")
        try:
            return WorldHiveLink(
                world_entity_id=WorldEntityId.from_dict(dict(entity_raw)),
                knowledge_id=KnowledgeId.parse(knowledge_raw),
                evidence=tuple(evidence),
                observed_at=_parse_time(raw.get("observed_at"), "observed_at"),
                environment_id=environment_id,
                verification_status=LinkVerificationStatus(status_raw),
                contradiction_ids=tuple(KnowledgeId.parse(str(item)) for item in contradictions),
                supersedes_ids=tuple(KnowledgeId.parse(str(item)) for item in supersedes),
                durable=durable,
            )
        except ValueError as exc:
            raise WorldModelValidationError("stored world/Hive identity is malformed") from exc


def normalize_filesystem_path(path: str, *, windows: bool) -> str:
    checked = _text(path, "path", max_length=4096)
    normalized = ntpath.normpath(checked) if windows else os.path.normpath(checked)
    absolute = ntpath.isabs(normalized) if windows else os.path.isabs(normalized)
    if not absolute:
        raise WorldModelValidationError("filesystem context requires an absolute path")
    return ntpath.normcase(normalized) if windows else normalized


class _FilesystemProvider:
    def __init__(self, paths: Mapping[WorldEntityId, str], *, source: ProvenanceReference, ttl: timedelta, task_id: TaskId | None, correlation_id: UUID | None) -> None:
        self.paths, self.source, self.ttl = dict(paths), source, ttl
        self.task_id, self.correlation_id = task_id, correlation_id

    def observe(self, entity_id: WorldEntityId) -> WorldStateValue | None:
        path = self.paths.get(entity_id)
        if path is None:
            return None
        now = datetime.now(UTC)
        metadata = ObservationMetadata(f"filesystem:{entity_id.value}:{now.timestamp()}", self.source, now, self.ttl, entity_id.environment_id, task_id=self.task_id, correlation_id=self.correlation_id)
        try:
            result = Path(path).stat()
        except FileNotFoundError:
            return FilesystemState(entity_id, entity_id.value, path, FilesystemExistence.MISSING, FilesystemEntityType.UNKNOWN, None, None, True, metadata)
        except (PermissionError, OSError):
            return FilesystemState(entity_id, entity_id.value, path, FilesystemExistence.INACCESSIBLE, FilesystemEntityType.UNKNOWN, None, None, True, metadata)
        entity_type = FilesystemEntityType.FILE if stat.S_ISREG(result.st_mode) else FilesystemEntityType.DIRECTORY if stat.S_ISDIR(result.st_mode) else FilesystemEntityType.OTHER
        return FilesystemState(entity_id, entity_id.value, path, FilesystemExistence.EXISTS, entity_type, result.st_size, result.st_mtime_ns, True, metadata)


@dataclass(frozen=True, slots=True)
class LocalWindowsDeviceObservation:
    entity_id: WorldEntityId
    state: DeviceState


class WorldModel:
    """Canonical outer composition for AX-409/411/414-421."""

    def __init__(self, *, knowledge_store: KnowledgeStore | None = None) -> None:
        self.cache = LazyWorldStateCache()
        self.applications = ApplicationRegistry()
        self.tasks = TaskWorldBinder()
        self.hive = WorldHiveLinkage(knowledge_store=knowledge_store)
        self._filesystem_paths: dict[WorldEntityId, str] = {}
        self._lock = RLock()

    @staticmethod
    def active_window_id(environment_id: str) -> WorldEntityId:
        return WorldEntityId(environment_id, WorldEntityKind.WINDOW, ACTIVE_WINDOW_KEY)

    def ingest_windows_snapshot(self, snapshot: WindowsProcessSnapshot, *, metadata: ObservationMetadata, application_by_pid: Mapping[int, ApplicationIdentity] | None = None, foreground_handle: int | None = None) -> tuple[tuple[ProcessState, ...], tuple[WindowState, ...]]:
        applications = {} if application_by_pid is None else dict(application_by_pid)
        process_ids = {
            process.process_id: WorldEntityId(metadata.environment_id, WorldEntityKind.PROCESS, f"pid:{process.process_id}|image:{process.executable_path or process.executable_name or 'unknown'}")
            for process in snapshot.processes
        }
        window_ids = {window.handle: WorldEntityId(metadata.environment_id, WorldEntityKind.WINDOW, f"hwnd:{window.handle}|pid:{window.process_id}") for window in snapshot.windows}
        windows: list[WindowState] = []
        for window in snapshot.windows:
            app = applications.get(window.process_id)
            state = WindowState(
                window_ids[window.handle], window.handle, process_ids.get(window.process_id),
                None if app is None else app.entity_id, window.title, None, window.is_visible,
                WindowShowState.UNKNOWN, None if foreground_handle is None else window.handle == foreground_handle,
                WorldAvailability.AVAILABLE, metadata,
            )
            self.cache.put(state)
            windows.append(state)
        processes: list[ProcessState] = []
        for process in snapshot.processes:
            app = applications.get(process.process_id)
            state = ProcessState(
                process_ids[process.process_id], process.process_id, process.executable_name,
                process.executable_path, process.parent_process_id, None if app is None else app.entity_id,
                tuple(window_ids[handle] for handle in process.window_handles if handle in window_ids),
                WorldAvailability.AVAILABLE, metadata,
            )
            self.cache.put(state)
            processes.append(state)
        for app in sorted(set(applications.values())):
            if self.applications.get(app) is None:
                continue
            updated = self.applications.update_relationships(
                app,
                process_ids=tuple(state.entity_id for state in processes if state.application_id == app.entity_id),
                window_ids=tuple(state.entity_id for state in windows if state.application_id == app.entity_id),
                metadata=metadata,
            )
            self.cache.put(updated)
        active_id = self.active_window_id(metadata.environment_id)
        if foreground_handle is not None and foreground_handle in window_ids:
            selected = next(state for state in windows if state.handle == foreground_handle)
            self.cache.put(replace(selected, entity_id=active_id, is_foreground=True))
        else:
            self.cache.invalidate(active_id, reason="foreground window not trustworthily observed")
        return tuple(processes), tuple(windows)

    def ingest_browser_observation(self, observation: BrowserDomObservation, *, metadata: ObservationMetadata, active: bool, application_id: WorldEntityId | None = None, process_id: WorldEntityId | None = None) -> BrowserPageState:
        target: BrowserTargetRef = observation.target
        provider = target.provider_id.value
        session_id = WorldEntityId(metadata.environment_id, WorldEntityKind.BROWSER_SESSION, f"provider:{provider}|session:{target.session_id.value}")
        page_id = WorldEntityId(metadata.environment_id, WorldEntityKind.BROWSER_PAGE, f"provider:{provider}|session:{target.session_id.value}|target:{target.target_id.value}")
        old = self.cache.lookup(page_id, at=metadata.observed_at, refresh=False).value
        if isinstance(old, BrowserPageState) and (old.url != target.url or old.document_version != observation.document_version):
            self.cache.invalidate(page_id, reason="browser navigation/document replacement")
        parsed = None if target.url is None else urlsplit(target.url)
        origin = None if parsed is None or not parsed.scheme or not parsed.netloc else f"{parsed.scheme}://{parsed.netloc}"
        available = target.state.value == "available" and observation.state is BrowserDomObservationState.OBSERVED
        availability = WorldAvailability.AVAILABLE if available else WorldAvailability.UNAVAILABLE
        page = BrowserPageState(
            page_id, session_id, target.target_id.value, target.title, target.url, origin,
            observation.document_version, f"dom:{target.target_id.value}:{observation.document_version or 'unknown'}:{_iso(observation.observed_at)}",
            active, availability, metadata,
        )
        session = BrowserSessionState(session_id, provider, target.session_id.value, availability, page_id if active else None, application_id, process_id, metadata)
        self.cache.put(session)
        self.cache.put(page)
        return page

    def close_browser_page(self, page_id: WorldEntityId) -> None:
        if page_id.kind is not WorldEntityKind.BROWSER_PAGE:
            raise WorldModelValidationError("page_id must identify a browser page")
        self.cache.invalidate(page_id, reason="browser page closed")
        self.tasks.invalidate_entities((page_id,))

    def ingest_device_descriptor(self, descriptor: DeviceDescriptor, *, metadata: ObservationMetadata, role: str) -> DeviceState:
        entity_id = WorldEntityId(metadata.environment_id, WorldEntityKind.DEVICE, f"provider:{descriptor.provider_id}|device:{descriptor.device_id.value}")
        availability = WorldAvailability.AVAILABLE if descriptor.availability is DeviceAvailability.AVAILABLE else WorldAvailability.UNAVAILABLE
        health = tuple(sorted((str(item.capability), item.availability.value) for item in descriptor.capabilities))
        state = DeviceState(entity_id, descriptor.platform.value, role, availability, health, descriptor.last_seen, metadata)
        self.cache.put(state)
        return state

    def observe_local_windows_device(self, *, environment_id: str, source: ProvenanceReference, ttl: timedelta, at: datetime) -> LocalWindowsDeviceObservation:
        moment = _time(at, "at")
        host = platform.node().strip() or "local-host"
        entity_id = WorldEntityId(environment_id, WorldEntityKind.DEVICE, f"local:{host.casefold()}")
        metadata = ObservationMetadata(f"local-device:{host}:{_iso(moment)}", source, moment, ttl, environment_id)
        state = DeviceState(entity_id, DevicePlatform.WINDOWS.value, "local_host", WorldAvailability.AVAILABLE if os.name == "nt" else WorldAvailability.UNAVAILABLE, (), moment, metadata)
        self.cache.put(state)
        return LocalWindowsDeviceObservation(entity_id, state)

    def track_filesystem_path(self, path: str, *, environment_id: str, windows: bool, source: ProvenanceReference, ttl: timedelta, task_id: TaskId | None = None, correlation_id: UUID | None = None) -> WorldEntityId:
        normalized = normalize_filesystem_path(path, windows=windows)
        entity_id = WorldEntityId(environment_id, WorldEntityKind.FILESYSTEM, normalized)
        with self._lock:
            self._filesystem_paths[entity_id] = path
            self.cache.register_provider(WorldEntityKind.FILESYSTEM, _FilesystemProvider(self._filesystem_paths, source=source, ttl=ttl, task_id=task_id, correlation_id=correlation_id))
        return entity_id

    def invalidate_filesystem_path(self, entity_id: WorldEntityId, *, reason: str) -> None:
        if entity_id.kind is not WorldEntityKind.FILESYSTEM:
            raise WorldModelValidationError("entity_id must identify filesystem context")
        self.cache.invalidate(entity_id, reason=reason)
        self.tasks.invalidate_entities((entity_id,))

    def invalidate_active_window(self, *, environment_id: str, reason: str) -> None:
        entity_id = self.active_window_id(environment_id)
        self.cache.invalidate(entity_id, reason=reason)
        self.tasks.invalidate_entities((entity_id,))

    def handle_event(self, event: Event) -> None:
        if event.event_type in (EventType.TASK_COMPLETED, EventType.TASK_FAILED):
            if event.task_id is not None:
                try:
                    self.tasks.complete_task(TaskId.parse(event.task_id))
                except ValueError:
                    pass
            return
        if event.event_type is not EventType.ACTION_COMPLETED or not isinstance(event.payload, ActionPayload):
            return
        capability = event.payload.name.partition("@")[0]
        if capability == "filesystem.write_text" and event.task_id is not None:
            try:
                task_id = TaskId.parse(event.task_id)
            except ValueError:
                return
            binding = self.tasks.get(task_id)
            if binding is not None:
                for entity_id in binding.entity_ids:
                    if entity_id.kind is WorldEntityKind.FILESYSTEM:
                        self.cache.invalidate(entity_id, reason="governed filesystem mutation")
        elif capability in {"windows.window.activate", "windows.window_management.activate"}:
            environments = {item.environment_id for item in self.cache.snapshot_ids() if item.kind is WorldEntityKind.WINDOW}
            for environment_id in environments:
                self.invalidate_active_window(environment_id=environment_id, reason="focus-changing action completed")
        elif capability.startswith("browser."):
            self.cache.invalidate_where(lambda entity_id, _value: entity_id.kind is WorldEntityKind.BROWSER_PAGE, reason="browser action completed")

    def task_context(self, task_id: TaskId, *, at: datetime) -> tuple[CacheLookup, ...]:
        return self.tasks.assemble(task_id, cache=self.cache, at=at)


__all__ = [
    "ACTIVE_WINDOW_KEY", "ApplicationIdentity", "ApplicationRecord", "ApplicationRegistry",
    "ApplicationRegistryConflictError", "ApplicationRegistryLimitError", "BrowserPageState",
    "BrowserSessionState", "CacheLookup", "CacheRefreshState", "DeviceState",
    "FilesystemEntityType", "FilesystemExistence", "FilesystemState", "LazyWorldStateCache",
    "LinkVerificationStatus", "LocalWindowsDeviceObservation", "ObservationMetadata",
    "PerceptionObservation", "PerceptionRegion", "ProcessState", "ScreenBounds", "TaskWorldBinder",
    "TaskWorldBinding", "WindowShowState", "WindowState", "WorldAvailability", "WorldEntityId",
    "WorldEntityKind", "WorldFreshness", "WorldHiveLink", "WorldHiveLinkage", "WorldModel",
    "WorldModelError", "WorldModelValidationError", "WorldStateProvider", "normalize_filesystem_path",
]
