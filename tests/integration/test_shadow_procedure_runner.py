"""Integration tests for the N2.16 shadow-procedure validation runner.

These exercise the runner end-to-end against a real durable ``ProcedureStore``
(backed by SQLite via ``tmp_path``) to prove a crucial property: shadow
validation emits canonical M5.05 evidence while leaving the store and the live
procedure revision completely untouched. The candidate is executed only
through a deterministic fake harness; nothing is promoted, activated, retired,
rolled back, or routed into production.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from agentx.core.events import VerificationPayload
from agentx.core.ids import ProcedureId, TaskId
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureStatus,
)
from agentx.core.shadow_repair import (
    ShadowRepairDisposition,
    ShadowRepairMode,
    ShadowRepairResult,
)
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.shadow_procedure_runner import (
    ShadowCaseRequest,
    ShadowHarnessTrial,
    ShadowProcedureCandidate,
    ShadowValidationCase,
    run_shadow_validation,
)
from tests.support.shadow_fakes import build_trial, make_port, observed_step

_T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "agentx-shadow-runner-integration")


def make_clock(start: datetime = _T0) -> Callable[[], datetime]:
    state = {"t": start}

    def clock() -> datetime:
        state["t"] = state["t"] + timedelta(microseconds=1)
        return state["t"]

    return clock


def make_run_ids() -> Callable[[], uuid.UUID]:
    state = {"n": 0}

    def factory() -> uuid.UUID:
        state["n"] += 1
        return uuid.uuid5(_NAMESPACE, f"integration-run-{state['n']}")

    return factory


def _payload(text: str) -> ProcedurePayload:
    return ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=text)


def _case(task_id: TaskId | None = None) -> ShadowValidationCase:
    return ShadowValidationCase(task_id=task_id, target_node_id="n.act")


def _store_with_active_source(tmp_path) -> tuple[ProcedureStore, ProcedureId]:
    """Create a store holding source revision 1 (ACTIVE) and candidate rev 2."""
    procedure_id = ProcedureId.create()
    store = ProcedureStore(SQLiteDatabase(tmp_path / "shadow-runner.sqlite3"))
    source = ProcedureRecord.create(
        procedure_id=procedure_id,
        revision=1,
        payload=_payload("source procedure revision 1"),
        scope=ProcedureScope(),
        created_at=_T0,
    )
    store.insert(source)
    store.update_status(procedure_id, 1, ProcedureStatus.ACTIVE, updated_at=_T0)
    candidate = ProcedureRecord.create(
        procedure_id=procedure_id,
        revision=2,
        payload=_payload("repaired candidate revision 2"),
        scope=ProcedureScope(),
        created_at=_T0,
    )
    store.insert(candidate)
    return store, procedure_id


def _passing_handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
    return build_trial(
        request,
        verification=VerificationPayload(passed=True, detail="independent canonical check"),
        steps=(observed_step(node_id=request.case.target_node_id),),
    )


def test_shadow_run_emits_canonical_evidence_and_never_mutates_store(tmp_path) -> None:
    store, procedure_id = _store_with_active_source(tmp_path)
    before = store.list_records()
    before_active = store.get(procedure_id, 1)
    before_candidate = store.get(procedure_id, 2)
    assert before_active is not None and before_active.status is ProcedureStatus.ACTIVE
    assert before_candidate is not None and before_candidate.status is ProcedureStatus.CANDIDATE

    correlation_id = uuid.uuid4()
    run = run_shadow_validation(
        procedure_id=procedure_id,
        source_revision=1,
        candidate=ShadowProcedureCandidate(
            content="repaired candidate procedure text",
            candidate_revision=2,
        ),
        cases=[_case(TaskId.create()), _case(TaskId.create()), _case(TaskId.create())],
        harness=make_port(_passing_handler),
        correlation_id=correlation_id,
        clock=make_clock(),
        run_id_factory=make_run_ids(),
    )

    # All bounded cases produced PASSED canonical records bound to the run.
    assert run.all_passed is True
    assert run.summary.total == 3
    assert len(run.trials) == 3
    for trial in run.trials:
        assert isinstance(trial, ShadowRepairResult)
        assert trial.procedure_id == procedure_id
        assert trial.source_revision == 1
        assert trial.candidate_revision == 2
        assert trial.correlation_id == correlation_id
        assert trial.mode is ShadowRepairMode.NON_COMMITTING
        assert trial.disposition is ShadowRepairDisposition.PASSED
        # Canonical records re-parse from their own JSON unchanged.
        assert ShadowRepairResult.from_json(trial.to_json()) == trial

    # Shadow evidence is only evidence: the durable store is byte-for-byte
    # unchanged and the candidate was never promoted, activated, or routed in.
    assert store.list_records() == before
    after_active = store.get(procedure_id, 1)
    after_candidate = store.get(procedure_id, 2)
    assert after_active is not None and after_active.status is ProcedureStatus.ACTIVE
    assert after_candidate is not None and after_candidate.status is ProcedureStatus.CANDIDATE


def test_shadow_failure_is_preserved_and_store_still_untouched(tmp_path) -> None:
    store, procedure_id = _store_with_active_source(tmp_path)
    before = store.list_records()

    def failing_handler(request: ShadowCaseRequest) -> ShadowHarnessTrial:
        if request.case_index == 2:
            return build_trial(
                request,
                verification=VerificationPayload(passed=False),
                steps=(observed_step(),),
            )
        return _passing_handler(request)

    run = run_shadow_validation(
        procedure_id=procedure_id,
        source_revision=1,
        candidate=ShadowProcedureCandidate(
            content="repaired candidate procedure text",
            candidate_revision=2,
        ),
        cases=[_case(), _case(), _case()],
        harness=make_port(failing_handler),
        correlation_id=uuid.uuid4(),
        clock=make_clock(),
        run_id_factory=make_run_ids(),
    )

    assert run.summary.passed == 2
    assert run.summary.failed == 1
    assert run.all_passed is False
    assert store.list_records() == before
    after_candidate = store.get(procedure_id, 2)
    assert after_candidate is not None and after_candidate.status is ProcedureStatus.CANDIDATE
