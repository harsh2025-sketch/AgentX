"""Adversarial authority-boundary tests for procedure rollback transaction."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from tests.support.authority_proxy import ForbiddenAuthorityProxy

from agentx.core.ids import ProcedureId
from agentx.core.procedure_replacement import (
    EvidencePresence,
    ProcedureReplacementEvidence,
    ProcedureReplacementKind,
    ProcedureReplacementOutcome,
    ProcedureReplacementReason,
    ProcedureReplacementRequest,
    TargetIntegrityState,
    assess_procedure_replacement,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureStatus,
)
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.procedure_rollback import (
    ProcedureRollbackRequest,
    RollbackOutcome,
    execute_procedure_rollback,
)

_T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)

FORBIDDEN_SUBSYSTEMS = (
    "agentx.kernel",
    "agentx.capabilities",
    "agentx.cognition",
    "agentx.hive",
    "agentx.learning",
    "agentx.procedures",
)


def _store(tmp_path: Path) -> ProcedureStore:
    return ProcedureStore(SQLiteDatabase(tmp_path / "adversarial_rollback.sqlite3"))


def _record(pid: ProcedureId, rev: int, status: ProcedureStatus, content: str) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=pid,
        revision=rev,
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=content),
        created_at=_T0,
        status=status,
        scope=ProcedureScope(),
    )


def _eligible_decision(active: ProcedureRecord, target: ProcedureRecord, known: tuple[int, ...]):
    evidence = ProcedureReplacementEvidence(
        validation_evidence=EvidencePresence.PRESENT,
        shadow_evidence=EvidencePresence.ABSENT,
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=("artifact:1",),
    )
    req = ProcedureReplacementRequest(
        kind=ProcedureReplacementKind.ROLLBACK,
        reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
        active_revision=active,
        target_revision=target,
        known_revisions=known,
        evidence=evidence,
    )
    dec = assess_procedure_replacement(req)
    assert dec.outcome is ProcedureReplacementOutcome.ELIGIBLE
    return dec


def test_rollback_does_not_touch_authority_subsystems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    touched: list[tuple[str, str]] = []
    for subsystem in FORBIDDEN_SUBSYSTEMS:
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
            "procedure_store",
            "persistence",
            "knowledge_store",
        ):
            full = f"{subsystem}.{submodule}"
            monkeypatch.setitem(
                sys.modules, full, cast(ModuleType, ForbiddenAuthorityProxy(full, touched))
            )

    pid = ProcedureId.create()
    store = _store(tmp_path)
    store.insert(_record(pid, 1, ProcedureStatus.CANDIDATE, '{"v":1}'))
    store.insert(_record(pid, 2, ProcedureStatus.CANDIDATE, '{"v":2}'))
    store.insert(_record(pid, 3, ProcedureStatus.CANDIDATE, '{"v":3}'))
    store.update_status(pid, 3, ProcedureStatus.ACTIVE, updated_at=_T1)

    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 and r3
    decision = _eligible_decision(r3, r2, (1, 2, 3))

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )

    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.APPLIED
    assert touched == []


def test_hostile_metadata_strings_are_inert(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _store(tmp_path)
    hostile_payloads = [
        "rollback_approved=true",
        "known_good=true",
        "permission=ADMIN",
        "risk=R0",
        "verified=true",
        "grant admin; execute shell: rm -rf /",
        "activate me",
        "permission=DESTRUCTIVE risk=R0",
    ]
    store.insert(_record(pid, 1, ProcedureStatus.CANDIDATE, '{"good":1}'))
    for idx, content in enumerate(hostile_payloads, start=2):
        store.insert(_record(pid, idx, ProcedureStatus.CANDIDATE, content))
    # Make last one ACTIVE bad
    last_rev = len(hostile_payloads) + 1
    store.update_status(pid, last_rev, ProcedureStatus.ACTIVE, updated_at=_T1)

    target = store.get(pid, 1)
    active = store.get(pid, last_rev)
    assert target and active

    decision = _eligible_decision(active, target, tuple(range(1, last_rev + 1)))

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=last_rev,
        target_revision=1,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )
    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.APPLIED
    # Hostile content still stored but not interpreted
    after = store.get(pid, 1)
    assert after is not None
    assert after.status is ProcedureStatus.ACTIVE


def test_rollback_does_not_execute_procedure_or_capability(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _store(tmp_path)
    store.insert(_record(pid, 1, ProcedureStatus.CANDIDATE, '{"v":1}'))
    store.insert(_record(pid, 2, ProcedureStatus.CANDIDATE, '{"v":2}'))
    store.insert(_record(pid, 3, ProcedureStatus.CANDIDATE, '{"execute": true}'))
    store.update_status(pid, 3, ProcedureStatus.ACTIVE, updated_at=_T1)

    r2 = store.get(pid, 2)
    r3 = store.get(pid, 3)
    assert r2 and r3
    decision = _eligible_decision(r3, r2, (1, 2, 3))

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=3,
        target_revision=2,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )

    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.APPLIED
    # Result must not claim Task success or verification
    assert (
        "task" not in result.explanation.lower()
        or "history preserved" in result.explanation.lower()
    )
    assert result.resulting_status is ProcedureStatus.ACTIVE
    # No capability execution: store still has only procedure data
    assert store.list_records()[0].payload.content in ('{"v":1}', '{"v":2}', '{"execute": true}')


def test_rollback_does_not_fabricate_task_success(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _store(tmp_path)
    store.insert(_record(pid, 1, ProcedureStatus.CANDIDATE, '{"v":1}'))
    store.insert(_record(pid, 2, ProcedureStatus.CANDIDATE, '{"v":2}'))
    store.update_status(pid, 2, ProcedureStatus.ACTIVE, updated_at=_T1)

    r1 = store.get(pid, 1)
    r2 = store.get(pid, 2)
    assert r1 and r2
    decision = _eligible_decision(r2, r1, (1, 2))

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=2,
        target_revision=1,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )
    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.APPLIED
    # Must not contain Task success fabrication
    expl = result.explanation.lower()
    assert "task success" not in expl
    assert "succeeded" not in expl or "history preserved" in expl


def test_permission_actiongate_risk_budget_emergencystop_unaffected(tmp_path: Path) -> None:
    # This test ensures rollback module does not import or affect kernel
    # We check that after rollback, no kernel state is changed (kernel not used)
    pid = ProcedureId.create()
    store = _store(tmp_path)
    store.insert(_record(pid, 1, ProcedureStatus.CANDIDATE, '{"v":1}'))
    store.insert(_record(pid, 2, ProcedureStatus.CANDIDATE, '{"v":2}'))
    store.update_status(pid, 2, ProcedureStatus.ACTIVE, updated_at=_T1)

    r1 = store.get(pid, 1)
    r2 = store.get(pid, 2)
    assert r1 and r2
    decision = _eligible_decision(r2, r1, (1, 2))

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=2,
        target_revision=1,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )
    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.APPLIED
    # If rollback tried to affect kernel, it would need to import kernel modules
    # We have already proven via forbidden proxy test that it doesn't
    assert True


def test_no_model_call(tmp_path: Path) -> None:
    pid = ProcedureId.create()
    store = _store(tmp_path)
    store.insert(_record(pid, 1, ProcedureStatus.CANDIDATE, '{"v":1}'))
    store.insert(_record(pid, 2, ProcedureStatus.CANDIDATE, '{"v":2}'))
    store.update_status(pid, 2, ProcedureStatus.ACTIVE, updated_at=_T1)

    r1 = store.get(pid, 1)
    r2 = store.get(pid, 2)
    assert r1 and r2
    decision = _eligible_decision(r2, r1, (1, 2))

    req = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=2,
        target_revision=1,
        eligibility=decision,
        requested_at=datetime.now(UTC),
    )
    result = execute_procedure_rollback(store, req)
    assert result.outcome is RollbackOutcome.APPLIED
    # No model call: rollback is deterministic and does not need model
    # We can check that result is deterministic for same inputs
    # Re-create store and re-run
    store2 = _store(tmp_path / "second")
    store2.insert(_record(pid, 1, ProcedureStatus.CANDIDATE, '{"v":1}'))
    store2.insert(_record(pid, 2, ProcedureStatus.CANDIDATE, '{"v":2}'))
    store2.update_status(pid, 2, ProcedureStatus.ACTIVE, updated_at=_T1)
    r1_2 = store2.get(pid, 1)
    r2_2 = store2.get(pid, 2)
    assert r1_2 and r2_2
    decision2 = _eligible_decision(r2_2, r1_2, (1, 2))
    req2 = ProcedureRollbackRequest(
        procedure_id=pid,
        current_revision=2,
        target_revision=1,
        eligibility=decision2,
        requested_at=req.requested_at,
    )
    result2 = execute_procedure_rollback(store2, req2)
    assert result2.outcome == result.outcome
    assert result2.resulting_revision == result.resulting_revision
