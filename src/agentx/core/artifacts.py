"""Canonical inert ArtifactRecord contract for C2.04.

Artifacts are durable metadata/references to outputs or externally stored objects.
The record never opens, fetches, imports, executes, or otherwise interprets its
locator. Integrity metadata is descriptive only and never grants authority.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.core.ids import ArtifactId, TaskId

__all__ = [
    "CURRENT_ARTIFACT_SCHEMA_VERSION",
    "ArtifactKind",
    "ArtifactRecord",
    "ArtifactValidationError",
    "UnsupportedArtifactSchemaVersionError",
]

CURRENT_ARTIFACT_SCHEMA_VERSION: Final[int] = 1
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_ARTIFACT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "artifact_id",
        "kind",
        "created_at",
        "task_id",
        "correlation_id",
        "locator",
        "media_type",
        "size_bytes",
        "sha256",
    }
)


class ArtifactValidationError(ValueError):
    """Raised when artifact data violates the canonical contract."""


class UnsupportedArtifactSchemaVersionError(ArtifactValidationError):
    """Raised when encoded artifact data uses an unsupported schema version."""


class ArtifactKind(StrEnum):
    """Small storage/reference-shape vocabulary for retained artifacts."""

    FILE = "file"
    DIRECTORY = "directory"
    URI = "uri"
    REFERENCE = "reference"


def _validate_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ArtifactValidationError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ArtifactValidationError(f"{field_name} must be non-empty and trimmed")
    return value


def _validate_optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _validate_text(value, field_name=field_name)


def _validate_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ArtifactValidationError("created_at must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ArtifactValidationError("created_at must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ArtifactValidationError("created_at must be an ISO-8601 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ArtifactValidationError("created_at must be a valid ISO-8601 datetime") from exc
    return _validate_timestamp(parsed)


def _parse_artifact_id(value: object) -> ArtifactId:
    if not isinstance(value, str):
        raise ArtifactValidationError("artifact_id must be a UUID string")
    try:
        return ArtifactId.parse(value)
    except ValueError as exc:
        raise ArtifactValidationError("artifact_id must be a valid non-nil UUID string") from exc


def _parse_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ArtifactValidationError("task_id must be a UUID string or null")
    try:
        return TaskId.parse(value)
    except ValueError as exc:
        raise ArtifactValidationError("task_id must be a valid non-nil UUID string") from exc


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise ArtifactValidationError(f"{field_name} must be a UUID")
    if value.int == 0:
        raise ArtifactValidationError(f"{field_name} must not be the nil UUID")
    return value


def _parse_uuid(value: object, *, field_name: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ArtifactValidationError(f"{field_name} must be a UUID string or null")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ArtifactValidationError(f"{field_name} must be a valid UUID string") from exc
    return _validate_uuid(parsed, field_name=field_name)


@dataclass(frozen=True, slots=True, kw_only=True)
class ArtifactRecord:
    """Immutable metadata/reference record for one retained artifact.

    ``locator`` is opaque data. Its kind does not cause path access, URI fetches,
    imports, or execution. ``sha256`` is optional integrity metadata only.
    """

    artifact_id: ArtifactId
    kind: ArtifactKind
    created_at: datetime
    locator: str
    task_id: TaskId | None = None
    correlation_id: UUID | None = None
    media_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_id, ArtifactId):
            raise ArtifactValidationError("artifact_id must be an ArtifactId")
        if not isinstance(self.kind, ArtifactKind):
            raise ArtifactValidationError("kind must be an ArtifactKind")
        object.__setattr__(self, "created_at", _validate_timestamp(self.created_at))
        _validate_text(self.locator, field_name="locator")

        if self.task_id is not None and not isinstance(self.task_id, TaskId):
            raise ArtifactValidationError("task_id must be a TaskId or None")
        if self.correlation_id is not None:
            _validate_uuid(self.correlation_id, field_name="correlation_id")
        _validate_optional_text(self.media_type, field_name="media_type")

        if self.size_bytes is not None:
            if type(self.size_bytes) is not int:
                raise ArtifactValidationError("size_bytes must be an int or None")
            if self.size_bytes < 0:
                raise ArtifactValidationError("size_bytes must not be negative")

        if self.sha256 is not None and (
            not isinstance(self.sha256, str) or not _SHA256_PATTERN.fullmatch(self.sha256)
        ):
            raise ArtifactValidationError("sha256 must be 64 lowercase hexadecimal characters")

    @classmethod
    def create(
        cls,
        *,
        kind: ArtifactKind,
        locator: str,
        task_id: TaskId | None = None,
        correlation_id: UUID | None = None,
        media_type: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
        artifact_id: ArtifactId | None = None,
        created_at: datetime | None = None,
    ) -> ArtifactRecord:
        """Create an artifact record with fresh identity/time unless supplied."""

        return cls(
            artifact_id=ArtifactId.create() if artifact_id is None else artifact_id,
            kind=kind,
            created_at=datetime.now(UTC) if created_at is None else created_at,
            locator=locator,
            task_id=task_id,
            correlation_id=correlation_id,
            media_type=media_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic versioned JSON-compatible representation."""

        return {
            "schema_version": CURRENT_ARTIFACT_SCHEMA_VERSION,
            "artifact_id": self.artifact_id.to_str(),
            "kind": self.kind.value,
            "created_at": _format_timestamp(self.created_at),
            "task_id": self.task_id.to_str() if self.task_id is not None else None,
            "correlation_id": str(self.correlation_id) if self.correlation_id is not None else None,
            "locator": self.locator,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    def to_json(self) -> str:
        """Serialize deterministically without executable or dynamic types."""

        return json.dumps(self.to_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ArtifactRecord:
        """Reconstruct one canonical artifact record from versioned data."""

        if not isinstance(raw, Mapping):
            raise ArtifactValidationError("artifact payload must be a mapping")
        actual = set(raw)
        if actual != _ARTIFACT_FIELDS:
            raise ArtifactValidationError(
                f"artifact payload must contain exactly {sorted(_ARTIFACT_FIELDS)}; "
                f"got {sorted(actual)}"
            )

        schema_version = raw["schema_version"]
        if type(schema_version) is not int:
            raise ArtifactValidationError("schema_version must be an int")
        if schema_version != CURRENT_ARTIFACT_SCHEMA_VERSION:
            raise UnsupportedArtifactSchemaVersionError(
                f"unsupported artifact schema version: {schema_version}"
            )

        kind_raw = raw["kind"]
        if not isinstance(kind_raw, str):
            raise ArtifactValidationError("kind must be a string")
        try:
            kind = ArtifactKind(kind_raw)
        except ValueError as exc:
            raise ArtifactValidationError(f"unknown artifact kind: {kind_raw!r}") from exc

        size_raw = raw["size_bytes"]
        if size_raw is not None and type(size_raw) is not int:
            raise ArtifactValidationError("size_bytes must be an int or null")

        return cls(
            artifact_id=_parse_artifact_id(raw["artifact_id"]),
            kind=kind,
            created_at=_parse_timestamp(raw["created_at"]),
            locator=_validate_text(raw["locator"], field_name="locator"),
            task_id=_parse_task_id(raw["task_id"]),
            correlation_id=_parse_uuid(raw["correlation_id"], field_name="correlation_id"),
            media_type=_validate_optional_text(raw["media_type"], field_name="media_type"),
            size_bytes=size_raw,
            sha256=_validate_optional_text(raw["sha256"], field_name="sha256"),
        )

    @classmethod
    def from_json(cls, payload: str) -> ArtifactRecord:
        """Decode deterministic JSON and reject malformed/non-object payloads."""

        if not isinstance(payload, str):
            raise ArtifactValidationError("artifact JSON payload must be a string")
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ArtifactValidationError("artifact JSON payload is malformed") from exc
        if not isinstance(raw, dict):
            raise ArtifactValidationError("artifact JSON payload must decode to an object")
        return cls.from_dict(raw)
