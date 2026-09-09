from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentx.core.ids import ProcedureId
from agentx.core.procedure_replacement import (
    ProcedureReplacementDecision,
    ProcedureReplacementKind,
    ProcedureReplacementOutcome,
    ProcedureReplacementReason,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
)
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.procedure_replacement_transaction import execute_replacement_transaction

_T0 = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> ProcedureStore:
    return ProcedureStore(SQLiteDatabase(tmp_path / "replacement.sqlite3"))


def _record(
    pid: ProcedureId,
    revision: int,
    status: ProcedureStatus,
    content: str,
) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=pid,
        revision=revision,
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=content),
        created_at=_T0,
        status=status,
    )


def _decision(
    pid: ProcedureId,
    active: int,
    target: int,
    *,
    kind: ProcedureReplacementKind = ProcedureReplacementKind.FORWARD_REPLACEMENT,
    outcome: ProcedureReplacementOutcome = ProcedureReplacementOutcome.ELIGIBLE,
) -> ProcedureReplacementDecision:
    return ProcedureReplacementDecision(
        outcome=outcome,
        kind=kind,
        reason=(
            ProcedureReplacementReason.VALIDATED_REPAIR
            if kind is ProcedureReplacementKind.FORWARD_REPLACEMENT
            else ProcedureReplacementReason.MANUAL_ROLLBACK
        ),
        findings=(),
        procedure_id=pid,
        active_revision=active,
        target_revision=target,
    )


def test_forward_replacement_appends_candidate_and_keeps_one_active(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pid = ProcedureId.create()
    active = _record(pid, 1, ProcedureStatus.ACTIVE, '{"v":1}')
    target = _record(pid, 2, ProcedureStatus.CANDIDATE, '{"v":2}')
    store.insert(active)

    result = execute_replacement_transaction(store, _decision(pid, 1, 2), active, target)

    assert result.status == "APPLIED"
    history = store.history(pid)
    assert [record.status for record in history] == [
        ProcedureStatus.RETIRED,
        ProcedureStatus.ACTIVE,
    ]
    assert history[0].payload.content == '{"v":1}'
    assert history[1].payload.content == '{"v":2}'


def test_forward_replacement_can_activate_exact_existing_latest_candidate(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    pid = ProcedureId.create()
    active = _record(pid, 1, ProcedureStatus.ACTIVE, '{"v":1}')
    target = _record(pid, 2, ProcedureStatus.CANDIDATE, '{"v":2}')
    store.insert(active)
    store.insert(target)

    result = execute_replacement_transaction(store, _decision(pid, 1, 2), active, target)

    assert result.status == "APPLIED"
    stored_target = store.get(pid, 2)
    assert stored_target is not None
    assert stored_target.status is ProcedureStatus.ACTIVE


def test_n2_17_rejects_rollback_and_never_resurrects_retired_target(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    pid = ProcedureId.create()
    retired = _record(pid, 1, ProcedureStatus.RETIRED, '{"old":true}')
    active = _record(pid, 2, ProcedureStatus.ACTIVE, '{"live":true}')
    store.insert(retired)
    store.insert(active)

    result = execute_replacement_transaction(
        store,
        _decision(pid, 2, 1, kind=ProcedureReplacementKind.ROLLBACK),
        active,
        retired,
    )

    assert result.status == "REJECTED"
    assert "FORWARD_REPLACEMENT" in (result.reason or "")
    assert store.history(pid) == (retired, active)


def test_stale_exact_active_snapshot_is_rejected_without_partial_mutation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    pid = ProcedureId.create()
    active = _record(pid, 1, ProcedureStatus.ACTIVE, '{"v":1}')
    target = _record(pid, 2, ProcedureStatus.CANDIDATE, '{"v":2}')
    store.insert(active)
    stale = active
    updated = store.update_status(pid, 1, ProcedureStatus.ACTIVE, updated_at=_T0)
    assert updated != stale

    result = execute_replacement_transaction(store, _decision(pid, 1, 2), stale, target)

    assert result.status == "REJECTED"
    assert store.history(pid) == (updated,)


def test_gap_and_retired_forward_target_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pid = ProcedureId.create()
    active = _record(pid, 1, ProcedureStatus.ACTIVE, '{"v":1}')
    store.insert(active)
    gap = _record(pid, 3, ProcedureStatus.CANDIDATE, '{"v":3}')
    assert (
        execute_replacement_transaction(store, _decision(pid, 1, 3), active, gap).status
        == "REJECTED"
    )

    retired = _record(pid, 2, ProcedureStatus.RETIRED, '{"v":2}')
    result = execute_replacement_transaction(store, _decision(pid, 1, 2), active, retired)
    assert result.status == "REJECTED"
    assert store.history(pid) == (active,)


def test_ineligible_decision_cannot_mutate_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pid = ProcedureId.create()
    active = _record(pid, 1, ProcedureStatus.ACTIVE, '{"v":1}')
    target = _record(pid, 2, ProcedureStatus.CANDIDATE, '{"v":2}')
    store.insert(active)
    decision = _decision(
        pid,
        1,
        2,
        outcome=ProcedureReplacementOutcome.INSUFFICIENT_EVIDENCE,
    )
    result = execute_replacement_transaction(store, decision, active, target)
    assert result.status == "REJECTED"
    assert store.history(pid) == (active,)


def test_bad_argument_types_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with pytest.raises(TypeError):
        execute_replacement_transaction(
            store,
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        )
