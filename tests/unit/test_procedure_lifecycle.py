"""Unit tests for the M4.03 procedure lifecycle transition policy.

The policy answers one structural question — *is this status transition
permitted for this typed reason?* — and never answers *has this candidate
earned promotion?*. These tests pin the transition matrix, the typed reason
vocabulary, the explicit same-status behaviour, monotonic retirement,
fail-closed typing, determinism, and the absence of any store, revision,
record, clock, or authority effect.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, cast

import pytest

from agentx.core.ids import ProcedureId
from agentx.core.knowledge import KnowledgeStatus
from agentx.core.procedure_lifecycle import (
    LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS,
    PERMITTED_TRANSITION_REASONS,
    TERMINAL_PROCEDURE_STATUSES,
    ProcedureLifecycleAssessment,
    ProcedureLifecycleDecision,
    ProcedureLifecycleError,
    ProcedureLifecycleReason,
    assess_procedure_transition,
    is_terminal_status,
    is_transition_permitted,
    legal_target_statuses,
    permitted_reasons_for,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
    ProcedureValidationError,
)
from agentx.core.tasks import TaskStatus

_T0 = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
_PROCEDURE_ID = ProcedureId.create()

_RETIREMENT_REASONS = (
    ProcedureLifecycleReason.MANUAL_RETIREMENT,
    ProcedureLifecycleReason.SUPERSEDED_BY_NEW_REVISION,
    ProcedureLifecycleReason.SAFETY_RETIREMENT,
)

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
)


def _assess(
    current: ProcedureStatus,
    target: ProcedureStatus,
    reason: ProcedureLifecycleReason,
    *,
    procedure_id: ProcedureId = _PROCEDURE_ID,
    revision: int = 1,
    requested_at: datetime = _T0,
) -> ProcedureLifecycleAssessment:
    return assess_procedure_transition(
        procedure_id=procedure_id,
        revision=revision,
        current_status=current,
        target_status=target,
        reason=reason,
        requested_at=requested_at,
    )


# ---------------------------------------------------------------------------
# Transition matrix.
# ---------------------------------------------------------------------------


def test_matrix_is_exhaustive_over_the_canonical_status_vocabulary() -> None:
    assert set(LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS) == set(ProcedureStatus)
    assert LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS[ProcedureStatus.CANDIDATE] == frozenset(
        {ProcedureStatus.ACTIVE, ProcedureStatus.RETIRED}
    )
    assert LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS[ProcedureStatus.ACTIVE] == frozenset(
        {ProcedureStatus.RETIRED}
    )
    assert LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS[ProcedureStatus.RETIRED] == frozenset()


def test_only_retired_is_terminal_and_terminality_derives_from_the_matrix() -> None:
    assert set(TERMINAL_PROCEDURE_STATUSES) == {ProcedureStatus.RETIRED}
    assert is_terminal_status(ProcedureStatus.RETIRED) is True
    assert is_terminal_status(ProcedureStatus.CANDIDATE) is False
    assert is_terminal_status(ProcedureStatus.ACTIVE) is False


def test_matrix_and_reason_table_agree_on_which_pairs_exist() -> None:
    matrix_pairs = {
        (current, target)
        for current, targets in LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS.items()
        for target in targets
    }
    assert matrix_pairs == set(PERMITTED_TRANSITION_REASONS)
    for reasons in PERMITTED_TRANSITION_REASONS.values():
        assert reasons, "a legal transition must have at least one permitted reason"


def test_matrix_mappings_are_read_only() -> None:
    with pytest.raises(TypeError):
        cast(Any, LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS)[ProcedureStatus.RETIRED] = frozenset(
            {ProcedureStatus.ACTIVE}
        )
    with pytest.raises(TypeError):
        cast(Any, PERMITTED_TRANSITION_REASONS)[
            (ProcedureStatus.RETIRED, ProcedureStatus.ACTIVE)
        ] = frozenset(ProcedureLifecycleReason)


# ---------------------------------------------------------------------------
# Allowed transitions.
# ---------------------------------------------------------------------------


def test_candidate_to_active_is_allowed_only_through_validation_promotion() -> None:
    assessment = _assess(
        ProcedureStatus.CANDIDATE,
        ProcedureStatus.ACTIVE,
        ProcedureLifecycleReason.VALIDATION_PROMOTION,
    )
    assert assessment.decision is ProcedureLifecycleDecision.ALLOWED
    assert assessment.permits_status_write is True
    assert assessment.is_rejected is False
    assert assessment.permitted_reasons == (ProcedureLifecycleReason.VALIDATION_PROMOTION,)
    assert "structurally allowed is not evidence validated" in assessment.explanation


@pytest.mark.parametrize("reason", _RETIREMENT_REASONS)
def test_candidate_to_retired_is_allowed_for_every_retirement_reason(
    reason: ProcedureLifecycleReason,
) -> None:
    assessment = _assess(ProcedureStatus.CANDIDATE, ProcedureStatus.RETIRED, reason)
    assert assessment.decision is ProcedureLifecycleDecision.ALLOWED
    assert assessment.permits_status_write is True


@pytest.mark.parametrize("reason", _RETIREMENT_REASONS)
def test_active_to_retired_is_allowed_for_every_retirement_reason(
    reason: ProcedureLifecycleReason,
) -> None:
    assessment = _assess(ProcedureStatus.ACTIVE, ProcedureStatus.RETIRED, reason)
    assert assessment.decision is ProcedureLifecycleDecision.ALLOWED
    assert assessment.permits_status_write is True


def test_exactly_three_status_pairs_are_ever_allowed() -> None:
    allowed: set[tuple[ProcedureStatus, ProcedureStatus]] = set()
    for current in ProcedureStatus:
        for target in ProcedureStatus:
            for reason in ProcedureLifecycleReason:
                if _assess(current, target, reason).permits_status_write:
                    allowed.add((current, target))
    assert allowed == {
        (ProcedureStatus.CANDIDATE, ProcedureStatus.ACTIVE),
        (ProcedureStatus.CANDIDATE, ProcedureStatus.RETIRED),
        (ProcedureStatus.ACTIVE, ProcedureStatus.RETIRED),
    }


# ---------------------------------------------------------------------------
# Rejected transitions: backwards moves and retirement monotonicity.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", list(ProcedureLifecycleReason))
def test_active_to_candidate_is_always_rejected(reason: ProcedureLifecycleReason) -> None:
    assessment = _assess(ProcedureStatus.ACTIVE, ProcedureStatus.CANDIDATE, reason)
    assert assessment.decision is ProcedureLifecycleDecision.REJECTED_ILLEGAL_TRANSITION
    assert assessment.permits_status_write is False
    assert assessment.is_rejected is True
    assert assessment.permitted_reasons == ()


@pytest.mark.parametrize("target", [ProcedureStatus.ACTIVE, ProcedureStatus.CANDIDATE])
@pytest.mark.parametrize("reason", list(ProcedureLifecycleReason))
def test_retired_never_moves_backwards(
    target: ProcedureStatus, reason: ProcedureLifecycleReason
) -> None:
    assessment = _assess(ProcedureStatus.RETIRED, target, reason)
    assert assessment.decision is ProcedureLifecycleDecision.REJECTED_RETIREMENT_IS_TERMINAL
    assert assessment.permits_status_write is False
    assert "never resurrected" in assessment.explanation


def test_retirement_is_monotonic_no_reason_or_timestamp_can_resurrect() -> None:
    """No reason, revision, identity, or instant unlocks a retired revision."""
    for reason in ProcedureLifecycleReason:
        for target in (ProcedureStatus.CANDIDATE, ProcedureStatus.ACTIVE):
            for revision in (1, 2, 97):
                for moment in (_T0, _T0 + timedelta(days=3650), _T0 - timedelta(days=1)):
                    assessment = _assess(
                        ProcedureStatus.RETIRED,
                        target,
                        reason,
                        revision=revision,
                        requested_at=moment,
                    )
                    assert assessment.permits_status_write is False
                    assert assessment.is_rejected is True


def test_retired_has_no_legal_targets_at_all() -> None:
    assert legal_target_statuses(ProcedureStatus.RETIRED) == frozenset()
    for target in ProcedureStatus:
        assert permitted_reasons_for(ProcedureStatus.RETIRED, target) == frozenset()


# ---------------------------------------------------------------------------
# Same-status requests.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", list(ProcedureStatus))
@pytest.mark.parametrize("reason", list(ProcedureLifecycleReason))
def test_same_status_requests_are_explicit_no_ops(
    status: ProcedureStatus, reason: ProcedureLifecycleReason
) -> None:
    assessment = _assess(status, status, reason)
    assert assessment.decision is ProcedureLifecycleDecision.NO_OP
    assert assessment.is_no_op is True
    assert assessment.permits_status_write is False
    assert assessment.is_rejected is False
    assert "no transition occurs" in assessment.explanation


@pytest.mark.parametrize("status", list(ProcedureStatus))
def test_same_status_is_never_reported_as_a_transition(status: ProcedureStatus) -> None:
    assert (
        is_transition_permitted(status, status, ProcedureLifecycleReason.VALIDATION_PROMOTION)
        is False
    )


# ---------------------------------------------------------------------------
# Reason semantics.
# ---------------------------------------------------------------------------


def test_reason_vocabulary_is_closed_and_exact() -> None:
    assert {reason.value for reason in ProcedureLifecycleReason} == {
        "validation_promotion",
        "manual_retirement",
        "superseded_by_new_revision",
        "safety_retirement",
    }


def test_decision_vocabulary_is_closed_and_exact() -> None:
    assert {decision.value for decision in ProcedureLifecycleDecision} == {
        "allowed",
        "no_op",
        "rejected_illegal_transition",
        "rejected_retirement_is_terminal",
        "rejected_reason_mismatch",
    }


@pytest.mark.parametrize("reason", _RETIREMENT_REASONS)
def test_promotion_with_a_retirement_reason_is_a_reason_mismatch(
    reason: ProcedureLifecycleReason,
) -> None:
    assessment = _assess(ProcedureStatus.CANDIDATE, ProcedureStatus.ACTIVE, reason)
    assert assessment.decision is ProcedureLifecycleDecision.REJECTED_REASON_MISMATCH
    assert assessment.permits_status_write is False
    assert assessment.permitted_reasons == (ProcedureLifecycleReason.VALIDATION_PROMOTION,)


@pytest.mark.parametrize("current", [ProcedureStatus.CANDIDATE, ProcedureStatus.ACTIVE])
def test_retirement_with_the_promotion_reason_is_a_reason_mismatch(
    current: ProcedureStatus,
) -> None:
    assessment = _assess(
        current, ProcedureStatus.RETIRED, ProcedureLifecycleReason.VALIDATION_PROMOTION
    )
    assert assessment.decision is ProcedureLifecycleDecision.REJECTED_REASON_MISMATCH
    assert assessment.permits_status_write is False


def test_permitted_reasons_helper_matches_the_reason_table() -> None:
    assert permitted_reasons_for(ProcedureStatus.CANDIDATE, ProcedureStatus.ACTIVE) == frozenset(
        {ProcedureLifecycleReason.VALIDATION_PROMOTION}
    )
    assert permitted_reasons_for(ProcedureStatus.ACTIVE, ProcedureStatus.RETIRED) == frozenset(
        _RETIREMENT_REASONS
    )
    assert permitted_reasons_for(ProcedureStatus.ACTIVE, ProcedureStatus.CANDIDATE) == frozenset()


def test_is_transition_permitted_agrees_with_the_assessment() -> None:
    for current in ProcedureStatus:
        for target in ProcedureStatus:
            for reason in ProcedureLifecycleReason:
                assert is_transition_permitted(current, target, reason) == (
                    _assess(current, target, reason).permits_status_write
                )


# ---------------------------------------------------------------------------
# Fail-closed typing: raw strings and foreign vocabularies are never coerced.
# ---------------------------------------------------------------------------


def test_plain_string_reason_is_rejected_even_though_the_enum_is_a_strenum() -> None:
    promotion_as_text: str = ProcedureLifecycleReason.VALIDATION_PROMOTION
    assert promotion_as_text == "validation_promotion"
    with pytest.raises(ProcedureLifecycleError, match="reason must be a ProcedureLifecycleReason"):
        _assess(
            ProcedureStatus.CANDIDATE,
            ProcedureStatus.ACTIVE,
            cast(ProcedureLifecycleReason, "validation_promotion"),
        )


@pytest.mark.parametrize("raw", ["active", "candidate", "retired", "ACTIVE", "verified"])
def test_plain_string_statuses_are_rejected(raw: str) -> None:
    with pytest.raises(ProcedureLifecycleError, match="must be a ProcedureStatus"):
        _assess(
            cast(ProcedureStatus, raw),
            ProcedureStatus.RETIRED,
            ProcedureLifecycleReason.MANUAL_RETIREMENT,
        )
    with pytest.raises(ProcedureLifecycleError, match="must be a ProcedureStatus"):
        _assess(
            ProcedureStatus.CANDIDATE,
            cast(ProcedureStatus, raw),
            ProcedureLifecycleReason.MANUAL_RETIREMENT,
        )


@pytest.mark.parametrize(
    "foreign", [KnowledgeStatus.VERIFIED, TaskStatus.SUCCEEDED, None, 1, object()]
)
def test_foreign_status_vocabularies_are_rejected(foreign: object) -> None:
    with pytest.raises(ProcedureLifecycleError, match="must be a ProcedureStatus"):
        _assess(
            cast(ProcedureStatus, foreign),
            ProcedureStatus.RETIRED,
            ProcedureLifecycleReason.SAFETY_RETIREMENT,
        )


def test_lifecycle_errors_are_procedure_validation_errors() -> None:
    assert issubclass(ProcedureLifecycleError, ProcedureValidationError)
    assert issubclass(ProcedureLifecycleError, ValueError)


@pytest.mark.parametrize("bad_id", ["not-an-id", None, 7, _PROCEDURE_ID.to_str()])
def test_procedure_identity_must_be_a_canonical_procedure_id(bad_id: object) -> None:
    with pytest.raises(ProcedureLifecycleError, match="procedure_id must be a ProcedureId"):
        _assess(
            ProcedureStatus.CANDIDATE,
            ProcedureStatus.ACTIVE,
            ProcedureLifecycleReason.VALIDATION_PROMOTION,
            procedure_id=cast(ProcedureId, bad_id),
        )


@pytest.mark.parametrize("bad_revision", [0, -1, 1.0, "1", True, None])
def test_revision_must_be_a_positive_integer(bad_revision: object) -> None:
    with pytest.raises(ProcedureLifecycleError, match="revision must be"):
        _assess(
            ProcedureStatus.CANDIDATE,
            ProcedureStatus.ACTIVE,
            ProcedureLifecycleReason.VALIDATION_PROMOTION,
            revision=cast(int, bad_revision),
        )


@pytest.mark.parametrize(
    "bad_timestamp",
    [datetime(2026, 9, 8, 12, 0, 0), "2026-09-08T12:00:00Z", 0, None],
)
def test_requested_at_must_be_a_timezone_aware_datetime(bad_timestamp: object) -> None:
    with pytest.raises(ProcedureLifecycleError, match="requested_at must be"):
        _assess(
            ProcedureStatus.CANDIDATE,
            ProcedureStatus.ACTIVE,
            ProcedureLifecycleReason.VALIDATION_PROMOTION,
            requested_at=cast(datetime, bad_timestamp),
        )


def test_requested_at_is_normalized_to_utc_without_changing_the_instant() -> None:
    offset = timezone(timedelta(hours=5, minutes=30))
    local = _T0.astimezone(offset)
    assessment = _assess(
        ProcedureStatus.CANDIDATE,
        ProcedureStatus.ACTIVE,
        ProcedureLifecycleReason.VALIDATION_PROMOTION,
        requested_at=local,
    )
    assert assessment.requested_at == _T0
    assert assessment.requested_at.tzinfo is UTC


def test_timestamp_never_changes_transition_legality() -> None:
    late = _T0 + timedelta(days=9999)
    early = _T0 - timedelta(days=9999)
    for moment in (_T0, late, early):
        assert (
            _assess(
                ProcedureStatus.RETIRED,
                ProcedureStatus.ACTIVE,
                ProcedureLifecycleReason.VALIDATION_PROMOTION,
                requested_at=moment,
            ).decision
            is ProcedureLifecycleDecision.REJECTED_RETIREMENT_IS_TERMINAL
        )


def test_helper_functions_reject_wrong_types() -> None:
    with pytest.raises(ProcedureLifecycleError):
        legal_target_statuses(cast(ProcedureStatus, "candidate"))
    with pytest.raises(ProcedureLifecycleError):
        is_terminal_status(cast(ProcedureStatus, "retired"))
    with pytest.raises(ProcedureLifecycleError):
        permitted_reasons_for(cast(ProcedureStatus, "candidate"), ProcedureStatus.ACTIVE)
    with pytest.raises(ProcedureLifecycleError):
        is_transition_permitted(
            ProcedureStatus.CANDIDATE,
            ProcedureStatus.ACTIVE,
            cast(ProcedureLifecycleReason, "validation_promotion"),
        )


# ---------------------------------------------------------------------------
# Revision identity, records, and inertness of payload content.
# ---------------------------------------------------------------------------


def _hostile_record(status: ProcedureStatus, revision: int = 3) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=_PROCEDURE_ID,
        revision=revision,
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content=" ".join(_HOSTILE_STRINGS),
        ),
        created_at=_T0,
        status=status,
    )


def test_revision_identity_is_echoed_verbatim_and_never_incremented() -> None:
    other_id = ProcedureId.create()
    assessment = _assess(
        ProcedureStatus.CANDIDATE,
        ProcedureStatus.ACTIVE,
        ProcedureLifecycleReason.VALIDATION_PROMOTION,
        procedure_id=other_id,
        revision=42,
    )
    assert assessment.procedure_id == other_id
    assert assessment.revision == 42


def test_identity_and_revision_never_change_transition_legality() -> None:
    baseline = _assess(
        ProcedureStatus.CANDIDATE,
        ProcedureStatus.ACTIVE,
        ProcedureLifecycleReason.VALIDATION_PROMOTION,
    )
    for revision in (1, 2, 5, 1000):
        other = _assess(
            ProcedureStatus.CANDIDATE,
            ProcedureStatus.ACTIVE,
            ProcedureLifecycleReason.VALIDATION_PROMOTION,
            procedure_id=ProcedureId.create(),
            revision=revision,
        )
        assert other.decision is baseline.decision


def test_assessing_a_record_status_never_mutates_the_record() -> None:
    record = _hostile_record(ProcedureStatus.CANDIDATE)
    snapshot = record.to_json()
    assessment = _assess(
        record.status,
        ProcedureStatus.ACTIVE,
        ProcedureLifecycleReason.VALIDATION_PROMOTION,
        procedure_id=record.procedure_id,
        revision=record.revision,
    )
    assert assessment.decision is ProcedureLifecycleDecision.ALLOWED
    assert record.status is ProcedureStatus.CANDIDATE
    assert record.revision == 3
    assert record.updated_at is None
    assert record.to_json() == snapshot


@pytest.mark.parametrize("hostile", _HOSTILE_STRINGS)
def test_hostile_payload_content_is_inert_for_every_transition(hostile: str) -> None:
    record = ProcedureRecord.create(
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=hostile),
        procedure_id=_PROCEDURE_ID,
        created_at=_T0,
    )
    clean = ProcedureRecord.create(
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content="step one"),
        procedure_id=_PROCEDURE_ID,
        created_at=_T0,
    )
    for target in ProcedureStatus:
        for reason in ProcedureLifecycleReason:
            hostile_decision = _assess(
                record.status, target, reason, revision=record.revision
            ).decision
            clean_decision = _assess(clean.status, target, reason, revision=clean.revision).decision
            assert hostile_decision is clean_decision


def test_status_is_never_inferred_from_a_record_or_its_payload() -> None:
    """A retired revision whose payload screams 'ACTIVE' stays retired-terminal."""
    retired = _hostile_record(ProcedureStatus.RETIRED)
    assessment = _assess(
        retired.status,
        ProcedureStatus.ACTIVE,
        ProcedureLifecycleReason.VALIDATION_PROMOTION,
        revision=retired.revision,
    )
    assert assessment.decision is ProcedureLifecycleDecision.REJECTED_RETIREMENT_IS_TERMINAL
    for hostile in _HOSTILE_STRINGS:
        assert hostile not in assessment.explanation


# ---------------------------------------------------------------------------
# Determinism and immutability.
# ---------------------------------------------------------------------------


def test_identical_inputs_produce_equal_assessments() -> None:
    for current in ProcedureStatus:
        for target in ProcedureStatus:
            for reason in ProcedureLifecycleReason:
                first = _assess(current, target, reason)
                second = _assess(current, target, reason)
                assert first == second
                assert first.explanation == second.explanation
                assert first.permitted_reasons == second.permitted_reasons


def test_repeated_calls_are_stable_across_many_iterations() -> None:
    expected = _assess(
        ProcedureStatus.CANDIDATE,
        ProcedureStatus.RETIRED,
        ProcedureLifecycleReason.SUPERSEDED_BY_NEW_REVISION,
    )
    for _ in range(50):
        assert (
            _assess(
                ProcedureStatus.CANDIDATE,
                ProcedureStatus.RETIRED,
                ProcedureLifecycleReason.SUPERSEDED_BY_NEW_REVISION,
            )
            == expected
        )


def test_permitted_reasons_are_deterministically_ordered() -> None:
    assessment = _assess(
        ProcedureStatus.ACTIVE,
        ProcedureStatus.RETIRED,
        ProcedureLifecycleReason.MANUAL_RETIREMENT,
    )
    assert assessment.permitted_reasons == tuple(
        sorted(assessment.permitted_reasons, key=lambda reason: reason.value)
    )
    assert len(set(assessment.permitted_reasons)) == len(assessment.permitted_reasons)


def test_assessments_are_immutable() -> None:
    assessment = _assess(
        ProcedureStatus.CANDIDATE,
        ProcedureStatus.ACTIVE,
        ProcedureLifecycleReason.VALIDATION_PROMOTION,
    )
    for field_name, value in (
        ("decision", ProcedureLifecycleDecision.ALLOWED),
        ("current_status", ProcedureStatus.RETIRED),
        ("target_status", ProcedureStatus.ACTIVE),
        ("revision", 99),
        ("explanation", "anything"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(assessment, field_name, value)


def test_assessment_has_no_dict_and_no_extra_attributes() -> None:
    assessment = _assess(
        ProcedureStatus.ACTIVE,
        ProcedureStatus.RETIRED,
        ProcedureLifecycleReason.SAFETY_RETIREMENT,
    )
    assert not hasattr(assessment, "__dict__")
    assert "promoted" not in ProcedureLifecycleAssessment.__slots__
    undeclared_attribute = "promoted"
    with pytest.raises((AttributeError, TypeError, FrozenInstanceError)):
        setattr(assessment, undeclared_attribute, True)


# ---------------------------------------------------------------------------
# Hand-built assessments are validated too (fail-closed construction).
# ---------------------------------------------------------------------------


def _assessment_kwargs() -> dict[str, object]:
    return {
        "procedure_id": _PROCEDURE_ID,
        "revision": 1,
        "current_status": ProcedureStatus.CANDIDATE,
        "target_status": ProcedureStatus.ACTIVE,
        "reason": ProcedureLifecycleReason.VALIDATION_PROMOTION,
        "requested_at": _T0,
        "decision": ProcedureLifecycleDecision.ALLOWED,
        "permitted_reasons": (ProcedureLifecycleReason.VALIDATION_PROMOTION,),
        "explanation": "structural verdict",
    }


@pytest.mark.parametrize(
    ("field_name", "value", "match"),
    [
        ("decision", "allowed", "decision must be"),
        ("permitted_reasons", ["validation_promotion"], "permitted_reasons must be a tuple"),
        ("permitted_reasons", ("validation_promotion",), "permitted_reasons entry"),
        (
            "permitted_reasons",
            (
                ProcedureLifecycleReason.MANUAL_RETIREMENT,
                ProcedureLifecycleReason.MANUAL_RETIREMENT,
            ),
            "duplicates",
        ),
        ("explanation", "   ", "explanation must be"),
        ("explanation", 7, "explanation must be"),
        ("revision", 0, "revision must be"),
        ("current_status", "candidate", "must be a ProcedureStatus"),
    ],
)
def test_hand_built_assessments_fail_closed(field_name: str, value: object, match: str) -> None:
    kwargs = _assessment_kwargs()
    kwargs[field_name] = value
    with pytest.raises(ProcedureLifecycleError, match=match):
        ProcedureLifecycleAssessment(**cast(Any, kwargs))


def test_hand_built_assessment_accepts_canonical_values() -> None:
    assessment = ProcedureLifecycleAssessment(**cast(Any, _assessment_kwargs()))
    assert assessment.permits_status_write is True
    assert assessment.requested_at == _T0


# ---------------------------------------------------------------------------
# Structural absence: no store, no revision creation, no authority surface.
# ---------------------------------------------------------------------------


def test_module_exposes_only_the_documented_surface() -> None:
    from agentx.core import procedure_lifecycle

    assert set(procedure_lifecycle.__all__) == {
        "LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS",
        "PERMITTED_TRANSITION_REASONS",
        "TERMINAL_PROCEDURE_STATUSES",
        "ProcedureLifecycleAssessment",
        "ProcedureLifecycleDecision",
        "ProcedureLifecycleError",
        "ProcedureLifecycleReason",
        "assess_procedure_transition",
        "is_terminal_status",
        "is_transition_permitted",
        "legal_target_statuses",
        "permitted_reasons_for",
    }
    public = {name for name in vars(procedure_lifecycle) if not name.startswith("_")}
    exported = set(procedure_lifecycle.__all__)
    # Only imported canonical contracts may appear alongside the exports.
    assert public - exported <= {
        "ProcedureId",
        "ProcedureStatus",
        "ProcedureValidationError",
        "Mapping",
        "MappingProxyType",
        "Final",
        "StrEnum",
        "UTC",
        "datetime",
        "dataclass",
        "annotations",
    }


def test_module_offers_no_promotion_persistence_or_revision_factory() -> None:
    from agentx.core import procedure_lifecycle

    forbidden = (
        "promote",
        "promote_procedure",
        "retire",
        "update_status",
        "save",
        "store",
        "persist",
        "next_revision",
        "create_revision",
        "new_revision",
        "execute",
        "compile",
        "now",
    )
    for name in forbidden:
        assert not hasattr(procedure_lifecycle, name), name


def test_module_does_not_import_storage_kernel_or_other_subsystems() -> None:
    import sys

    from agentx.core import procedure_lifecycle

    module_globals = vars(procedure_lifecycle)
    for value in module_globals.values():
        module_name = getattr(value, "__module__", None)
        if isinstance(module_name, str) and module_name.startswith("agentx."):
            assert module_name.startswith("agentx.core."), module_name
    assert "agentx.infrastructure.procedure_store" not in str(
        sorted(name for name in sys.modules if name.startswith("agentx.core.procedure_lifecycle"))
    )
