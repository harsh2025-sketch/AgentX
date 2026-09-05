"""Tests for the C1.09-to-C2.04 audit persistence boundary."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from agentx.core.audit_records import (
    CURRENT_AUDIT_RECORD_SCHEMA_VERSION,
    AuditRecordSnapshot,
    AuditRecordValidationError,
    UnsupportedAuditRecordSchemaVersionError,
)
from agentx.core.ids import TaskId
from agentx.kernel.audit import AuditContext, AuditOutcome, SecurityAuditRecord
from agentx.kernel.audit_persistence import (
    AuditPersistenceAdapterError,
    restore_security_audit,
    snapshot_security_audit,
)
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskLevel
from agentx.kernel.secrets import SecretRef

_T0 = datetime(2026, 2, 3, 4, 5, 6, 789012, tzinfo=UTC)


def _security_record() -> SecurityAuditRecord:
    return SecurityAuditRecord(
        audit_id=uuid4(),
        timestamp=_T0,
        operation="artifact.register",
        outcome=AuditOutcome.ALLOW,
        reason="Explicit historical audit fact.",
        task_id=TaskId.create(),
        correlation_id=uuid4(),
        risk_level=RiskLevel.R4,
        context=AuditContext(
            actor="agentx",
            target="artifact:report",
            permission=Permission.WRITE,
            secret_ref=SecretRef("provider/api-key"),
        ),
    )


def _snapshot() -> AuditRecordSnapshot:
    return snapshot_security_audit(_security_record())


def test_security_audit_maps_to_persistence_snapshot_without_policy_duplication() -> None:
    source = _security_record()
    snapshot = snapshot_security_audit(source)

    assert snapshot.audit_id == source.audit_id
    assert snapshot.outcome == AuditOutcome.ALLOW.value
    assert snapshot.risk_level == RiskLevel.R4.value
    assert snapshot.permission == Permission.WRITE.value
    assert snapshot.secret_ref == "provider/api-key"
    assert snapshot.task_id == source.task_id
    assert snapshot.correlation_id == source.correlation_id


def test_adapter_round_trip_preserves_canonical_c1_09_record() -> None:
    source = _security_record()

    restored = restore_security_audit(snapshot_security_audit(source))

    assert restored == source
    assert isinstance(restored.outcome, AuditOutcome)
    assert restored.context is not None
    assert isinstance(restored.context.permission, Permission)
    assert isinstance(restored.risk_level, RiskLevel)


def test_snapshot_serialization_is_deterministic_and_versioned() -> None:
    snapshot = _snapshot()

    encoded = snapshot.to_json()
    assert encoded == snapshot.to_json()
    assert AuditRecordSnapshot.from_json(encoded) == snapshot
    assert json.loads(encoded)["schema_version"] == CURRENT_AUDIT_RECORD_SCHEMA_VERSION


def test_snapshot_serialization_uses_exact_structure() -> None:
    payload = _snapshot().to_dict()

    assert set(payload) == {
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
    assert payload["timestamp"] == "2026-02-03T04:05:06.789012Z"


def test_snapshot_unsupported_schema_is_rejected() -> None:
    payload = _snapshot().to_dict()
    payload["schema_version"] = CURRENT_AUDIT_RECORD_SCHEMA_VERSION + 1

    with pytest.raises(UnsupportedAuditRecordSchemaVersionError):
        AuditRecordSnapshot.from_dict(payload)


def test_snapshot_malformed_identity_and_timestamp_are_rejected() -> None:
    payload = _snapshot().to_dict()
    payload["audit_id"] = str(UUID(int=0))
    with pytest.raises(AuditRecordValidationError, match="audit_id"):
        AuditRecordSnapshot.from_dict(payload)

    payload = _snapshot().to_dict()
    payload["timestamp"] = "not-time"
    with pytest.raises(AuditRecordValidationError, match="timestamp"):
        AuditRecordSnapshot.from_dict(payload)


def test_snapshot_unknown_or_missing_structure_is_rejected() -> None:
    payload = _snapshot().to_dict()
    payload["extra"] = "x"
    with pytest.raises(AuditRecordValidationError, match="exactly"):
        AuditRecordSnapshot.from_dict(payload)

    payload = _snapshot().to_dict()
    del payload["outcome"]
    with pytest.raises(AuditRecordValidationError, match="exactly"):
        AuditRecordSnapshot.from_dict(payload)


def test_snapshot_malformed_json_is_rejected() -> None:
    with pytest.raises(AuditRecordValidationError, match="malformed"):
        AuditRecordSnapshot.from_json("{bad-json")
    with pytest.raises(AuditRecordValidationError, match="object"):
        AuditRecordSnapshot.from_json("[]")


def test_snapshot_is_immutable() -> None:
    snapshot = _snapshot()

    with pytest.raises(FrozenInstanceError):
        snapshot.outcome = "DENY"  # type: ignore[misc]


def test_adapter_revalidates_outcome_against_existing_c1_09_enum() -> None:
    source = _snapshot()
    hostile = AuditRecordSnapshot(
        audit_id=source.audit_id,
        timestamp=source.timestamp,
        operation=source.operation,
        outcome="SUPER_ADMIN",
        reason=source.reason,
        task_id=source.task_id,
        correlation_id=source.correlation_id,
        risk_level=source.risk_level,
        actor=source.actor,
        target=source.target,
        permission=source.permission,
        secret_ref=source.secret_ref,
    )

    with pytest.raises(AuditPersistenceAdapterError, match="AuditOutcome"):
        restore_security_audit(hostile)


def test_adapter_revalidates_permission_and_risk_against_kernel_vocabularies() -> None:
    source = _snapshot()
    invalid_permission = AuditRecordSnapshot(
        audit_id=uuid4(),
        timestamp=source.timestamp,
        operation=source.operation,
        outcome=source.outcome,
        reason=source.reason,
        permission="ADMIN",
    )
    invalid_risk = AuditRecordSnapshot(
        audit_id=uuid4(),
        timestamp=source.timestamp,
        operation=source.operation,
        outcome=source.outcome,
        reason=source.reason,
        risk_level="R99",
    )

    with pytest.raises(AuditPersistenceAdapterError, match="permission"):
        restore_security_audit(invalid_permission)
    with pytest.raises(AuditPersistenceAdapterError, match="risk_level"):
        restore_security_audit(invalid_risk)


def test_adapter_accepts_no_context_and_never_persists_secret_material() -> None:
    record = SecurityAuditRecord.create(
        operation="read",
        outcome=AuditOutcome.SUCCEEDED,
        reason="Historical read completed.",
        audit_id=uuid4(),
        timestamp=_T0,
    )

    snapshot = snapshot_security_audit(record)
    restored = restore_security_audit(snapshot)

    assert snapshot.secret_ref is None
    assert snapshot.permission is None
    assert restored.context is None
