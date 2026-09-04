"""Canonical AgentX event envelope, taxonomy, payloads, and serialization.

This module defines records only. Events carry facts about what happened; they
never grant authority or execute behavior. The contract is deliberately
independent of any event bus, persistence engine, model provider, or task
implementation.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID, uuid4

CURRENT_EVENT_SCHEMA_VERSION: Final[int] = 1


type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None


class EventValidationError(ValueError):
    """Raised when an event or payload violates the canonical contract."""


class UnsupportedEventSchemaVersionError(EventValidationError):
    """Raised when encoded event data uses a schema version this code cannot read."""


class EventCategory(StrEnum):
    """Stable top-level namespaces for the AgentX event taxonomy."""

    SYSTEM = "system"
    GOAL = "goal"
    TASK = "task"
    ROUTING = "routing"
    POLICY = "policy"
    CAPABILITY = "capability"
    ACTION = "action"
    OBSERVATION = "observation"
    VERIFICATION = "verification"
    HIVE = "hive"
    PROCEDURE = "procedure"
    REASONING = "reasoning"
    RESEARCH = "research"
    LEARNING = "learning"
    REPAIR = "repair"
    SECURITY = "security"
    RESOURCE = "resource"
    HUMAN = "human"


class EventType(StrEnum):
    """Controlled concrete event kinds implemented by schema version 1."""

    SYSTEM_STARTED = "system.started"
    SYSTEM_STOPPED = "system.stopped"
    GOAL_RECEIVED = "goal.received"
    TASK_CREATED = "task.created"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    ROUTING_DECISION = "routing.decision"
    POLICY_DECISION = "policy.decision"
    CAPABILITY_SELECTED = "capability.selected"
    ACTION_REQUESTED = "action.requested"
    ACTION_COMPLETED = "action.completed"
    ACTION_FAILED = "action.failed"
    OBSERVATION_RECORDED = "observation.recorded"
    VERIFICATION_COMPLETED = "verification.completed"

    @property
    def category(self) -> EventCategory:
        """Return the controlled category encoded by this event type's prefix."""
        prefix, separator, _ = self.value.partition(".")
        if not separator:  # pragma: no cover - impossible for declared enum members
            raise EventValidationError(f"event type has no category prefix: {self.value!r}")
        return EventCategory(prefix)


def _validate_nonempty_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise EventValidationError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise EventValidationError(f"{field_name} must be non-empty and trimmed")
    return value


def _freeze_json(value: object, *, path: str) -> object:
    """Validate JSON compatibility and return an immutable defensive copy."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EventValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise EventValidationError(f"{path} contains a non-string object key")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        )
    raise EventValidationError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _freeze_json_object(value: object, *, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise EventValidationError(f"{path} must be a JSON object")
    frozen = _freeze_json(value, path=path)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
        raise AssertionError("JSON object freezing produced a non-mapping")
    return frozen


def _to_json_value(value: object, *, path: str) -> JsonValue:
    """Convert an internally frozen JSON value back to JSON-compatible primitives."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):  # defensive; construction already rejects this
            raise EventValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):  # defensive; construction already rejects this
                raise EventValidationError(f"{path} contains a non-string object key")
            result[key] = _to_json_value(item, path=f"{path}.{key}")
        return result
    if isinstance(value, tuple | list):
        return [_to_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise EventValidationError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _to_json_object(value: Mapping[str, object], *, path: str) -> dict[str, JsonValue]:
    converted = _to_json_value(value, path=path)
    if not isinstance(converted, dict):  # pragma: no cover - mapping always converts to dict
        raise AssertionError("JSON object conversion produced a non-dict")
    return converted


def _require_exact_keys(
    raw: Mapping[str, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    context: str,
) -> None:
    actual = set(raw)
    missing = required - actual
    unknown = actual - required - optional
    if missing:
        raise EventValidationError(f"{context} missing required fields: {sorted(missing)}")
    if unknown:
        raise EventValidationError(f"{context} contains unknown fields: {sorted(unknown)}")


def _as_string_key_mapping(value: object, *, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise EventValidationError(f"{path} must be a JSON object")
    copied: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise EventValidationError(f"{path} contains a non-string object key")
        copied[key] = item
    return copied


@dataclass(frozen=True, slots=True)
class EmptyPayload:
    """Payload for lifecycle events that need no schema-specific data."""

    def to_dict(self) -> dict[str, JsonValue]:
        return {}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EmptyPayload:
        _require_exact_keys(raw, required=frozenset(), context="payload")
        return cls()


@dataclass(frozen=True, slots=True)
class GoalPayload:
    """Minimal payload for recording a received goal without defining Task state."""

    text: str

    def __post_init__(self) -> None:
        _validate_nonempty_text(self.text, field_name="payload.text")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"text": self.text}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> GoalPayload:
        _require_exact_keys(raw, required=frozenset({"text"}), context="payload")
        return cls(text=_validate_nonempty_text(raw["text"], field_name="payload.text"))


@dataclass(frozen=True, slots=True)
class DecisionPayload:
    """Minimal payload for routing or policy decisions."""

    decision: str
    reason: str | None = None

    def __post_init__(self) -> None:
        _validate_nonempty_text(self.decision, field_name="payload.decision")
        if self.reason is not None:
            _validate_nonempty_text(self.reason, field_name="payload.reason")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"decision": self.decision, "reason": self.reason}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> DecisionPayload:
        _require_exact_keys(
            raw,
            required=frozenset({"decision"}),
            optional=frozenset({"reason"}),
            context="payload",
        )
        reason = raw.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise EventValidationError("payload.reason must be a string or null")
        return cls(
            decision=_validate_nonempty_text(raw["decision"], field_name="payload.decision"),
            reason=reason,
        )


@dataclass(frozen=True, slots=True)
class SelectionPayload:
    """Minimal payload for recording a selected capability without defining its ABI."""

    selection: str
    reason: str | None = None

    def __post_init__(self) -> None:
        _validate_nonempty_text(self.selection, field_name="payload.selection")
        if self.reason is not None:
            _validate_nonempty_text(self.reason, field_name="payload.reason")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"selection": self.selection, "reason": self.reason}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> SelectionPayload:
        _require_exact_keys(
            raw,
            required=frozenset({"selection"}),
            optional=frozenset({"reason"}),
            context="payload",
        )
        reason = raw.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise EventValidationError("payload.reason must be a string or null")
        return cls(
            selection=_validate_nonempty_text(raw["selection"], field_name="payload.selection"),
            reason=reason,
        )


@dataclass(frozen=True, slots=True)
class ActionPayload:
    """Opaque action record; ``data`` is descriptive and never executable authority."""

    name: str
    data: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_nonempty_text(self.name, field_name="payload.name")
        object.__setattr__(
            self,
            "data",
            _freeze_json_object(self.data, path="payload.data"),
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {"name": self.name, "data": _to_json_object(self.data, path="payload.data")}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ActionPayload:
        _require_exact_keys(
            raw,
            required=frozenset({"name"}),
            optional=frozenset({"data"}),
            context="payload",
        )
        data = raw.get("data", {})
        return cls(
            name=_validate_nonempty_text(raw["name"], field_name="payload.name"),
            data=_as_string_key_mapping(data, path="payload.data"),
        )


@dataclass(frozen=True, slots=True)
class ObservationPayload:
    """Opaque JSON-compatible observation content, including untrusted external data."""

    value: object

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _freeze_json(self.value, path="payload.value"))

    def to_dict(self) -> dict[str, JsonValue]:
        return {"value": _to_json_value(self.value, path="payload.value")}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ObservationPayload:
        _require_exact_keys(raw, required=frozenset({"value"}), context="payload")
        return cls(value=raw["value"])


@dataclass(frozen=True, slots=True)
class VerificationPayload:
    """Minimal verification result without defining a broader verifier protocol."""

    passed: bool
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.passed, bool):
            raise EventValidationError("payload.passed must be a boolean")
        if self.detail is not None:
            _validate_nonempty_text(self.detail, field_name="payload.detail")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"passed": self.passed, "detail": self.detail}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> VerificationPayload:
        _require_exact_keys(
            raw,
            required=frozenset({"passed"}),
            optional=frozenset({"detail"}),
            context="payload",
        )
        passed = raw["passed"]
        if not isinstance(passed, bool):
            raise EventValidationError("payload.passed must be a boolean")
        detail = raw.get("detail")
        if detail is not None and not isinstance(detail, str):
            raise EventValidationError("payload.detail must be a string or null")
        return cls(passed=passed, detail=detail)


type EventPayload = (
    EmptyPayload
    | GoalPayload
    | DecisionPayload
    | SelectionPayload
    | ActionPayload
    | ObservationPayload
    | VerificationPayload
)

_EMPTY_PAYLOAD_EVENTS: Final = frozenset(
    {
        EventType.SYSTEM_STARTED,
        EventType.SYSTEM_STOPPED,
        EventType.TASK_CREATED,
        EventType.TASK_STARTED,
        EventType.TASK_COMPLETED,
        EventType.TASK_FAILED,
    }
)
_DECISION_PAYLOAD_EVENTS: Final = frozenset({EventType.ROUTING_DECISION, EventType.POLICY_DECISION})
_ACTION_PAYLOAD_EVENTS: Final = frozenset(
    {EventType.ACTION_REQUESTED, EventType.ACTION_COMPLETED, EventType.ACTION_FAILED}
)


def _validate_payload_type(event_type: EventType, payload: EventPayload) -> None:
    valid = (
        (event_type in _EMPTY_PAYLOAD_EVENTS and isinstance(payload, EmptyPayload))
        or (event_type is EventType.GOAL_RECEIVED and isinstance(payload, GoalPayload))
        or (event_type in _DECISION_PAYLOAD_EVENTS and isinstance(payload, DecisionPayload))
        or (event_type is EventType.CAPABILITY_SELECTED and isinstance(payload, SelectionPayload))
        or (event_type in _ACTION_PAYLOAD_EVENTS and isinstance(payload, ActionPayload))
        or (
            event_type is EventType.OBSERVATION_RECORDED and isinstance(payload, ObservationPayload)
        )
        or (
            event_type is EventType.VERIFICATION_COMPLETED
            and isinstance(payload, VerificationPayload)
        )
    )
    if not valid:
        raise EventValidationError(
            f"payload type {type(payload).__name__} is invalid for event type {event_type.value}"
        )


def _decode_payload(event_type: EventType, raw: Mapping[str, object]) -> EventPayload:
    if event_type in _EMPTY_PAYLOAD_EVENTS:
        return EmptyPayload.from_dict(raw)
    if event_type is EventType.GOAL_RECEIVED:
        return GoalPayload.from_dict(raw)
    if event_type in _DECISION_PAYLOAD_EVENTS:
        return DecisionPayload.from_dict(raw)
    if event_type is EventType.CAPABILITY_SELECTED:
        return SelectionPayload.from_dict(raw)
    if event_type in _ACTION_PAYLOAD_EVENTS:
        return ActionPayload.from_dict(raw)
    if event_type is EventType.OBSERVATION_RECORDED:
        return ObservationPayload.from_dict(raw)
    if event_type is EventType.VERIFICATION_COMPLETED:
        return VerificationPayload.from_dict(raw)
    raise EventValidationError(f"no payload decoder registered for event type {event_type.value}")


def _parse_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, str):
        raise EventValidationError(f"{field_name} must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise EventValidationError(f"{field_name} must be a valid UUID string") from exc
    if parsed.int == 0:
        raise EventValidationError(f"{field_name} must not be the nil UUID")
    return parsed


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise EventValidationError(f"{field_name} must be a UUID")
    if value.int == 0:
        raise EventValidationError(f"{field_name} must not be the nil UUID")
    return value


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise EventValidationError("timestamp must be an ISO-8601 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EventValidationError("timestamp must be a valid ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EventValidationError("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    utc_value = value.astimezone(UTC)
    return utc_value.isoformat(timespec="microseconds").replace("+00:00", "Z")


_EVENT_FIELDS: Final = frozenset(
    {
        "event_id",
        "event_type",
        "timestamp",
        "schema_version",
        "source",
        "correlation_id",
        "causation_id",
        "task_id",
        "payload",
        "metadata",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Event:
    """Immutable canonical AgentX event envelope.

    ``correlation_id`` groups the events belonging to one logical execution or
    task flow. A root event created without an explicit correlation identifier
    uses its own ``event_id`` as the correlation root.

    ``causation_id`` names the immediately preceding event that directly caused
    this event. It is optional for roots or externally-originated events. The
    contract records identifiers only; it does not require the referenced event
    to be loaded or persisted.
    """

    event_id: UUID
    event_type: EventType
    timestamp: datetime
    schema_version: int
    source: str
    correlation_id: UUID
    causation_id: UUID | None
    task_id: str | None
    payload: EventPayload
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_uuid(self.event_id, field_name="event_id")
        if not isinstance(self.event_type, EventType):
            raise EventValidationError("event_type must be an EventType")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise EventValidationError("schema_version must be an integer")
        if self.schema_version != CURRENT_EVENT_SCHEMA_VERSION:
            raise UnsupportedEventSchemaVersionError(
                f"unsupported event schema version {self.schema_version}; "
                f"supported version is {CURRENT_EVENT_SCHEMA_VERSION}"
            )
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise EventValidationError("timestamp must be timezone-aware")
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))
        _validate_nonempty_text(self.source, field_name="source")
        _validate_uuid(self.correlation_id, field_name="correlation_id")
        if self.causation_id is not None:
            _validate_uuid(self.causation_id, field_name="causation_id")
            if self.causation_id == self.event_id:
                raise EventValidationError("causation_id must not reference the event itself")
        if self.task_id is not None:
            _validate_nonempty_text(self.task_id, field_name="task_id")
        _validate_payload_type(self.event_type, self.payload)
        object.__setattr__(
            self,
            "metadata",
            _freeze_json_object(self.metadata, path="metadata"),
        )

    @property
    def category(self) -> EventCategory:
        """Return the category implied by ``event_type``."""
        return self.event_type.category

    @classmethod
    def create(
        cls,
        *,
        event_type: EventType,
        source: str,
        payload: EventPayload | None = None,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
        task_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
        timestamp: datetime | None = None,
    ) -> Event:
        """Create a new event with a UUID identifier and UTC timestamp."""
        event_id = uuid4()
        actual_payload = EmptyPayload() if payload is None else payload
        return cls(
            event_id=event_id,
            event_type=event_type,
            timestamp=datetime.now(UTC) if timestamp is None else timestamp,
            schema_version=CURRENT_EVENT_SCHEMA_VERSION,
            source=source,
            correlation_id=event_id if correlation_id is None else correlation_id,
            causation_id=causation_id,
            task_id=task_id,
            payload=actual_payload,
            metadata={} if metadata is None else metadata,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the canonical JSON-compatible schema-v1 representation."""
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type.value,
            "timestamp": _format_timestamp(self.timestamp),
            "schema_version": self.schema_version,
            "source": self.source,
            "correlation_id": str(self.correlation_id),
            "causation_id": None if self.causation_id is None else str(self.causation_id),
            "task_id": self.task_id,
            "payload": self.payload.to_dict(),
            "metadata": _to_json_object(self.metadata, path="metadata"),
        }

    def to_json(self) -> str:
        """Serialize to deterministic UTF-8-safe JSON text."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> Event:
        """Validate and deserialize a canonical JSON-compatible event object."""
        if "schema_version" not in raw:
            raise EventValidationError("event missing required field: schema_version")
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise EventValidationError("schema_version must be an integer")
        if version != CURRENT_EVENT_SCHEMA_VERSION:
            raise UnsupportedEventSchemaVersionError(
                f"unsupported event schema version {version}; "
                f"supported version is {CURRENT_EVENT_SCHEMA_VERSION}"
            )

        _require_exact_keys(raw, required=_EVENT_FIELDS, context="event")

        event_type_raw = raw["event_type"]
        if not isinstance(event_type_raw, str):
            raise EventValidationError("event_type must be a string")
        try:
            event_type = EventType(event_type_raw)
        except ValueError as exc:
            raise EventValidationError(f"unknown event_type: {event_type_raw!r}") from exc

        source = raw["source"]
        if not isinstance(source, str):
            raise EventValidationError("source must be a string")

        causation_raw = raw["causation_id"]
        causation_id = (
            None if causation_raw is None else _parse_uuid(causation_raw, field_name="causation_id")
        )

        task_raw = raw["task_id"]
        if task_raw is not None and not isinstance(task_raw, str):
            raise EventValidationError("task_id must be a string or null")

        payload_raw = _as_string_key_mapping(raw["payload"], path="payload")
        metadata_raw = _as_string_key_mapping(raw["metadata"], path="metadata")

        return cls(
            event_id=_parse_uuid(raw["event_id"], field_name="event_id"),
            event_type=event_type,
            timestamp=_parse_timestamp(raw["timestamp"]),
            schema_version=version,
            source=source,
            correlation_id=_parse_uuid(raw["correlation_id"], field_name="correlation_id"),
            causation_id=causation_id,
            task_id=task_raw,
            payload=_decode_payload(event_type, payload_raw),
            metadata=metadata_raw,
        )

    @classmethod
    def from_json(cls, raw: str) -> Event:
        """Deserialize JSON text without dynamic imports or arbitrary object construction."""
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EventValidationError("event JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise EventValidationError("event JSON root must be an object")
        return cls.from_dict(_as_string_key_mapping(decoded, path="event"))


__all__ = [
    "CURRENT_EVENT_SCHEMA_VERSION",
    "ActionPayload",
    "DecisionPayload",
    "EmptyPayload",
    "Event",
    "EventCategory",
    "EventPayload",
    "EventType",
    "EventValidationError",
    "GoalPayload",
    "JsonValue",
    "ObservationPayload",
    "SelectionPayload",
    "UnsupportedEventSchemaVersionError",
    "VerificationPayload",
]
