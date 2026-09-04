"""Immutable security-audit contracts for the AgentX Trusted Kernel.

Audit records describe security-relevant facts after a decision or observation.
They never grant authority, execute actions, publish events, or persist
themselves. C2.04 owns eventual audit storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from agentx.core.ids import TaskId
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskLevel
from agentx.kernel.secrets import SecretRef, SecretValue


class AuditOutcome(Enum):
    """Controlled descriptive outcomes for security-relevant audit facts."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    STOP_REQUESTED = "STOP_REQUESTED"


def _validate_audit_text(value: object, *, field_name: str) -> str:
    if isinstance(value, SecretValue):
        raise TypeError(f"{field_name} must never contain a SecretValue")
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty and trimmed")
    return value


def _validate_optional_audit_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _validate_audit_text(value, field_name=field_name)


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID")
    if value.int == 0:
        raise ValueError(f"{field_name} must not be the nil UUID")
    return value


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class AuditContext:
    """Small typed security context that is safe to retain with an audit fact.

    This deliberately is not an arbitrary metadata dictionary. A permission or
    secret reference recorded here is descriptive history only.
    """

    actor: str | None = None
    target: str | None = None
    permission: Permission | None = None
    secret_ref: SecretRef | None = None

    def __post_init__(self) -> None:
        _validate_optional_audit_text(self.actor, field_name="actor")
        _validate_optional_audit_text(self.target, field_name="target")
        if self.permission is not None and not isinstance(self.permission, Permission):
            raise TypeError("permission must be a Permission or None")
        if self.secret_ref is not None and not isinstance(self.secret_ref, SecretRef):
            raise TypeError("secret_ref must be a SecretRef or None")
        if (
            self.actor is None
            and self.target is None
            and self.permission is None
            and self.secret_ref is None
        ):
            raise ValueError("AuditContext must contain at least one explicit context field")

    def __repr__(self) -> str:
        permission = self.permission.value if self.permission is not None else None
        return (
            "AuditContext("
            f"actor={self.actor!r}, "
            f"target={self.target!r}, "
            f"permission={permission!r}, "
            f"secret_ref={self.secret_ref!r}"
            ")"
        )


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class SecurityAuditRecord:
    """Immutable descriptive record of one security-relevant fact."""

    audit_id: UUID
    timestamp: datetime
    operation: str
    outcome: AuditOutcome
    reason: str
    task_id: TaskId | None = None
    correlation_id: UUID | None = None
    risk_level: RiskLevel | None = None
    context: AuditContext | None = None

    def __post_init__(self) -> None:
        _validate_uuid(self.audit_id, field_name="audit_id")
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))

        _validate_audit_text(self.operation, field_name="operation")
        if not isinstance(self.outcome, AuditOutcome):
            raise TypeError("outcome must be an AuditOutcome")
        _validate_audit_text(self.reason, field_name="reason")

        if self.task_id is not None and not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be a TaskId or None")
        if self.correlation_id is not None:
            _validate_uuid(self.correlation_id, field_name="correlation_id")
        if self.risk_level is not None and not isinstance(self.risk_level, RiskLevel):
            raise TypeError("risk_level must be a RiskLevel or None")
        if self.context is not None and not isinstance(self.context, AuditContext):
            raise TypeError("context must be an AuditContext or None")

    @classmethod
    def create(
        cls,
        *,
        operation: str,
        outcome: AuditOutcome,
        reason: str,
        task_id: TaskId | None = None,
        correlation_id: UUID | None = None,
        risk_level: RiskLevel | None = None,
        context: AuditContext | None = None,
        audit_id: UUID | None = None,
        timestamp: datetime | None = None,
    ) -> SecurityAuditRecord:
        """Create one record with fresh identity/time unless explicitly supplied."""

        return cls(
            audit_id=uuid4() if audit_id is None else audit_id,
            timestamp=datetime.now(UTC) if timestamp is None else timestamp,
            operation=operation,
            outcome=outcome,
            reason=reason,
            task_id=task_id,
            correlation_id=correlation_id,
            risk_level=risk_level,
            context=context,
        )

    def __repr__(self) -> str:
        task_id = self.task_id.to_str() if self.task_id is not None else None
        correlation_id = str(self.correlation_id) if self.correlation_id is not None else None
        risk_level = self.risk_level.value if self.risk_level is not None else None
        return (
            "SecurityAuditRecord("
            f"audit_id={str(self.audit_id)!r}, "
            f"timestamp={_format_timestamp(self.timestamp)!r}, "
            f"operation={self.operation!r}, "
            f"outcome={self.outcome.value!r}, "
            f"reason={self.reason!r}, "
            f"task_id={task_id!r}, "
            f"correlation_id={correlation_id!r}, "
            f"risk_level={risk_level!r}, "
            f"context={self.context!r}"
            ")"
        )


__all__ = ["AuditContext", "AuditOutcome", "SecurityAuditRecord"]
