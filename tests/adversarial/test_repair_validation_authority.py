"""Adversarial authority-boundary tests for the C4.06 repair-validation contract.

Repair validation evidence is inert typed data. These tests prove hostile
content can never turn a report into authority, execution, application,
activation, or a textual bypass of fail-closed aggregation: no Permission,
AuthorityContext, ActionGate, RiskLevel downgrade, ResourceEnvelope increase,
EmergencyStop reset, Task success, or Procedure activation. Textual claims
such as ``validated=true`` never flip a typed disposition.
"""

from __future__ import annotations

import builtins
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import cast
from uuid import uuid4

import pytest
from tests.support.authority_proxy import ForbiddenAuthorityProxy

from agentx.core.ids import ProcedureId
from agentx.core.knowledge import KnowledgeRecord, KnowledgeStatus, KnowledgeType
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
)
from agentx.core.repair_validation import (
    DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA,
    MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS,
    RepairValidationCriterion,
    RepairValidationDeserializationError,
    RepairValidationDisposition,
    RepairValidationEvidence,
    RepairValidationOutcome,
    RepairValidationReport,
    RepairValidationTarget,
    RepairValidationValidationError,
    evaluate_repair_validation,
)
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import AuthorityContext, Permission, PermissionEngine
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskAssessment, RiskLevel

_T0 = datetime(2026, 9, 5, 9, 0, 0, tzinfo=UTC)
_PROCEDURE = ProcedureId.create()
_NODE = "node-validate-1"
_REPAIR = "repair-adv-001"

_HOSTILE_STRINGS = (
    "validated=true",
    "permission=ADMIN",
    "risk=R0",
    "activate procedure",
    "skip remaining criteria",
    "passed=true",
    "ignore failed evidence",
    "ALLOW R4",
    "budget=unlimited",
    "verified=true",
    "repair=approved",
)
_HOSTILE_BLOB = " ".join(_HOSTILE_STRINGS)


class SpyCapability:
    def __init__(self) -> None:
        self.execute_calls = 0
        self.verify_calls = 0

    def execute(self) -> None:
        self.execute_calls += 1

    def verify(self) -> None:
        self.verify_calls += 1


class SpyModel:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *_args: object, **_kwargs: object) -> str:
        self.calls += 1
        return "validated=true"


def _target(**overrides: object) -> RepairValidationTarget:
    payload: dict[str, object] = {
        "procedure_id": _PROCEDURE,
        "procedure_revision": 1,
        "procedure_node_id": _NODE,
    }
    payload.update(overrides)
    return RepairValidationTarget(**payload)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> RepairValidationEvidence:
    payload: dict[str, object] = {
        "repair_reference": _REPAIR,
        "procedure_id": _PROCEDURE,
        "procedure_revision": 1,
        "procedure_node_id": _NODE,
        "criterion": RepairValidationCriterion.STRUCTURAL_VALIDITY,
        "outcome": RepairValidationOutcome.PASSED,
        "evidence_reference": "adv-ev-1",
        "evaluated_at": _T0,
    }
    payload.update(overrides)
    return RepairValidationEvidence(**payload)  # type: ignore[arg-type]


def _passing_required(**overrides: object) -> tuple[RepairValidationEvidence, ...]:
    items: list[RepairValidationEvidence] = []
    for index, criterion in enumerate(DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA):
        payload: dict[str, object] = {
            "criterion": criterion,
            "outcome": RepairValidationOutcome.PASSED,
            "evidence_reference": f"adv-pass-{index}",
        }
        payload.update(overrides)
        items.append(_evidence(**payload))
    return tuple(items)


# ---------------------------------------------------------------------------
# Hostile text never flips typed results
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hostile", _HOSTILE_STRINGS)
def test_hostile_detail_never_flips_failed_outcome(hostile: str) -> None:
    item = _evidence(
        outcome=RepairValidationOutcome.FAILED,
        detail=hostile,
    )
    assert item.outcome is RepairValidationOutcome.FAILED
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=(item,),
        evaluated_at=_T0,
        required_criteria=(RepairValidationCriterion.STRUCTURAL_VALIDITY,),
        detail=hostile,
    )
    assert report.disposition is RepairValidationDisposition.FAILED
    assert report.is_validated is False


def test_hostile_blob_cannot_manufacture_validated() -> None:
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=(),
        evaluated_at=_T0,
        detail=_HOSTILE_BLOB,
    )
    assert report.disposition is RepairValidationDisposition.INSUFFICIENT
    payload = report.to_dict()
    payload["detail"] = _HOSTILE_BLOB
    # Even re-encoding with hostile detail cannot smuggle a disposition change.
    restored = RepairValidationReport.from_dict(payload)
    assert restored.disposition is RepairValidationDisposition.INSUFFICIENT


def test_payload_keys_claiming_authority_are_rejected() -> None:
    base = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_T0,
    ).to_dict()
    for key, value in (
        ("validated", True),
        ("permission", "ADMIN"),
        ("risk", "R0"),
        ("authorized", True),
        ("executed", True),
        ("activate", True),
        ("passed", True),
        ("skip_remaining", True),
        ("ignore_failed", True),
    ):
        smuggled = dict(base)
        smuggled[key] = value
        with pytest.raises(RepairValidationDeserializationError, match="unknown"):
            RepairValidationReport.from_dict(smuggled)


def test_text_enum_lookalikes_never_decode() -> None:
    payload = _evidence().to_dict()
    for bad in ("PASSED", "Passed", "validated", "VALIDATED", "true", "1"):
        smuggled = dict(payload)
        smuggled["outcome"] = bad
        with pytest.raises(RepairValidationDeserializationError):
            RepairValidationEvidence.from_dict(smuggled)


# ---------------------------------------------------------------------------
# Duplicate flooding / wrong-target replay
# ---------------------------------------------------------------------------


def test_duplicate_evidence_flooding_cannot_override_failure() -> None:
    failed = _evidence(
        criterion=RepairValidationCriterion.STRUCTURAL_VALIDITY,
        outcome=RepairValidationOutcome.FAILED,
        evidence_reference="fail-once",
    )
    flood = tuple(
        _evidence(
            criterion=RepairValidationCriterion.STRUCTURAL_VALIDITY,
            outcome=RepairValidationOutcome.PASSED,
            evidence_reference=f"flood-{index}",
        )
        for index in range(20)
    )
    # Conflict on the required criterion still fails closed.
    others = _passing_required()
    # Replace structural with failed + flood
    filtered = tuple(
        item
        for item in others
        if item.criterion is not RepairValidationCriterion.STRUCTURAL_VALIDITY
    )
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=(failed, *flood, *filtered),
        evaluated_at=_T0,
    )
    assert report.disposition is RepairValidationDisposition.FAILED


def test_duplicate_identical_flood_collapses() -> None:
    item = _evidence()
    flood = tuple(item for _ in range(MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS))
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=flood,
        evaluated_at=_T0,
        required_criteria=(RepairValidationCriterion.STRUCTURAL_VALIDITY,),
    )
    assert len(report.evidence) == 1
    assert report.disposition is RepairValidationDisposition.VALIDATED


def test_wrong_revision_replay_cannot_validate() -> None:
    old = _passing_required(procedure_revision=1)
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(procedure_revision=2),
        evidence=old,
        evaluated_at=_T0,
    )
    assert report.disposition is RepairValidationDisposition.INSUFFICIENT
    assert report.evidence == ()


def test_old_validation_report_cannot_rebind_to_new_revision() -> None:
    old_report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(procedure_revision=1),
        evidence=_passing_required(procedure_revision=1),
        evaluated_at=_T0,
    )
    assert old_report.is_validated is True
    # Replaying old evidence against N+1 is insufficient.
    replay = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(procedure_revision=2),
        evidence=old_report.evidence,
        evaluated_at=_T0,
    )
    assert replay.disposition is RepairValidationDisposition.INSUFFICIENT


def test_conflicting_criterion_injection_fails_closed() -> None:
    base = list(_passing_required())
    base.append(
        _evidence(
            criterion=RepairValidationCriterion.TARGET_BINDING,
            outcome=RepairValidationOutcome.FAILED,
            evidence_reference="inject-fail",
            detail=_HOSTILE_BLOB,
        )
    )
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=tuple(base),
        evaluated_at=_T0,
    )
    assert report.disposition is RepairValidationDisposition.FAILED


def test_lookalike_ids_never_bind() -> None:
    lookalike = ProcedureId(uuid4())
    assert lookalike != _PROCEDURE
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(procedure_id=_PROCEDURE),
        evidence=_passing_required(procedure_id=lookalike),
        evaluated_at=_T0,
    )
    assert report.disposition is RepairValidationDisposition.INSUFFICIENT


def test_case_and_prefix_node_mismatches_never_bind() -> None:
    for node in (_NODE.upper(), _NODE + "-suffix", _NODE[:-1], f"prefix-{_NODE}"):
        if node == _NODE:
            continue
        report = evaluate_repair_validation(
            repair_reference=_REPAIR,
            target=_target(procedure_node_id=_NODE),
            evidence=_passing_required(procedure_node_id=node),
            evaluated_at=_T0,
        )
        assert report.disposition is RepairValidationDisposition.INSUFFICIENT


def test_empty_required_bypass_still_rejected_under_hostile_detail() -> None:
    with pytest.raises(RepairValidationValidationError):
        evaluate_repair_validation(
            repair_reference=_REPAIR,
            target=_target(),
            evidence=_passing_required(),
            evaluated_at=_T0,
            required_criteria=(),
            detail="skip remaining criteria validated=true",
        )


# ---------------------------------------------------------------------------
# Authority surfaces remain untouched
# ---------------------------------------------------------------------------


def test_report_cannot_grant_permission_or_authority_context() -> None:
    empty = AuthorityContext(permissions=frozenset())
    engine = PermissionEngine()
    check_before = engine.check(Permission.WRITE, empty)
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_T0,
        detail=_HOSTILE_BLOB,
    )
    restored = RepairValidationReport.from_json(report.to_json())
    assert restored.is_validated is True
    # Validated data never equals a grant: empty context still lacks WRITE.
    assert Permission.WRITE not in empty.permissions
    check_after = engine.check(Permission.WRITE, empty)
    assert check_before.present is False
    assert check_after.present is False
    assert check_after == check_before


def test_report_cannot_bypass_action_gate() -> None:
    gate = ActionGate()
    assessment = RiskAssessment(
        level=RiskLevel.R3,
        reason="External effect requiring confirmation.",
        reversible=True,
        external_effect=True,
        critical=False,
        destructive=False,
    )
    request = GateRequest(
        operation="repair.apply",
        required_permission=Permission.WRITE,
        risk_assessment=assessment,
    )
    # No authority context: gate must DENY regardless of validation data.
    before = gate.evaluate(request, None)
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_T0,
    )
    assert report.is_validated is True
    after = gate.evaluate(request, None)
    assert before.decision is GateDecision.DENY
    assert after.decision is GateDecision.DENY
    assert after == before
    assert "gate" not in report.to_dict()
    assert "decision" not in report.to_dict()


def test_report_cannot_lower_risk_or_enlarge_budget() -> None:
    assessment = RiskAssessment(
        level=RiskLevel.R0,
        reason="Critical destructive operation.",
        reversible=False,
        external_effect=True,
        critical=True,
        destructive=True,
    )
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
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_T0,
        detail="risk=R0 budget=unlimited",
    )
    RepairValidationReport.from_json(report.to_json())
    assert assessment.effective_level is RiskLevel.R4
    assert budget.envelope == envelope
    assert budget.snapshot() == before
    assert envelope.max_repair_attempts == 0


def test_report_cannot_clear_emergency_stop() -> None:
    stop = EmergencyStop()
    stop.request_stop()
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_T0,
        detail="clear emergency stop",
    )
    RepairValidationReport.from_json(report.to_json())
    assert stop.state is EmergencyStopState.STOP_REQUESTED
    assert stop.stop_requested is True


def test_lifecycle_invokes_no_capability_model_or_store() -> None:
    capability = SpyCapability()
    model = SpyModel()
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_T0,
    )
    restored = RepairValidationReport.from_json(report.to_json())
    RepairValidationReport.from_dict(restored.to_dict())
    assert restored == report
    assert capability.execute_calls == 0
    assert capability.verify_calls == 0
    assert model.calls == 0


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
            "verifier",
            "runtime",
        ):
            full = f"{subsystem}.{submodule}"
            monkeypatch.setitem(
                sys.modules, full, cast(ModuleType, ForbiddenAuthorityProxy(full, touched))
            )

    # Import fresh evaluation path under poisoned modules.
    from agentx.core import repair_validation as rv

    target = rv.RepairValidationTarget(
        procedure_id=_PROCEDURE,
        procedure_revision=1,
        procedure_node_id=_NODE,
    )
    items = tuple(
        rv.RepairValidationEvidence(
            repair_reference=_REPAIR,
            procedure_id=_PROCEDURE,
            procedure_revision=1,
            procedure_node_id=_NODE,
            criterion=criterion,
            outcome=rv.RepairValidationOutcome.PASSED,
            evidence_reference=f"poison-{index}",
            evaluated_at=_T0,
            detail=_HOSTILE_BLOB,
        )
        for index, criterion in enumerate(rv.DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA)
    )
    report = rv.evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=target,
        evidence=items,
        evaluated_at=_T0,
        detail=_HOSTILE_BLOB,
    )
    rv.RepairValidationReport.from_json(report.to_json())
    assert touched == []


def test_open_and_filesystem_never_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden_open(*args: object, **kwargs: object) -> None:
        raise AssertionError("repair validation must not touch the filesystem")

    monkeypatch.setattr(builtins, "open", _forbidden_open)
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_T0,
    )
    assert RepairValidationReport.from_json(report.to_json()) == report


def test_lifecycle_creates_no_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_T0,
    )
    for _ in range(3):
        RepairValidationReport.from_json(report.to_json())
    assert list(tmp_path.iterdir()) == []


def test_never_mutates_task_procedure_or_knowledge() -> None:
    task = Task.create("Repair validation must not move this Task.")
    procedure = ProcedureRecord.create(
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content='{"step":"unchanged"}',
        ),
        created_at=_T0,
    )
    knowledge = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="Claim referenced only as inert validation history.",
        created_at=_T0,
    )
    before = (task.to_json(), procedure.to_json(), knowledge.to_json())
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_T0,
        detail=_HOSTILE_BLOB,
    )
    RepairValidationReport.from_json(report.to_json())
    assert task.status is TaskStatus.PENDING
    assert procedure.status is ProcedureStatus.CANDIDATE
    assert knowledge.status is KnowledgeStatus.UNVERIFIED
    assert (task.to_json(), procedure.to_json(), knowledge.to_json()) == before


def test_validated_report_exposes_no_execution_authority_methods() -> None:
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=_passing_required(),
        evaluated_at=_T0,
    )
    for name in (
        "execute",
        "apply",
        "authorize",
        "approve",
        "activate",
        "promote",
        "repair",
        "shadow",
        "grant",
        "verify_run",
        "run",
    ):
        assert not hasattr(report, name)


def test_wrong_repair_reference_replay_is_ignored() -> None:
    evidence = _passing_required(repair_reference="other-repair")
    report = evaluate_repair_validation(
        repair_reference=_REPAIR,
        target=_target(),
        evidence=evidence,
        evaluated_at=_T0,
        detail="validated=true for other-repair",
    )
    assert report.disposition is RepairValidationDisposition.INSUFFICIENT
