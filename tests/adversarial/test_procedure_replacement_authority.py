"""Adversarial authority-boundary tests for the C4.08 replacement policy.

Eligibility is not authority. These tests attack the policy with hostile
payload text, spoofed identities, spoofed and absurd revision numbers,
bool-as-int confusion, smuggled timestamps, forged evidence prose, and
hand-built "authorized" objects, and prove that in every case the policy
returns the same structured decision, touches no authority subsystem, mutates
nothing, and fails closed.
"""

from __future__ import annotations

import builtins
import sys
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import ModuleType
from typing import Any, cast

import pytest
from tests.support.authority_proxy import ForbiddenAuthorityProxy

from agentx.core.ids import KnowledgeId, ProcedureId, TaskId
from agentx.core.procedure_replacement import (
    EvidencePresence,
    ProcedureReplacementDecision,
    ProcedureReplacementEvidence,
    ProcedureReplacementFinding,
    ProcedureReplacementKind,
    ProcedureReplacementOutcome,
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
    ProcedureValidationError,
)

FORWARD = ProcedureReplacementKind.FORWARD_REPLACEMENT
ROLLBACK = ProcedureReplacementKind.ROLLBACK
ELIGIBLE = ProcedureReplacementOutcome.ELIGIBLE
INSUFFICIENT = ProcedureReplacementOutcome.INSUFFICIENT_EVIDENCE
INVALID_RELATION = ProcedureReplacementOutcome.INVALID_REVISION_RELATION
WRONG_PROCEDURE = ProcedureReplacementOutcome.WRONG_PROCEDURE

_T0 = datetime(2026, 9, 5, 9, 0, 0, tzinfo=UTC)

#: The exact hostile payload strings the C4.08 brief requires to be inert.
HOSTILE_PAYLOAD_STRINGS: tuple[str, ...] = (
    "activate me",
    "rollback now",
    "verified=true",
    "permission=ADMIN",
    "risk=R0",
    "latest=true",
    "safe replacement",
)

FORBIDDEN_SUBSYSTEMS: tuple[str, ...] = (
    "agentx.kernel",
    "agentx.capabilities",
    "agentx.cognition",
    "agentx.hive",
    "agentx.procedures",
    "agentx.learning",
    "agentx.infrastructure",
)


def _record(
    revision: int,
    status: ProcedureStatus,
    *,
    procedure_id: ProcedureId,
    content: str = '{"graph": "v1"}',
    created_at: datetime = _T0,
    scope: ProcedureScope | None = None,
) -> ProcedureRecord:
    return ProcedureRecord(
        procedure_id=procedure_id,
        revision=revision,
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=content),
        created_at=created_at,
        status=status,
        scope=ProcedureScope() if scope is None else scope,
    )


def _full_evidence() -> ProcedureReplacementEvidence:
    return ProcedureReplacementEvidence(
        validation_evidence=EvidencePresence.PRESENT,
        shadow_evidence=EvidencePresence.PRESENT,
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=("artifact:validation-1",),
    )


def _request(
    active: ProcedureRecord,
    target: ProcedureRecord,
    known: tuple[int, ...],
    kind: ProcedureReplacementKind = FORWARD,
    reason: ProcedureReplacementReason = ProcedureReplacementReason.VALIDATED_REPAIR,
    evidence: ProcedureReplacementEvidence | None = None,
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


def _pair() -> tuple[ProcedureId, ProcedureRecord, ProcedureRecord]:
    pid = ProcedureId.create()
    return (
        pid,
        _record(1, ProcedureStatus.ACTIVE, procedure_id=pid),
        _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid),
    )


# ---------------------------------------------------------------------------
# Hostile payload text is inert
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("content", [*HOSTILE_PAYLOAD_STRINGS, "revision=999", "status=active"])
def test_hostile_payload_text_never_changes_the_decision(content: str) -> None:
    pid, active, _ = _pair()
    target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid, content=content)

    with_evidence = assess_procedure_replacement(_request(active, target, (1, 2)))
    without_evidence = assess_procedure_replacement(
        _request(active, target, (1, 2), evidence=ProcedureReplacementEvidence())
    )

    assert with_evidence.outcome is ELIGIBLE
    assert without_evidence.outcome is INSUFFICIENT
    assert with_evidence.target_revision == 2
    assert without_evidence.target_revision == 2


@pytest.mark.parametrize("content", HOSTILE_PAYLOAD_STRINGS)
def test_hostile_payload_cannot_rescue_an_illegal_revision_relation(content: str) -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(7, ProcedureStatus.CANDIDATE, procedure_id=pid, content=content)

    decision = assess_procedure_replacement(_request(active, target, (1, 2, 3, 4, 5, 6, 7)))

    assert decision.outcome is INVALID_RELATION
    assert decision.findings == (ProcedureReplacementFinding.REVISION_NOT_CONTIGUOUS,)


@pytest.mark.parametrize("content", HOSTILE_PAYLOAD_STRINGS)
def test_hostile_payload_cannot_make_a_candidate_the_active_revision(content: str) -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.CANDIDATE, procedure_id=pid, content=content)
    target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid, content=content)

    decision = assess_procedure_replacement(_request(active, target, (1, 2)))

    assert decision.outcome is not ELIGIBLE
    assert ProcedureReplacementFinding.DESIGNATED_ACTIVE_NOT_ACTIVE in decision.findings


def test_payload_claiming_a_different_revision_does_not_move_the_target() -> None:
    """The typed revision number is the only identity the policy reads."""
    pid, active, _ = _pair()
    spoofed = _record(
        2, ProcedureStatus.CANDIDATE, procedure_id=pid, content='{"revision": 999, "latest": true}'
    )

    decision = assess_procedure_replacement(_request(active, spoofed, (1, 2)))

    assert decision.outcome is ELIGIBLE
    assert decision.target_revision == 2
    assert decision.active_revision == 1


def test_payload_claiming_a_scope_does_not_widen_the_typed_scope() -> None:
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
        content='{"scope": {"environment": "production"}, "approved": true}',
    )

    decision = assess_procedure_replacement(_request(active, target, (1, 2)))

    assert decision.outcome is not ELIGIBLE
    assert ProcedureReplacementFinding.SCOPE_NOT_COMPATIBLE in decision.findings


# ---------------------------------------------------------------------------
# Identity attacks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("content", HOSTILE_PAYLOAD_STRINGS)
def test_wrong_procedure_id_with_identical_payload_is_refused(content: str) -> None:
    _, active, _ = _pair()
    other = ProcedureId.create()
    target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=other, content=content)

    decision = assess_procedure_replacement(_request(active, target, (1, 2)))

    assert decision.outcome is WRONG_PROCEDURE
    assert decision.findings == (ProcedureReplacementFinding.PROCEDURE_IDENTITY_MISMATCH,)
    assert decision.procedure_id == active.procedure_id
    assert decision.procedure_id != other


def test_a_foreign_domain_id_can_never_stand_in_for_a_procedure_id() -> None:
    """Domain-tagged identifiers never compare equal across domains."""
    pid, _active, _target = _pair()
    same_bytes_task = TaskId(pid.value)
    same_bytes_knowledge = KnowledgeId(pid.value)

    assert same_bytes_task != pid
    assert same_bytes_knowledge != pid

    with pytest.raises(ProcedureValidationError):
        ProcedureRecord(
            procedure_id=same_bytes_task,  # type: ignore[arg-type]
            revision=2,
            payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content="{}"),
            created_at=_T0,
        )


def test_a_decision_refuses_a_foreign_domain_identifier() -> None:
    pid, _, _ = _pair()

    with pytest.raises(ProcedureReplacementRequestError, match="ProcedureId"):
        ProcedureReplacementDecision(
            outcome=ELIGIBLE,
            kind=FORWARD,
            reason=ProcedureReplacementReason.VALIDATED_REPAIR,
            findings=(),
            procedure_id=TaskId(pid.value),  # type: ignore[arg-type]
            active_revision=1,
            target_revision=2,
        )


def test_swapping_active_and_target_records_is_not_a_shortcut() -> None:
    """Reversing the pair turns a legal forward step into an illegal one."""
    _pid, active, target = _pair()

    forward = assess_procedure_replacement(_request(active, target, (1, 2)))
    reversed_ = assess_procedure_replacement(
        _request(
            target,
            active,
            (1, 2),
            kind=ROLLBACK,
            reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
        )
    )

    assert forward.outcome is ELIGIBLE
    assert reversed_.outcome is not ELIGIBLE


# ---------------------------------------------------------------------------
# Revision spoofing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "revision",
    [2**31, 2**62, 2**63, sys.maxsize, 10**100],
)
def test_absurd_forward_revision_numbers_are_refused(revision: int) -> None:
    pid, active, _ = _pair()
    target = _record(revision, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(_request(active, target, (1, revision)))

    assert decision.outcome is INVALID_RELATION
    assert decision.findings == (ProcedureReplacementFinding.REVISION_NOT_CONTIGUOUS,)


@pytest.mark.parametrize("revision", [2**31, 2**62, sys.maxsize, 10**100])
def test_absurd_rollback_targets_are_reported_verbatim_and_never_renumbered(revision: int) -> None:
    pid = ProcedureId.create()
    active = _record(revision, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(revision - 1, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(
            active,
            target,
            (revision - 1, revision),
            kind=ROLLBACK,
            reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
        )
    )

    assert decision.outcome is ELIGIBLE
    # The enormous active revision is reported verbatim; nothing is renumbered.
    assert decision.active_revision == revision
    assert decision.target_revision == revision - 1


def test_rollback_to_a_fabricated_historical_revision_is_refused() -> None:
    pid = ProcedureId.create()
    active = _record(5, ProcedureStatus.ACTIVE, procedure_id=pid)
    fabricated = _record(3, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(
            active,
            fabricated,
            (1, 5),
            kind=ROLLBACK,
            reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
        )
    )

    assert decision.outcome is INVALID_RELATION
    assert decision.findings == (ProcedureReplacementFinding.TARGET_NOT_IN_KNOWN_HISTORY,)


def test_a_new_revision_cannot_be_smuggled_in_ahead_of_the_stored_maximum() -> None:
    pid = ProcedureId.create()
    active = _record(2, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(3, ProcedureStatus.CANDIDATE, procedure_id=pid)

    decision = assess_procedure_replacement(_request(active, target, (1, 2, 9)))

    assert decision.outcome is INVALID_RELATION
    assert decision.findings == (
        ProcedureReplacementFinding.NEW_REVISION_BREAKS_APPEND_ONLY_HISTORY,
    )


@pytest.mark.parametrize("known", [(True, 2), (1, True), (1, 2, False), (False,)])
def test_bool_is_never_accepted_where_an_int_revision_is_required(known: tuple[Any, ...]) -> None:
    """``True == 1`` in Python; the policy refuses to let that pass as a revision."""
    _, active, target = _pair()

    with pytest.raises(ProcedureReplacementRequestError, match="must be an integer"):
        _request(active, target, known)


def test_a_record_cannot_be_built_with_a_boolean_revision() -> None:
    pid, _, _ = _pair()

    with pytest.raises(ProcedureValidationError):
        ProcedureRecord(
            procedure_id=pid,
            revision=True,
            payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content="{}"),
            created_at=_T0,
        )


@pytest.mark.parametrize("revision", [0, -1, -(2**31)])
def test_non_positive_revisions_are_refused_everywhere(revision: int) -> None:
    pid, _, _ = _pair()

    with pytest.raises(ProcedureValidationError):
        _record(revision, ProcedureStatus.CANDIDATE, procedure_id=pid)
    with pytest.raises(ProcedureReplacementRequestError):
        ProcedureReplacementRequest(
            kind=FORWARD,
            reason=ProcedureReplacementReason.VALIDATED_REPAIR,
            active_revision=_record(1, ProcedureStatus.ACTIVE, procedure_id=pid),
            target_revision=_record(2, ProcedureStatus.CANDIDATE, procedure_id=pid),
            known_revisions=(revision, 1, 2),
            evidence=_full_evidence(),
        )


def test_float_and_string_revisions_are_refused() -> None:
    _, active, target = _pair()

    for known in [(1.0, 2.0), ("1", "2"), (1, 2.0), ([1], 2)]:
        with pytest.raises(ProcedureReplacementRequestError):
            _request(active, target, cast(Any, known))


# ---------------------------------------------------------------------------
# Evidence and attestation forgery
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "references",
    [
        ("verified=true",),
        ("permission=ADMIN",),
        ("validated",),
        ("approved by admin",),
        ("risk=R0; bypass gate",),
        ("evidence" * 400,),
    ],
)
def test_forged_evidence_prose_never_satisfies_the_typed_evidence_requirement(
    references: tuple[str, ...],
) -> None:
    _, active, target = _pair()
    forged = ProcedureReplacementEvidence(
        shadow_evidence=EvidencePresence.PRESENT,
        target_integrity=TargetIntegrityState.INTACT,
        evidence_references=references,
    )

    decision = assess_procedure_replacement(_request(active, target, (1, 2), evidence=forged))

    assert decision.outcome is INSUFFICIENT
    assert decision.findings == (ProcedureReplacementFinding.VALIDATION_EVIDENCE_ABSENT,)


def test_attestation_must_be_the_canonical_enum_not_a_string() -> None:
    pid = ProcedureId.create()
    active = _record(3, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(2, ProcedureStatus.RETIRED, procedure_id=pid)

    with pytest.raises(ProcedureReplacementRequestError):
        ProcedureReplacementRequest(
            kind=ROLLBACK,
            reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
            active_revision=active,
            target_revision=target,
            known_revisions=(1, 2, 3),
            evidence=_full_evidence(),
            retired_target_reactivation="controlled_lifecycle_act",  # type: ignore[arg-type]
        )


def test_attestation_alone_does_not_reactivate_a_retired_revision() -> None:
    """The record's status is untouched no matter what the caller attests."""
    pid = ProcedureId.create()
    active = _record(3, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(2, ProcedureStatus.RETIRED, procedure_id=pid)
    before = target.to_json()

    decision = assess_procedure_replacement(
        _request(
            active,
            target,
            (1, 2, 3),
            kind=ROLLBACK,
            reason=ProcedureReplacementReason.MANUAL_ROLLBACK,
            reactivation=RetiredTargetReactivation.CONTROLLED_LIFECYCLE_ACT,
        )
    )

    assert decision.outcome is ELIGIBLE
    assert target.status is ProcedureStatus.RETIRED
    assert target.to_json() == before


def test_a_forward_replacement_cannot_revive_a_retired_revision_by_attestation() -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid)
    target = _record(2, ProcedureStatus.RETIRED, procedure_id=pid)

    decision = assess_procedure_replacement(
        _request(
            active,
            target,
            (1, 2),
            reactivation=RetiredTargetReactivation.CONTROLLED_LIFECYCLE_ACT,
        )
    )

    assert decision.outcome is not ELIGIBLE
    assert ProcedureReplacementFinding.FORWARD_TARGET_IS_RETIRED in decision.findings


def test_evidence_enum_fields_reject_truthy_and_string_substitutes() -> None:
    for kwargs in (
        {"validation_evidence": True},
        {"shadow_evidence": "present"},
        {"target_integrity": "intact"},
        {"validation_evidence": 1},
    ):
        with pytest.raises(ProcedureReplacementRequestError):
            ProcedureReplacementEvidence(**cast(Any, kwargs))


# ---------------------------------------------------------------------------
# Smuggled timestamps and "latest wins"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target_created_at",
    [
        datetime(1970, 1, 1, tzinfo=UTC),
        datetime(2026, 9, 5, 9, 0, 1, tzinfo=UTC),
        datetime(2999, 12, 31, 23, 59, 59, tzinfo=UTC),
    ],
)
def test_timestamps_never_influence_the_decision(target_created_at: datetime) -> None:
    pid, active, _ = _pair()
    target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid, created_at=target_created_at)

    with_evidence = assess_procedure_replacement(_request(active, target, (1, 2)))
    without_evidence = assess_procedure_replacement(
        _request(active, target, (1, 2), evidence=ProcedureReplacementEvidence())
    )

    assert with_evidence.outcome is ELIGIBLE
    assert without_evidence.outcome is INSUFFICIENT


def test_a_far_future_timestamp_cannot_rescue_an_illegal_request() -> None:
    pid = ProcedureId.create()
    far_future = datetime(2999, 1, 1, tzinfo=UTC)
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid, created_at=far_future)
    target = _record(4, ProcedureStatus.CANDIDATE, procedure_id=pid, created_at=far_future)

    decision = assess_procedure_replacement(_request(active, target, (1, 2, 3, 4)))

    assert decision.outcome is INVALID_RELATION


# ---------------------------------------------------------------------------
# Eligibility is not execution permission
# ---------------------------------------------------------------------------


def test_assessment_never_touches_an_authority_or_runtime_subsystem(
    monkeypatch: pytest.MonkeyPatch,
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

    _pid, active, target = _pair()
    for decision in (
        assess_procedure_replacement(_request(active, target, (1, 2))),
        assess_procedure_replacement(
            _request(active, target, (1, 2), evidence=ProcedureReplacementEvidence())
        ),
        assess_procedure_replacement(_request(active, active, (1,))),
    ):
        decision.to_json()
        decision.to_dict()

    assert touched == []


def test_an_eligible_decision_carries_no_authority_surface() -> None:
    _pid, active, target = _pair()
    decision = assess_procedure_replacement(_request(active, target, (1, 2)))

    assert decision.outcome is ELIGIBLE
    forbidden = {
        "permission",
        "authority",
        "authorize",
        "grant",
        "gate",
        "risk",
        "budget",
        "emergency_stop",
        "execute",
        "run",
        "apply",
        "commit",
        "activate",
        "retire",
        "promote",
        "verify",
        "approve",
    }
    members = {name.lower() for name in dir(decision) if not name.startswith("_")}
    assert members.isdisjoint(forbidden)

    serialized = decision.to_json().lower()
    for token in ("permission", "authority", "risk", "budget", "emergency"):
        assert token not in serialized


def test_an_eligible_decision_mutates_neither_record_nor_history() -> None:
    _pid, active, target = _pair()
    active_before, target_before = active.to_json(), target.to_json()
    known = (1, 2)

    decision = assess_procedure_replacement(_request(active, target, known))

    assert decision.outcome is ELIGIBLE
    assert active.to_json() == active_before
    assert target.to_json() == target_before
    assert active.status is ProcedureStatus.ACTIVE
    assert target.status is ProcedureStatus.CANDIDATE
    assert known == (1, 2)


def test_an_eligible_decision_object_exposes_no_execution_surface() -> None:
    """An eligible decision is a record, not a handle: it reaches nothing."""
    pid, _, _ = _pair()
    forged = ProcedureReplacementRequest(
        kind=FORWARD,
        reason=ProcedureReplacementReason.VALIDATED_REPAIR,
        active_revision=_record(1, ProcedureStatus.ACTIVE, procedure_id=pid),
        target_revision=_record(2, ProcedureStatus.CANDIDATE, procedure_id=pid),
        known_revisions=(1, 2),
        evidence=_full_evidence(),
    )
    decision = assess_procedure_replacement(forged)

    assert isinstance(decision.to_dict(), dict)
    assert not hasattr(decision, "execute")
    assert not hasattr(decision, "apply")
    assert not hasattr(decision, "grant")


def test_the_outcome_cannot_be_edited_after_the_fact() -> None:
    _, active, target = _pair()
    decision = assess_procedure_replacement(
        _request(active, target, (1, 2), evidence=ProcedureReplacementEvidence())
    )

    assert decision.outcome is INSUFFICIENT
    with pytest.raises(FrozenInstanceError):
        decision.outcome = ELIGIBLE  # type: ignore[misc]
    assert decision.outcome is INSUFFICIENT


def test_reassessing_with_better_evidence_yields_a_new_decision_not_a_mutated_one() -> None:
    _, active, target = _pair()
    denied = assess_procedure_replacement(
        _request(active, target, (1, 2), evidence=ProcedureReplacementEvidence())
    )

    granted = assess_procedure_replacement(_request(active, target, (1, 2)))

    assert denied.outcome is INSUFFICIENT
    assert granted.outcome is ELIGIBLE
    assert denied is not granted
    assert denied.outcome is INSUFFICIENT


# ---------------------------------------------------------------------------
# Fail closed: no hidden I/O, clock, or randomness
# ---------------------------------------------------------------------------


def test_assessment_never_opens_a_file_or_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the replacement policy must never open a file or socket")

    monkeypatch.setattr(builtins, "open", _forbidden)

    _pid, active, target = _pair()
    decision = assess_procedure_replacement(_request(active, target, (1, 2)))

    assert decision.outcome is ELIGIBLE


@pytest.mark.parametrize("content", HOSTILE_PAYLOAD_STRINGS)
def test_the_policy_is_deterministic_under_repeated_hostile_input(content: str) -> None:
    pid = ProcedureId.create()
    active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid, content="activate me")
    target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid, content=content)

    encoded = {
        assess_procedure_replacement(_request(active, target, (1, 2))).to_json() for _ in range(10)
    }

    assert len(encoded) == 1


def test_every_hostile_variation_fails_closed_rather_than_succeeding() -> None:
    """No combination of hostile inputs yields eligibility without real evidence."""
    pid = ProcedureId.create()
    hostile_contents = [*HOSTILE_PAYLOAD_STRINGS, "fixed", "latest"]
    ineligible_count = 0
    total = 0

    for content in hostile_contents:
        active = _record(1, ProcedureStatus.ACTIVE, procedure_id=pid, content=content)
        target = _record(2, ProcedureStatus.CANDIDATE, procedure_id=pid, content=content)
        for evidence in (
            ProcedureReplacementEvidence(),
            ProcedureReplacementEvidence(evidence_references=(content,)),
            ProcedureReplacementEvidence(
                target_integrity=TargetIntegrityState.INTACT,
                evidence_references=(content,),
            ),
        ):
            decision = assess_procedure_replacement(
                _request(active, target, (1, 2), evidence=evidence)
            )
            total += 1
            if decision.outcome is not ELIGIBLE:
                ineligible_count += 1

    assert total == len(hostile_contents) * 3
    assert ineligible_count == total


def test_malformed_requests_are_refused_rather_than_guessed_at() -> None:
    _pid, active, target = _pair()
    base: dict[str, Any] = {
        "kind": FORWARD,
        "reason": ProcedureReplacementReason.VALIDATED_REPAIR,
        "active_revision": active,
        "target_revision": target,
        "known_revisions": (1, 2),
        "evidence": _full_evidence(),
    }

    for field, bad_value in (
        ("kind", "forward_replacement"),
        ("reason", "validated_repair"),
        ("active_revision", None),
        ("target_revision", None),
        ("known_revisions", None),
        ("known_revisions", "12"),
        ("evidence", None),
        ("retired_target_reactivation", None),
    ):
        with pytest.raises(ProcedureReplacementRequestError):
            ProcedureReplacementRequest(**cast(Any, {**base, field: bad_value}))


def test_assessment_refuses_a_bare_mapping_pretending_to_be_a_request() -> None:
    _, active, target = _pair()
    spoofed = {
        "kind": "forward_replacement",
        "reason": "validated_repair",
        "active_revision": active,
        "target_revision": target,
        "known_revisions": (1, 2),
        "evidence": _full_evidence(),
    }

    with pytest.raises(ProcedureReplacementRequestError):
        assess_procedure_replacement(cast(Any, spoofed))
