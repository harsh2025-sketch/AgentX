"""Persistence-neutral inert audit-history snapshot contract for C2.04.

The Trusted Kernel owns audit semantics in :mod:`agentx.kernel.audit`. This core
record owns only a stable, deterministic historical representation that outer
persistence can store without importing the kernel. String-valued outcome,
risk, permission, and secret-reference fields are descriptive data, never
authority.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from agentx.core.ids import TaskId

__all__ = [
    "CURRENT_AUDIT_RECORD_SCHEMA_VERSION",
    "AuditRecordSnapshot",
    "AuditRecordValidationError",
    "UnsupportedAuditRecordSchemaVersionError",
]

CURRENT_AUDIT_RECORD_SCHEMA_VERSION: Final[int] = 1
_AUDIT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "audit_id",
        "timestamp",
        "operation",
        "outcome",
        "reason",
        "task_id",
        "correlation_id",
        "risk_level",
        "actor",
        "target",
        "permission",
        "secret_ref",
    }
)


class AuditRecordValidationError(ValueError):
    """Raised when a persistent audit snapshot is malformed."""


class UnsupportedAuditRecordSchemaVersionError(AuditRecordValidationError):
    """Raised when encoded audit history uses an unsupported schema version."""


def _validate_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise AuditRecordValidationError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise AuditRecordValidationError(f"{field_name} must be non-empty and trimmed")
    return value


def _validate_optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _validate_text(value, field_name=field_name)


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise AuditRecordValidationError(f"{field_name} must be a UUID")
    if value.int == 0:
        raise AuditRecordValidationError(f"{field_name} must not be the nil UUID")
    return value


def _parse_uuid(value: object, *, field_name: str, optional: bool = False) -> UUID | None:
    if value is None:
        if optional:
            return None
        raise AuditRecordValidationError(f"{field_name} must be a UUID string")
    if not isinstance(value, str):
        suffix = " or null" if optional else ""
        raise AuditRecordValidationError(f"{field_name} must be a UUID string{suffix}")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise AuditRecordValidationError(f"{field_name} must be a valid UUID string") from exc
    return _validate_uuid(parsed, field_name=field_name)


def _validate_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise AuditRecordValidationError("timestamp must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise AuditRecordValidationError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise AuditRecordValidationError("timestamp must be an ISO-8601 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise AuditRecordValidationError("timestamp must be a valid ISO-8601 datetime") from exc
    return _validate_timestamp(parsed)


def _parse_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AuditRecordValidationError("task_id must be a UUID string or null")
    try:
        return TaskId.parse(value)
    except ValueError as exc:
        raise AuditRecordValidationError("task_id must be a valid non-nil UUID string") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditRecordSnapshot:
    """Immutable historical snapshot of one security-relevant audit fact.

    Controlled Trusted-Kernel vocabularies are adapted into their canonical
    string values by ``agentx.kernel.audit_persistence``. This core record does
    not reinterpret those strings and cannot create authority from them.
    """

    audit_id: UUID
    timestamp: datetime
    operation: str
    outcome: str
    reason: str
    task_id: TaskId | None = None
    correlation_id: UUID | None = None
    risk_level: str | None = None
    actor: str | None = None
    target: str | None = None
    permission: str | None = None
    secret_ref: str | None = None

    def __post_init__(self) -> None:
        _validate_uuid(self.audit_id, field_name="audit_id")
        object.__setattr__(self, "timestamp", _validate_timestamp(self.timestamp))
        _validate_text(self.operation, field_name="operation")
        _validate_text(self.outcome, field_name="outcome")
        _validate_text(self.reason, field_name="reason")
        if self.task_id is not None and not isinstance(self.task_id, TaskId):
            raise AuditRecordValidationError("task_id must be a TaskId or None")
        if self.correlation_id is not None:
            _validate_uuid(self.correlation_id, field_name="correlation_id")
        _validate_optional_text(self.risk_level, field_name="risk_level")
        _validate_optional_text(self.actor, field_name="actor")
        _validate_optional_text(self.target, field_name="target")
        _validate_optional_text(self.permission, field_name="permission")
        _validate_optional_text(self.secret_ref, field_name="secret_ref")

    def to_dict(self) -> dict[str, object]:
        """Return the stable versioned JSON-compatible representation."""

        return {
            "schema_version": CURRENT_AUDIT_RECORD_SCHEMA_VERSION,
            "audit_id": str(self.audit_id),
            "timestamp": _format_timestamp(self.timestamp),
            "operation": self.operation,
            "outcome": self.outcome,
            "reason": self.reason,
            "task_id": self.task_id.to_str() if self.task_id is not None else None,
            "correlation_id": str(self.correlation_id) if self.correlation_id is not None else None,
            "risk_level": self.risk_level,
            "actor": self.actor,
            "target": self.target,
            "permission": self.permission,
            "secret_ref": self.secret_ref,
        }

    def to_json(self) -> str:
        """Serialize deterministically without executable/dynamic types."""

        return json.dumps(self.to_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> AuditRecordSnapshot:
        """Reconstruct a validated immutable historical snapshot."""

        if not isinstance(raw, Mapping):
            raise AuditRecordValidationError("audit payload must be a mapping")
        actual = set(raw)
        if actual != _AUDIT_FIELDS:
            raise AuditRecordValidationError(
                f"audit payload must contain exactly {sorted(_AUDIT_FIELDS)}; got {sorted(actual)}"
            )

        schema_version = raw["schema_version"]
        if type(schema_version) is not int:
            raise AuditRecordValidationError("schema_version must be an int")
        if schema_version != CURRENT_AUDIT_RECORD_SCHEMA_VERSION:
            raise UnsupportedAuditRecordSchemaVersionError(
                f"unsupported audit record schema version: {schema_version}"
            )

        audit_id = _parse_uuid(raw["audit_id"], field_name="audit_id")
        assert audit_id is not None
        correlation_id = _parse_uuid(
            raw["correlation_id"], field_name="correlation_id", optional=True
        )

        return cls(
            audit_id=audit_id,
            timestamp=_parse_timestamp(raw["timestamp"]),
            operation=_validate_text(raw["operation"], field_name="operation"),
            outcome=_validate_text(raw["outcome"], field_name="outcome"),
            reason=_validate_text(raw["reason"], field_name="reason"),
            task_id=_parse_task_id(raw["task_id"]),
            correlation_id=correlation_id,
            risk_level=_validate_optional_text(raw["risk_level"], field_name="risk_level"),
            actor=_validate_optional_text(raw["actor"], field_name="actor"),
            target=_validate_optional_text(raw["target"], field_name="target"),
            permission=_validate_optional_text(raw["permission"], field_name="permission"),
            secret_ref=_validate_optional_text(raw["secret_ref"], field_name="secret_ref"),
        )

    @classmethod
    def from_json(cls, payload: str) -> AuditRecordSnapshot:
        """Decode JSON and reject malformed/non-object historical data."""

        if not isinstance(payload, str):
            raise AuditRecordValidationError("audit JSON payload must be a string")
        try:
            raw = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise AuditRecordValidationError("audit JSON payload is malformed") from exc
        if not isinstance(raw, dict):
            raise AuditRecordValidationError("audit JSON payload must decode to an object")
        return cls.from_dict(raw)
