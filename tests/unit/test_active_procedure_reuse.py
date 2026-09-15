"""Focused AX-172 tests for durable ACTIVE-only procedure reuse."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentx.active_procedure_reuse import ActiveProcedureReuse
from agentx.core.procedure_matching import ProcedureRequirement
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.infrastructure.active_procedure_reader import (
    ActiveProcedureIntegrityError,
    ActiveProcedureReader,
)
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.procedure_reuse_selector import ProcedureReuseSelectionOutcome

_T0 = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)
_SCOPE = ProcedureScope({ProcedureScopeDimension.OPERATING_SYSTEM: "windows"})


def _store(path: Path) -> ProcedureStore:
    return ProcedureStore(SQLiteDatabase(path))


def _candidate(*, procedure_id=None, revision: int = 1, label: str = "a") -> ProcedureRecord:
    return ProcedureRecord.create(
        procedure_id=procedure_id,
        revision=revision,
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content=f'{{"label":"{label}"}}',
        ),
        scope=_SCOPE,
        created_at=_T0,
    )


def test_restart_reconstructs_store_and_selects_only_persisted_active(tmp_path: Path) -> None:
    path = tmp_path / "agentx.sqlite3"
    first = _store(path)
    record = _candidate()
    first.insert(record)
    active = first.update_status(record.procedure_id, 1, ProcedureStatus.ACTIVE, updated_at=_T0)

    # Simulate a process restart: no object from the first runtime is reused.
    restarted = _store(path)
    reuse = ActiveProcedureReuse(ActiveProcedureReader(restarted), {})
    result = reuse.select(ProcedureRequirement(scope=_SCOPE))

    assert result.outcome is ProcedureReuseSelectionOutcome.SELECTED
    assert result.selected is not None
    assert result.selected.record == active
    assert result.procedure_id == record.procedure_id
    assert result.revision == 1


def test_candidate_and_retired_revisions_are_never_resurrected(tmp_path: Path) -> None:
    store = _store(tmp_path / "agentx.sqlite3")
    candidate = _candidate(label="candidate")
    store.insert(candidate)

    retired_source = _candidate(label="retired")
    store.insert(retired_source)
    store.update_status(
        retired_source.procedure_id,
        1,
        ProcedureStatus.RETIRED,
        updated_at=_T0,
    )

    result = ActiveProcedureReuse(ActiveProcedureReader(store), {}).select(
        ProcedureRequirement(scope=_SCOPE)
    )
    assert result.outcome is ProcedureReuseSelectionOutcome.NO_MATCH
    assert result.selected is None


def test_stale_historical_revision_is_not_reused_when_new_revision_is_active(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "agentx.sqlite3")
    revision1 = _candidate(label="old")
    store.insert(revision1)
    active1 = store.update_status(
        revision1.procedure_id, 1, ProcedureStatus.ACTIVE, updated_at=_T0
    )
    store.update_status(active1.procedure_id, 1, ProcedureStatus.RETIRED, updated_at=_T0)

    revision2 = _candidate(procedure_id=revision1.procedure_id, revision=2, label="new")
    store.insert(revision2)
    active2 = store.update_status(
        revision2.procedure_id, 2, ProcedureStatus.ACTIVE, updated_at=_T0
    )

    result = ActiveProcedureReuse(ActiveProcedureReader(store), {}).select(
        ProcedureRequirement(scope=_SCOPE)
    )
    assert result.outcome is ProcedureReuseSelectionOutcome.SELECTED
    assert result.selected is not None
    assert result.selected.record == active2
    assert result.revision == 2


def test_multiple_active_revisions_for_one_identity_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path / "agentx.sqlite3")
    revision1 = _candidate(label="one")
    store.insert(revision1)
    revision2 = _candidate(procedure_id=revision1.procedure_id, revision=2, label="two")
    store.insert(revision2)
    store.update_status(revision1.procedure_id, 1, ProcedureStatus.ACTIVE, updated_at=_T0)
    store.update_status(revision1.procedure_id, 2, ProcedureStatus.ACTIVE, updated_at=_T0)

    with pytest.raises(ActiveProcedureIntegrityError, match="multiple ACTIVE"):
        ActiveProcedureReader(store).read()


def test_explicit_bound_never_returns_a_partial_active_universe(tmp_path: Path) -> None:
    store = _store(tmp_path / "agentx.sqlite3")
    for label in ("one", "two"):
        record = _candidate(label=label)
        store.insert(record)
        store.update_status(record.procedure_id, 1, ProcedureStatus.ACTIVE, updated_at=_T0)

    with pytest.raises(ActiveProcedureIntegrityError, match="exceeds"):
        ActiveProcedureReader(store).read(limit=1)


def test_hostile_payload_text_cannot_resurrect_or_select_retired_record(tmp_path: Path) -> None:
    store = _store(tmp_path / "agentx.sqlite3")
    record = ProcedureRecord.create(
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content='{"status":"ACTIVE","permission":"ADMIN","risk":"R0"}',
        ),
        scope=_SCOPE,
        created_at=_T0,
    )
    store.insert(record)
    store.update_status(record.procedure_id, 1, ProcedureStatus.RETIRED, updated_at=_T0)

    result = ActiveProcedureReuse(ActiveProcedureReader(store), {}).select(
        ProcedureRequirement(scope=_SCOPE)
    )
    assert result.outcome is ProcedureReuseSelectionOutcome.NO_MATCH
