"""Typed, authority-free scheduling contracts for AgentX M12."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast
from uuid import UUID, uuid4

from agentx.core.tasks import TaskPriority

SCHEDULE_SCHEMA_VERSION: Final[int] = 1
_MAX_TEXT: Final[int] = 512
_MAX_CONTEXT_BYTES: Final[int] = 16_384
_MAX_INTERVAL: Final[timedelta] = timedelta(days=3650)


class ScheduleValidationError(ValueError):
    pass


class ScheduleKind(StrEnum):
    ONE_SHOT = "one_shot"
    INTERVAL = "interval"


class ScheduleStatus(StrEnum):
    ENABLED = "enabled"
    DISPATCHING = "dispatching"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class BackgroundRunStatus(StrEnum):
    RESERVED = "reserved"
    RUNNING = "running"
    VERIFIED = "verified"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


def _text(value: object, *, field_name: str, maximum: int = _MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip() or len(value) > maximum:
        raise ScheduleValidationError(f"{field_name} must be bounded non-empty trimmed text")
    if any(character < " " or character == "\x7f" for character in value):
        raise ScheduleValidationError(f"{field_name} must not contain control characters")
    return value


def _time(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _freeze_json(value: object, *, path: str, depth: int = 0) -> object:
    if depth > 6:
        raise ScheduleValidationError(f"{path} nesting is too deep")
    if value is None or isinstance(value, bool | int | str):
        if isinstance(value, str) and len(value) > 4096:
            raise ScheduleValidationError(f"{path} string is too long")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ScheduleValidationError(f"{path} contains non-finite float")
        return value
    if isinstance(value, Mapping):
        if len(value) > 128:
            raise ScheduleValidationError(f"{path} has too many fields")
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 128:
                raise ScheduleValidationError(f"{path} keys must be bounded strings")
            lowered = key.lower()
            reserved = (
                "permission",
                "authority",
                "privilege",
                "bypass",
                "secret",
                "token",
                "password",
                "api_key",
            )
            if any(token in lowered for token in reserved):
                raise ScheduleValidationError(
                    f"{path} key {key!r} is reserved for authority or secrets"
                )
            result[key] = _freeze_json(item, path=f"{path}.{key}", depth=depth + 1)
        return MappingProxyType(result)
    if isinstance(value, tuple | list):
        if len(value) > 256:
            raise ScheduleValidationError(f"{path} array is too large")
        return tuple(_freeze_json(item, path=f"{path}[]", depth=depth + 1) for item in value)
    raise ScheduleValidationError(f"{path} contains unsupported type {type(value).__name__}")


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _format_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ScheduleValidationError(f"{field_name} must be an ISO-8601 string")
    text = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ScheduleValidationError(f"{field_name} is not a valid ISO-8601 datetime") from exc
    return _time(parsed, field_name=field_name)


@dataclass(frozen=True, slots=True, kw_only=True)
class ScheduledTaskIntent:
    """Durable DATA describing work to pass to trusted AgentX orchestration."""

    objective: str
    route_key: str
    source: str
    priority: TaskPriority = TaskPriority.NORMAL
    context: Mapping[str, object] = field(default_factory=dict)
    estimated_resource_units: int = 1
    estimated_model_calls: int = 0
    estimated_machine_actions: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "objective",
            _text(self.objective, field_name="objective", maximum=2048),
        )
        object.__setattr__(self, "route_key", _text(self.route_key, field_name="route_key"))
        object.__setattr__(self, "source", _text(self.source, field_name="source"))
        if not isinstance(self.priority, TaskPriority):
            raise TypeError("priority must be TaskPriority")
        frozen = _freeze_json(self.context, path="context")
        if not isinstance(frozen, Mapping):
            raise TypeError("context must be a mapping")
        encoded = json.dumps(
            _json_value(frozen),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        if len(encoded) > _MAX_CONTEXT_BYTES:
            raise ScheduleValidationError("context exceeds encoded size bound")
        object.__setattr__(self, "context", frozen)
        fields = (
            "estimated_resource_units",
            "estimated_model_calls",
            "estimated_machine_actions",
        )
        for name in fields:
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 1_000_000:
                raise ScheduleValidationError(f"{name} must be an int from 0 to 1000000")

    def to_dict(self) -> dict[str, object]:
        return {
            "objective": self.objective,
            "route_key": self.route_key,
            "source": self.source,
            "priority": self.priority.value,
            "context": _json_value(self.context),
            "estimated_resource_units": self.estimated_resource_units,
            "estimated_model_calls": self.estimated_model_calls,
            "estimated_machine_actions": self.estimated_machine_actions,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ScheduledTaskIntent:
        required = {
            "objective",
            "route_key",
            "source",
            "priority",
            "context",
            "estimated_resource_units",
            "estimated_model_calls",
            "estimated_machine_actions",
        }
        if set(raw) != required:
            raise ScheduleValidationError("scheduled intent has missing or unknown fields")
        priority_raw = raw["priority"]
        if not isinstance(priority_raw, str):
            raise ScheduleValidationError("priority must be a string")
        try:
            priority = TaskPriority(priority_raw)
        except ValueError as exc:
            raise ScheduleValidationError("unknown task priority") from exc
        context = raw["context"]
        if not isinstance(context, Mapping):
            raise ScheduleValidationError("context must be an object")
        for field_name in (
            "estimated_resource_units",
            "estimated_model_calls",
            "estimated_machine_actions",
        ):
            if type(raw[field_name]) is not int:
                raise ScheduleValidationError(f"{field_name} must be an integer")
        objective = raw["objective"]
        route_key = raw["route_key"]
        source = raw["source"]
        if not isinstance(objective, str):
            raise ScheduleValidationError("objective must be a string")
        if not isinstance(route_key, str):
            raise ScheduleValidationError("route_key must be a string")
        if not isinstance(source, str):
            raise ScheduleValidationError("source must be a string")
        return cls(
            objective=objective,
            route_key=route_key,
            source=source,
            priority=priority,
            context=context,
            estimated_resource_units=cast(int, raw["estimated_resource_units"]),
            estimated_model_calls=cast(int, raw["estimated_model_calls"]),
            estimated_machine_actions=cast(int, raw["estimated_machine_actions"]),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ScheduledTask:
    schedule_id: UUID
    intent: ScheduledTaskIntent
    kind: ScheduleKind
    created_at: datetime
    next_due_at: datetime
    status: ScheduleStatus = ScheduleStatus.ENABLED
    interval: timedelta | None = None
    expires_at: datetime | None = None
    max_runs: int | None = None
    run_count: int = 0
    last_dispatch_id: UUID | None = None
    last_error: str | None = None
    schema_version: int = SCHEDULE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.schedule_id, UUID) or self.schedule_id.int == 0:
            raise ScheduleValidationError("schedule_id must be a non-nil UUID")
        if not isinstance(self.intent, ScheduledTaskIntent):
            raise TypeError("intent must be ScheduledTaskIntent")
        if not isinstance(self.kind, ScheduleKind):
            raise TypeError("kind must be ScheduleKind")
        if not isinstance(self.status, ScheduleStatus):
            raise TypeError("status must be ScheduleStatus")
        object.__setattr__(self, "created_at", _time(self.created_at, field_name="created_at"))
        object.__setattr__(self, "next_due_at", _time(self.next_due_at, field_name="next_due_at"))
        if self.expires_at is not None:
            object.__setattr__(
                self,
                "expires_at",
                _time(self.expires_at, field_name="expires_at"),
            )
            if self.expires_at <= self.created_at:
                raise ScheduleValidationError("expires_at must be after created_at")
        if self.kind is ScheduleKind.ONE_SHOT:
            if self.interval is not None:
                raise ScheduleValidationError("one-shot schedule must not define interval")
        else:
            if (
                not isinstance(self.interval, timedelta)
                or not timedelta(seconds=1) <= self.interval <= _MAX_INTERVAL
            ):
                raise ScheduleValidationError(
                    "recurring interval must be from 1 second to 3650 days"
                )
        if self.max_runs is not None and (type(self.max_runs) is not int or self.max_runs < 1):
            raise ScheduleValidationError("max_runs must be a positive int or None")
        if type(self.run_count) is not int or self.run_count < 0:
            raise ScheduleValidationError("run_count must be a non-negative int")
        if self.max_runs is not None and self.run_count > self.max_runs:
            raise ScheduleValidationError("run_count must not exceed max_runs")
        if self.last_dispatch_id is not None and (
            not isinstance(self.last_dispatch_id, UUID) or self.last_dispatch_id.int == 0
        ):
            raise ScheduleValidationError("last_dispatch_id must be a non-nil UUID or None")
        if self.last_error is not None:
            object.__setattr__(
                self,
                "last_error",
                _text(self.last_error, field_name="last_error", maximum=1024),
            )
        if self.schema_version != SCHEDULE_SCHEMA_VERSION:
            raise ScheduleValidationError("unsupported schedule schema version")

    @classmethod
    def one_shot(
        cls,
        *,
        intent: ScheduledTaskIntent,
        due_at: datetime,
        schedule_id: UUID | None = None,
        created_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> ScheduledTask:
        created = datetime.now(UTC) if created_at is None else created_at
        return cls(
            schedule_id=uuid4() if schedule_id is None else schedule_id,
            intent=intent,
            kind=ScheduleKind.ONE_SHOT,
            created_at=created,
            next_due_at=due_at,
            expires_at=expires_at,
        )

    @classmethod
    def recurring(
        cls,
        *,
        intent: ScheduledTaskIntent,
        first_due_at: datetime,
        interval: timedelta,
        schedule_id: UUID | None = None,
        created_at: datetime | None = None,
        expires_at: datetime | None = None,
        max_runs: int | None = None,
    ) -> ScheduledTask:
        created = datetime.now(UTC) if created_at is None else created_at
        return cls(
            schedule_id=uuid4() if schedule_id is None else schedule_id,
            intent=intent,
            kind=ScheduleKind.INTERVAL,
            created_at=created,
            next_due_at=first_due_at,
            interval=interval,
            expires_at=expires_at,
            max_runs=max_runs,
        )

    def to_dict(self) -> dict[str, object]:
        interval_microseconds = (
            None if self.interval is None else int(self.interval.total_seconds() * 1_000_000)
        )
        return {
            "schema_version": self.schema_version,
            "schedule_id": str(self.schedule_id),
            "intent": self.intent.to_dict(),
            "kind": self.kind.value,
            "created_at": _format_time(self.created_at),
            "next_due_at": _format_time(self.next_due_at),
            "status": self.status.value,
            "interval_microseconds": interval_microseconds,
            "expires_at": (None if self.expires_at is None else _format_time(self.expires_at)),
            "max_runs": self.max_runs,
            "run_count": self.run_count,
            "last_dispatch_id": (
                None if self.last_dispatch_id is None else str(self.last_dispatch_id)
            ),
            "last_error": self.last_error,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ScheduledTask:
        required = {
            "schema_version",
            "schedule_id",
            "intent",
            "kind",
            "created_at",
            "next_due_at",
            "status",
            "interval_microseconds",
            "expires_at",
            "max_runs",
            "run_count",
            "last_dispatch_id",
            "last_error",
        }
        if set(raw) != required:
            raise ScheduleValidationError("scheduled task has missing or unknown fields")
        if raw["schema_version"] != SCHEDULE_SCHEMA_VERSION:
            raise ScheduleValidationError("unsupported schedule schema version")
        try:
            schedule_id = UUID(str(raw["schedule_id"]))
            kind = ScheduleKind(str(raw["kind"]))
            status = ScheduleStatus(str(raw["status"]))
        except (ValueError, TypeError) as exc:
            raise ScheduleValidationError("invalid schedule identity or enum") from exc
        intent_raw = raw["intent"]
        if not isinstance(intent_raw, Mapping):
            raise ScheduleValidationError("intent must be an object")
        interval_raw = raw["interval_microseconds"]
        interval = None
        if interval_raw is not None:
            if type(interval_raw) is not int:
                raise ScheduleValidationError("interval_microseconds must be int or null")
            interval = timedelta(microseconds=interval_raw)
        expires_raw = raw["expires_at"]
        dispatch_raw = raw["last_dispatch_id"]
        max_runs_raw = raw["max_runs"]
        if max_runs_raw is not None and type(max_runs_raw) is not int:
            raise ScheduleValidationError("max_runs must be an integer or null")
        run_count_raw = raw["run_count"]
        if type(run_count_raw) is not int:
            raise ScheduleValidationError("run_count must be an integer")
        last_error_raw = raw["last_error"]
        if last_error_raw is not None and not isinstance(last_error_raw, str):
            raise ScheduleValidationError("last_error must be a string or null")
        return cls(
            schedule_id=schedule_id,
            intent=ScheduledTaskIntent.from_dict(intent_raw),
            kind=kind,
            created_at=_parse_time(raw["created_at"], field_name="created_at"),
            next_due_at=_parse_time(raw["next_due_at"], field_name="next_due_at"),
            status=status,
            interval=interval,
            expires_at=(
                None if expires_raw is None else _parse_time(expires_raw, field_name="expires_at")
            ),
            max_runs=max_runs_raw,
            run_count=run_count_raw,
            last_dispatch_id=(None if dispatch_raw is None else UUID(str(dispatch_raw))),
            last_error=last_error_raw,
        )

    @classmethod
    def from_json(cls, raw: str) -> ScheduledTask:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ScheduleValidationError("scheduled task JSON is malformed") from exc
        if not isinstance(value, Mapping):
            raise ScheduleValidationError("scheduled task JSON root must be an object")
        return cls.from_dict(value)


def next_recurring_due(
    *,
    previous_due: datetime,
    interval: timedelta,
    now: datetime,
) -> datetime:
    """Return first interval boundary strictly after now; missed runs are coalesced."""
    previous = _time(previous_due, field_name="previous_due")
    moment = _time(now, field_name="now")
    if not isinstance(interval, timedelta) or interval < timedelta(seconds=1):
        raise ScheduleValidationError("interval must be at least one second")
    if previous > moment:
        return previous
    interval_us = interval // timedelta(microseconds=1)
    elapsed_us = (moment - previous) // timedelta(microseconds=1)
    skipped = elapsed_us // interval_us + 1
    return previous + interval * skipped


__all__ = [
    "SCHEDULE_SCHEMA_VERSION",
    "BackgroundRunStatus",
    "ScheduleKind",
    "ScheduleStatus",
    "ScheduleValidationError",
    "ScheduledTask",
    "ScheduledTaskIntent",
    "next_recurring_due",
]
