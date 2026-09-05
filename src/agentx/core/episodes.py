"""Canonical inert episodic-experience record for future AgentX Hive consumers.

An EpisodeRecord is historical data only. It describes one meaningful experience
and may reference supporting canonical Event identities, but it never grants
authority, executes behavior, mutates Task state, or performs persistence.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.core.ids import EpisodeId, TaskId

EPISODE_SCHEMA_VERSION: Final[int] = 1
_MAX_SUMMARY_LENGTH: Final[int] = 4_096
_EPISODE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "episode_id",
        "task_id",
        "correlation_id",
        "created_at",
        "started_at",
        "ended_at",
        "outcome",
        "summary",
        "supporting_event_ids",
    }
)

JsonValue = bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None


class EpisodeValidationError(ValueError):
    """Raised when an EpisodeRecord violates the canonical episode contract."""


class EpisodeDeserializationError(EpisodeValidationError):
    """Raised when encoded episode data cannot be decoded safely."""


class UnsupportedEpisodeSchemaVersionError(EpisodeDeserializationError):
    """Raised when encoded episode data uses an unsupported schema version."""


class EpisodeOutcome(StrEnum):
    """Small historical outcome vocabulary for a completed or interrupted episode."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


def _validate_episode_id(value: object) -> EpisodeId:
    if not isinstance(value, EpisodeId):
        raise TypeError(f"episode_id must be an EpisodeId, got {type(value).__name__}")
    return value


def _validate_optional_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, TaskId):
        raise TypeError(f"task_id must be a TaskId or None, got {type(value).__name__}")
    return value


def _validate_optional_uuid(value: object, *, field_name: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID or None, got {type(value).__name__}")
    if value.int == 0:
        raise EpisodeValidationError(f"{field_name} must not be the nil UUID")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise EpisodeValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_optional_timestamp(value: object, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _validate_timestamp(value, field_name=field_name)


def _validate_summary(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"summary must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise EpisodeValidationError("summary must be non-empty and trimmed")
    if len(value) > _MAX_SUMMARY_LENGTH:
        raise EpisodeValidationError(f"summary must be at most {_MAX_SUMMARY_LENGTH} characters")
    return value


def _validate_outcome(value: object) -> EpisodeOutcome:
    if not isinstance(value, EpisodeOutcome):
        raise TypeError(f"outcome must be an EpisodeOutcome, got {type(value).__name__}")
    return value


def _validate_supporting_event_ids(value: object) -> tuple[UUID, ...]:
    if not isinstance(value, tuple):
        raise TypeError("supporting_event_ids must be a tuple of UUID values")

    validated: list[UUID] = []
    for index, event_id in enumerate(value):
        if not isinstance(event_id, UUID):
            raise TypeError(
                "supporting_event_ids must contain only UUID values; "
                f"index {index} is {type(event_id).__name__}"
            )
        if event_id.int == 0:
            raise EpisodeValidationError(f"supporting_event_ids[{index}] must not be the nil UUID")
        validated.append(event_id)

    if len(set(validated)) != len(validated):
        raise EpisodeValidationError("supporting_event_ids must not contain duplicates")
    return tuple(validated)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise EpisodeDeserializationError(
            f"{field_name} is not a valid ISO-8601 timestamp: {value!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EpisodeDeserializationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _parse_optional_timestamp(value: object, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _parse_timestamp(value, field_name=field_name)


def _parse_episode_id(value: object) -> EpisodeId:
    if not isinstance(value, str):
        raise TypeError(f"episode_id must be a string, got {type(value).__name__}")
    try:
        parsed = EpisodeId.parse(value)
    except ValueError as exc:
        raise EpisodeDeserializationError(
            f"episode_id is not a valid EpisodeId: {value!r}"
        ) from exc
    return parsed


def _parse_optional_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"task_id must be a string or null, got {type(value).__name__}")
    try:
        parsed = TaskId.parse(value)
    except ValueError as exc:
        raise EpisodeDeserializationError(f"task_id is not a valid TaskId: {value!r}") from exc
    return parsed


def _parse_optional_uuid(value: object, *, field_name: str) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or null, got {type(value).__name__}")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise EpisodeDeserializationError(f"{field_name} is not a valid UUID: {value!r}") from exc
    if parsed.int == 0:
        raise EpisodeDeserializationError(f"{field_name} must not be the nil UUID")
    return parsed


def _parse_outcome(value: object) -> EpisodeOutcome:
    if not isinstance(value, str):
        raise TypeError(f"outcome must be a string, got {type(value).__name__}")
    try:
        return EpisodeOutcome(value)
    except ValueError as exc:
        raise EpisodeDeserializationError(
            f"outcome must be one of {[member.value for member in EpisodeOutcome]}; got {value!r}"
        ) from exc


def _parse_supporting_event_ids(value: object) -> tuple[UUID, ...]:
    if not isinstance(value, list):
        raise TypeError("supporting_event_ids must be a JSON array")
    parsed: list[UUID] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise TypeError(f"supporting_event_ids[{index}] must be a string")
        try:
            event_id = UUID(item)
        except ValueError as exc:
            raise EpisodeDeserializationError(
                f"supporting_event_ids[{index}] is not a valid UUID: {item!r}"
            ) from exc
        if event_id.int == 0:
            raise EpisodeDeserializationError(
                f"supporting_event_ids[{index}] must not be the nil UUID"
            )
        parsed.append(event_id)
    return _validate_supporting_event_ids(tuple(parsed))


@dataclass(frozen=True, slots=True)
class EpisodeRecord:
    """Immutable, minimal record of one meaningful historical experience."""

    episode_id: EpisodeId
    outcome: EpisodeOutcome
    summary: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    task_id: TaskId | None = None
    correlation_id: UUID | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    supporting_event_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "episode_id", _validate_episode_id(self.episode_id))
        object.__setattr__(self, "outcome", _validate_outcome(self.outcome))
        object.__setattr__(self, "summary", _validate_summary(self.summary))
        object.__setattr__(
            self,
            "created_at",
            _validate_timestamp(self.created_at, field_name="created_at"),
        )
        object.__setattr__(self, "task_id", _validate_optional_task_id(self.task_id))
        object.__setattr__(
            self,
            "correlation_id",
            _validate_optional_uuid(self.correlation_id, field_name="correlation_id"),
        )
        started_at = _validate_optional_timestamp(self.started_at, field_name="started_at")
        ended_at = _validate_optional_timestamp(self.ended_at, field_name="ended_at")
        if ended_at is not None and started_at is None:
            raise EpisodeValidationError("ended_at requires started_at")
        if started_at is not None and ended_at is not None and ended_at < started_at:
            raise EpisodeValidationError("ended_at must not be earlier than started_at")
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "ended_at", ended_at)
        object.__setattr__(
            self,
            "supporting_event_ids",
            _validate_supporting_event_ids(self.supporting_event_ids),
        )

    @classmethod
    def create(
        cls,
        *,
        outcome: EpisodeOutcome,
        summary: str,
        episode_id: EpisodeId | None = None,
        created_at: datetime | None = None,
        task_id: TaskId | None = None,
        correlation_id: UUID | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        supporting_event_ids: tuple[UUID, ...] = (),
    ) -> EpisodeRecord:
        """Create one inert record with fresh identity/time unless supplied."""
        return cls(
            episode_id=_new_episode_id() if episode_id is None else episode_id,
            outcome=outcome,
            summary=summary,
            created_at=datetime.now(UTC) if created_at is None else created_at,
            task_id=task_id,
            correlation_id=correlation_id,
            started_at=started_at,
            ended_at=ended_at,
            supporting_event_ids=supporting_event_ids,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the deterministic JSON-compatible schema-v1 representation."""
        return {
            "schema_version": EPISODE_SCHEMA_VERSION,
            "episode_id": self.episode_id.to_str(),
            "task_id": None if self.task_id is None else self.task_id.to_str(),
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
            "created_at": _format_timestamp(self.created_at),
            "started_at": None if self.started_at is None else _format_timestamp(self.started_at),
            "ended_at": None if self.ended_at is None else _format_timestamp(self.ended_at),
            "outcome": self.outcome.value,
            "summary": self.summary,
            "supporting_event_ids": [str(event_id) for event_id in self.supporting_event_ids],
        }

    def to_json(self) -> str:
        """Serialize to deterministic JSON text without executable object hooks."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EpisodeRecord:
        """Validate and reconstruct one canonical episode object."""
        if "schema_version" not in raw:
            raise EpisodeDeserializationError("episode missing required field: schema_version")
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise EpisodeDeserializationError("schema_version must be an integer")
        if version != EPISODE_SCHEMA_VERSION:
            raise UnsupportedEpisodeSchemaVersionError(
                f"unsupported episode schema version {version}; "
                f"supported version is {EPISODE_SCHEMA_VERSION}"
            )

        actual = set(raw)
        missing = _EPISODE_FIELDS - actual
        unknown = actual - _EPISODE_FIELDS
        if missing:
            raise EpisodeDeserializationError(f"episode missing required fields: {sorted(missing)}")
        if unknown:
            raise EpisodeDeserializationError(f"episode contains unknown fields: {sorted(unknown)}")

        return cls(
            episode_id=_parse_episode_id(raw["episode_id"]),
            task_id=_parse_optional_task_id(raw["task_id"]),
            correlation_id=_parse_optional_uuid(raw["correlation_id"], field_name="correlation_id"),
            created_at=_parse_timestamp(raw["created_at"], field_name="created_at"),
            started_at=_parse_optional_timestamp(raw["started_at"], field_name="started_at"),
            ended_at=_parse_optional_timestamp(raw["ended_at"], field_name="ended_at"),
            outcome=_parse_outcome(raw["outcome"]),
            summary=_validate_summary(raw["summary"]),
            supporting_event_ids=_parse_supporting_event_ids(raw["supporting_event_ids"]),
        )

    @classmethod
    def from_json(cls, text: str) -> EpisodeRecord:
        """Validate and reconstruct canonical episode JSON."""
        if not isinstance(text, str):
            raise TypeError(f"episode JSON must be a string, got {type(text).__name__}")
        try:
            decoded = json.loads(text)
        except ValueError as exc:
            raise EpisodeDeserializationError(f"episode JSON is malformed: {exc}") from exc
        if not isinstance(decoded, Mapping):
            raise EpisodeDeserializationError("episode JSON root must be an object")
        return cls.from_dict(decoded)


def _new_episode_id() -> EpisodeId:
    return EpisodeId.create()


__all__ = [
    "EPISODE_SCHEMA_VERSION",
    "EpisodeDeserializationError",
    "EpisodeOutcome",
    "EpisodeRecord",
    "EpisodeValidationError",
    "UnsupportedEpisodeSchemaVersionError",
]
