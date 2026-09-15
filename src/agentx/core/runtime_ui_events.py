"""One-way runtime-to-UI telemetry protocol (AX-452).

The UI receives bounded descriptive telemetry only. This contract deliberately
contains no command, approval, permission, ActionGate, registry, execution, or
Task-mutation surface. Canonical runtime events are projected through a
redaction policy before crossing the UI boundary.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from agentx.core.events import (
    ActionPayload,
    DecisionPayload,
    EmptyPayload,
    Event,
    GoalPayload,
    ObservationPayload,
    SelectionPayload,
    VerificationPayload,
)

__all__ = [
    "RUNTIME_UI_SCHEMA_VERSION",
    "RuntimeUiEvent",
    "RuntimeUiEventKind",
    "RuntimeUiValidationError",
    "project_event_for_ui",
]

RUNTIME_UI_SCHEMA_VERSION: Final[int] = 1
MAX_RUNTIME_UI_SEQUENCE: Final[int] = (1 << 63) - 1
_MAX_STATE_LENGTH: Final[int] = 256
_REDACTED: Final[str] = "[redacted]"


class RuntimeUiValidationError(ValueError):
    """Raised when a UI telemetry envelope violates the one-way contract."""


class RuntimeUiEventKind(StrEnum):
    EVENT = "event"
    DISCONNECTED = "disconnected"
    RUNTIME_RESTARTED = "runtime_restarted"


def _timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeUiValidationError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise RuntimeUiValidationError(f"{field_name} must be a non-nil UUID")
    return value


def _state(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("state must be a string")
    if not value or value != value.strip() or len(value) > _MAX_STATE_LENGTH:
        raise RuntimeUiValidationError("state must be bounded non-empty trimmed text")
    return value


def _format(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise RuntimeUiValidationError("timestamp must be a string")
    text = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        return _timestamp(datetime.fromisoformat(text))
    except ValueError as exc:
        raise RuntimeUiValidationError("timestamp is invalid") from exc


def _safe_payload(event: Event) -> Mapping[str, object]:
    """Project canonical payloads without exposing arbitrary user/provider data."""
    payload = event.payload
    if isinstance(payload, EmptyPayload):
        return MappingProxyType({})
    if isinstance(payload, GoalPayload):
        return MappingProxyType({"text": _REDACTED})
    if isinstance(payload, DecisionPayload):
        return MappingProxyType({"decision": payload.decision, "reason": _REDACTED})
    if isinstance(payload, SelectionPayload):
        return MappingProxyType({"selection": payload.selection, "reason": _REDACTED})
    if isinstance(payload, ActionPayload):
        return MappingProxyType({"name": payload.name, "data": _REDACTED})
    if isinstance(payload, ObservationPayload):
        return MappingProxyType({"value": _REDACTED})
    if isinstance(payload, VerificationPayload):
        return MappingProxyType({"passed": payload.passed, "detail": _REDACTED})
    raise RuntimeUiValidationError("unsupported canonical event payload")


def _validate_safe_payload(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RuntimeUiValidationError("payload must be a mapping")
    result: dict[str, object] = {}
    if len(value) > 16:
        raise RuntimeUiValidationError("payload has too many fields")
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            raise RuntimeUiValidationError("payload keys must be bounded strings")
        if item is not None and not isinstance(item, bool | int | float | str):
            raise RuntimeUiValidationError("UI payload values must be scalar JSON data")
        if isinstance(item, str) and len(item) > 4096:
            raise RuntimeUiValidationError("UI payload string is too large")
        result[key] = item
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 16_384:
        raise RuntimeUiValidationError("UI payload exceeds encoded size bound")
    return MappingProxyType(result)


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeUiEvent:
    """Sequenced one-way telemetry record from one runtime instance."""

    runtime_instance_id: UUID
    sequence: int
    timestamp: datetime
    kind: RuntimeUiEventKind
    state: str
    correlation_id: UUID | None = None
    task_id: str | None = None
    payload: Mapping[str, object] = MappingProxyType({})
    schema_version: int = RUNTIME_UI_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _uuid(self.runtime_instance_id, field_name="runtime_instance_id")
        if type(self.sequence) is not int or not 0 <= self.sequence <= MAX_RUNTIME_UI_SEQUENCE:
            raise RuntimeUiValidationError("sequence is outside the supported range")
        object.__setattr__(self, "timestamp", _timestamp(self.timestamp))
        if not isinstance(self.kind, RuntimeUiEventKind):
            raise TypeError("kind must be RuntimeUiEventKind")
        object.__setattr__(self, "state", _state(self.state))
        if self.correlation_id is not None:
            _uuid(self.correlation_id, field_name="correlation_id")
        if self.task_id is not None:
            if not isinstance(self.task_id, str) or not self.task_id.strip() or len(self.task_id) > 512:
                raise RuntimeUiValidationError("task_id must be bounded non-empty text")
        object.__setattr__(self, "payload", _validate_safe_payload(self.payload))
        if self.schema_version != RUNTIME_UI_SCHEMA_VERSION:
            raise RuntimeUiValidationError("unsupported runtime UI schema version")
        if self.kind is RuntimeUiEventKind.EVENT and self.correlation_id is None:
            raise RuntimeUiValidationError("runtime event telemetry requires correlation_id")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "runtime_instance_id": str(self.runtime_instance_id),
            "sequence": self.sequence,
            "timestamp": _format(self.timestamp),
            "kind": self.kind.value,
            "state": self.state,
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
            "task_id": self.task_id,
            "payload": dict(self.payload),
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
    def from_dict(cls, raw: Mapping[str, object]) -> RuntimeUiEvent:
        expected = {
            "schema_version",
            "runtime_instance_id",
            "sequence",
            "timestamp",
            "kind",
            "state",
            "correlation_id",
            "task_id",
            "payload",
        }
        if set(raw) != expected:
            raise RuntimeUiValidationError("runtime UI fields are incomplete or unknown")
        try:
            runtime_id = UUID(str(raw["runtime_instance_id"]))
            correlation_raw = raw["correlation_id"]
            correlation_id = None if correlation_raw is None else UUID(str(correlation_raw))
            kind = RuntimeUiEventKind(str(raw["kind"]))
        except (ValueError, TypeError) as exc:
            raise RuntimeUiValidationError("runtime UI identity or kind is invalid") from exc
        task_id = raw["task_id"]
        if task_id is not None and not isinstance(task_id, str):
            raise RuntimeUiValidationError("task_id must be a string or null")
        payload = raw["payload"]
        if not isinstance(payload, Mapping):
            raise RuntimeUiValidationError("payload must be an object")
        return cls(
            runtime_instance_id=runtime_id,
            sequence=raw["sequence"],  # type: ignore[arg-type]
            timestamp=_parse_timestamp(raw["timestamp"]),
            kind=kind,
            state=_state(raw["state"]),
            correlation_id=correlation_id,
            task_id=task_id,
            payload=payload,
            schema_version=raw["schema_version"],  # type: ignore[arg-type]
        )

    @classmethod
    def from_json(cls, text: str) -> RuntimeUiEvent:
        if not isinstance(text, str):
            raise TypeError("runtime UI JSON must be text")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeUiValidationError("runtime UI JSON is malformed") from exc
        if not isinstance(raw, Mapping):
            raise RuntimeUiValidationError("runtime UI JSON root must be an object")
        return cls.from_dict(raw)


def project_event_for_ui(
    event: Event,
    *,
    runtime_instance_id: UUID,
    sequence: int,
) -> RuntimeUiEvent:
    """Create redacted UI telemetry from one canonical runtime event."""
    if not isinstance(event, Event):
        raise TypeError("event must be a canonical Event")
    return RuntimeUiEvent(
        runtime_instance_id=runtime_instance_id,
        sequence=sequence,
        timestamp=event.timestamp,
        kind=RuntimeUiEventKind.EVENT,
        state=event.event_type.value,
        correlation_id=event.correlation_id,
        task_id=event.task_id,
        payload=_safe_payload(event),
    )
