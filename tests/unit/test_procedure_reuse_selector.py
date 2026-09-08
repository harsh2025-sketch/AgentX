from __future__ import annotations

from datetime import UTC, datetime

from agentx.core.ids import ProcedureId
from agentx.core.procedure_matching import ProcedureCandidate, ProcedureRequirement
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)
from agentx.procedure_reuse_selector import (
    ProcedureReuseSelectionOutcome,
    ProcedureReuseSelector,
)


def _record(
    revision: int = 1,
    status: ProcedureStatus = ProcedureStatus.ACTIVE,
    *,
    scope: ProcedureScope | None = None,
) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=ProcedureId.create(),
        revision=revision,
        payload=ProcedurePayload(ProcedurePayloadKind.CANONICAL_JSON, "select_me=true"),
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        status=status,
        scope=ProcedureScope() if scope is None else scope,
    )


def test_one_active_exact_candidate_is_selected_and_identity_preserved():
    record = _record()
    result = ProcedureReuseSelector().select(
        [ProcedureCandidate(record)], ProcedureRequirement()
    )
    assert result.outcome is ProcedureReuseSelectionOutcome.SELECTED
    assert result.selected is not None
    assert (result.procedure_id, result.revision) == (record.procedure_id, record.revision)


def test_empty_excluded_and_inapplicable_candidates_are_no_match():
    selector = ProcedureReuseSelector()
    assert (
        selector.select([], ProcedureRequirement()).outcome
        is ProcedureReuseSelectionOutcome.NO_MATCH
    )
    retired = _record(status=ProcedureStatus.RETIRED)
    assert (
        selector.select([ProcedureCandidate(retired)], ProcedureRequirement()).outcome
        is ProcedureReuseSelectionOutcome.NO_MATCH
    )
    scoped = _record(
        scope=ProcedureScope({ProcedureScopeDimension.OPERATING_SYSTEM: "windows"})
    )
    requirement = ProcedureRequirement(
        scope=ProcedureScope({ProcedureScopeDimension.OPERATING_SYSTEM: "linux"})
    )
    assert (
        selector.select([ProcedureCandidate(scoped)], requirement).outcome
        is ProcedureReuseSelectionOutcome.NO_MATCH
    )


def test_exact_match_is_categorical_preference_over_compatible_match():
    exact = _record()
    broad = _record()
    requirement = ProcedureRequirement(
        scope=ProcedureScope({ProcedureScopeDimension.OPERATING_SYSTEM: "windows"})
    )
    exact = ProcedureRecord(
        procedure_id=exact.procedure_id,
        revision=exact.revision,
        payload=exact.payload,
        created_at=exact.created_at,
        status=exact.status,
        scope=requirement.scope,
    )
    result = ProcedureReuseSelector().select(
        [ProcedureCandidate(broad), ProcedureCandidate(exact)], requirement
    )
    assert result.outcome is ProcedureReuseSelectionOutcome.SELECTED
    assert result.selected is not None and result.selected.record is exact


def test_equal_active_candidates_are_ambiguous_and_duplicate_is_idempotent():
    first = _record()
    second = _record()
    selector = ProcedureReuseSelector()
    result = selector.select(
        [ProcedureCandidate(first), ProcedureCandidate(first), ProcedureCandidate(second)],
        ProcedureRequirement(),
    )
    assert result.outcome is ProcedureReuseSelectionOutcome.AMBIGUOUS
    one = selector.select(
        [ProcedureCandidate(first), ProcedureCandidate(first)], ProcedureRequirement()
    )
    assert one.outcome is ProcedureReuseSelectionOutcome.SELECTED


def test_revision_number_does_not_break_tie_or_hostile_metadata():
    first = _record(revision=1)
    second = _record(revision=2)
    result = ProcedureReuseSelector().select(
        [ProcedureCandidate(second), ProcedureCandidate(first)], ProcedureRequirement()
    )
    assert result.outcome is ProcedureReuseSelectionOutcome.AMBIGUOUS
