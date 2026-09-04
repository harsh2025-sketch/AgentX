"""Tests for C1.09 immutable security-audit contracts."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest

from agentx.core.ids import TaskId
from agentx.kernel.action_gate import ActionGate, GateRequest
from agentx.kernel.audit import AuditContext, AuditOutcome, SecurityAuditRecord
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.risk import RiskAssessment, RiskLevel
from agentx.kernel.secrets import SecretRef, SecretValue

_AUDIT_ID = UUID("11111111-1111-4111-8111-111111111111")
_CORRELATION_ID = UUID("22222222-2222-4222-8222-222222222222")
_TASK_ID = TaskId(UUID("33333333-3333-4333-8333-333333333333"))
_TIMESTAMP = datetime(2026, 9, 5, 0, 0, tzinfo=UTC)


def _record(**overrides: object) -> SecurityAuditRecord:
    values: dict[str, object] = {
        "audit_id": _AUDIT_ID,
        "timestamp": _TIMESTAMP,
        "operation": "filesystem.write",
        "outcome": AuditOutcome.DENY,
        "reason": "Required permission was absent.",
        "task_id": _TASK_ID,
        "correlation_id": _CORRELATION_ID,
        "risk_level": RiskLevel.R2,
        "context": AuditContext(
            actor="kernel.action_gate",
            target="C:/AgentX/state.json",
            permission=Permission.WRITE,
            secret_ref=SecretRef("provider/api-key"),
        ),
    }
    values.update(overrides)
    return SecurityAuditRecord(**values)  # type: ignore[arg-type]


def _risk() -> RiskAssessment:
    return RiskAssessment(
        level=RiskLevel.R2,
        reason="R2 test assessment.",
        reversible=False,
        external_effect=False,
    )


def test_audit_record_is_immutable() -> None:
    record = _record()

    with pytest.raises(FrozenInstanceError):
        record.__setattr__("reason", "changed")

    assert record.reason == "Required permission was absent."


def test_audit_context_is_structured_not_arbitrary_metadata() -> None:
    assert tuple(field.name for field in fields(AuditContext)) == (
        "actor",
        "target",
        "permission",
        "secret_ref",
    )
    with pytest.raises(ValueError, match="at least one"):
        AuditContext()


def test_audit_record_validates_required_fields_and_types() -> None:
    with pytest.raises(ValueError, match="operation"):
        _record(operation=" filesystem.write")
    with pytest.raises(ValueError, match="reason"):
        _record(reason="")
    with pytest.raises(TypeError, match="AuditOutcome"):
        _record(outcome=cast(AuditOutcome, "DENY"))
    with pytest.raises(TypeError, match="TaskId"):
        _record(task_id=cast(TaskId, "task-1"))
    with pytest.raises(ValueError, match="nil"):
        _record(correlation_id=UUID(int=0))
    with pytest.raises(ValueError, match="timezone-aware"):
        _record(timestamp=datetime(2026, 9, 5))


def test_audit_timestamp_is_normalized_to_utc() -> None:
    offset = _TIMESTAMP.astimezone(timezone(timedelta(hours=5, minutes=30)))
    record = _record(timestamp=offset)

    assert record.timestamp == _TIMESTAMP
    assert record.timestamp.tzinfo is UTC


def test_audit_repr_is_deterministic_and_structured() -> None:
    first = _record()
    second = _record()

    assert repr(first) == repr(second)
    assert "SecurityAuditRecord(" in repr(first)
    assert "audit_id='11111111-1111-4111-8111-111111111111'" in repr(first)
    assert "outcome='DENY'" in repr(first)
    assert "risk_level='R2'" in repr(first)
    assert "permission='WRITE'" in repr(first)
    assert "provider/api-key" in repr(first)


def test_secret_value_cannot_enter_supported_audit_text_or_context_path() -> None:
    raw = "super-secret-material-92834"
    secret = SecretValue(raw)

    with pytest.raises(TypeError, match="SecretValue"):
        _record(reason=cast(str, secret))
    with pytest.raises(TypeError, match="SecretValue"):
        AuditContext(actor=cast(str, secret))
    with pytest.raises(TypeError, match="SecretRef"):
        AuditContext(secret_ref=cast(SecretRef, secret))

    safe = _record(context=AuditContext(secret_ref=SecretRef("provider/api-key")))
    assert raw not in repr(safe)


def test_audit_record_has_no_raw_secret_field() -> None:
    names = {field.name for field in fields(SecurityAuditRecord)}

    assert "secret" not in names
    assert "secret_value" not in names
    assert "secret_material" not in names


def test_audit_record_is_descriptive_not_action_authority() -> None:
    record = _record()
    request = GateRequest(
        operation="filesystem.write",
        required_permission=Permission.WRITE,
        risk_assessment=_risk(),
    )

    with pytest.raises(TypeError, match="AuthorityContext"):
        ActionGate().evaluate(request, cast(AuthorityContext, record))


def test_historical_audit_outcome_does_not_execute_or_grant_permission() -> None:
    record = _record(outcome=AuditOutcome.ALLOW)
    authority = AuthorityContext(permissions=frozenset())

    assert record.outcome is AuditOutcome.ALLOW
    assert Permission.WRITE not in authority.permissions


def test_create_supplies_identity_and_timestamp_without_side_effects() -> None:
    record = SecurityAuditRecord.create(
        operation="security.observe",
        outcome=AuditOutcome.SUCCEEDED,
        reason="Security state was observed.",
    )

    assert record.audit_id.int != 0
    assert record.timestamp.tzinfo is UTC
    assert record.task_id is None
    assert record.context is None
