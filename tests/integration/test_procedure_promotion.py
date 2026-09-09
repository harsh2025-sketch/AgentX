"""Integration tests for the N2.10 explicit procedure-promotion transaction.

These exercise the transaction over the real durable stack — canonical
validation evidence (M4.02) and lifecycle decisions (M4.03) composed against
a real ``ProcedureStore`` on a real SQLite database file — and pin the
properties that only appear end to end:

- a promoted revision persists as ACTIVE across a full store restart;
- history is append-only and preserved; candidates and evidence are never
  deleted;
- the compare-and-set write makes concurrent promotions of one candidate
  single-winner and makes a concurrent retirement win over a stale promotion;
- every rejected transaction leaves persistence byte-identical.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from threading import Thread

from agentx.capabilities.abi import (
    CapabilityObservation,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.core.ids import TaskId
from agentx.core.procedure_lifecycle import (
    ProcedureLifecycleReason,
    assess_procedure_transition,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
)
from agentx.core.task_state import try_transition_task
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.kernel.resource_budget import ResourceUsage
from agentx.procedure_promotion import (
    ProcedurePromotionOutcome,
    ProcedurePromotionRequest,
    ProcedurePromotionResult,
    promote_procedure_candidate,
)
from agentx.procedure_validation import (
    ProcedureCandidateIdentity,
    ValidationDecision,
    ValidationPolicy,
    ValidationReport,
    ValidationRunEvidence,
)

_T0 = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 9, 8, 12, 30, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Canonical end-to-end builders.
# ---------------------------------------------------------------------------


def _store(path: Path) -> ProcedureStore:
    store = ProcedureStore(database=SQLiteDatabase(path=path))
    assert store.list_records() == ()  # migrate/WAL before any use
    return store


def _candidate(store: ProcedureStore, *, content: str = '{"steps": []}') -> ProcedureRecord:
    record = ProcedureRecord.create(
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=content),
        revision=1,
        created_at=_T0,
    )
    store.insert(record)
    return record


def _verified_outcome(run: int) -> ClosedLoopOutcome:
    observation = CapabilityObservation(summary="step completed", data={"run": run})
    pending = Task.create(objective=f"validate the candidate (run {run})")
    running = try_transition_task(pending, TaskStatus.RUNNING).unwrap()
    return ClosedLoopOutcome(
        task=try_transition_task(running, TaskStatus.SUCCEEDED).unwrap(),
        kind=LoopOutcome.VERIFIED,
        error=None,
        execution=ExecutionResult(
            succeeded=True, message="invocation produced a result", observation=observation
        ),
        observation=observation,
        verification=VerificationResult(passed=True, detail="postcondition holds"),
        budget_usage=ResourceUsage.zero(),
    )


def _eligible_report(record: ProcedureRecord) -> ValidationReport:
    evidence = [
        ValidationRunEvidence(
            run_id=TaskId.create(),
            procedure_id=record.procedure_id,
            revision=record.revision,
            outcome=_verified_outcome(index),
            parameter_binding={"path": f"C:/data/file-{index}.txt"},
            recorded_at=_T0,
        )
        for index in range(2)
    ]
    report = ValidationPolicy().evaluate(
        ProcedureCandidateIdentity(procedure_id=record.procedure_id, revision=record.revision),
        evidence,
    )
    assert report.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION
    return report


def _promotion_request(record: ProcedureRecord) -> ProcedurePromotionRequest:
    report = _eligible_report(record)
    assessment = assess_procedure_transition(
        procedure_id=record.procedure_id,
        revision=record.revision,
        current_status=record.status,
        target_status=ProcedureStatus.ACTIVE,
        reason=ProcedureLifecycleReason.VALIDATION_PROMOTION,
        requested_at=_T1,
    )
    return ProcedurePromotionRequest(
        procedure_id=record.procedure_id,
        revision=record.revision,
        expected_current=record,
        validation_report=report,
        lifecycle_assessment=assessment,
        requested_at=_T1,
    )


# ---------------------------------------------------------------------------
# The full canonical pipeline promotes and persists across restarts.
# ---------------------------------------------------------------------------


def test_eligible_candidate_promotes_and_survives_a_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "procedures.sqlite3"
    store = _store(db_path)
    record = _candidate(store)
    request = _promotion_request(record)

    result = promote_procedure_candidate(store, request)

    assert result.promoted is True
    assert result.outcome is ProcedurePromotionOutcome.PROMOTED
    assert result.prior_status is ProcedureStatus.CANDIDATE
    assert result.resulting_status is ProcedureStatus.ACTIVE

    # A completely new store instance over the same file — a restart.
    restarted = ProcedureStore(database=SQLiteDatabase(path=db_path))
    stored = restarted.get(record.procedure_id, 1)
    assert stored is not None
    assert stored.status is ProcedureStatus.ACTIVE
    assert stored.updated_at == _T1
    assert stored.payload == record.payload
    assert restarted.history(record.procedure_id) == (stored,)


def test_promotion_appends_no_revision_and_keeps_history_append_only(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "procedures.sqlite3"
    store = _store(db_path)
    record = _candidate(store)

    assert promote_procedure_candidate(store, _promotion_request(record)).promoted is True

    # Revision creation belongs to other boundaries; a NEW candidate revision
    # is appended (never back-filled), and the promoted revision is preserved.
    successor = ProcedureRecord.create(
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content='{"v": 2}'),
        procedure_id=record.procedure_id,
        revision=2,
        created_at=_T1,
    )
    store.insert(successor)
    store.update_status(
        record.procedure_id, 1, ProcedureStatus.RETIRED, updated_at=_T1
    )  # canonical explicit operator act, outside N2.10

    history = store.history(record.procedure_id)
    assert [(item.revision, item.status) for item in history] == [
        (1, ProcedureStatus.RETIRED),
        (2, ProcedureStatus.CANDIDATE),
    ]

    result = promote_procedure_candidate(store, _promotion_request(successor))
    assert result.promoted is True
    history = store.history(record.procedure_id)
    assert [(item.revision, item.status) for item in history] == [
        (1, ProcedureStatus.RETIRED),
        (2, ProcedureStatus.ACTIVE),
    ]
    assert len(store.list_records()) == 2  # no row was ever deleted or added by promotion


# ---------------------------------------------------------------------------
# Stale-revision behavior and atomicity.
# ---------------------------------------------------------------------------


def test_retired_revision_wins_over_a_stale_promotion(tmp_path: Path) -> None:
    db_path = tmp_path / "procedures.sqlite3"
    store = _store(db_path)
    record = _candidate(store)
    request = _promotion_request(record)  # validated while the revision was CANDIDATE

    # An operator retires the candidate before the promotion transaction runs.
    store.update_status(record.procedure_id, 1, ProcedureStatus.RETIRED, updated_at=_T1)

    result = promote_procedure_candidate(store, request)

    assert result.outcome is ProcedurePromotionOutcome.REJECTED_STALE_CURRENT_RECORD
    assert result.prior_status is ProcedureStatus.RETIRED
    restarted = ProcedureStore(database=SQLiteDatabase(path=db_path))
    stored = restarted.get(record.procedure_id, 1)
    assert stored is not None
    assert stored.status is ProcedureStatus.RETIRED  # never resurrected, atomically


def test_promoted_revision_wins_over_a_stale_repetition(tmp_path: Path) -> None:
    db_path = tmp_path / "procedures.sqlite3"
    store = _store(db_path)
    record = _candidate(store)
    request = _promotion_request(record)
    assert promote_procedure_candidate(store, request).promoted is True
    winner = store.get(record.procedure_id, 1)

    replay = promote_procedure_candidate(store, request)

    assert replay.outcome is ProcedurePromotionOutcome.REJECTED_STALE_CURRENT_RECORD
    assert store.get(record.procedure_id, 1) == winner  # byte-identical; no second write


def test_concurrent_promotions_have_exactly_one_winner(tmp_path: Path) -> None:
    db_path = tmp_path / "procedures.sqlite3"
    store = _store(db_path)
    record = _candidate(store)
    request = _promotion_request(record)
    worker_count = 4
    outcomes: dict[int, object] = {}
    errors: dict[int, object] = {}

    def worker(index: int) -> None:
        # Each worker opens its own store over the same database file, like
        # independent processes would.
        worker_store = ProcedureStore(database=SQLiteDatabase(path=db_path))
        try:
            outcomes[index] = promote_procedure_candidate(worker_store, request)
        except Exception as error:
            errors[index] = error

    threads = [Thread(target=worker, args=(index,)) for index in range(worker_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    promoted = [
        index
        for index, outcome in outcomes.items()
        if isinstance(outcome, ProcedurePromotionResult)
        and outcome.outcome is ProcedurePromotionOutcome.PROMOTED
    ]
    assert errors == {} or all(
        type(error).__name__ == "TransactionError" for error in errors.values()
    ), errors  # only infrastructure write-lock contention may surface
    assert len(promoted) >= 1  # the compare-and-set write always has a winner
    assert len(promoted) <= 1  # ...and never more than one

    stored = store.get(record.procedure_id, 1)
    assert stored is not None
    assert stored.status is ProcedureStatus.ACTIVE
    assert len(store.history(record.procedure_id)) == 1  # append-only preserved


def test_concurrent_promotion_of_distinct_procedures_is_independent(tmp_path: Path) -> None:
    db_path = tmp_path / "procedures.sqlite3"
    store = _store(db_path)
    records = [_candidate(store, content=f'{{"n": {index}}}') for index in range(3)]
    requests = [_promotion_request(record) for record in records]
    outcomes: dict[int, object] = {}

    def worker(index: int) -> None:
        worker_store = ProcedureStore(database=SQLiteDatabase(path=db_path))
        outcomes[index] = promote_procedure_candidate(worker_store, requests[index])

    threads = [Thread(target=worker, args=(index,)) for index in range(len(records))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(
        isinstance(outcome, ProcedurePromotionResult)
        and outcome.outcome is ProcedurePromotionOutcome.PROMOTED
        for outcome in outcomes.values()
    ), outcomes
    for record in records:
        stored = store.get(record.procedure_id, 1)
        assert stored is not None
        assert stored.status is ProcedureStatus.ACTIVE


def test_rejected_transactions_leave_persistence_byte_identical(tmp_path: Path) -> None:
    db_path = tmp_path / "procedures.sqlite3"
    store = _store(db_path)
    record = _candidate(store)
    request = _promotion_request(record)  # validated while the revision was CANDIDATE

    before = [item.to_json() for item in store.list_records()]
    store.update_status(record.procedure_id, 1, ProcedureStatus.RETIRED, updated_at=_T1)
    after_retirement = [item.to_json() for item in store.list_records()]
    assert after_retirement != before  # the operator retirement stands

    result = promote_procedure_candidate(store, request)
    assert result.promoted is False
    assert result.outcome is ProcedurePromotionOutcome.REJECTED_STALE_CURRENT_RECORD
    assert [item.to_json() for item in store.list_records()] == after_retirement

    replay = promote_procedure_candidate(store, request)  # the stale request again
    assert replay.promoted is False
    assert [item.to_json() for item in store.list_records()] == after_retirement
