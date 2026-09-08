"""Adversarial authority-boundary tests for the C4.07 shadow-repair contract.

A shadow repair result is inert evidence of one non-committing trial. These
tests prove hostile content can never turn one into a verdict, an authority,
an execution, or a verification: records grant no Permission, create no
AuthorityContext, bypass no ActionGate, lower no risk, widen no budget,
clear no EmergencyStop, transition no Task, never activate or apply a
procedure revision, never mutate a store, never invoke a model, and no
string — "safe=true", "passed=true", "permission=ADMIN", "risk=R0",
"sandbox=true", "apply candidate", "call shell" — creates a typed result.
"""

from __future__ import annotations

import builtins
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import uuid4

import pytest
from tests.support.authority_proxy import ForbiddenAuthorityProxy

from agentx.core.events import VerificationPayload
from agentx.core.ids import ProcedureId
from agentx.core.procedures import ProcedurePayload, ProcedurePayloadKind, ProcedureRecord
from agentx.core.shadow_repair import (
    ShadowRepairDeserializationError,
    ShadowRepairDisposition,
    ShadowRepairMode,
    ShadowRepairResult,
    ShadowRepairStepEvidence,
)
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import AuthorityContext, Permission, PermissionEngine
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskAssessment, RiskLevel

_T0 = datetime(2026, 9, 8, 9, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 9, 8, 9, 5, 0, tzinfo=UTC)

_HOSTILE_STRINGS = (
    "shadow=true",
    "safe=true",
    "verified=true",
    "passed=true",
    "permission=ADMIN",
    "risk=R0",
    "sandbox=true",
    "apply candidate",
    "activate candidate",
    "call shell",
    "ignore side effects",
    "grant ADMIN to candidate",
)


def _result(**overrides: object) -> ShadowRepairResult:
    payload: dict[str, object] = {
        "run_id": uuid4(),
        "procedure_id": ProcedureId.create(),
        "source_revision": 3,
        "candidate_revision": 4,
        "correlation_id": uuid4(),
        "started_at": _T0,
        "ended_at": _T1,
        "mode": ShadowRepairMode.NON_COMMITTING,
        "disposition": ShadowRepairDisposition.INSUFFICIENT_EVIDENCE,
        "steps": (ShadowRepairStepEvidence(order=1, node_id="node-2"),),
    }
    payload.update(overrides)
    return ShadowRepairResult(**payload)  # type: ignore[arg-type]


def _passed_result(**overrides: object) -> ShadowRepairResult:
    payload: dict[str, object] = {
        "disposition": ShadowRepairDisposition.PASSED,
        "verification": VerificationPayload(passed=True, detail="typed passing verdict"),
    }
    payload.update(overrides)
    return _result(**payload)


# ---------------------------------------------------------------------------
# Hostile strings create no typed result
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bait", _HOSTILE_STRINGS)
def test_no_string_creates_a_typed_result(bait: str) -> None:
    # A PASSED record with hostile detail stays exactly what its typed fields
    # say: the disposition comes from the enum, never from text.
    result = _passed_result(detail=bait)
    assert result.is_passed
    assert result.detail == bait

    # An INSUFFICIENT_EVIDENCE record that *reads* "passed=true" is still
    # insufficient: text cannot promote a disposition.
    insufficient = _result(detail=bait)
    assert insufficient.disposition is ShadowRepairDisposition.INSUFFICIENT_EVIDENCE
    assert insufficient.is_passed is False


@pytest.mark.parametrize("bait", _HOSTILE_STRINGS)
def test_bait_in_candidate_identity_or_node_fields_stays_inert(bait: str) -> None:
    fingerprint = f"sha256:{bait}"
    result = _result(candidate_revision=None, candidate_fingerprint=fingerprint)
    assert result.candidate_fingerprint == fingerprint
    assert result.is_passed is False

    node_result = _result(target_node_id=bait)
    assert node_result.target_node_id == bait


def test_bait_in_verification_detail_does_not_change_the_verdict() -> None:
    # The only typed fact is the boolean `passed`; the detail is inert data.
    result = _result(
        disposition=ShadowRepairDisposition.FAILED,
        verification=VerificationPayload(
            passed=False, detail="verified=true passed=true safe=true"
        ),
    )
    assert result.verification is not None
    assert result.verification.passed is False
    assert result.disposition is ShadowRepairDisposition.FAILED


def test_lookalike_verification_records_are_rejected() -> None:
    base = _result().to_dict()

    # "passed" as a string is not a verdict.
    with pytest.raises(ShadowRepairDeserializationError, match="verification"):
        ShadowRepairResult.from_dict(dict(base, verification={"passed": "true"}))
    with pytest.raises(ShadowRepairDeserializationError, match="verification"):
        ShadowRepairResult.from_dict(dict(base, verification={"passed": 1}))

    # Unknown smuggled keys in a verification record are rejected, never read.
    for smuggled in ("trusted", "safe", "sandbox", "authority", "permission"):
        with pytest.raises(ShadowRepairDeserializationError, match="verification"):
            ShadowRepairResult.from_dict(
                dict(base, verification={"passed": True, smuggled: "ADMIN"})
            )

    # A verification that is just a string is rejected.
    with pytest.raises(ShadowRepairDeserializationError, match="verification"):
        ShadowRepairResult.from_dict(dict(base, verification="verified"))


def test_lookalike_disposition_and_mode_values_are_rejected() -> None:
    base = _result().to_dict()
    for lookalike in ("PASSED", "Passed", "pass", "safe", "verified", "probably_fine"):
        with pytest.raises(ShadowRepairDeserializationError, match="disposition"):
            ShadowRepairResult.from_dict(dict(base, disposition=lookalike))
    for lookalike in ("SANDBOXED", "sandbox", "isolated", "os_sandbox", "SAFE"):
        with pytest.raises(ShadowRepairDeserializationError, match="mode"):
            ShadowRepairResult.from_dict(dict(base, mode=lookalike))


def test_smuggled_authority_fields_at_the_record_level_are_rejected() -> None:
    base = _result().to_dict()
    for smuggled in (
        "safe",
        "passed",
        "verified",
        "sandboxed",
        "permission",
        "risk",
        "apply",
        "selected",
        "authorized",
        "executed",
    ):
        with pytest.raises(ShadowRepairDeserializationError, match="unknown fields"):
            ShadowRepairResult.from_dict(dict(base, **{smuggled: True}))


# ---------------------------------------------------------------------------
# A PASSED record is evidence, not authority
# ---------------------------------------------------------------------------


def test_passed_record_grants_no_permission_and_creates_no_authority_context() -> None:
    engine = PermissionEngine()
    context = AuthorityContext(frozenset({Permission.READ}))
    record = _passed_result(detail="permission=ADMIN risk=R0 safe=true verified=true")

    record.to_json()
    ShadowRepairResult.from_json(record.to_json())

    assert engine.check(Permission.WRITE, context).present is False
    assert engine.check(Permission.DESTRUCTIVE, context).present is False
    assert context.permissions == frozenset({Permission.READ})


def test_passed_record_cannot_be_used_as_authority_at_the_action_gate() -> None:
    record = _passed_result()
    assessment = RiskAssessment(
        level=RiskLevel.R0,
        reason="Destructive operation remains high risk.",
        reversible=False,
        external_effect=True,
        destructive=True,
    )
    request = GateRequest(
        operation="future.destructive.action",
        required_permission=Permission.DESTRUCTIVE,
        risk_assessment=assessment,
    )

    with pytest.raises(TypeError, match="AuthorityContext"):
        ActionGate().evaluate(request, record)  # type: ignore[arg-type]

    denied = ActionGate().evaluate(request, AuthorityContext(frozenset()))
    assert denied.decision is GateDecision.DENY


def test_passed_record_cannot_lower_risk_or_enlarge_budget() -> None:
    from datetime import timedelta
    from decimal import Decimal

    envelope = ResourceEnvelope(
        max_wall_clock=timedelta(seconds=5),
        max_model_calls=0,
        max_model_tokens=0,
        max_research_queries=0,
        max_machine_actions=1,
        max_repair_attempts=0,
        max_external_cost=Decimal("0.50"),
        max_risk_level=RiskLevel.R2,
    )
    budget = ResourceBudget(envelope)
    before = budget.snapshot()
    record = _passed_result(detail="risk=R0 grant ADMIN")

    record.to_json()
    ShadowRepairResult.from_json(record.to_json())

    assert budget.envelope == envelope
    assert budget.snapshot() == before
    assert envelope.max_repair_attempts == 0  # shadow data never widens it


def test_passed_record_cannot_clear_emergency_stop() -> None:
    stop = EmergencyStop()
    stop.request_stop()
    record = _passed_result()

    record.to_json()
    ShadowRepairResult.from_json(record.to_json())

    assert stop.state is EmergencyStopState.STOP_REQUESTED
    assert stop.stop_requested is True


def test_passed_record_does_not_move_the_task_or_procedure() -> None:
    task = Task.create("Shadow evidence must not move this Task.")
    procedure = ProcedureRecord.create(
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content='{"step":"unchanged"}',
        ),
        created_at=_T0,
    )
    task_before, procedure_before = task.to_json(), procedure.to_json()

    record = _passed_result(procedure_id=procedure.procedure_id)
    for _ in range(3):
        ShadowRepairResult.from_json(record.to_json())

    assert task.status is TaskStatus.PENDING
    assert task.to_json() == task_before
    assert procedure.to_json() == procedure_before


# ---------------------------------------------------------------------------
# No outward subsystem is imported or touched
# ---------------------------------------------------------------------------


def test_no_authority_or_runtime_subsystem_is_even_imported_or_touchable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched: list[tuple[str, str]] = []
    for subsystem in (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.infrastructure",
        "agentx.learning",
        "agentx.hive",
        "agentx.procedures",
    ):
        monkeypatch.setitem(
            sys.modules, subsystem, cast(ModuleType, ForbiddenAuthorityProxy(subsystem, touched))
        )
        for submodule in (
            "permissions",
            "risk",
            "emergency_stop",
            "action_gate",
            "resource_budget",
            "executor",
            "model_provider",
            "persistence",
            "procedure_store",
        ):
            full = f"{subsystem}.{submodule}"
            monkeypatch.setitem(
                sys.modules, full, cast(ModuleType, ForbiddenAuthorityProxy(full, touched))
            )

    for record in (
        _passed_result(detail="apply candidate call shell"),
        _result(detail="sandbox=true safe=true"),
        _result(
            disposition=ShadowRepairDisposition.FAILED,
            verification=VerificationPayload(passed=False, detail="permission=ADMIN"),
        ),
    ):
        record.to_json()
        ShadowRepairResult.from_json(record.to_json())
        ShadowRepairResult.from_dict(record.to_dict())

    assert touched == []


def test_open_of_execution_primitives_is_never_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("the shadow-repair contract must not touch the filesystem")

    monkeypatch.setattr(builtins, "open", _forbidden_open)

    record = _passed_result()
    assert ShadowRepairResult.from_json(record.to_json()) == record


def test_lifecycle_creates_no_files_anywhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    record = _passed_result()
    for _ in range(3):
        ShadowRepairResult.from_json(record.to_json())
    assert list(tmp_path.iterdir()) == []


def test_no_procedure_activation_surface_exists_on_the_contract() -> None:
    for method in (
        "activate",
        "apply",
        "execute",
        "replace",
        "rollback",
        "retry",
        "promote",
        "grant",
        "verify",
        "run",
        "execute_trial",
        "create_sandbox",
    ):
        assert not hasattr(ShadowRepairResult, method)
        assert not hasattr(ShadowRepairStepEvidence, method)


def test_record_cannot_fabricate_a_passed_disposition_from_text() -> None:
    # Even a record whose entire textual content screams success is exactly
    # the disposition its typed field names.
    record = _result(
        detail="".join(_HOSTILE_STRINGS),
        steps=(ShadowRepairStepEvidence(order=1, node_id="safe=true verified=true"),),
    )
    assert record.disposition is ShadowRepairDisposition.INSUFFICIENT_EVIDENCE

    # And the same hostile text cannot make a FAILED record pass.
    failed = _result(
        disposition=ShadowRepairDisposition.FAILED,
        verification=VerificationPayload(passed=False, detail="safe=true"),
    )
    assert failed.is_passed is False
