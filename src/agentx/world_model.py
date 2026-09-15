"""Canonical M10 world-model composition for AgentX.

This outer composition layer deliberately sits outside ``agentx.core``.  The
existing M6.01 ``core.world_state`` module remains the inert provider-neutral
snapshot envelope; this module composes provider observations into a lazy,
evidence-backed, freshness-aware runtime view.

World state is data, never authority.  Nothing here grants permissions,
changes risk, bypasses the Action Gate, clears the Emergency Stop, executes a
capability, or marks a Task successful.  Observation is also not verification.
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
from types import MappingProxyType
from typing import Final, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from agentx.capabilities.browser_connection import BrowserTargetRef
from agentx.capabilities.browser_dom import BrowserDomObservation
from agentx.capabilities.device import (
    DeviceAvailability,
    DeviceDescriptor,
    DevicePlatform,
)
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

__all__ = [
    "ACTIVE_WINDOW_KEY",
    "ApplicationIdentity",
    "ApplicationRecord",
    "ApplicationRegistry",
    "ApplicationRegistryConflictError",
    "ApplicationRegistryLimitError",
    "BrowserPageState",
    "BrowserSessionState",
    "CacheLookup",
    "CacheRefreshState",
    "DeviceState",
    "FilesystemEntityType",
    "FilesystemExistence",
    "FilesystemState",
    "LazyWorldStateCache",
    "LinkVerificationStatus",
    "LocalWindowsDeviceObservation",
    "ObservationMetadata",
    "PerceptionObservation",
    "PerceptionRegion",
    "ProcessState",
    "ScreenBounds",
    "TaskWorldBinder",
    "TaskWorldBinding",
    "WindowShowState",
    "WindowState",
    "WorldAvailability",
    "WorldEntityId",
    "WorldEntityKind",
    "WorldFreshness",
    "WorldHiveLink",
    "WorldHiveLinkage",
    "WorldModel",
    "WorldModelError",
    "WorldModelValidationError",
    "WorldStateProvider",
    "normalize_filesystem_path",
]

_WORLD_LINK_PROVENANCE: Final[str] = "agentx.world_model.link/v1"
_WORLD_LINK_SCHEMA_VERSION: Final[int] = 1
_MAX_TEXT: Final[int] = 4096
_MAX_IDENTIFIER: Final[int] = 1024
_MAX_REGIONS: Final[int] = 512
_MAX_REGION_TEXT: Final[int] = 16_384
_MAX_STRUCTURED_REFS: Final[int] = 128
_MAX_CACHE_ENTRIES: Final[int] = 1024
_MAX_APPLICATIONS: Final[int] = 256
_MAX_ALIASES_PER_APPLICATION: Final[int] = 32
_MAX_RELATIONSHIPS_PER_APPLICATION: Final[int] = 256
_MAX_TASK_ENTITIES: Final[int] = 256
_MAX_TASK_BINDINGS: Final[int] = 1024
_MAX_LINKS: Final[int] = 4096


class WorldModelError(ValueError):
    """Base error for M10 world-model contract violations."""


class WorldModelValidationError(WorldModelError):
    """Raised when runtime world-state data violates a typed contract."""


class ApplicationRegistryConflictError(WorldModelError):
    """Raised when a stable application identity is registered incompatibly."""


class ApplicationRegistryLimitError(WorldModelError):
    """Raised when a bounded application-registry limit would be exceeded."""


class WorldFreshness(StrEnum):
    """Current evidentiary freshness; never a truth or verification verdict."""

    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class WorldEntityKind(StrEnum):
    """Closed world-entity vocabulary for the M10 package."""

    PERCEPTION = "perception"
    APPLICATION = "application"
    PROCESS = "process"
    WINDOW = "window"
    BROWSER_SESSION = "browser_session"
    BROWSER_PAGE = "browser_page"
    FILESYSTEM = "filesystem"
    DEVICE = "device"


class WorldAvailability(StrEnum):
    """Observed entity availability; descriptive only."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class WindowShowState(StrEnum):
    """Observed window show state when a provider exposes it."""

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
    """Outcome of an explicit cache lookup/refresh attempt."""

    CACHE_MISS = "cache_miss"
    FRESH_HIT = "fresh_hit"
    STALE_HIT = "stale_hit"
    INVALIDATED = "invalidated"
    REFRESH_SUCCESS = "refresh_success"
    REFRESH_FAILURE = "refresh_failure"
    SOURCE_UNAVAILABLE = "source_unavailable"


class LinkVerificationStatus(StrEnum):
    """Verification state of a world/Hive relationship, not authority."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    CONTRADICTED = "contradicted"
    SUPERSEDED = "superseded"


def _validate_text(
    value: object,
    *,
    field_name: str,
    max_length: int = _MAX_TEXT,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if (not allow_empty and not value) or value != value.strip():
        raise WorldModelValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > max_length:
        raise WorldModelValidationError(f"{field_name} must not exceed {max_length} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise WorldModelValidationError(f"{field_name} must not contain control characters")
    return value


def _validate_untrusted_text(
    value: object,
    *,
    field_name: str,
    max_length: int = _MAX_REGION_TEXT,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None")
    if len(value) > max_length:
        raise WorldModelValidationError(f"{field_name} must not exceed {max_length} characters")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise WorldModelValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_ttl(value: object) -> timedelta:
    if not isinstance(value, timedelta):
        raise TypeError("ttl must be a timedelta")
    if value <= timedelta(0):
        raise WorldModelValidationError("ttl must be strictly positive")
    return value


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise WorldModelValidationError(f"{field_name} must be an ISO-8601 string")
    text = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WorldModelValidationError(f"{field_name} is not a valid ISO-8601 timestamp") from exc
    return _validate_timestamp(parsed, field_name=field_name)


def _validate_uuid(value: object, *, field_name: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID or None")
    if value.int == 0:
        raise WorldModelValidationError(f"{field_name} must not be the nil UUID")
    return value


def _sorted_unique_text(values: tuple[str, ...], *, field_name: str, limit: int) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise TypeError(f"{field_name} must be a tuple")
    if len(values) > limit:
        raise WorldModelValidationError(f"{field_name} exceeds limit {limit}")
    checked = tuple(
        _validate_text(item, field_name=f"{field_name} entry", max_length=_MAX_IDENTIFIER)
        for item in values
    )
    if len(set(checked)) != len(checked):
        raise WorldModelValidationError(f"{field_name} must not contain duplicates")
    return tuple(sorted(checked))


@dataclass(frozen=True, slots=True, order=True)
class WorldEntityId:
    """Environment-scoped stable identity; display strings are never identity."""

    environment_id: str
    kind: WorldEntityKind
    value: str

    def __post_init__(self) -> None:
        _validate_text(self.environment_id, field_name="environment_id", max_length=_MAX_IDENTIFIER)
        if not isinstance(self.kind, WorldEntityKind):
            raise TypeError("kind must be a WorldEntityKind")
        _validate_text(self.value, field_name="entity identity", max_length=_MAX_IDENTIFIER)

    def to_str(self) -> str:
        return f"{self.environment_id}:{self.kind.value}:{self.value}"

    def to_dict(self) -> dict[str, str]:
        return {
            "environment_id": self.environment_id,
            "kind": self.kind.value,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> WorldEntityId:
        if set(raw) != {"environment_id", "kind", "value"}:
            raise WorldModelValidationError("world entity identity fields are malformed")
        environment_id = raw["environment_id"]
        kind_raw = raw["kind"]
        value = raw["value"]
        if (
            not isinstance(environment_id, str)
            or not isinstance(kind_raw, str)
            or not isinstance(value, str)
        ):
            raise WorldModelValidationError("world entity identity fields must be strings")
        try:
            kind = WorldEntityKind(kind_raw)
        except ValueError as exc:
            raise WorldModelValidationError(f"unknown world entity kind: {kind_raw!r}") from exc
        return cls(environment_id=environment_id, kind=kind, value=value)


ACTIVE_WINDOW_KEY: Final[str] = "__active_window__"


@dataclass(frozen=True, slots=True)
class ObservationMetadata:
    """Evidence/freshness envelope shared by current-state models."""

    observation_id: str
    source: ProvenanceReference
    observed_at: datetime
    ttl: timedelta
    environment_id: str
    device_id: WorldEntityId | None = None
    task_id: TaskId | None = None
    correlation_id: UUID | None = None

    def __post_init__(self) -> None:
        _validate_text(
            self.observation_id,
            field_name="observation_id",
            max_length=_MAX_IDENTIFIER,
        )
        if not isinstance(self.source, ProvenanceReference):
            raise TypeError("source must be a ProvenanceReference")
        object.__setattr__(
            self,
            "observed_at",
            _validate_timestamp(self.observed_at, field_name="observed_at"),
        )
        object.__setattr__(self, "ttl", _validate_ttl(self.ttl))
        _validate_text(self.environment_id, field_name="environment_id", max_length=_MAX_IDENTIFIER)
        if self.device_id is not None:
            if not isinstance(self.device_id, WorldEntityId):
                raise TypeError("device_id must be a WorldEntityId or None")
            if self.device_id.kind is not WorldEntityKind.DEVICE:
                raise WorldModelValidationError("device_id must identify a DEVICE entity")
            if self.device_id.environment_id != self.environment_id:
                raise WorldModelValidationError("device_id must be in the same environment")
        if self.task_id is not None and not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be a TaskId or None")
        _validate_uuid(self.correlation_id, field_name="correlation_id")

    @property
    def expires_at(self) -> datetime:
        return self.observed_at + self.ttl

    def freshness(self, at: datetime, *, invalidated: bool = False) -> WorldFreshness:
        moment = _validate_timestamp(at, field_name="at")
        if invalidated:
            return WorldFreshness.STALE
        return WorldFreshness.FRESH if moment < self.expires_at else WorldFreshness.STALE

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id,
            "source": self.source.to_dict(),
            "observed_at": _format_timestamp(self.observed_at),
            "ttl_microseconds": int(self.ttl / timedelta(microseconds=1)),
            "environment_id": self.environment_id,
            "device_id": None if self.device_id is None else self.device_id.to_dict(),
            "task_id": None if self.task_id is None else self.task_id.to_str(),
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ObservationMetadata:
        expected = {
            "observation_id",
            "source",
            "observed_at",
            "ttl_microseconds",
            "environment_id",
            "device_id",
            "task_id",
            "correlation_id",
        }
        if set(raw) != expected:
            raise WorldModelValidationError("observation metadata fields are malformed")
        source_raw = raw["source"]
        if not isinstance(source_raw, Mapping):
            raise WorldModelValidationError("metadata source must be an object")
        source_copy: dict[str, object] = {}
        for key, value in source_raw.items():
            if not isinstance(key, str):
                raise WorldModelValidationError("metadata source keys must be strings")
            source_copy[key] = value
        source = ProvenanceReference.from_dict(source_copy)
        ttl_raw = raw["ttl_microseconds"]
        if type(ttl_raw) is not int or ttl_raw <= 0:
            raise WorldModelValidationError("ttl_microseconds must be a positive int")
        environment_id = raw["environment_id"]
        observation_id = raw["observation_id"]
        if not isinstance(environment_id, str) or not isinstance(observation_id, str):
            raise WorldModelValidationError("metadata identity fields must be strings")
        device_raw = raw["device_id"]
        if device_raw is None:
            device_id = None
        elif isinstance(device_raw, Mapping):
            device_copy: dict[str, object] = {}
            for key, value in device_raw.items():
                if not isinstance(key, str):
                    raise WorldModelValidationError("device identity keys must be strings")
                device_copy[key] = value
            device_id = WorldEntityId.from_dict(device_copy)
        else:
            raise WorldModelValidationError("device_id must be an object or null")
        task_raw = raw["task_id"]
        if task_raw is None:
            task_id = None
        elif isinstance(task_raw, str):
            try:
                task_id = TaskId.parse(task_raw)
            except ValueError as exc:
                raise WorldModelValidationError("task_id is invalid") from exc
        else:
            raise WorldModelValidationError("task_id must be a string or null")
        correlation_raw = raw["correlation_id"]
        if correlation_raw is None:
            correlation_id = None
        elif isinstance(correlation_raw, str):
            try:
                correlation_id = UUID(correlation_raw)
            except ValueError as exc:
                raise WorldModelValidationError("correlation_id is invalid") from exc
        else:
            raise WorldModelValidationError("correlation_id must be a string or null")
        return cls(
            observation_id=observation_id,
            source=source,
            observed_at=_parse_timestamp(raw["observed_at"], field_name="observed_at"),
            ttl=timedelta(microseconds=ttl_raw),
            environment_id=environment_id,
            device_id=device_id,
            task_id=task_id,
            correlation_id=correlation_id,
        )


@dataclass(frozen=True, slots=True, order=True)
class ScreenBounds:
    """Integer surface-relative bounds. DPI/multi-monitor normalization is later scope."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("x", self.x),
            ("y", self.y),
            ("width", self.width),
            ("height", self.height),
        ):
            if type(value) is not int:
                raise TypeError(f"{field_name} must be an int")
        if self.width <= 0 or self.height <= 0:
            raise WorldModelValidationError("bounds width and height must be positive")
        if self.width > 1_000_000 or self.height > 1_000_000:
            raise WorldModelValidationError("bounds dimensions exceed the defensive limit")

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True, slots=True)
class PerceptionRegion:
    """Structured screen-derived region; observed text is inert untrusted data."""

    region_id: str
    bounds: ScreenBounds
    text: str | None = None
    role: str | None = None
    confidence: float | None = None
    structured_observation_ref: str | None = None

    def __post_init__(self) -> None:
        _validate_text(self.region_id, field_name="region_id", max_length=_MAX_IDENTIFIER)
        if not isinstance(self.bounds, ScreenBounds):
            raise TypeError("bounds must be ScreenBounds")
        _validate_untrusted_text(self.text, field_name="region text")
        _validate_untrusted_text(self.role, field_name="region role", max_length=_MAX_TEXT)
        if self.confidence is not None:
            if isinstance(self.confidence, bool) or not isinstance(self.confidence, int | float):
                raise TypeError("confidence must be a number or None")
            confidence = float(self.confidence)
            if confidence < 0.0 or confidence > 1.0:
                raise WorldModelValidationError("confidence must be within [0, 1]")
            object.__setattr__(self, "confidence", confidence)
        if self.structured_observation_ref is not None:
            _validate_text(
                self.structured_observation_ref,
                field_name="structured_observation_ref",
                max_length=_MAX_IDENTIFIER,
            )

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
    """AX-409 canonical typed representation; it performs no screen capture."""

    entity_id: WorldEntityId
    metadata: ObservationMetadata
    surface_id: str
    bounds: ScreenBounds
    regions: tuple[PerceptionRegion, ...] = ()
    structured_observation_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.entity_id, WorldEntityId):
            raise TypeError("entity_id must be WorldEntityId")
        if self.entity_id.kind is not WorldEntityKind.PERCEPTION:
            raise WorldModelValidationError("perception entity_id must use PERCEPTION kind")
        if not isinstance(self.metadata, ObservationMetadata):
            raise TypeError("metadata must be ObservationMetadata")
        if self.entity_id.environment_id != self.metadata.environment_id:
            raise WorldModelValidationError("perception identity and metadata environment differ")
        _validate_text(self.surface_id, field_name="surface_id", max_length=_MAX_IDENTIFIER)
        if not isinstance(self.bounds, ScreenBounds):
            raise TypeError("bounds must be ScreenBounds")
        if not isinstance(self.regions, tuple):
            raise TypeError("regions must be a tuple")
        if len(self.regions) > _MAX_REGIONS:
            raise WorldModelValidationError("too many perception regions")
        seen: set[str] = set()
        for region in self.regions:
            if not isinstance(region, PerceptionRegion):
                raise TypeError("regions must contain PerceptionRegion values")
            if region.region_id in seen:
                raise WorldModelValidationError("region identities must be unique")
            seen.add(region.region_id)
        object.__setattr__(
            self,
            "structured_observation_refs",
            _sorted_unique_text(
                self.structured_observation_refs,
                field_name="structured_observation_refs",
                limit=_MAX_STRUCTURED_REFS,
            ),
        )

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
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, raw: str) -> PerceptionObservation:
        if not isinstance(raw, str):
            raise TypeError("perception JSON must be a string")
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WorldModelValidationError("perception JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise WorldModelValidationError("perception JSON root must be an object")
        if decoded.get("schema_version") != 1:
            raise WorldModelValidationError("unsupported perception schema version")
        entity_raw = decoded.get("entity")
        metadata_raw = decoded.get("metadata")
        bounds_raw = decoded.get("bounds")
        regions_raw = decoded.get("regions")
        refs_raw = decoded.get("structured_observation_refs")
        surface_id = decoded.get("surface_id")
        if not isinstance(entity_raw, Mapping) or not isinstance(metadata_raw, Mapping):
            raise WorldModelValidationError("perception identity/metadata is malformed")
        if not isinstance(bounds_raw, Mapping) or not isinstance(regions_raw, list):
            raise WorldModelValidationError("perception bounds/regions are malformed")
        if not isinstance(refs_raw, list) or not isinstance(surface_id, str):
            raise WorldModelValidationError("perception surface/link data is malformed")
        entity = WorldEntityId.from_dict(dict(entity_raw))
        metadata = ObservationMetadata.from_dict(dict(metadata_raw))
        try:
            bounds = ScreenBounds(
                x=int(bounds_raw["x"]),
                y=int(bounds_raw["y"]),
                width=int(bounds_raw["width"]),
                height=int(bounds_raw["height"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise WorldModelValidationError("perception bounds are malformed") from exc
        regions: list[PerceptionRegion] = []
        for item in regions_raw:
            if not isinstance(item, Mapping):
                raise WorldModelValidationError("perception region must be an object")
            region_bounds = item.get("bounds")
            if not isinstance(region_bounds, Mapping):
                raise WorldModelValidationError("perception region bounds are malformed")
            try:
                rb = ScreenBounds(
                    x=int(region_bounds["x"]),
                    y=int(region_bounds["y"]),
                    width=int(region_bounds["width"]),
                    height=int(region_bounds["height"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise WorldModelValidationError("perception region bounds are malformed") from exc
            region_id = item.get("region_id")
            if not isinstance(region_id, str):
                raise WorldModelValidationError("perception region_id must be a string")
            text = item.get("text")
            role = item.get("role")
            confidence = item.get("confidence")
            structured_ref = item.get("structured_observation_ref")
            if text is not None and not isinstance(text, str):
                raise WorldModelValidationError("perception region text must be string/null")
            if role is not None and not isinstance(role, str):
                raise WorldModelValidationError("perception region role must be string/null")
            if structured_ref is not None and not isinstance(structured_ref, str):
                raise WorldModelValidationError("structured observation ref must be string/null")
            if confidence is not None and (
                isinstance(confidence, bool) or not isinstance(confidence, int | float)
            ):
                raise WorldModelValidationError("perception confidence must be numeric/null")
            regions.append(
                PerceptionRegion(
                    region_id=region_id,
                    bounds=rb,
                    text=text,
                    role=role,
                    confidence=None if confidence is None else float(confidence),
                    structured_observation_ref=structured_ref,
                )
            )
        if not all(isinstance(item, str) for item in refs_raw):
            raise WorldModelValidationError("structured observation refs must be strings")
        return cls(
            entity_id=entity,
            metadata=metadata,
            surface_id=surface_id,
            bounds=bounds,
            regions=tuple(regions),
            structured_observation_refs=tuple(refs_raw),
        )


@dataclass(frozen=True, slots=True, order=True)
class ApplicationIdentity:
    """Stable application identity independent of processes, windows and paths."""

    environment_id: str
    platform: str
    stable_id: str

    def __post_init__(self) -> None:
        _validate_text(self.environment_id, field_name="environment_id", max_length=_MAX_IDENTIFIER)
        _validate_text(self.platform, field_name="platform", max_length=128)
        _validate_text(self.stable_id, field_name="stable_id", max_length=_MAX_IDENTIFIER)

    @property
    def entity_id(self) -> WorldEntityId:
        return WorldEntityId(
            environment_id=self.environment_id,
            kind=WorldEntityKind.APPLICATION,
            value=self.stable_id,
        )


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
        _validate_text(self.canonical_name, field_name="canonical_name", max_length=_MAX_TEXT)
        _validate_untrusted_text(self.display_name, field_name="display_name", max_length=_MAX_TEXT)
        for field_name in ("executable_ids", "package_ids", "launch_targets", "aliases"):
            values = getattr(self, field_name)
            limit = _MAX_ALIASES_PER_APPLICATION if field_name == "aliases" else 64
            object.__setattr__(
                self,
                field_name,
                _sorted_unique_text(values, field_name=field_name, limit=limit),
            )
        if not isinstance(self.availability, WorldAvailability):
            raise TypeError("availability must be WorldAvailability")
        for field_name, expected_kind in (
            ("process_ids", WorldEntityKind.PROCESS),
            ("window_ids", WorldEntityKind.WINDOW),
        ):
            values = getattr(self, field_name)
            if not isinstance(values, tuple):
                raise TypeError(f"{field_name} must be a tuple")
            if len(values) > _MAX_RELATIONSHIPS_PER_APPLICATION:
                raise WorldModelValidationError(f"{field_name} is unbounded")
            if len(set(values)) != len(values):
                raise WorldModelValidationError(f"{field_name} must be unique")
            for entity_id in values:
                if not isinstance(entity_id, WorldEntityId) or entity_id.kind is not expected_kind:
                    raise WorldModelValidationError(f"{field_name} contains wrong entity kind")
                if entity_id.environment_id != self.identity.environment_id:
                    raise WorldModelValidationError(f"{field_name} crosses environment scope")
            object.__setattr__(self, field_name, tuple(sorted(values)))
        if not isinstance(self.metadata, ObservationMetadata):
            raise TypeError("metadata must be ObservationMetadata")
        if self.metadata.environment_id != self.identity.environment_id:
            raise WorldModelValidationError("application metadata has wrong environment")

    @property
    def entity_id(self) -> WorldEntityId:
        return self.identity.entity_id


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
        if self.entity_id.kind is not WorldEntityKind.PROCESS:
            raise WorldModelValidationError("process state requires PROCESS identity")
        if type(self.pid) is not int or self.pid < 0:
            raise WorldModelValidationError("pid must be a non-negative int")
        if self.parent_pid is not None and (
            type(self.parent_pid) is not int or self.parent_pid < 0
        ):
            raise WorldModelValidationError("parent_pid must be a non-negative int or None")
        _validate_untrusted_text(self.executable_name, field_name="executable_name", max_length=512)
        _validate_untrusted_text(
            self.executable_path, field_name="executable_path", max_length=4096
        )
        if (
            self.application_id is not None
            and self.application_id.kind is not WorldEntityKind.APPLICATION
        ):
            raise WorldModelValidationError("application_id must identify APPLICATION")
        for window_id in self.window_ids:
            if window_id.kind is not WorldEntityKind.WINDOW:
                raise WorldModelValidationError("window_ids must identify WINDOW entities")
        if len(set(self.window_ids)) != len(self.window_ids):
            raise WorldModelValidationError("window_ids must be unique")
        if not isinstance(self.availability, WorldAvailability):
            raise TypeError("availability must be WorldAvailability")
        if not isinstance(self.metadata, ObservationMetadata):
            raise TypeError("metadata must be ObservationMetadata")


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
            raise WorldModelValidationError("window state requires WINDOW identity")
        if self.handle is not None and (type(self.handle) is not int or self.handle < 1):
            raise WorldModelValidationError("handle must be a positive int or None")
        if self.process_id is not None and self.process_id.kind is not WorldEntityKind.PROCESS:
            raise WorldModelValidationError("process_id must identify PROCESS")
        if (
            self.application_id is not None
            and self.application_id.kind is not WorldEntityKind.APPLICATION
        ):
            raise WorldModelValidationError("application_id must identify APPLICATION")
        _validate_untrusted_text(self.title, field_name="window title", max_length=4096)
        if self.bounds is not None and not isinstance(self.bounds, ScreenBounds):
            raise TypeError("bounds must be ScreenBounds or None")
        if self.visible is not None and type(self.visible) is not bool:
            raise TypeError("visible must be bool or None")
        if not isinstance(self.show_state, WindowShowState):
            raise TypeError("show_state must be WindowShowState")
        if self.is_foreground is not None and type(self.is_foreground) is not bool:
            raise TypeError("is_foreground must be bool or None")
        if not isinstance(self.availability, WorldAvailability):
            raise TypeError("availability must be WorldAvailability")
        if not isinstance(self.metadata, ObservationMetadata):
            raise TypeError("metadata must be ObservationMetadata")


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
            raise WorldModelValidationError("browser session requires BROWSER_SESSION identity")
        _validate_text(self.provider_id, field_name="provider_id", max_length=_MAX_IDENTIFIER)
        _validate_text(self.session_id, field_name="session_id", max_length=_MAX_IDENTIFIER)
        if (
            self.active_page_id is not None
            and self.active_page_id.kind is not WorldEntityKind.BROWSER_PAGE
        ):
            raise WorldModelValidationError("active_page_id must identify BROWSER_PAGE")
        if (
            self.application_id is not None
            and self.application_id.kind is not WorldEntityKind.APPLICATION
        ):
            raise WorldModelValidationError("application_id must identify APPLICATION")
        if self.process_id is not None and self.process_id.kind is not WorldEntityKind.PROCESS:
            raise WorldModelValidationError("process_id must identify PROCESS")
        if not isinstance(self.availability, WorldAvailability):
            raise TypeError("availability must be WorldAvailability")
        if not isinstance(self.metadata, ObservationMetadata):
            raise TypeError("metadata must be ObservationMetadata")


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
        if self.entity_id.kind is not WorldEntityKind.BROWSER_PAGE:
            raise WorldModelValidationError("browser page requires BROWSER_PAGE identity")
        if self.session_id.kind is not WorldEntityKind.BROWSER_SESSION:
            raise WorldModelValidationError("session_id must identify BROWSER_SESSION")
        _validate_text(self.target_id, field_name="target_id", max_length=_MAX_IDENTIFIER)
        _validate_untrusted_text(self.title, field_name="browser title", max_length=4096)
        _validate_untrusted_text(self.url, field_name="browser url", max_length=16_384)
        _validate_untrusted_text(self.origin, field_name="browser origin", max_length=4096)
        if self.document_version is not None:
            _validate_text(
                self.document_version,
                field_name="document_version",
                max_length=_MAX_IDENTIFIER,
            )
        if self.dom_observation_ref is not None:
            _validate_text(
                self.dom_observation_ref,
                field_name="dom_observation_ref",
                max_length=_MAX_IDENTIFIER,
            )
        if type(self.active) is not bool:
            raise TypeError("active must be bool")
        if not isinstance(self.availability, WorldAvailability):
            raise TypeError("availability must be WorldAvailability")
        if not isinstance(self.metadata, ObservationMetadata):
            raise TypeError("metadata must be ObservationMetadata")


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
            raise WorldModelValidationError("filesystem state requires FILESYSTEM identity")
        _validate_text(self.normalized_path, field_name="normalized_path", max_length=4096)
        _validate_text(self.original_path, field_name="original_path", max_length=4096)
        if not isinstance(self.existence, FilesystemExistence):
            raise TypeError("existence must be FilesystemExistence")
        if not isinstance(self.entity_type, FilesystemEntityType):
            raise TypeError("entity_type must be FilesystemEntityType")
        if self.size_bytes is not None and (
            type(self.size_bytes) is not int or self.size_bytes < 0
        ):
            raise WorldModelValidationError("size_bytes must be a non-negative int or None")
        if self.modified_ns is not None and (
            type(self.modified_ns) is not int or self.modified_ns < 0
        ):
            raise WorldModelValidationError("modified_ns must be a non-negative int or None")
        if type(self.task_relevant) is not bool:
            raise TypeError("task_relevant must be bool")
        if not isinstance(self.metadata, ObservationMetadata):
            raise TypeError("metadata must be ObservationMetadata")


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
            raise WorldModelValidationError("device state requires DEVICE identity")
        _validate_text(self.platform, field_name="platform", max_length=128)
        _validate_text(self.role, field_name="role", max_length=128)
        if not isinstance(self.availability, WorldAvailability):
            raise TypeError("availability must be WorldAvailability")
        if not isinstance(self.capability_health, tuple):
            raise TypeError("capability_health must be a tuple")
        previous: tuple[str, str] | None = None
        for item in self.capability_health:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("capability_health entries must be (identity, state) tuples")
            identity, state = item
            _validate_text(identity, field_name="capability identity", max_length=_MAX_IDENTIFIER)
            _validate_text(state, field_name="capability health", max_length=128)
            if previous is not None and item <= previous:
                raise WorldModelValidationError("capability_health must be strictly sorted")
            previous = item
        object.__setattr__(
            self,
            "last_seen",
            _validate_timestamp(self.last_seen, field_name="last_seen"),
        )
        if not isinstance(self.metadata, ObservationMetadata):
            raise TypeError("metadata must be ObservationMetadata")


type WorldStateValue = (
    PerceptionObservation
    | ApplicationRecord
    | ProcessState
    | WindowState
    | BrowserSessionState
    | BrowserPageState
    | FilesystemState
    | DeviceState
)


def _metadata_of(value: WorldStateValue) -> ObservationMetadata:
    return value.metadata


class WorldStateProvider(Protocol):
    """On-demand structured observation provider; it grants no authority."""

    def observe(self, entity_id: WorldEntityId) -> WorldStateValue | None:
        """Return a current observation or ``None`` when the source is unavailable."""
        ...


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
    invalidated_reason: str | None = None


class LazyWorldStateCache:
    """Bounded, thread-safe, on-demand world cache with race-safe refresh."""

    def __init__(self, *, max_entries: int = _MAX_CACHE_ENTRIES) -> None:
        if type(max_entries) is not int or max_entries < 1:
            raise WorldModelValidationError("max_entries must be a positive int")
        self._max_entries = max_entries
        self._entries: dict[WorldEntityId, _CacheEntry] = {}
        self._providers: dict[WorldEntityKind, WorldStateProvider] = {}
        self._epochs: dict[WorldEntityId, int] = {}
        self._lock = RLock()

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def register_provider(self, kind: WorldEntityKind, provider: WorldStateProvider) -> None:
        if not isinstance(kind, WorldEntityKind):
            raise TypeError("kind must be WorldEntityKind")
        if not hasattr(provider, "observe"):
            raise TypeError("provider must implement observe(entity_id)")
        with self._lock:
            self._providers[kind] = provider

    def put(self, value: WorldStateValue) -> None:
        entity_id = value.entity_id
        if _metadata_of(value).environment_id != entity_id.environment_id:
            raise WorldModelValidationError("cache value crosses environment scope")
        with self._lock:
            if entity_id not in self._entries and len(self._entries) >= self._max_entries:
                self._evict_one_locked()
            self._entries[entity_id] = _CacheEntry(value=value)
            self._epochs[entity_id] = self._epochs.get(entity_id, 0) + 1

    def lookup(
        self, entity_id: WorldEntityId, *, at: datetime, refresh: bool = True
    ) -> CacheLookup:
        moment = _validate_timestamp(at, field_name="at")
        with self._lock:
            entry = self._entries.get(entity_id)
            provider = self._providers.get(entity_id.kind)
            if entry is not None:
                freshness = _metadata_of(entry.value).freshness(
                    moment,
                    invalidated=entry.invalidated,
                )
                if freshness is WorldFreshness.FRESH:
                    return CacheLookup(
                        entity_id,
                        WorldFreshness.FRESH,
                        CacheRefreshState.FRESH_HIT,
                        entry.value,
                    )
                stale_value = entry.value
                stale_state = (
                    CacheRefreshState.INVALIDATED
                    if entry.invalidated
                    else CacheRefreshState.STALE_HIT
                )
                if not refresh:
                    return CacheLookup(
                        entity_id,
                        WorldFreshness.STALE,
                        stale_state,
                        stale_value,
                        entry.invalidated_reason,
                    )
            else:
                stale_value = None
                stale_state = CacheRefreshState.CACHE_MISS
                if not refresh:
                    return CacheLookup(
                        entity_id,
                        WorldFreshness.UNKNOWN,
                        stale_state,
                        None,
                    )
            if provider is None:
                return CacheLookup(
                    entity_id,
                    WorldFreshness.STALE if stale_value is not None else WorldFreshness.UNKNOWN,
                    CacheRefreshState.SOURCE_UNAVAILABLE,
                    stale_value,
                    "no provider registered",
                )
            epoch = self._epochs.get(entity_id, 0)

        try:
            observed = provider.observe(entity_id)
        except Exception as exc:  # provider failures become explicit cache state
            return CacheLookup(
                entity_id,
                WorldFreshness.STALE if stale_value is not None else WorldFreshness.UNKNOWN,
                CacheRefreshState.REFRESH_FAILURE,
                stale_value,
                f"{type(exc).__name__}: {exc}",
            )

        if observed is None:
            return CacheLookup(
                entity_id,
                WorldFreshness.STALE if stale_value is not None else WorldFreshness.UNKNOWN,
                CacheRefreshState.SOURCE_UNAVAILABLE,
                stale_value,
                "provider returned no observation",
            )
        if observed.entity_id != entity_id:
            return CacheLookup(
                entity_id,
                WorldFreshness.STALE if stale_value is not None else WorldFreshness.UNKNOWN,
                CacheRefreshState.REFRESH_FAILURE,
                stale_value,
                "provider returned the wrong entity identity",
            )

        with self._lock:
            if self._epochs.get(entity_id, 0) != epoch:
                current = self._entries.get(entity_id)
                current_value = None if current is None else current.value
                return CacheLookup(
                    entity_id,
                    WorldFreshness.STALE if current_value is not None else WorldFreshness.UNKNOWN,
                    CacheRefreshState.REFRESH_FAILURE,
                    current_value,
                    "refresh raced with invalidation/update; observation discarded",
                )
            if entity_id not in self._entries and len(self._entries) >= self._max_entries:
                self._evict_one_locked()
            self._entries[entity_id] = _CacheEntry(value=observed)
            self._epochs[entity_id] = epoch + 1
        freshness = _metadata_of(observed).freshness(moment)
        return CacheLookup(
            entity_id,
            freshness,
            CacheRefreshState.REFRESH_SUCCESS,
            observed,
        )

    def invalidate(self, entity_id: WorldEntityId, *, reason: str) -> bool:
        checked_reason = _validate_text(reason, field_name="invalidation reason", max_length=512)
        with self._lock:
            self._epochs[entity_id] = self._epochs.get(entity_id, 0) + 1
            entry = self._entries.get(entity_id)
            if entry is None:
                return False
            entry.invalidated = True
            entry.invalidated_reason = checked_reason
            return True

    def invalidate_where(
        self,
        predicate: Callable[[WorldEntityId, WorldStateValue], bool],
        *,
        reason: str,
    ) -> tuple[WorldEntityId, ...]:
        checked_reason = _validate_text(reason, field_name="invalidation reason", max_length=512)
        invalidated: list[WorldEntityId] = []
        with self._lock:
            for entity_id in sorted(self._entries):
                entry = self._entries[entity_id]
                if predicate(entity_id, entry.value):
                    entry.invalidated = True
                    entry.invalidated_reason = checked_reason
                    self._epochs[entity_id] = self._epochs.get(entity_id, 0) + 1
                    invalidated.append(entity_id)
        return tuple(invalidated)

    def cleanup_stale(self, *, at: datetime) -> int:
        moment = _validate_timestamp(at, field_name="at")
        removed = 0
        with self._lock:
            stale_ids = [
                entity_id
                for entity_id, entry in self._entries.items()
                if _metadata_of(entry.value).freshness(moment, invalidated=entry.invalidated)
                is WorldFreshness.STALE
            ]
            for entity_id in stale_ids:
                del self._entries[entity_id]
                self._epochs[entity_id] = self._epochs.get(entity_id, 0) + 1
                removed += 1
        return removed

    def snapshot_ids(self) -> tuple[WorldEntityId, ...]:
        with self._lock:
            return tuple(sorted(self._entries))

    def _evict_one_locked(self) -> None:
        if not self._entries:
            return
        victim = min(
            self._entries,
            key=lambda entity_id: (
                _metadata_of(self._entries[entity_id].value).observed_at,
                entity_id,
            ),
        )
        del self._entries[victim]
        self._epochs[victim] = self._epochs.get(victim, 0) + 1


class ApplicationRegistry:
    """Bounded application registry; metadata never becomes launch authority."""

    def __init__(self, *, max_applications: int = _MAX_APPLICATIONS) -> None:
        if type(max_applications) is not int or max_applications < 1:
            raise WorldModelValidationError("max_applications must be a positive int")
        self._max_applications = max_applications
        self._records: dict[ApplicationIdentity, ApplicationRecord] = {}
        self._lock = RLock()

    def register(self, record: ApplicationRecord) -> ApplicationRecord:
        if not isinstance(record, ApplicationRecord):
            raise TypeError("record must be ApplicationRecord")
        with self._lock:
            existing = self._records.get(record.identity)
            if existing is None and len(self._records) >= self._max_applications:
                raise ApplicationRegistryLimitError("application registry is full")
            if existing is not None and existing.canonical_name != record.canonical_name:
                raise ApplicationRegistryConflictError(
                    "stable application identity conflicts on canonical_name"
                )
            self._records[record.identity] = record
            return record

    def get(self, identity: ApplicationIdentity) -> ApplicationRecord | None:
        with self._lock:
            return self._records.get(identity)

    def resolve_alias(self, *, environment_id: str, alias: str) -> tuple[ApplicationRecord, ...]:
        needle = _validate_text(alias, field_name="alias", max_length=_MAX_TEXT).casefold()
        with self._lock:
            matches = [
                record
                for record in self._records.values()
                if record.identity.environment_id == environment_id
                and needle
                in {
                    record.canonical_name.casefold(),
                    record.display_name.casefold(),
                    *(item.casefold() for item in record.aliases),
                }
            ]
        return tuple(sorted(matches, key=lambda item: item.identity))

    def update_relationships(
        self,
        identity: ApplicationIdentity,
        *,
        process_ids: tuple[WorldEntityId, ...] | None = None,
        window_ids: tuple[WorldEntityId, ...] | None = None,
        availability: WorldAvailability | None = None,
        metadata: ObservationMetadata | None = None,
    ) -> ApplicationRecord:
        with self._lock:
            current = self._records.get(identity)
            if current is None:
                raise KeyError(identity)
            if availability is not None and not isinstance(availability, WorldAvailability):
                raise TypeError("availability must be WorldAvailability or None")
            updated = replace(
                current,
                process_ids=current.process_ids if process_ids is None else process_ids,
                window_ids=current.window_ids if window_ids is None else window_ids,
                availability=current.availability if availability is None else availability,
                metadata=current.metadata if metadata is None else metadata,
            )
            self._records[identity] = updated
            return updated

    def mark_unavailable(
        self,
        identity: ApplicationIdentity,
        *,
        metadata: ObservationMetadata,
    ) -> ApplicationRecord:
        with self._lock:
            current = self._records.get(identity)
            if current is None:
                raise KeyError(identity)
            updated = replace(
                current,
                availability=WorldAvailability.UNAVAILABLE,
                process_ids=(),
                window_ids=(),
                metadata=metadata,
            )
            self._records[identity] = updated
            return updated

    def reconcile_environment(
        self,
        *,
        environment_id: str,
        current_process_ids: frozenset[WorldEntityId],
        current_window_ids: frozenset[WorldEntityId],
        metadata: ObservationMetadata,
    ) -> tuple[ApplicationRecord, ...]:
        """Drop relationships absent from a full refreshed environment snapshot."""
        _validate_text(environment_id, field_name="environment_id", max_length=_MAX_IDENTIFIER)
        if metadata.environment_id != environment_id:
            raise WorldModelValidationError("registry reconciliation crosses environment scope")
        updated_records: list[ApplicationRecord] = []
        with self._lock:
            for identity, current in tuple(self._records.items()):
                if identity.environment_id != environment_id:
                    continue
                process_ids = tuple(
                    item for item in current.process_ids if item in current_process_ids
                )
                window_ids = tuple(
                    item for item in current.window_ids if item in current_window_ids
                )
                availability = (
                    WorldAvailability.AVAILABLE
                    if process_ids or window_ids
                    else WorldAvailability.UNAVAILABLE
                )
                if (
                    process_ids == current.process_ids
                    and window_ids == current.window_ids
                    and availability is current.availability
                ):
                    continue
                updated = replace(
                    current,
                    process_ids=process_ids,
                    window_ids=window_ids,
                    availability=availability,
                    metadata=metadata,
                )
                self._records[identity] = updated
                updated_records.append(updated)
        return tuple(sorted(updated_records, key=lambda item: item.identity))

    def stable_snapshot_json(self) -> str:
        """Persist stable metadata only; current availability is reconstructed."""
        with self._lock:
            records = sorted(self._records.values(), key=lambda item: item.identity)
            payload = [
                {
                    "environment_id": record.identity.environment_id,
                    "platform": record.identity.platform,
                    "stable_id": record.identity.stable_id,
                    "canonical_name": record.canonical_name,
                    "display_name": record.display_name,
                    "executable_ids": list(record.executable_ids),
                    "package_ids": list(record.package_ids),
                    "launch_targets": list(record.launch_targets),
                    "aliases": list(record.aliases),
                }
                for record in records
            ]
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_stable_snapshot_json(
        cls,
        raw: str,
        *,
        metadata: ObservationMetadata,
        max_applications: int = _MAX_APPLICATIONS,
    ) -> ApplicationRegistry:
        """Reconstruct stable metadata while resetting environmental availability."""
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WorldModelValidationError("application registry JSON is malformed") from exc
        if not isinstance(decoded, list):
            raise WorldModelValidationError("application registry JSON must be an array")
        registry = cls(max_applications=max_applications)
        for item in decoded:
            if not isinstance(item, Mapping):
                raise WorldModelValidationError("application registry entry must be an object")
            required = {
                "environment_id",
                "platform",
                "stable_id",
                "canonical_name",
                "display_name",
                "executable_ids",
                "package_ids",
                "launch_targets",
                "aliases",
            }
            if set(item) != required:
                raise WorldModelValidationError("application registry entry fields are malformed")
            identity = ApplicationIdentity(
                environment_id=str(item["environment_id"]),
                platform=str(item["platform"]),
                stable_id=str(item["stable_id"]),
            )
            if identity.environment_id != metadata.environment_id:
                raise WorldModelValidationError("restored application crosses environment scope")
            tuple_fields: dict[str, tuple[str, ...]] = {}
            for field_name in ("executable_ids", "package_ids", "launch_targets", "aliases"):
                values = item[field_name]
                if not isinstance(values, list) or not all(
                    isinstance(value, str) for value in values
                ):
                    raise WorldModelValidationError(
                        f"application registry {field_name} must be a string array"
                    )
                tuple_fields[field_name] = tuple(values)
            canonical_name = item["canonical_name"]
            display_name = item["display_name"]
            if not isinstance(canonical_name, str) or not isinstance(display_name, str):
                raise WorldModelValidationError("application names must be strings")
            registry.register(
                ApplicationRecord(
                    identity=identity,
                    canonical_name=canonical_name,
                    display_name=display_name,
                    executable_ids=tuple_fields["executable_ids"],
                    package_ids=tuple_fields["package_ids"],
                    launch_targets=tuple_fields["launch_targets"],
                    aliases=tuple_fields["aliases"],
                    availability=WorldAvailability.UNKNOWN,
                    process_ids=(),
                    window_ids=(),
                    metadata=metadata,
                )
            )
        return registry


@dataclass(frozen=True, slots=True)
class TaskWorldBinding:
    """Explicit evidence-backed task/world relationship; never permission."""

    task_id: TaskId
    correlation_id: UUID
    entity_ids: tuple[WorldEntityId, ...]
    evidence: tuple[EvidenceReference, ...]
    updated_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be TaskId")
        if not isinstance(self.correlation_id, UUID) or self.correlation_id.int == 0:
            raise WorldModelValidationError("correlation_id must be a non-nil UUID")
        if not isinstance(self.entity_ids, tuple):
            raise TypeError("entity_ids must be a tuple")
        if len(self.entity_ids) > _MAX_TASK_ENTITIES:
            raise WorldModelValidationError("task binding exceeds entity bound")
        if len(set(self.entity_ids)) != len(self.entity_ids):
            raise WorldModelValidationError("task binding entities must be unique")
        object.__setattr__(self, "entity_ids", tuple(sorted(self.entity_ids)))
        if not isinstance(self.evidence, tuple) or not self.evidence:
            raise WorldModelValidationError("task binding requires explicit evidence")
        for reference in self.evidence:
            if not isinstance(reference, EvidenceReference):
                raise TypeError("evidence must contain EvidenceReference values")
        object.__setattr__(
            self,
            "updated_at",
            _validate_timestamp(self.updated_at, field_name="updated_at"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id.to_str(),
            "correlation_id": str(self.correlation_id),
            "entity_ids": [entity_id.to_str() for entity_id in self.entity_ids],
            "evidence": [reference.to_dict() for reference in self.evidence],
            "updated_at": _format_timestamp(self.updated_at),
        }


class TaskWorldBinder:
    """Bounded task-local binding registry with strict cross-task isolation."""

    def __init__(self, *, max_bindings: int = _MAX_TASK_BINDINGS) -> None:
        if type(max_bindings) is not int or max_bindings < 1:
            raise WorldModelValidationError("max_bindings must be a positive int")
        self._max_bindings = max_bindings
        self._bindings: dict[TaskId, TaskWorldBinding] = {}
        self._lock = RLock()

    def bind(
        self,
        context: ExecutionContext,
        *,
        entity_ids: tuple[WorldEntityId, ...],
        evidence: tuple[EvidenceReference, ...],
        updated_at: datetime,
    ) -> TaskWorldBinding:
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be ExecutionContext")
        if context.task_id is None:
            raise WorldModelValidationError("task/world binding requires context.task_id")
        binding = TaskWorldBinding(
            task_id=context.task_id,
            correlation_id=context.correlation_id,
            entity_ids=entity_ids,
            evidence=evidence,
            updated_at=updated_at,
        )
        with self._lock:
            if context.task_id not in self._bindings and len(self._bindings) >= self._max_bindings:
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
                if not remaining:
                    del self._bindings[task_id]
                else:
                    self._bindings[task_id] = replace(binding, entity_ids=remaining)
        return tuple(sorted(changed, key=TaskId.to_str))

    def complete_task(self, task_id: TaskId) -> bool:
        with self._lock:
            return self._bindings.pop(task_id, None) is not None

    def assemble(
        self,
        task_id: TaskId,
        *,
        cache: LazyWorldStateCache,
        at: datetime,
    ) -> tuple[CacheLookup, ...]:
        binding = self.get(task_id)
        if binding is None:
            return ()
        return tuple(
            cache.lookup(entity_id, at=at, refresh=False) for entity_id in binding.entity_ids
        )


@dataclass(frozen=True, slots=True)
class WorldHiveLink:
    """Explicit relationship from transient world entity to durable Hive knowledge."""

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
        if not isinstance(self.world_entity_id, WorldEntityId):
            raise TypeError("world_entity_id must be WorldEntityId")
        if not isinstance(self.knowledge_id, KnowledgeId):
            raise TypeError("knowledge_id must be KnowledgeId")
        if not isinstance(self.evidence, tuple) or not self.evidence:
            raise WorldModelValidationError("world/Hive link requires evidence")
        for reference in self.evidence:
            if not isinstance(reference, EvidenceReference):
                raise TypeError("evidence must contain EvidenceReference values")
        object.__setattr__(
            self,
            "observed_at",
            _validate_timestamp(self.observed_at, field_name="observed_at"),
        )
        _validate_text(self.environment_id, field_name="environment_id", max_length=_MAX_IDENTIFIER)
        if self.environment_id != self.world_entity_id.environment_id:
            raise WorldModelValidationError("world/Hive link crosses environment scope")
        if not isinstance(self.verification_status, LinkVerificationStatus):
            raise TypeError("verification_status must be LinkVerificationStatus")
        for field_name in ("contradiction_ids", "supersedes_ids"):
            values = getattr(self, field_name)
            if not isinstance(values, tuple):
                raise TypeError(f"{field_name} must be a tuple")
            for knowledge_id in values:
                if not isinstance(knowledge_id, KnowledgeId):
                    raise TypeError(f"{field_name} must contain KnowledgeId values")
            if len(set(values)) != len(values):
                raise WorldModelValidationError(f"{field_name} must be unique")
            object.__setattr__(self, field_name, tuple(sorted(values, key=KnowledgeId.to_str)))
        if type(self.durable) is not bool:
            raise TypeError("durable must be bool")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _WORLD_LINK_SCHEMA_VERSION,
            "world_entity": {
                "environment_id": self.world_entity_id.environment_id,
                "kind": self.world_entity_id.kind.value,
                "value": self.world_entity_id.value,
            },
            "knowledge_id": self.knowledge_id.to_str(),
            "evidence": [item.to_dict() for item in self.evidence],
            "observed_at": _format_timestamp(self.observed_at),
            "environment_id": self.environment_id,
            "verification_status": self.verification_status.value,
            "contradiction_ids": [item.to_str() for item in self.contradiction_ids],
            "supersedes_ids": [item.to_str() for item in self.supersedes_ids],
            "durable": self.durable,
        }


class WorldHiveLinkage:
    """World/Hive boundary using canonical KnowledgeStore for durable history."""

    def __init__(
        self, *, knowledge_store: KnowledgeStore | None = None, max_links: int = _MAX_LINKS
    ) -> None:
        if type(max_links) is not int or max_links < 1:
            raise WorldModelValidationError("max_links must be a positive int")
        self._knowledge_store = knowledge_store
        self._max_links = max_links
        self._links: list[WorldHiveLink] = []
        self._lock = RLock()

    def add(self, link: WorldHiveLink) -> KnowledgeId | None:
        if not isinstance(link, WorldHiveLink):
            raise TypeError("link must be WorldHiveLink")
        with self._lock:
            if link in self._links:
                return None
            if len(self._links) >= self._max_links:
                raise WorldModelValidationError("world/Hive link set is full")
            if not link.durable:
                self._links.append(link)
                return None
            if self._knowledge_store is None:
                raise WorldModelValidationError("durable world/Hive link requires KnowledgeStore")
            record = KnowledgeRecord.create(
                knowledge_type=KnowledgeType.OBSERVATION,
                content=json.dumps(
                    link.to_dict(),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                scope=KnowledgeScope(dimensions={ScopeDimension.ENVIRONMENT: link.environment_id}),
                provenance=ProvenanceReference(
                    kind=ProvenanceKind.DERIVED,
                    reference=_WORLD_LINK_PROVENANCE,
                ),
                created_at=link.observed_at,
            )
            # Keep persistence and in-memory registration atomic from the
            # linkage object's perspective. A store failure leaves no live link.
            self._knowledge_store.insert(record)
            self._links.append(link)
            return record.knowledge_id

    def load_durable(self) -> tuple[WorldHiveLink, ...]:
        if self._knowledge_store is None:
            return ()
        loaded: list[WorldHiveLink] = []
        for record in self._knowledge_store.list_records():
            provenance = record.provenance
            if (
                provenance is None
                or provenance.kind is not ProvenanceKind.DERIVED
                or provenance.reference != _WORLD_LINK_PROVENANCE
            ):
                continue
            loaded.append(self._decode_link(record.content))
        with self._lock:
            existing = set(self._links)
            for link in loaded:
                if link not in existing:
                    if len(self._links) >= self._max_links:
                        raise WorldModelValidationError(
                            "world/Hive link set is full during restart"
                        )
                    self._links.append(link)
                    existing.add(link)
        return tuple(sorted(loaded, key=self._link_sort_key))

    def links_for(self, entity_id: WorldEntityId) -> tuple[WorldHiveLink, ...]:
        with self._lock:
            values = [link for link in self._links if link.world_entity_id == entity_id]
        return tuple(sorted(values, key=self._link_sort_key))

    @staticmethod
    def _link_sort_key(link: WorldHiveLink) -> tuple[str, str, str]:
        return (
            link.world_entity_id.to_str(),
            _format_timestamp(link.observed_at),
            link.knowledge_id.to_str(),
        )

    @staticmethod
    def _decode_link(content: str) -> WorldHiveLink:
        try:
            raw: object = json.loads(content)
        except json.JSONDecodeError as exc:
            raise WorldModelValidationError("stored world/Hive link JSON is malformed") from exc
        if not isinstance(raw, Mapping):
            raise WorldModelValidationError("stored world/Hive link must be a JSON object")
        if raw.get("schema_version") != _WORLD_LINK_SCHEMA_VERSION:
            raise WorldModelValidationError("stored world/Hive link schema is unsupported")
        entity_raw = raw.get("world_entity")
        if not isinstance(entity_raw, Mapping):
            raise WorldModelValidationError("stored world entity is malformed")
        try:
            entity_kind = WorldEntityKind(str(entity_raw["kind"]))
            entity_id = WorldEntityId(
                environment_id=str(entity_raw["environment_id"]),
                kind=entity_kind,
                value=str(entity_raw["value"]),
            )
            knowledge_id = KnowledgeId.parse(str(raw["knowledge_id"]))
            verification = LinkVerificationStatus(str(raw["verification_status"]))
            observed_at = _parse_timestamp(raw["observed_at"], field_name="observed_at")
        except (KeyError, ValueError) as exc:
            raise WorldModelValidationError(
                "stored world/Hive link contains invalid identity data"
            ) from exc
        evidence_raw = raw.get("evidence")
        if not isinstance(evidence_raw, list) or not evidence_raw:
            raise WorldModelValidationError("stored world/Hive link evidence is malformed")
        evidence = tuple(
            EvidenceReference.from_dict(item) for item in evidence_raw if isinstance(item, Mapping)
        )
        if len(evidence) != len(evidence_raw):
            raise WorldModelValidationError("stored world/Hive link evidence is malformed")
        contradiction_raw = raw.get("contradiction_ids", [])
        supersedes_raw = raw.get("supersedes_ids", [])
        if not isinstance(contradiction_raw, list) or not isinstance(supersedes_raw, list):
            raise WorldModelValidationError("stored world/Hive relationship lists are malformed")
        return WorldHiveLink(
            world_entity_id=entity_id,
            knowledge_id=knowledge_id,
            evidence=evidence,
            observed_at=observed_at,
            environment_id=str(raw.get("environment_id", "")),
            verification_status=verification,
            contradiction_ids=tuple(KnowledgeId.parse(str(item)) for item in contradiction_raw),
            supersedes_ids=tuple(KnowledgeId.parse(str(item)) for item in supersedes_raw),
            durable=bool(raw.get("durable", False)),
        )


def normalize_filesystem_path(path: str, *, windows: bool) -> str:
    """Normalize a task-relevant path without touching the filesystem."""
    checked = _validate_text(path, field_name="path", max_length=4096)
    if windows:
        normalized = ntpath.normpath(checked)
        if not ntpath.isabs(normalized):
            raise WorldModelValidationError("Windows filesystem context requires an absolute path")
        return ntpath.normcase(normalized)
    normalized = os.path.normpath(checked)
    if not Path(normalized).is_absolute():
        raise WorldModelValidationError("filesystem context requires an absolute path")
    return normalized


@dataclass(frozen=True, slots=True)
class LocalWindowsDeviceObservation:
    """Safe local-host observation result; no Android or remote execution."""

    entity_id: WorldEntityId
    state: DeviceState


class _NativeFilesystemProvider:
    def __init__(
        self,
        *,
        source: ProvenanceReference,
        ttl: timedelta,
        task_id: TaskId | None,
        correlation_id: UUID | None,
        original_paths: Mapping[WorldEntityId, str],
    ) -> None:
        self._source = source
        self._ttl = ttl
        self._task_id = task_id
        self._correlation_id = correlation_id
        self._original_paths = original_paths

    def observe(self, entity_id: WorldEntityId) -> WorldStateValue | None:
        path = self._original_paths.get(entity_id)
        if path is None:
            return None
        now = datetime.now(UTC)
        metadata = ObservationMetadata(
            observation_id=f"filesystem:{entity_id.value}:{now.timestamp()}",
            source=self._source,
            observed_at=now,
            ttl=self._ttl,
            environment_id=entity_id.environment_id,
            task_id=self._task_id,
            correlation_id=self._correlation_id,
        )
        try:
            stat_result = Path(path).stat()
        except FileNotFoundError:
            return FilesystemState(
                entity_id=entity_id,
                normalized_path=entity_id.value,
                original_path=path,
                existence=FilesystemExistence.MISSING,
                entity_type=FilesystemEntityType.UNKNOWN,
                size_bytes=None,
                modified_ns=None,
                task_relevant=True,
                metadata=metadata,
            )
        except (PermissionError, OSError):
            return FilesystemState(
                entity_id=entity_id,
                normalized_path=entity_id.value,
                original_path=path,
                existence=FilesystemExistence.INACCESSIBLE,
                entity_type=FilesystemEntityType.UNKNOWN,
                size_bytes=None,
                modified_ns=None,
                task_relevant=True,
                metadata=metadata,
            )
        entity_type = FilesystemEntityType.OTHER
        if stat.S_ISREG(stat_result.st_mode):
            entity_type = FilesystemEntityType.FILE
        elif stat.S_ISDIR(stat_result.st_mode):
            entity_type = FilesystemEntityType.DIRECTORY
        return FilesystemState(
            entity_id=entity_id,
            normalized_path=entity_id.value,
            original_path=path,
            existence=FilesystemExistence.EXISTS,
            entity_type=entity_type,
            size_bytes=stat_result.st_size,
            modified_ns=stat_result.st_mtime_ns,
            task_relevant=True,
            metadata=metadata,
        )


class WorldModel:
    """Single canonical outer composition for AX-409/411/414-421."""

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

    def ingest_windows_snapshot(
        self,
        snapshot: WindowsProcessSnapshot,
        *,
        metadata: ObservationMetadata,
        application_by_pid: Mapping[int, ApplicationIdentity] | None = None,
        foreground_handle: int | None = None,
    ) -> tuple[tuple[ProcessState, ...], tuple[WindowState, ...]]:
        if not isinstance(snapshot, WindowsProcessSnapshot):
            raise TypeError("snapshot must be WindowsProcessSnapshot")
        applications = {} if application_by_pid is None else dict(application_by_pid)
        process_ids: dict[int, WorldEntityId] = {}
        process_states: list[ProcessState] = []
        window_states: list[WindowState] = []

        for process in snapshot.processes:
            image_identity = process.executable_path or process.executable_name or "unknown-image"
            entity_id = WorldEntityId(
                metadata.environment_id,
                WorldEntityKind.PROCESS,
                f"pid:{process.process_id}|image:{image_identity}",
            )
            process_ids[process.process_id] = entity_id

        window_ids: dict[int, WorldEntityId] = {}
        for window in snapshot.windows:
            entity_id = WorldEntityId(
                metadata.environment_id,
                WorldEntityKind.WINDOW,
                f"hwnd:{window.handle}|pid:{window.process_id}",
            )
            window_ids[window.handle] = entity_id
            app_identity = applications.get(window.process_id)
            state = WindowState(
                entity_id=entity_id,
                handle=window.handle,
                process_id=process_ids.get(window.process_id),
                application_id=None if app_identity is None else app_identity.entity_id,
                title=window.title,
                bounds=None,
                visible=window.is_visible,
                show_state=WindowShowState.UNKNOWN,
                is_foreground=(
                    None if foreground_handle is None else window.handle == foreground_handle
                ),
                availability=WorldAvailability.AVAILABLE,
                metadata=metadata,
            )
            self.cache.put(state)
            window_states.append(state)

        for process in snapshot.processes:
            app_identity = applications.get(process.process_id)
            state = ProcessState(
                entity_id=process_ids[process.process_id],
                pid=process.process_id,
                executable_name=process.executable_name,
                executable_path=process.executable_path,
                parent_pid=process.parent_process_id,
                application_id=None if app_identity is None else app_identity.entity_id,
                window_ids=tuple(
                    window_ids[handle] for handle in process.window_handles if handle in window_ids
                ),
                availability=WorldAvailability.AVAILABLE,
                metadata=metadata,
            )
            self.cache.put(state)
            process_states.append(state)

        for app_identity in sorted(set(applications.values())):
            record = self.applications.get(app_identity)
            if record is None:
                continue
            pids = tuple(
                state.entity_id
                for state in process_states
                if state.application_id == app_identity.entity_id
            )
            wins = tuple(
                state.entity_id
                for state in window_states
                if state.application_id == app_identity.entity_id
            )
            updated = self.applications.update_relationships(
                app_identity,
                process_ids=pids,
                window_ids=wins,
                availability=(
                    WorldAvailability.AVAILABLE if pids or wins else WorldAvailability.UNAVAILABLE
                ),
                metadata=metadata,
            )
            self.cache.put(updated)

        current_process_ids = {state.entity_id for state in process_states}
        current_window_ids = {state.entity_id for state in window_states}
        disappeared = self.cache.invalidate_where(
            lambda entity_id, _value: (
                entity_id.environment_id == metadata.environment_id
                and (
                    (
                        entity_id.kind is WorldEntityKind.PROCESS
                        and entity_id not in current_process_ids
                    )
                    or (
                        entity_id.kind is WorldEntityKind.WINDOW
                        and entity_id.value != ACTIVE_WINDOW_KEY
                        and entity_id not in current_window_ids
                    )
                )
            ),
            reason="absent from refreshed Windows process/window snapshot",
        )
        if disappeared:
            self.tasks.invalidate_entities(disappeared)
        reconciled = self.applications.reconcile_environment(
            environment_id=metadata.environment_id,
            current_process_ids=frozenset(current_process_ids),
            current_window_ids=frozenset(current_window_ids),
            metadata=metadata,
        )
        for record in reconciled:
            self.cache.put(record)

        active_id = self.active_window_id(metadata.environment_id)
        if foreground_handle is None or foreground_handle not in window_ids:
            self.cache.invalidate(active_id, reason="foreground window not trustworthily observed")
            self.tasks.invalidate_entities((active_id,))
        else:
            active_window = next(
                state for state in window_states if state.handle == foreground_handle
            )
            active_state = replace(active_window, entity_id=active_id, is_foreground=True)
            self.cache.put(active_state)
        return tuple(process_states), tuple(window_states)

    def ingest_browser_observation(
        self,
        observation: BrowserDomObservation,
        *,
        metadata: ObservationMetadata,
        active: bool,
        application_id: WorldEntityId | None = None,
        process_id: WorldEntityId | None = None,
    ) -> BrowserPageState:
        if not isinstance(observation, BrowserDomObservation):
            raise TypeError("observation must be BrowserDomObservation")
        target: BrowserTargetRef = observation.target
        provider = str(target.provider_id)
        session_value = target.session_id.value
        session_id = WorldEntityId(
            metadata.environment_id,
            WorldEntityKind.BROWSER_SESSION,
            f"provider:{provider}|session:{session_value}",
        )
        page_id = WorldEntityId(
            metadata.environment_id,
            WorldEntityKind.BROWSER_PAGE,
            f"provider:{provider}|session:{session_value}|target:{target.target_id.value}",
        )
        previous = self.cache.lookup(page_id, at=metadata.observed_at, refresh=False).value
        if isinstance(previous, BrowserPageState) and (
            previous.url != target.url or previous.document_version != observation.document_version
        ):
            self.cache.invalidate(page_id, reason="browser navigation/document replacement")
        origin: str | None = None
        if target.url is not None:
            parsed = urlsplit(target.url)
            if parsed.scheme and parsed.netloc:
                origin = f"{parsed.scheme}://{parsed.netloc}"
        availability = (
            WorldAvailability.AVAILABLE
            if target.state.value == "available"
            else WorldAvailability.UNAVAILABLE
        )
        dom_ref = (
            f"dom:{observation.target_id.value}:{observation.document_version or 'unknown'}:"
            f"{_format_timestamp(observation.observed_at)}"
        )
        page = BrowserPageState(
            entity_id=page_id,
            session_id=session_id,
            target_id=target.target_id.value,
            title=target.title,
            url=target.url,
            origin=origin,
            document_version=observation.document_version,
            dom_observation_ref=dom_ref,
            active=active,
            availability=availability,
            metadata=metadata,
        )
        session = BrowserSessionState(
            entity_id=session_id,
            provider_id=provider,
            session_id=session_value,
            availability=availability,
            active_page_id=page_id if active else None,
            application_id=application_id,
            process_id=process_id,
            metadata=metadata,
        )
        if active:
            previously_active = self.cache.invalidate_where(
                lambda entity_id, value: (
                    entity_id.kind is WorldEntityKind.BROWSER_PAGE
                    and entity_id != page_id
                    and isinstance(value, BrowserPageState)
                    and value.session_id == session_id
                    and value.active
                ),
                reason="browser active page changed",
            )
            if previously_active:
                self.tasks.invalidate_entities(previously_active)
        self.cache.put(session)
        self.cache.put(page)
        return page

    def close_browser_page(self, page_id: WorldEntityId) -> None:
        if page_id.kind is not WorldEntityKind.BROWSER_PAGE:
            raise WorldModelValidationError("page_id must identify BROWSER_PAGE")
        self.cache.invalidate(page_id, reason="browser tab/page closed")
        affected_sessions = self.cache.invalidate_where(
            lambda entity_id, value: (
                entity_id.kind is WorldEntityKind.BROWSER_SESSION
                and isinstance(value, BrowserSessionState)
                and value.active_page_id == page_id
            ),
            reason="active browser page closed",
        )
        self.tasks.invalidate_entities((page_id, *affected_sessions))

    def ingest_device_descriptor(
        self,
        descriptor: DeviceDescriptor,
        *,
        metadata: ObservationMetadata,
        role: str,
    ) -> DeviceState:
        if not isinstance(descriptor, DeviceDescriptor):
            raise TypeError("descriptor must be DeviceDescriptor")
        entity_id = WorldEntityId(
            metadata.environment_id,
            WorldEntityKind.DEVICE,
            f"provider:{descriptor.provider_id}|device:{descriptor.device_id.value}",
        )
        availability = (
            WorldAvailability.AVAILABLE
            if descriptor.availability is DeviceAvailability.AVAILABLE
            else WorldAvailability.UNAVAILABLE
        )
        health = tuple(
            sorted(
                (str(item.capability), item.availability.value) for item in descriptor.capabilities
            )
        )
        state = DeviceState(
            entity_id=entity_id,
            platform=descriptor.platform.value,
            role=role,
            availability=availability,
            capability_health=health,
            last_seen=descriptor.last_seen,
            metadata=metadata,
        )
        self.cache.put(state)
        if availability is not WorldAvailability.AVAILABLE:
            dependent = self.cache.invalidate_where(
                lambda dependent_id, value: (
                    dependent_id != entity_id and _metadata_of(value).device_id == entity_id
                ),
                reason="device is unavailable",
            )
            if dependent:
                self.tasks.invalidate_entities(dependent)
        return state

    def observe_local_windows_device(
        self,
        *,
        environment_id: str,
        source: ProvenanceReference,
        ttl: timedelta,
        at: datetime,
    ) -> LocalWindowsDeviceObservation:
        moment = _validate_timestamp(at, field_name="at")
        host = platform.node().strip() or "local-host"
        entity_id = WorldEntityId(
            environment_id, WorldEntityKind.DEVICE, f"local:{host.casefold()}"
        )
        actual_windows = os.name == "nt"
        metadata = ObservationMetadata(
            observation_id=f"local-device:{host}:{_format_timestamp(moment)}",
            source=source,
            observed_at=moment,
            ttl=ttl,
            environment_id=environment_id,
        )
        state = DeviceState(
            entity_id=entity_id,
            platform=DevicePlatform.WINDOWS.value,
            role="local_host",
            availability=(
                WorldAvailability.AVAILABLE if actual_windows else WorldAvailability.UNAVAILABLE
            ),
            capability_health=(),
            last_seen=moment,
            metadata=metadata,
        )
        self.cache.put(state)
        return LocalWindowsDeviceObservation(entity_id=entity_id, state=state)

    def track_filesystem_path(
        self,
        path: str,
        *,
        environment_id: str,
        windows: bool,
        source: ProvenanceReference,
        ttl: timedelta,
        task_id: TaskId | None = None,
        correlation_id: UUID | None = None,
    ) -> WorldEntityId:
        normalized = normalize_filesystem_path(path, windows=windows)
        entity_id = WorldEntityId(environment_id, WorldEntityKind.FILESYSTEM, normalized)
        with self._lock:
            self._filesystem_paths[entity_id] = path
            provider = _NativeFilesystemProvider(
                source=source,
                ttl=ttl,
                task_id=task_id,
                correlation_id=correlation_id,
                original_paths=MappingProxyType(dict(self._filesystem_paths)),
            )
            self.cache.register_provider(WorldEntityKind.FILESYSTEM, provider)
        return entity_id

    def invalidate_filesystem_path(self, entity_id: WorldEntityId, *, reason: str) -> None:
        if entity_id.kind is not WorldEntityKind.FILESYSTEM:
            raise WorldModelValidationError("entity_id must identify FILESYSTEM")
        self.cache.invalidate(entity_id, reason=reason)
        self.tasks.invalidate_entities((entity_id,))

    def invalidate_active_window(self, *, environment_id: str, reason: str) -> None:
        entity_id = self.active_window_id(environment_id)
        self.cache.invalidate(entity_id, reason=reason)
        self.tasks.invalidate_entities((entity_id,))

    def handle_event(self, event: Event) -> None:
        """Consume canonical terminal events for targeted invalidation/cleanup only."""
        if not isinstance(event, Event):
            raise TypeError("event must be Event")
        if event.event_type in (EventType.TASK_COMPLETED, EventType.TASK_FAILED):
            if event.task_id is not None:
                try:
                    self.tasks.complete_task(TaskId.parse(event.task_id))
                except ValueError:
                    return
            return
        if event.event_type is not EventType.ACTION_COMPLETED:
            return
        if not isinstance(event.payload, ActionPayload):
            return
        capability_name = event.payload.name.partition("@")[0]
        if capability_name == "filesystem.write_text":
            # Runtime action events intentionally do not contain request params;
            # invalidate only task-bound filesystem entities for the same task.
            if event.task_id is None:
                return
            try:
                task_id = TaskId.parse(event.task_id)
            except ValueError:
                return
            binding = self.tasks.get(task_id)
            if binding is None:
                return
            for entity_id in binding.entity_ids:
                if entity_id.kind is WorldEntityKind.FILESYSTEM:
                    self.cache.invalidate(entity_id, reason="governed filesystem mutation")
        elif capability_name in {
            "windows.window.activate",
            "windows.window_management.activate",
            "windows.keyboard_text_clipboard.send_text",
        }:
            if event.task_id is None:
                return
            try:
                task_id = TaskId.parse(event.task_id)
            except ValueError:
                return
            binding = self.tasks.get(task_id)
            if binding is None:
                return
            environments = {
                entity_id.environment_id
                for entity_id in binding.entity_ids
                if entity_id.kind in {WorldEntityKind.WINDOW, WorldEntityKind.APPLICATION}
            }
            for environment_id in environments:
                self.invalidate_active_window(
                    environment_id=environment_id,
                    reason="focus-affecting governed action completed",
                )
        elif capability_name.startswith("browser."):
            if event.task_id is None:
                return
            try:
                task_id = TaskId.parse(event.task_id)
            except ValueError:
                return
            binding = self.tasks.get(task_id)
            if binding is None:
                return
            affected = tuple(
                entity_id
                for entity_id in binding.entity_ids
                if entity_id.kind
                in {
                    WorldEntityKind.BROWSER_PAGE,
                    WorldEntityKind.BROWSER_SESSION,
                }
            )
            for entity_id in affected:
                self.cache.invalidate(entity_id, reason="browser governed action completed")
            if affected:
                self.tasks.invalidate_entities(affected)

    def task_context(self, task_id: TaskId, *, at: datetime) -> tuple[CacheLookup, ...]:
        return self.tasks.assemble(task_id, cache=self.cache, at=at)
