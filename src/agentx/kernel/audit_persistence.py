"""Narrow C1.09-to-C2.04 audit persistence adapter.

This module does not change Trusted Kernel audit semantics. It converts between
the canonical :class:`SecurityAuditRecord` and the persistence-neutral core
:class:`AuditRecordSnapshot` so outer infrastructure never needs a kernel
dependency. Both directions preserve descriptive data only; neither direction
creates authority.
"""

from __future__ import annotations

from agentx.core.audit_records import AuditRecordSnapshot
from agentx.kernel.audit import AuditContext, AuditOutcome, SecurityAuditRecord
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskLevel
from agentx.kernel.secrets import SecretRef

__all__ = [
    "AuditPersistenceAdapterError",
    "restore_security_audit",
    "snapshot_security_audit",
]


class AuditPersistenceAdapterError(ValueError):
    """Raised when persisted descriptive strings cannot map to C1.09 vocabulary."""


def snapshot_security_audit(record: SecurityAuditRecord) -> AuditRecordSnapshot:
    """Convert one canonical C1.09 audit fact to inert persistence data."""

    if not isinstance(record, SecurityAuditRecord):
        raise TypeError("record must be a SecurityAuditRecord")

    context = record.context
    return AuditRecordSnapshot(
        audit_id=record.audit_id,
        timestamp=record.timestamp,
        operation=record.operation,
        outcome=record.outcome.value,
        reason=record.reason,
        task_id=record.task_id,
        correlation_id=record.correlation_id,
        risk_level=record.risk_level.value if record.risk_level is not None else None,
        actor=context.actor if context is not None else None,
        target=context.target if context is not None else None,
        permission=(
            context.permission.value
            if context is not None and context.permission is not None
            else None
        ),
        secret_ref=(
            context.secret_ref.identifier
            if context is not None and context.secret_ref is not None
            else None
        ),
    )


def restore_security_audit(snapshot: AuditRecordSnapshot) -> SecurityAuditRecord:
    """Reconstruct the canonical C1.09 audit fact from inert persistence data.

    Persisted strings are revalidated against the *existing* C1.09 enums and
    SecretRef contract. This adapter defines no competing policy vocabulary.
    """

    if not isinstance(snapshot, AuditRecordSnapshot):
        raise TypeError("snapshot must be an AuditRecordSnapshot")

    try:
        outcome = AuditOutcome(snapshot.outcome)
    except ValueError as exc:
        raise AuditPersistenceAdapterError(
            f"stored outcome is not a canonical AuditOutcome: {snapshot.outcome!r}"
        ) from exc

    risk_level: RiskLevel | None = None
    if snapshot.risk_level is not None:
        try:
            risk_level = RiskLevel(snapshot.risk_level)
        except ValueError as exc:
            raise AuditPersistenceAdapterError(
                f"stored risk_level is not canonical: {snapshot.risk_level!r}"
            ) from exc

    permission: Permission | None = None
    if snapshot.permission is not None:
        try:
            permission = Permission(snapshot.permission)
        except ValueError as exc:
            raise AuditPersistenceAdapterError(
                f"stored permission is not canonical: {snapshot.permission!r}"
            ) from exc

    secret_ref: SecretRef | None = None
    if snapshot.secret_ref is not None:
        try:
            secret_ref = SecretRef(snapshot.secret_ref)
        except (TypeError, ValueError) as exc:
            raise AuditPersistenceAdapterError("stored secret_ref is not canonical") from exc

    context: AuditContext | None = None
    if any(
        value is not None for value in (snapshot.actor, snapshot.target, permission, secret_ref)
    ):
        context = AuditContext(
            actor=snapshot.actor,
            target=snapshot.target,
            permission=permission,
            secret_ref=secret_ref,
        )

    return SecurityAuditRecord(
        audit_id=snapshot.audit_id,
        timestamp=snapshot.timestamp,
        operation=snapshot.operation,
        outcome=outcome,
        reason=snapshot.reason,
        task_id=snapshot.task_id,
        correlation_id=snapshot.correlation_id,
        risk_level=risk_level,
        context=context,
    )
