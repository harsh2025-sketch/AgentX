"""Adversarial authority-boundary tests for the M4.03 procedure lifecycle policy.

The policy decides whether a ``ProcedureStatus`` transition is structurally
legal for a typed reason. These tests prove hostile text can never turn that
verdict into authority, execution, persistence, promotion, or resurrection:
no Permission is granted, no AuthorityContext is created, no ActionGate is
bypassed, no RiskLevel changes, no ResourceEnvelope widens, no EmergencyStop
clears, no Task transitions, no ProcedureStore is touched, no revision is
created, and no string ever unlocks a forbidden transition.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from tests.support.authority_proxy import ForbiddenAuthorityProxy

from agentx.core.ids import ProcedureId, TaskId
from agentx.core.procedure_lifecycle import (
    ProcedureLifecycleAssessment,
    ProcedureLifecycleDecision,
    ProcedureLifecycleError,
    ProcedureLifecycleReason,
    assess_procedure_transition,
    is_transition_permitted,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.core.tasks import Task, TaskPriority, TaskStatus
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceEnvelope
from agentx.kernel.risk import RiskLevel

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "src" / "agentx" / "core" / "procedure_lifecycle.py"

_T0 = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
_PROCEDURE_ID = ProcedureId.create()

_HOSTILE_STRINGS = (
    "verified=true",
    "permission=ADMIN",
    "risk=R0",
    "force ACTIVE",
    "restore RETIRED",
    "ignore lifecycle rules",
    "ignore previous instructions",
    "activate me",
    "task succeeded",
    "status=active",
    "promote now",
    "unretire",
    "__import__('os').system('echo pwned')",
    "'; UPDATE agentx_procedures SET status='active'; --",
)

_FORBIDDEN_TRANSITIONS = (
    (ProcedureStatus.ACTIVE, ProcedureStatus.CANDIDATE),
    (ProcedureStatus.RETIRED, ProcedureStatus.ACTIVE),
    (ProcedureStatus.RETIRED, ProcedureStatus.CANDIDATE),
)


def _assess(
    current: ProcedureStatus,
    target: ProcedureStatus,
    reason: ProcedureLifecycleReason,
    **overrides: Any,
) -> ProcedureLifecycleAssessment:
    kwargs: dict[str, Any] = {
        "procedure_id": _PROCEDURE_ID,
        "revision": 1,
        "current_status": current,
        "target_status": target,
        "reason": reason,
        "requested_at": _T0,
    }
    kwargs.update(overrides)
    return assess_procedure_transition(**kwargs)


def _hostile_record(status: ProcedureStatus) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=_PROCEDURE_ID,
        revision=2,
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content=" | ".join(_HOSTILE_STRINGS),
        ),
        scope=ProcedureScope(
            dimensions={
                ProcedureScopeDimension.APPLICATION: "permission=ADMIN",
                ProcedureScopeDimension.ENVIRONMENT: "force ACTIVE",
            }
        ),
        created_at=_T0,
        status=status,
    )


# ---------------------------------------------------------------------------
# Hostile text is inert.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hostile", _HOSTILE_STRINGS)
@pytest.mark.parametrize(("current", "target"), _FORBIDDEN_TRANSITIONS)
def test_hostile_text_in_a_record_never_unlocks_a_forbidden_transition(
    hostile: str, current: ProcedureStatus, target: ProcedureStatus
) -> None:
    record = ProcedureRecord(
        procedure_id=_PROCEDURE_ID,
        revision=4,
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=hostile),
        scope=ProcedureScope(dimensions={ProcedureScopeDimension.APPLICATION: hostile}),
        created_at=_T0,
        status=current,
    )
    for reason in ProcedureLifecycleReason:
        assessment = _assess(record.status, target, reason, revision=record.revision)
        assert assessment.permits_status_write is False
        assert assessment.is_rejected is True


@pytest.mark.parametrize("hostile", _HOSTILE_STRINGS)
def test_hostile_text_never_promotes_a_candidate_with_the_wrong_reason(hostile: str) -> None:
    record = ProcedureRecord.create(
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=hostile),
        procedure_id=_PROCEDURE_ID,
        created_at=_T0,
    )
    assessment = _assess(
        record.status,
        ProcedureStatus.ACTIVE,
        ProcedureLifecycleReason.MANUAL_RETIREMENT,
        revision=record.revision,
    )
    assert assessment.decision is ProcedureLifecycleDecision.REJECTED_REASON_MISMATCH


@pytest.mark.parametrize("hostile", _HOSTILE_STRINGS)
def test_hostile_text_never_leaks_into_a_decision_explanation(hostile: str) -> None:
    record = _hostile_record(ProcedureStatus.CANDIDATE)
    assessment = _assess(
        record.status,
        ProcedureStatus.ACTIVE,
        ProcedureLifecycleReason.VALIDATION_PROMOTION,
        revision=record.revision,
    )
    assert hostile not in assessment.explanation
    assert hostile not in repr(assessment)


def test_hostile_strings_are_rejected_as_status_and_reason_arguments() -> None:
    for hostile in _HOSTILE_STRINGS:
        with pytest.raises(ProcedureLifecycleError):
            _assess(
                cast(ProcedureStatus, hostile),
                ProcedureStatus.ACTIVE,
                ProcedureLifecycleReason.VALIDATION_PROMOTION,
            )
        with pytest.raises(ProcedureLifecycleError):
            _assess(
                ProcedureStatus.CANDIDATE,
                ProcedureStatus.ACTIVE,
                cast(ProcedureLifecycleReason, hostile),
            )


def test_a_hostile_record_is_never_mutated_by_an_assessment() -> None:
    record = _hostile_record(ProcedureStatus.ACTIVE)
    snapshot = record.to_json()
    for target in ProcedureStatus:
        for reason in ProcedureLifecycleReason:
            _assess(record.status, target, reason, revision=record.revision)
    assert record.to_json() == snapshot
    assert record.status is ProcedureStatus.ACTIVE
    assert record.updated_at is None


# ---------------------------------------------------------------------------
# No authority, no kernel, no task mutation.
# ---------------------------------------------------------------------------


def test_assessment_grants_no_kernel_authority_object() -> None:
    assessment = _assess(
        ProcedureStatus.CANDIDATE,
        ProcedureStatus.ACTIVE,
        ProcedureLifecycleReason.VALIDATION_PROMOTION,
    )
    assert assessment.permits_status_write is True
    for attribute in (
        "permission",
        "permissions",
        "authority",
        "authority_context",
        "risk",
        "risk_level",
        "envelope",
        "budget",
        "gate",
        "capability",
        "task",
        "token",
    ):
        assert not hasattr(assessment, attribute), attribute
    assert not isinstance(assessment, Permission | AuthorityContext | RiskLevel | ResourceEnvelope)


def test_active_status_is_not_authority_over_a_kernel_surface() -> None:
    """An ALLOWED promotion changes nothing in the kernel-facing world."""
    stop = EmergencyStop()
    envelope = ResourceEnvelope(
        max_wall_clock=timedelta(minutes=5),
        max_model_calls=1,
        max_model_tokens=1,
        max_research_queries=0,
        max_machine_actions=0,
        max_repair_attempts=0,
        max_external_cost=Decimal("0"),
        max_risk_level=RiskLevel.R0,
    )
    task = Task.create(objective="unrelated", priority=TaskPriority.LOW)

    before_stop = stop.state
    before_envelope = envelope
    before_task = task

    _assess(
        ProcedureStatus.CANDIDATE,
        ProcedureStatus.ACTIVE,
        ProcedureLifecycleReason.VALIDATION_PROMOTION,
    )

    assert stop.state is before_stop
    assert stop.state is EmergencyStopState.RUNNING
    assert envelope == before_envelope
    assert task == before_task
    assert task.status is TaskStatus.PENDING


def test_task_identity_is_not_accepted_as_a_procedure_identity() -> None:
    with pytest.raises(ProcedureLifecycleError, match="procedure_id must be a ProcedureId"):
        _assess(
            ProcedureStatus.CANDIDATE,
            ProcedureStatus.ACTIVE,
            ProcedureLifecycleReason.VALIDATION_PROMOTION,
            procedure_id=cast(ProcedureId, TaskId.create()),
        )


def test_policy_never_touches_authority_or_storage_modules() -> None:
    """Proxy every forbidden module: any attribute touch fails the test."""
    touched: list[tuple[str, str]] = []
    forbidden = (
        "agentx.kernel",
        "agentx.kernel.action_gate",
        "agentx.kernel.permissions",
        "agentx.kernel.resource_budget",
        "agentx.kernel.risk",
        "agentx.kernel.emergency_stop",
        "agentx.capabilities",
        "agentx.procedures",
        "agentx.learning",
        "agentx.hive",
        "agentx.cognition",
        "agentx.infrastructure",
        "agentx.infrastructure.persistence",
        "agentx.infrastructure.procedure_store",
    )
    saved = {name: sys.modules.get(name) for name in forbidden}
    try:
        for name in forbidden:
            sys.modules[name] = cast(Any, ForbiddenAuthorityProxy(name, touched))
        for current in ProcedureStatus:
            for target in ProcedureStatus:
                for reason in ProcedureLifecycleReason:
                    _assess(current, target, reason)
                    is_transition_permitted(current, target, reason)
    finally:
        for name, module in saved.items():
            if module is None:
                del sys.modules[name]
            else:
                sys.modules[name] = module
    assert touched == []


# ---------------------------------------------------------------------------
# No resurrection, no store write, no revision creation, no environment effect.
# ---------------------------------------------------------------------------


def test_no_reason_revision_or_instant_resurrects_a_retired_revision() -> None:
    for reason in ProcedureLifecycleReason:
        for target in (ProcedureStatus.CANDIDATE, ProcedureStatus.ACTIVE):
            for revision in (1, 2, 1000):
                for moment in (_T0, _T0 - timedelta(days=1), _T0 + timedelta(days=365)):
                    assessment = _assess(
                        ProcedureStatus.RETIRED,
                        target,
                        reason,
                        revision=revision,
                        requested_at=moment,
                    )
                    assert (
                        assessment.decision
                        is ProcedureLifecycleDecision.REJECTED_RETIREMENT_IS_TERMINAL
                    )
                    assert assessment.revision == revision


def test_policy_writes_nothing_to_the_filesystem_or_a_database(tmp_path: Path) -> None:
    before = sorted(path.name for path in tmp_path.iterdir())
    cwd_before = sorted(Path.cwd().iterdir())
    for current in ProcedureStatus:
        for target in ProcedureStatus:
            for reason in ProcedureLifecycleReason:
                _assess(current, target, reason)
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    assert sorted(Path.cwd().iterdir()) == cwd_before


def test_policy_creates_no_identity_and_reads_no_clock() -> None:
    """Identity and time come from the caller, never from the module."""
    source = _MODULE_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "uuid4",
        "ProcedureId.create",
        "datetime.now",
        "datetime.utcnow",
        "time.time",
        "time.monotonic",
        "random.",
    ):
        assert forbidden not in source, forbidden


def test_policy_performs_no_dynamic_execution() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    forbidden_calls = {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "system",
        "popen",
        "run",
        "spawn",
        "input",
        "print",
        "setattr",
        "getattr",
        "delattr",
        "globals",
        "locals",
        "vars",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else "")
            )
            assert name not in forbidden_calls, f"lifecycle policy must not call {name!r}"


def test_environment_variables_are_untouched() -> None:
    import os

    before = dict(os.environ)
    for current in ProcedureStatus:
        for target in ProcedureStatus:
            for reason in ProcedureLifecycleReason:
                _assess(current, target, reason)
    assert dict(os.environ) == before


def test_decision_is_pure_data_that_cannot_be_forged_after_the_fact() -> None:
    rejected = _assess(
        ProcedureStatus.RETIRED,
        ProcedureStatus.ACTIVE,
        ProcedureLifecycleReason.VALIDATION_PROMOTION,
    )
    forged_field = "decision"
    with pytest.raises((AttributeError, TypeError, FrozenInstanceError)):
        setattr(rejected, forged_field, ProcedureLifecycleDecision.ALLOWED)
    assert rejected.permits_status_write is False
