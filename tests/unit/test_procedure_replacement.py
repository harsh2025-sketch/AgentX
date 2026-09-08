"""Unit tests for the C4.08 procedure revision replacement / rollback policy.

These tests pin the deterministic eligibility rules of
``agentx.core.procedure_replacement``: the revision relation for each kind,
the closed decision vocabulary, the typed evidence requirements, the
history-preservation guarantees, and the guarantee that nothing incidental —
timestamps, payload text, or "latest wins" — can promote a revision.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any

import pytest

from agentx.core.ids import ProcedureId
from agentx.core.procedure_replacement import (
    CURRENT_PROCEDURE_REPLACEMENT_SCHEMA_VERSION,
    EvidencePresence,
    ProcedureReplacementDecision,
    ProcedureReplacementEvidence,
    ProcedureReplacementFinding,
    ProcedureReplacementKind,
    ProcedureReplacementOutcome,
    ProcedureReplacementPolicyError,
    ProcedureReplacementReason,
    ProcedureReplacementRequest,
    ProcedureReplacementRequestError,
    RetiredTargetReactivation,
    TargetIntegrityState,
    assess_procedure_replacement,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
)

FORWARD = ProcedureReplacementKind.FORWARD_REPLACEMENT
ROLLBACK = ProcedureReplacementKind.ROLLBACK
ELIGIBLE = ProcedureReplacementOutcome.ELIGIBLE
INELIGIBLE = ProcedureReplacementOutcome.INELIGIBLE
INSUFFICIENT = ProcedureReplacementOutcome.INSUFFICIENT_EVIDENCE
INVALID_RELATION = ProcedureReplacementOutcome.INVALID_REVISION_RELATION
WRONG_PROCEDURE = ProcedureReplacementOutcome.WRONG_PROCEDURE

_BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def _payload(content: str = '{"graph": "v1"}') -> ProcedurePayload:
    return ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=content)


def _record(
    revision: int,
    status: ProcedureStatus,
    *,
    procedure_id: ProcedureId | None = None,
    payload_content: str = '{"graph": "v1"}',
    created_at: datetime = _BASE_TIME,
    updated_at: datetime | None = None,
    scope: ProcedureScope | None = None,
) -> ProcedureRecord:
    """Build one canonical record; a fresh identity is generated when omitted."""
    return ProcedureRecord(
        procedure_id=ProcedureId.create() if procedure_id is None else procedure_id,
        revision=revision,
        payload=_payload(payload_content),
        created_at=created_at,
        status=status,
        scope=ProcedureScope() if scope is None else scope,
        updated_at=updated_at,
    )


def _full_evidence() -> ProcedureReplacementEvidence:
    return ProcedureReplacementEvidence(
        validation_evidence=EvidencePresence.PRESENT,
        shadow_evidence=EvidencePresence.PRESENT,
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=("artifact:validation-1", "episode:shadow-1"),
    )


def _request(
    kind: ProcedureReplacementKind,
    reason: ProcedureReplacementReason,
    active: ProcedureRecord,
    target: ProcedureRecord,
    known: tuple[int, ...],
    evidence: ProcedureReplacementEvidence | None = None,
    *,
    reactivation: RetiredTargetReactivation = RetiredTargetReactivation.NOT_ATTESTED,
) -> ProcedureReplacementRequest:
    return ProcedureReplacementRequest(
        kind=kind,
        reason=reason,
        active_revision=active,
        target_revision=target,
        known_revisions=known,
        evidence=_full_evidence() if evidence is None else evidence,
        retired_target_reactivation=reactivation,
    )


def _forward_pair(procedure_id: ProcedureId | None = None) -> tuple[ProcedureRecord, ...]:
    """Active revision 1 plus a candidate revision 2 of the same procedure."""
    pid = ProcedureId.create() if procedure_id is None else procedure_id
    return (
        _record(1, ProcedureStatus.ACTIVE, procedure_id=pid),
        _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid),
    )


# ---------------------------------------------------------------------------
# Forward replacement: revision relation
# ---------------------------------------------------------------------------


def test_same_procedure_contiguous_next_revision_is_eligible() -> None:
    active, target = _forward_pair()

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2))
    )

    assert decision.outcome is ELIGIBLE
    assert decision.findings == ()
    assert decision.kind is FORWARD
    assert decision.reason is ProcedureReplacementReason.VALIDATED_REPAIR
    assert decision.procedure_id == active.procedure_id
    assert (decision.active_revision, decision.target_revision) == (1, 2)


def test_forward_replacement_may_register_a_brand_new_next_revision() -> None:
    """A not-yet-stored target is legal when it is exactly the next revision."""
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1,))
    )

    assert decision.outcome is ELIGIBLE


def test_wrong_procedure_id_is_refused_even_with_identical_payload() -> None:
    active = _record(1, ProcedureStatus.ACTIVE)
    target = _record(
        2,
        ProcedureStatus.CANDIDATE,
        procedure_id=ProcedureId.create(),
        payload_content='{"graph": "v1"}',
    )

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2))
    )

    assert decision.outcome is WRONG_PROCEDURE
    assert decision.findings == (ProcedureReplacementFinding.PROCEDURE_IDENTITY_MISMATCH,)


def test_same_revision_forward_replacement_is_refused() -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(1, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1,))
    )

    assert decision.outcome is INVALID_RELATION
    assert decision.findings == (ProcedureReplacementFinding.REVISION_NOT_STRICTLY_LATER,)


def test_lower_revision_forward_replacement_is_refused() -> None:
    pid = ProcedureId.create()
    active = _record(3, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2, 3))
    )

    assert decision.outcome is INVALID_RELATION
    assert decision.findings == (ProcedureReplacementFinding.REVISION_NOT_STRICTLY_LATER,)


def test_non_contiguous_forward_revision_is_refused() -> None:
    """The canonical store keeps revisions contiguous, so jumps are refused."""
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(3, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2, 3))
    )

    assert decision.outcome is INVALID_RELATION
    assert decision.findings == (ProcedureReplacementFinding.REVISION_NOT_CONTIGUOUS,)


def test_new_revision_must_be_the_next_append_only_revision() -> None:
    """A prospective revision may not be inserted ahead of the stored maximum."""
    pid = ProcedureId.create()
    active = _record(2, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(3, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2, 4))
    )

    assert decision.outcome is INVALID_RELATION
    assert decision.findings == (
        ProcedureReplacementFinding.NEW_REVISION_BREAKS_APPEND_ONLY_HISTORY,
    )


def test_huge_revision_number_is_refused() -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(2**62, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1,))
    )

    assert decision.outcome is INVALID_RELATION
    assert ProcedureReplacementFinding.REVISION_NOT_CONTIGUOUS in decision.findings


# ---------------------------------------------------------------------------
# Rollback: revision relation and history
# ---------------------------------------------------------------------------


def _rollback_pair(
    target_status: ProcedureStatus,
) -> tuple[ProcedureRecord, ProcedureRecord, tuple[int, ...]]:
    pid = ProcedureId.create()
    active = _record(3, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(2, target_status, procedure_id=pid)
    return active, target, (1, 2, 3)


def test_manual_rollback_to_a_known_earlier_revision_is_eligible() -> None:
    active, target, known = _rollback_pair(ProcedureStatus.CANDIDATE)

    decision = assess_procedure_replacement(
        _request(ROLLBACK, ProcedureReplacementReason.MANUAL_ROLLBACK, active, target, known)
    )

    assert decision.outcome is ELIGIBLE
    assert decision.kind is ROLLBACK
    assert (decision.active_revision, decision.target_revision) == (3, 2)


def test_regression_rollback_is_eligible() -> None:
    active, target, known = _rollback_pair(ProcedureStatus.CANDIDATE)

    decision = assess_procedure_replacement(
        _request(ROLLBACK, ProcedureReplacementReason.REGRESSION_ROLLBACK, active, target, known)
    )

    assert decision.outcome is ELIGIBLE


def test_safety_rollback_is_eligible() -> None:
    active, target, known = _rollback_pair(ProcedureStatus.CANDIDATE)

    decision = assess_procedure_replacement(
        _request(ROLLBACK, ProcedureReplacementReason.SAFETY_ROLLBACK, active, target, known)
    )

    assert decision.outcome is ELIGIBLE


def test_higher_revision_rollback_is_refused() -> None:
    pid = ProcedureId.create()
    active = _record(2, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(5, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(
            ROLLBACK,
            ProcedureReplacementReason.MANUAL_ROLLBACK,
            active,
            target,
            (1, 2, 3, 4, 5),
        )
    )

    assert decision.outcome is INVALID_RELATION
    assert decision.findings == (ProcedureReplacementFinding.REVISION_NOT_STRICTLY_EARLIER,)


def test_rollback_to_the_same_revision_is_refused() -> None:
    pid = ProcedureId.create()
    active = _record(3, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(3, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(ROLLBACK, ProcedureReplacementReason.MANUAL_ROLLBACK, active, target, (1, 2, 3))
    )

    assert decision.outcome is INVALID_RELATION
    assert decision.findings == (ProcedureReplacementFinding.REVISION_NOT_STRICTLY_EARLIER,)


def test_rollback_target_must_be_a_known_historical_revision() -> None:
    pid = ProcedureId.create()
    active = _record(5, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(3, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(
            ROLLBACK,
            ProcedureReplacementReason.MANUAL_ROLLBACK,
            active,
            target,
            (1, 2, 5),
        )
    )

    assert decision.outcome is INVALID_RELATION
    assert decision.findings == (ProcedureReplacementFinding.TARGET_NOT_IN_KNOWN_HISTORY,)


def test_rollback_keeps_the_target_historical_identity() -> None:
    """A rollback selects an existing revision; it never clones into a new one."""
    active, target, known = _rollback_pair(ProcedureStatus.CANDIDATE)

    decision = assess_procedure_replacement(
        _request(ROLLBACK, ProcedureReplacementReason.MANUAL_ROLLBACK, active, target, known)
    )

    assert decision.target_revision == target.revision == 2
    assert decision.active_revision == 3
    assert target.revision == 2  # historical identity is untouched


# ---------------------------------------------------------------------------
# Target status behaviour
# ---------------------------------------------------------------------------


def test_candidate_target_is_the_normal_forward_case() -> None:
    active, target = _forward_pair()

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2))
    )

    assert decision.outcome is ELIGIBLE


def test_active_target_is_refused_so_two_active_revisions_can_never_coexist() -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(2, ProcedureStatus.ACTIVE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2))
    )

    assert decision.outcome is INELIGIBLE
    assert decision.findings == (ProcedureReplacementFinding.TARGET_ALREADY_ACTIVE,)


def test_designated_active_record_must_actually_be_active() -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.CANDIDATE, procedure_id=pid)
    target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2))
    )

    assert decision.outcome is INELIGIBLE
    assert decision.findings == (ProcedureReplacementFinding.DESIGNATED_ACTIVE_NOT_ACTIVE,)


def test_forward_replacement_to_a_retired_revision_is_structurally_refused() -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(2, ProcedureStatus.RETIRED, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2))
    )

    assert decision.outcome is INELIGIBLE
    assert decision.findings == (ProcedureReplacementFinding.FORWARD_TARGET_IS_RETIRED,)


def test_rollback_to_retired_history_requires_a_controlled_lifecycle_attestation() -> None:
    active, target, known = _rollback_pair(ProcedureStatus.RETIRED)

    unattested = assess_procedure_replacement(
        _request(ROLLBACK, ProcedureReplacementReason.MANUAL_ROLLBACK, active, target, known)
    )
    attested = assess_procedure_replacement(
        _request(
            ROLLBACK,
            ProcedureReplacementReason.MANUAL_ROLLBACK,
            active,
            target,
            known,
            reactivation=RetiredTargetReactivation.CONTROLLED_LIFECYCLE_ACT,
        )
    )

    assert unattested.outcome is INSUFFICIENT
    assert unattested.findings == (
        ProcedureReplacementFinding.CONTROLLED_REACTIVATION_NOT_ATTESTED,
    )
    assert attested.outcome is ELIGIBLE
    assert attested.findings == ()


def test_retired_reactivation_attestation_does_not_mutate_the_target_record() -> None:
    active, target, known = _rollback_pair(ProcedureStatus.RETIRED)
    before = target.to_json()

    assess_procedure_replacement(
        _request(
            ROLLBACK,
            ProcedureReplacementReason.MANUAL_ROLLBACK,
            active,
            target,
            known,
            reactivation=RetiredTargetReactivation.CONTROLLED_LIFECYCLE_ACT,
        )
    )

    assert target.status is ProcedureStatus.RETIRED
    assert target.to_json() == before


# ---------------------------------------------------------------------------
# Reasons
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        (FORWARD, ProcedureReplacementReason.MANUAL_ROLLBACK),
        (FORWARD, ProcedureReplacementReason.REGRESSION_ROLLBACK),
        (FORWARD, ProcedureReplacementReason.SAFETY_ROLLBACK),
        (ROLLBACK, ProcedureReplacementReason.VALIDATED_REPAIR),
    ],
)
def test_reason_must_be_permitted_for_the_requested_kind(
    kind: ProcedureReplacementKind,
    reason: ProcedureReplacementReason,
) -> None:
    pid = ProcedureId.create()
    if kind is FORWARD:
        active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
        target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid)
        known: tuple[int, ...] = (1, 2)
    else:
        active = _record(3, ProcedureStatus.ACTIVE, procedure_id=pid)
        target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid)
        known = (1, 2, 3)

    decision = assess_procedure_replacement(_request(kind, reason, active, target, known))

    assert decision.outcome is INELIGIBLE
    assert decision.findings == (ProcedureReplacementFinding.REASON_NOT_PERMITTED_FOR_KIND,)


@pytest.mark.parametrize(
    "reason",
    [
        ProcedureReplacementReason.ENVIRONMENT_INCOMPATIBILITY,
        ProcedureReplacementReason.MANUAL_ROLLBACK,
        ProcedureReplacementReason.REGRESSION_ROLLBACK,
        ProcedureReplacementReason.SAFETY_ROLLBACK,
    ],
)
def test_every_declared_rollback_reason_is_accepted(reason: ProcedureReplacementReason) -> None:
    active, target, known = _rollback_pair(ProcedureStatus.CANDIDATE)

    decision = assess_procedure_replacement(_request(ROLLBACK, reason, active, target, known))

    assert decision.outcome is ELIGIBLE


def test_environment_incompatibility_is_also_a_valid_forward_reason() -> None:
    active, target = _forward_pair()

    decision = assess_procedure_replacement(
        _request(
            FORWARD,
            ProcedureReplacementReason.ENVIRONMENT_INCOMPATIBILITY,
            active,
            target,
            (1, 2),
        )
    )

    assert decision.outcome is ELIGIBLE


# ---------------------------------------------------------------------------
# Scope compatibility
# ---------------------------------------------------------------------------


def test_target_may_narrow_scope_but_never_widen_or_alter_it() -> None:
    pid = ProcedureId.create()
    active = _record(
        1,
        ProcedureStatus.ACTIVE,
        procedure_id=pid,
        scope=ProcedureScope(
            {
                ProcedureScopeDimension.OPERATING_SYSTEM: "windows",
                ProcedureScopeDimension.PROJECT: "x",
            }
        ),
    )
    narrowed = _record(
        2,
        ProcedureStatus.CANDIDATE,
        procedure_id=pid,
        scope=ProcedureScope(
            {
                ProcedureScopeDimension.OPERATING_SYSTEM: "windows",
                ProcedureScopeDimension.PROJECT: "x",
                ProcedureScopeDimension.APPLICATION_VERSION: "17.2",
            }
        ),
    )
    widened = _record(
        2,
        ProcedureStatus.CANDIDATE,
        procedure_id=pid,
        scope=ProcedureScope({ProcedureScopeDimension.OPERATING_SYSTEM: "windows"}),
    )

    ok = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, narrowed, (1, 2))
    )
    bad = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, widened, (1, 2))
    )

    assert ok.outcome is ELIGIBLE
    assert bad.outcome is INELIGIBLE
    assert bad.findings == (ProcedureReplacementFinding.SCOPE_NOT_COMPATIBLE,)


def test_changed_scope_value_is_refused() -> None:
    pid = ProcedureId.create()
    active = _record(
        1,
        ProcedureStatus.ACTIVE,
        procedure_id=pid,
        scope=ProcedureScope({ProcedureScopeDimension.ENVIRONMENT: "production"}),
    )
    target = _record(
        2,
        ProcedureStatus.CANDIDATE,
        procedure_id=pid,
        scope=ProcedureScope({ProcedureScopeDimension.ENVIRONMENT: "staging"}),
    )

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2))
    )

    assert decision.outcome is INELIGIBLE
    assert decision.findings == (ProcedureReplacementFinding.SCOPE_NOT_COMPATIBLE,)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_missing_every_evidence_fact_reports_every_absent_fact() -> None:
    active, target = _forward_pair()

    decision = assess_procedure_replacement(
        _request(
            FORWARD,
            ProcedureReplacementReason.VALIDATED_REPAIR,
            active,
            target,
            (1, 2),
            ProcedureReplacementEvidence(),
        )
    )

    assert decision.outcome is INSUFFICIENT
    assert decision.findings == (
        ProcedureReplacementFinding.EVIDENCE_REFERENCE_ABSENT,
        ProcedureReplacementFinding.VALIDATION_EVIDENCE_ABSENT,
        ProcedureReplacementFinding.SHADOW_EVIDENCE_ABSENT,
        ProcedureReplacementFinding.TARGET_INTEGRITY_NOT_INTACT,
    )


def test_absent_validation_evidence_blocks_both_kinds() -> None:
    active, target = _forward_pair()
    evidence = ProcedureReplacementEvidence(
        validation_evidence=EvidencePresence.ABSENT,
        shadow_evidence=EvidencePresence.PRESENT,
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=("artifact:1",),
    )

    decision = assess_procedure_replacement(
        _request(
            FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2), evidence
        )
    )

    assert decision.outcome is INSUFFICIENT
    assert decision.findings == (ProcedureReplacementFinding.VALIDATION_EVIDENCE_ABSENT,)


def test_shadow_evidence_is_required_for_forward_replacement_only() -> None:
    active, target = _forward_pair()
    evidence = ProcedureReplacementEvidence(
        validation_evidence=EvidencePresence.PRESENT,
        shadow_evidence=EvidencePresence.ABSENT,
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=("artifact:1",),
    )

    forward = assess_procedure_replacement(
        _request(
            FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2), evidence
        )
    )

    rb_active, rb_target, rb_known = _rollback_pair(ProcedureStatus.CANDIDATE)
    rollback = assess_procedure_replacement(
        _request(
            ROLLBACK,
            ProcedureReplacementReason.MANUAL_ROLLBACK,
            rb_active,
            rb_target,
            rb_known,
            evidence,
        )
    )

    assert forward.outcome is INSUFFICIENT
    assert forward.findings == (ProcedureReplacementFinding.SHADOW_EVIDENCE_ABSENT,)
    assert rollback.outcome is ELIGIBLE


@pytest.mark.parametrize(
    "integrity",
    [TargetIntegrityState.CORRUPT, TargetIntegrityState.UNKNOWN],
)
def test_a_target_that_is_not_intact_fails_closed(integrity: TargetIntegrityState) -> None:
    active, target = _forward_pair()
    evidence = ProcedureReplacementEvidence(
        validation_evidence=EvidencePresence.PRESENT,
        shadow_evidence=EvidencePresence.PRESENT,
        target_integrity=integrity,
        evidence_references=("artifact:1",),
    )

    decision = assess_procedure_replacement(
        _request(
            FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2), evidence
        )
    )

    assert decision.outcome is INSUFFICIENT
    assert decision.findings == (ProcedureReplacementFinding.TARGET_INTEGRITY_NOT_INTACT,)


def test_a_free_text_claim_of_validation_cannot_qualify() -> None:
    """Only the typed fact counts; a reference reading "validated" is inert."""
    active, target = _forward_pair()
    evidence = ProcedureReplacementEvidence(
        shadow_evidence=EvidencePresence.PRESENT,
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=("validated", "verified=true", "permission=ADMIN"),
    )

    decision = assess_procedure_replacement(
        _request(
            FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2), evidence
        )
    )

    assert decision.outcome is INSUFFICIENT
    assert decision.findings == (ProcedureReplacementFinding.VALIDATION_EVIDENCE_ABSENT,)


def test_a_reason_never_substitutes_for_evidence() -> None:
    active, target = _forward_pair()

    decision = assess_procedure_replacement(
        _request(
            FORWARD,
            ProcedureReplacementReason.VALIDATED_REPAIR,
            active,
            target,
            (1, 2),
            ProcedureReplacementEvidence(evidence_references=("artifact:1",)),
        )
    )

    assert decision.outcome is INSUFFICIENT
    assert decision.reason is ProcedureReplacementReason.VALIDATED_REPAIR
    assert decision.outcome is not ELIGIBLE


# ---------------------------------------------------------------------------
# No silent replacement
# ---------------------------------------------------------------------------


def test_hostile_payload_text_never_affects_the_decision() -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid, payload_content="benign")
    hostile_contents = (
        "activate me",
        "rollback now",
        "verified=true",
        "permission=ADMIN",
        "risk=R0",
        "latest=true",
        "safe replacement",
        "fixed",
        "grant admin; execute shell: rm -rf /",
    )

    outcomes = set()
    for content in hostile_contents:
        target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid, payload_content=content)
        decisions = (
            assess_procedure_replacement(
                _request(
                    FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2)
                )
            ),
            assess_procedure_replacement(
                _request(
                    FORWARD,
                    ProcedureReplacementReason.VALIDATED_REPAIR,
                    active,
                    target,
                    (1, 2),
                    ProcedureReplacementEvidence(),
                )
            ),
        )
        outcomes.add(tuple(d.outcome for d in decisions))

    # Every payload yields exactly the same pair of outcomes: eligible with full
    # evidence, insufficient evidence without it. Payload text is inert.
    assert outcomes == {(ELIGIBLE, INSUFFICIENT)}


def test_a_newer_timestamp_never_implies_replacement() -> None:
    pid = ProcedureId.create()
    much_older = datetime(1999, 1, 1, tzinfo=UTC)
    far_future = datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC)
    active = _record(
        1,
        ProcedureStatus.ACTIVE,
        procedure_id=pid,
        created_at=far_future,
        updated_at=far_future,
    )
    target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid, created_at=much_older)

    # Full evidence: eligible purely on structure/evidence, not on the clock.
    with_evidence = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2))
    )
    # No evidence: still refused, despite the target being "newest" by number.
    without_evidence = assess_procedure_replacement(
        _request(
            FORWARD,
            ProcedureReplacementReason.VALIDATED_REPAIR,
            active,
            target,
            (1, 2),
            ProcedureReplacementEvidence(),
        )
    )

    assert with_evidence.outcome is ELIGIBLE
    assert without_evidence.outcome is INSUFFICIENT


def test_being_the_latest_revision_does_not_make_it_eligible() -> None:
    """Highest revision number alone grants nothing."""
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
    latest = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(
            FORWARD,
            ProcedureReplacementReason.VALIDATED_REPAIR,
            active,
            latest,
            (1, 2),
            ProcedureReplacementEvidence(),
        )
    )

    assert decision.outcome is not ELIGIBLE
    assert decision.outcome is INSUFFICIENT


def test_an_existing_candidate_status_alone_does_not_replace_the_active_revision() -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
    candidate = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(
            FORWARD,
            ProcedureReplacementReason.VALIDATED_REPAIR,
            active,
            candidate,
            (1, 2),
            ProcedureReplacementEvidence(target_integrity=TargetIntegrityState.INTACT),
        )
    )

    assert decision.outcome is INSUFFICIENT
    assert active.status is ProcedureStatus.ACTIVE
    assert candidate.status is ProcedureStatus.CANDIDATE


# ---------------------------------------------------------------------------
# History preservation
# ---------------------------------------------------------------------------


def test_assessment_never_mutates_either_record() -> None:
    active, target = _forward_pair()
    active_before, target_before = active.to_json(), target.to_json()

    assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2))
    )

    assert active.to_json() == active_before
    assert target.to_json() == target_before
    assert active.status is ProcedureStatus.ACTIVE
    assert target.status is ProcedureStatus.CANDIDATE


def test_a_failed_revision_is_never_removed_from_known_history() -> None:
    """Rollback selects future behaviour; revision 3 stays in the record."""
    active, target, known = _rollback_pair(ProcedureStatus.CANDIDATE)
    known_before = tuple(known)

    decision = assess_procedure_replacement(
        _request(ROLLBACK, ProcedureReplacementReason.REGRESSION_ROLLBACK, active, target, known)
    )

    assert decision.outcome is ELIGIBLE
    assert active.revision == 3
    assert tuple(known) == known_before == (1, 2, 3)
    assert 3 in known_before


def test_the_decision_exposes_no_history_rewriting_surface() -> None:
    active, target, known = _rollback_pair(ProcedureStatus.CANDIDATE)

    decision = assess_procedure_replacement(
        _request(ROLLBACK, ProcedureReplacementReason.REGRESSION_ROLLBACK, active, target, known)
    )

    forbidden = {
        "delete",
        "remove",
        "renumber",
        "rewrite",
        "mutate",
        "apply",
        "commit",
        "execute",
        "activate",
        "retire",
        "promote",
    }
    members = {name for name in dir(decision) if not name.startswith("_")}
    assert members.isdisjoint(forbidden)


# ---------------------------------------------------------------------------
# Determinism, immutability, exact binding
# ---------------------------------------------------------------------------


def test_identical_requests_produce_byte_identical_decisions() -> None:
    def build() -> ProcedureReplacementRequest:
        pid = ProcedureId.parse("11111111-1111-4111-8111-111111111111")
        active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
        target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid)
        return _request(
            FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2)
        )

    first = assess_procedure_replacement(build())
    second = assess_procedure_replacement(build())

    assert first == second
    assert first.to_json() == second.to_json()
    assert first.to_dict() == second.to_dict()


@pytest.mark.parametrize(
    "evidence",
    [
        None,
        ProcedureReplacementEvidence(),
        ProcedureReplacementEvidence(
            validation_evidence=EvidencePresence.PRESENT,
            shadow_evidence=EvidencePresence.PRESENT,
            target_integrity=TargetIntegrityState.CORRUPT,
            evidence_references=("artifact:1",),
        ),
    ],
)
def test_repeated_assessment_is_stable_for_every_evidence_shape(
    evidence: ProcedureReplacementEvidence | None,
) -> None:
    active, target = _forward_pair()

    def build() -> ProcedureReplacementRequest:
        return _request(
            FORWARD,
            ProcedureReplacementReason.VALIDATED_REPAIR,
            active,
            target,
            (1, 2),
            evidence,
        )

    outcomes = [assess_procedure_replacement(build()).to_json() for _ in range(5)]

    assert len(set(outcomes)) == 1


def test_decision_is_immutable() -> None:
    active, target = _forward_pair()
    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2))
    )

    with pytest.raises(FrozenInstanceError):
        decision.outcome = INELIGIBLE  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.findings = ()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        del decision.outcome


def test_request_and_evidence_are_immutable() -> None:
    active, target = _forward_pair()
    evidence = _full_evidence()
    request = _request(
        FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2), evidence
    )

    with pytest.raises(FrozenInstanceError):
        request.kind = ROLLBACK  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        evidence.validation_evidence = EvidencePresence.ABSENT  # type: ignore[misc]


def test_the_decision_binds_the_exact_revisions_it_assessed() -> None:
    pid = ProcedureId.create()
    active = _record(4, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(5, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(
            FORWARD,
            ProcedureReplacementReason.VALIDATED_REPAIR,
            active,
            target,
            (1, 2, 3, 4, 5),
        )
    )

    assert decision.procedure_id == pid
    assert decision.active_revision == 4
    assert decision.target_revision == 5
    assert decision.to_dict()["active_revision"] == 4
    assert decision.to_dict()["target_revision"] == 5


def test_a_decision_for_one_pair_does_not_validate_a_neighbouring_pair() -> None:
    pid = ProcedureId.create()
    active = _record(4, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(5, ProcedureStatus.CANDIDATE, procedure_id=pid)
    other = _record(6, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(
            FORWARD,
            ProcedureReplacementReason.VALIDATED_REPAIR,
            active,
            target,
            (1, 2, 3, 4, 5, 6),
        )
    )
    neighbouring = assess_procedure_replacement(
        _request(
            FORWARD,
            ProcedureReplacementReason.VALIDATED_REPAIR,
            active,
            other,
            (1, 2, 3, 4, 5, 6),
        )
    )

    assert decision.outcome is ELIGIBLE
    assert neighbouring.outcome is INVALID_RELATION
    assert decision.target_revision != neighbouring.target_revision


def test_decision_serialization_is_deterministic_and_json_safe() -> None:
    active, target = _forward_pair()
    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2))
    )

    encoded = decision.to_json()

    assert encoded == decision.to_json()
    decoded = json.loads(encoded)
    assert decoded["outcome"] == "eligible"
    assert decoded["schema_version"] == CURRENT_PROCEDURE_REPLACEMENT_SCHEMA_VERSION
    assert decoded["kind"] == "forward_replacement"
    assert decoded["reason"] == "validated_repair"
    assert decoded["findings"] == []
    assert decoded["active_revision"] == 1
    assert decoded["target_revision"] == 2


def test_findings_are_reported_in_canonical_declaration_order() -> None:
    """Findings order is fixed, so decisions stay byte-identical."""
    pid = ProcedureId.create()
    active = _record(
        1,
        ProcedureStatus.CANDIDATE,
        procedure_id=pid,
        scope=ProcedureScope({ProcedureScopeDimension.ENVIRONMENT: "production"}),
    )
    target = _record(
        2,
        ProcedureStatus.ACTIVE,
        procedure_id=pid,
        scope=ProcedureScope({ProcedureScopeDimension.ENVIRONMENT: "staging"}),
    )

    decision = assess_procedure_replacement(
        _request(
            FORWARD,
            ProcedureReplacementReason.SAFETY_ROLLBACK,
            active,
            target,
            (1, 2),
            ProcedureReplacementEvidence(),
        )
    )

    declared = list(ProcedureReplacementFinding)
    reported = list(decision.findings)
    assert decision.outcome is INELIGIBLE
    assert reported == sorted(reported, key=declared.index)
    assert reported == [
        ProcedureReplacementFinding.DESIGNATED_ACTIVE_NOT_ACTIVE,
        ProcedureReplacementFinding.TARGET_ALREADY_ACTIVE,
        ProcedureReplacementFinding.REASON_NOT_PERMITTED_FOR_KIND,
        ProcedureReplacementFinding.SCOPE_NOT_COMPATIBLE,
    ]


def test_structural_ineligibility_takes_precedence_over_missing_evidence() -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(2, ProcedureStatus.ACTIVE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(
            FORWARD,
            ProcedureReplacementReason.VALIDATED_REPAIR,
            active,
            target,
            (1, 2),
            ProcedureReplacementEvidence(),
        )
    )

    assert decision.outcome is INELIGIBLE
    assert decision.findings == (ProcedureReplacementFinding.TARGET_ALREADY_ACTIVE,)


def test_revision_relation_takes_precedence_over_every_later_check() -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(9, ProcedureStatus.ACTIVE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(
            FORWARD,
            ProcedureReplacementReason.SAFETY_ROLLBACK,
            active,
            target,
            (1, 9),
            ProcedureReplacementEvidence(),
        )
    )

    assert decision.outcome is INVALID_RELATION
    assert decision.findings == (ProcedureReplacementFinding.REVISION_NOT_CONTIGUOUS,)


def test_identity_takes_precedence_over_revision_relation() -> None:
    active = _record(1, ProcedureStatus.ACTIVE)
    target = _record(9, ProcedureStatus.ACTIVE, procedure_id=ProcedureId.create())

    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 9))
    )

    assert decision.outcome is WRONG_PROCEDURE
    assert decision.findings == (ProcedureReplacementFinding.PROCEDURE_IDENTITY_MISMATCH,)


def test_every_outcome_member_is_reachable() -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid)
    seen = {
        assess_procedure_replacement(
            _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2))
        ).outcome,
        assess_procedure_replacement(
            _request(
                FORWARD,
                ProcedureReplacementReason.VALIDATED_REPAIR,
                active,
                target,
                (1, 2),
                ProcedureReplacementEvidence(),
            )
        ).outcome,
        assess_procedure_replacement(
            _request(
                FORWARD,
                ProcedureReplacementReason.SAFETY_ROLLBACK,
                active,
                target,
                (1, 2),
            )
        ).outcome,
        assess_procedure_replacement(
            _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, active, (1,))
        ).outcome,
        assess_procedure_replacement(
            _request(
                FORWARD,
                ProcedureReplacementReason.VALIDATED_REPAIR,
                active,
                _record(2, ProcedureStatus.CANDIDATE, procedure_id=ProcedureId.create()),
                (1, 2),
            )
        ).outcome,
    }

    assert seen == set(ProcedureReplacementOutcome)


# ---------------------------------------------------------------------------
# Input validation (fail closed on malformed facts)
# ---------------------------------------------------------------------------


def test_known_revisions_must_be_non_empty_and_duplicate_free() -> None:
    active, target = _forward_pair()

    with pytest.raises(ProcedureReplacementRequestError, match="non-empty"):
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, ())
    with pytest.raises(ProcedureReplacementRequestError, match="duplicates"):
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 1, 2))


def test_known_revisions_must_contain_the_designated_active_revision() -> None:
    active, target = _forward_pair()

    with pytest.raises(
        ProcedureReplacementRequestError, match="must contain the designated active"
    ):
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (2, 3))


@pytest.mark.parametrize("known", [(True, 2), (1, False), (1, 2, True)])
def test_bool_is_never_accepted_as_a_revision_number(known: tuple[Any, ...]) -> None:
    active, target = _forward_pair()

    with pytest.raises(ProcedureReplacementRequestError, match="must be an integer"):
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, known)


@pytest.mark.parametrize("known", [(0, 1, 2), (1, -3), ("1", "2")])
def test_non_positive_or_non_integer_history_is_refused(known: tuple[Any, ...]) -> None:
    active, target = _forward_pair()

    with pytest.raises(ProcedureReplacementRequestError):
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, known)


def test_known_revisions_are_frozen_into_a_tuple() -> None:
    active, target = _forward_pair()
    mutable = [1, 2]
    request = _request(
        FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, tuple(mutable)
    )
    mutable.append(3)

    assert request.known_revisions == (1, 2)


def test_evidence_references_must_be_non_empty_trimmed_strings() -> None:
    for bad in (("",), ("  padded  "), ("ok", ""), (42,)):
        with pytest.raises(ProcedureReplacementRequestError):
            ProcedureReplacementEvidence(evidence_references=bad)  # type: ignore[arg-type]


def test_evidence_references_are_frozen_into_a_tuple() -> None:
    mutable = ["artifact:1"]
    evidence = ProcedureReplacementEvidence(evidence_references=tuple(mutable))
    mutable.append("artifact:2")

    assert evidence.evidence_references == ("artifact:1",)


def test_wrong_types_are_rejected_rather_than_coerced() -> None:
    active, target = _forward_pair()

    with pytest.raises(ProcedureReplacementRequestError):
        ProcedureReplacementRequest(
            kind="forward_replacement",  # type: ignore[arg-type]
            reason=ProcedureReplacementReason.VALIDATED_REPAIR,
            active_revision=active,
            target_revision=target,
            known_revisions=(1, 2),
            evidence=_full_evidence(),
        )
    with pytest.raises(ProcedureReplacementRequestError):
        ProcedureReplacementRequest(
            kind=FORWARD,
            reason="validated_repair",  # type: ignore[arg-type]
            active_revision=active,
            target_revision=target,
            known_revisions=(1, 2),
            evidence=_full_evidence(),
        )
    with pytest.raises(ProcedureReplacementRequestError):
        ProcedureReplacementRequest(
            kind=FORWARD,
            reason=ProcedureReplacementReason.VALIDATED_REPAIR,
            active_revision=active,
            target_revision=target,
            known_revisions=(1, 2),
            evidence={"validation_evidence": "present"},  # type: ignore[arg-type]
        )
    with pytest.raises(ProcedureReplacementRequestError):
        ProcedureReplacementRequest(
            kind=FORWARD,
            reason=ProcedureReplacementReason.VALIDATED_REPAIR,
            active_revision={"revision": 1},  # type: ignore[arg-type]
            target_revision=target,
            known_revisions=(1, 2),
            evidence=_full_evidence(),
        )


def test_evidence_enum_fields_must_be_the_canonical_enums() -> None:
    with pytest.raises(ProcedureReplacementRequestError):
        ProcedureReplacementEvidence(validation_evidence="present")  # type: ignore[arg-type]
    with pytest.raises(ProcedureReplacementRequestError):
        ProcedureReplacementEvidence(shadow_evidence=True)  # type: ignore[arg-type]
    with pytest.raises(ProcedureReplacementRequestError):
        ProcedureReplacementEvidence(target_integrity="intact")  # type: ignore[arg-type]


def test_decision_rejects_an_unsupported_schema_version() -> None:
    active, target = _forward_pair()

    with pytest.raises(ProcedureReplacementRequestError, match="unsupported"):
        ProcedureReplacementDecision(
            outcome=ELIGIBLE,
            kind=FORWARD,
            reason=ProcedureReplacementReason.VALIDATED_REPAIR,
            findings=(),
            procedure_id=active.procedure_id,
            active_revision=active.revision,
            target_revision=target.revision,
            schema_version=2,
        )


def test_assessment_requires_a_canonical_request_object() -> None:
    with pytest.raises(ProcedureReplacementRequestError):
        assess_procedure_replacement({"kind": "forward_replacement"})  # type: ignore[arg-type]


def test_policy_errors_share_the_canonical_value_error_root() -> None:
    assert issubclass(ProcedureReplacementRequestError, ProcedureReplacementPolicyError)
    assert issubclass(ProcedureReplacementPolicyError, ValueError)


def test_no_public_api_returns_a_naked_boolean() -> None:
    """The vocabulary is structured by construction: outcome plus findings."""
    from agentx.core import procedure_replacement as module

    public_functions = [
        name for name in module.__all__ if inspect.isfunction(getattr(module, name))
    ]

    assert public_functions == ["assess_procedure_replacement"]

    active, target = _forward_pair()
    decision = assess_procedure_replacement(
        _request(FORWARD, ProcedureReplacementReason.VALIDATED_REPAIR, active, target, (1, 2))
    )

    assert isinstance(decision, ProcedureReplacementDecision)
    assert isinstance(decision.outcome, ProcedureReplacementOutcome)
    assert isinstance(decision.findings, tuple)
    assert decision.to_dict()["outcome"] == decision.outcome.value
