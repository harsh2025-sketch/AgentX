"""Unit tests for the C4.09 / M5.03 bounded repair-attempt policy."""

from __future__ import annotations

from dataclasses import MISSING, FrozenInstanceError, fields
from uuid import uuid4

import pytest

from agentx.core.ids import ProcedureId
from agentx.core.repair_budget import (
    CANONICAL_REPAIR_ATTEMPT_OUTCOMES,
    ProcedureRepairTarget,
    RepairAttemptEvidence,
    RepairAttemptOutcome,
    RepairBudgetAssessment,
    RepairBudgetDecision,
    RepairBudgetLimits,
    RepairBudgetScope,
    RepairBudgetValidationError,
    RepairProgressMarker,
    RepairProposalFingerprint,
    RepairTarget,
    RepairTargetFingerprint,
    assess_repair_attempt,
)

_PROCEDURE = ProcedureId(uuid4())


def _procedure_target(
    *,
    procedure_id: ProcedureId = _PROCEDURE,
    revision: int = 2,
) -> RepairTarget:
    return RepairTarget(
        procedure=ProcedureRepairTarget(
            procedure_id=procedure_id,
            revision=revision,
        )
    )


def _opaque_target(name: str = "target:other") -> RepairTarget:
    return RepairTarget(opaque=RepairTargetFingerprint(name))


def _limits(
    *,
    total: int = 5,
    per_proposal: int = 2,
    no_progress: int = 3,
    scope: RepairBudgetScope = RepairBudgetScope.TARGET,
) -> RepairBudgetLimits:
    return RepairBudgetLimits(
        max_total_attempts=total,
        max_attempts_per_proposal=per_proposal,
        max_consecutive_failures_without_progress=no_progress,
        total_attempt_scope=scope,
    )


def _evidence(
    proposal: str,
    outcome: RepairAttemptOutcome = RepairAttemptOutcome.FAILED_VALIDATION,
    *,
    target: RepairTarget | None = None,
    progress: str | None = None,
) -> RepairAttemptEvidence:
    return RepairAttemptEvidence(
        target=target if target is not None else _procedure_target(),
        proposal=RepairProposalFingerprint(proposal),
        outcome=outcome,
        progress=None if progress is None else RepairProgressMarker(progress),
    )


# ---------------------------------------------------------------------------
# Empty and small histories.
# ---------------------------------------------------------------------------


def test_empty_history_allows_consideration_with_zero_counters() -> None:
    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:1"),
        history=(),
        limits=_limits(),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.history_attempt_count == 0
    assert result.target_attempt_count == 0
    assert result.proposal_attempt_count == 0
    assert result.consecutive_no_progress_count == 0
    assert result.distinct_progress_markers == 0
    assert result.reason.startswith("ALLOW_CONSIDERATION:")


def test_one_failed_attempt_is_within_every_limit() -> None:
    history = (_evidence("patch:1"),)

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:1"),
        history=history,
        limits=_limits(total=3, per_proposal=2, no_progress=3),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.target_attempt_count == 1
    assert result.proposal_attempt_count == 1
    assert result.consecutive_no_progress_count == 1


# ---------------------------------------------------------------------------
# Total-attempt ceiling.
# ---------------------------------------------------------------------------


def test_within_total_limit_allows_consideration() -> None:
    history = (
        _evidence("patch:1"),
        _evidence("patch:2"),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:3"),
        history=history,
        limits=_limits(total=3),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.target_attempt_count == 2


def test_exact_total_boundary_stops() -> None:
    history = (
        _evidence("patch:1"),
        _evidence("patch:2"),
        _evidence("patch:3"),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:4"),
        history=history,
        limits=_limits(total=3, per_proposal=9, no_progress=9),
    )

    assert result.decision is RepairBudgetDecision.STOP_TOTAL_LIMIT
    assert result.reason.startswith("STOP_TOTAL_LIMIT:")
    assert result.target_attempt_count == 3
    assert result.history_attempt_count == 3


def test_over_total_boundary_stays_stopped() -> None:
    history = tuple(_evidence(f"patch:{index}") for index in range(7))

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:next"),
        history=history,
        limits=_limits(total=3, per_proposal=9, no_progress=9),
    )

    assert result.decision is RepairBudgetDecision.STOP_TOTAL_LIMIT
    assert result.target_attempt_count == 7


def test_validated_outcome_never_resets_total_ceiling() -> None:
    history = tuple(
        _evidence(f"patch:{index}", RepairAttemptOutcome.VALIDATED) for index in range(3)
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:next"),
        history=history,
        limits=_limits(total=3, per_proposal=9, no_progress=9),
    )

    assert result.decision is RepairBudgetDecision.STOP_TOTAL_LIMIT


# ---------------------------------------------------------------------------
# Identical-proposal ceiling.
# ---------------------------------------------------------------------------


def test_same_proposal_repeated_to_boundary_stops() -> None:
    history = (
        _evidence("patch:same"),
        _evidence("patch:same"),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:same"),
        history=history,
        limits=_limits(total=9, per_proposal=2, no_progress=9),
    )

    assert result.decision is RepairBudgetDecision.STOP_REPEAT_LIMIT
    assert result.reason.startswith("STOP_REPEAT_LIMIT:")
    assert result.proposal_attempt_count == 2


def test_same_proposal_just_below_boundary_allows() -> None:
    history = (_evidence("patch:same"),)

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:same"),
        history=history,
        limits=_limits(total=9, per_proposal=2, no_progress=9),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.proposal_attempt_count == 1


def test_different_proposal_fingerprint_is_not_the_same_repetition() -> None:
    history = (
        _evidence("patch:same"),
        _evidence("patch:same"),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:different"),
        history=history,
        limits=_limits(total=9, per_proposal=2, no_progress=9),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.proposal_attempt_count == 0


def test_progress_marker_never_revives_exhausted_identical_proposal() -> None:
    history = (
        _evidence("patch:same", progress="progress:milestone-1"),
        _evidence("patch:same", progress="progress:milestone-2"),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:same"),
        history=history,
        limits=_limits(total=9, per_proposal=2, no_progress=9),
    )

    assert result.decision is RepairBudgetDecision.STOP_REPEAT_LIMIT
    assert result.proposal_attempt_count == 2
    assert result.distinct_progress_markers == 2


# ---------------------------------------------------------------------------
# No-progress ceiling.
# ---------------------------------------------------------------------------


def test_consecutive_no_progress_failures_reach_boundary_and_stop() -> None:
    history = (
        _evidence("patch:1", RepairAttemptOutcome.FAILED_SHADOW),
        _evidence("patch:2", RepairAttemptOutcome.FAILED_APPLICATION),
        _evidence("patch:3", RepairAttemptOutcome.FAILED_VALIDATION),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:4"),
        history=history,
        limits=_limits(total=9, per_proposal=9, no_progress=3),
    )

    assert result.decision is RepairBudgetDecision.STOP_NO_PROGRESS
    assert result.reason.startswith("STOP_NO_PROGRESS:")
    assert result.consecutive_no_progress_count == 3


def test_new_explicit_progress_marker_resets_no_progress_run() -> None:
    history = (
        _evidence("patch:1"),
        _evidence("patch:2"),
        _evidence("patch:3", progress="progress:milestone-1"),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:4"),
        history=history,
        limits=_limits(total=9, per_proposal=9, no_progress=3),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.consecutive_no_progress_count == 0
    assert result.distinct_progress_markers == 1


def test_second_new_progress_marker_advances_run_reset_again() -> None:
    history = (
        _evidence("patch:1", progress="progress:milestone-1"),
        _evidence("patch:2"),
        _evidence("patch:3", progress="progress:milestone-2"),
        _evidence("patch:4"),
        _evidence("patch:5"),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:6"),
        history=history,
        limits=_limits(total=9, per_proposal=9, no_progress=3),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.consecutive_no_progress_count == 2
    assert result.distinct_progress_markers == 2


def test_same_progress_marker_replay_does_not_reset_no_progress_run() -> None:
    # Only the FIRST occurrence of a marker demonstrates progress; the two
    # replays are no-progress failures, so the trailing run is 2. Had replay
    # reset the counter, the run would be 0 and the decision would be ALLOW.
    history = (
        _evidence("patch:1", progress="progress:milestone-1"),
        _evidence("patch:2", progress="progress:milestone-1"),
        _evidence("patch:3", progress="progress:milestone-1"),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:4"),
        history=history,
        limits=_limits(total=9, per_proposal=9, no_progress=2),
    )

    assert result.decision is RepairBudgetDecision.STOP_NO_PROGRESS
    assert result.consecutive_no_progress_count == 2
    assert result.distinct_progress_markers == 1


def test_reusing_older_marker_after_newer_one_does_not_reset() -> None:
    # p1 is first seen at index 0 and p2 at index 1; replaying p1 at the end
    # demonstrates nothing, so the trailing run is exactly the final replay.
    history = (
        _evidence("patch:1", progress="progress:milestone-1"),
        _evidence("patch:2", progress="progress:milestone-2"),
        _evidence("patch:3", progress="progress:milestone-1"),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:4"),
        history=history,
        limits=_limits(total=9, per_proposal=9, no_progress=1),
    )

    assert result.decision is RepairBudgetDecision.STOP_NO_PROGRESS
    assert result.consecutive_no_progress_count == 1
    assert result.distinct_progress_markers == 2


def test_validated_outcome_breaks_no_progress_run() -> None:
    history = (
        _evidence("patch:1"),
        _evidence("patch:2"),
        _evidence("patch:3", RepairAttemptOutcome.VALIDATED),
        _evidence("patch:4"),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:5"),
        history=history,
        limits=_limits(total=9, per_proposal=9, no_progress=3),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.consecutive_no_progress_count == 1


def test_aborted_and_unknown_outcomes_count_as_no_progress() -> None:
    history = (
        _evidence("patch:1", RepairAttemptOutcome.ABORTED),
        _evidence("patch:2", RepairAttemptOutcome.UNKNOWN),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:3"),
        history=history,
        limits=_limits(total=9, per_proposal=9, no_progress=2),
    )

    assert result.decision is RepairBudgetDecision.STOP_NO_PROGRESS
    assert result.consecutive_no_progress_count == 2


def test_failed_outcome_with_new_marker_is_not_no_progress() -> None:
    history = (
        _evidence("patch:1"),
        _evidence("patch:2", RepairAttemptOutcome.FAILED_SHADOW, progress="progress:stable-1"),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:3"),
        history=history,
        limits=_limits(total=9, per_proposal=9, no_progress=1),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.consecutive_no_progress_count == 0


# ---------------------------------------------------------------------------
# Trigger priority.
# ---------------------------------------------------------------------------


def test_total_limit_outranks_repeat_and_no_progress_limits() -> None:
    history = (
        _evidence("patch:same"),
        _evidence("patch:same"),
        _evidence("patch:same"),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:same"),
        history=history,
        limits=_limits(total=3, per_proposal=2, no_progress=2),
    )

    assert result.decision is RepairBudgetDecision.STOP_TOTAL_LIMIT


def test_repeat_limit_outranks_no_progress_limit() -> None:
    history = (
        _evidence("patch:same"),
        _evidence("patch:same"),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:same"),
        history=history,
        limits=_limits(total=9, per_proposal=2, no_progress=2),
    )

    assert result.decision is RepairBudgetDecision.STOP_REPEAT_LIMIT


# ---------------------------------------------------------------------------
# Target scoping.
# ---------------------------------------------------------------------------


def test_different_target_history_is_isolated_from_repeat_and_no_progress() -> None:
    other = _opaque_target("target:unrelated")
    history = (
        _evidence("patch:same", target=other),
        _evidence("patch:same", target=other),
        _evidence("patch:same", target=other),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:same"),
        history=history,
        limits=_limits(total=2, per_proposal=1, no_progress=1),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.history_attempt_count == 3
    assert result.target_attempt_count == 0
    assert result.proposal_attempt_count == 0
    assert result.consecutive_no_progress_count == 0


def test_same_procedure_id_different_revision_is_a_different_target() -> None:
    revision_two = _evidence("patch:same", target=_procedure_target(revision=2))
    revision_two_again = _evidence("patch:same", target=_procedure_target(revision=2))
    revision_three = _evidence("patch:same", target=_procedure_target(revision=3))

    for history in (
        (revision_two, revision_two_again, revision_three),
        (revision_three, revision_two, revision_two_again),
    ):
        result = assess_repair_attempt(
            target=_procedure_target(revision=3),
            proposal=RepairProposalFingerprint("patch:same"),
            history=history,
            limits=_limits(total=2, per_proposal=9, no_progress=9),
        )
        assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
        assert result.target_attempt_count == 1
        assert result.proposal_attempt_count == 1


def test_same_procedure_and_revision_is_always_the_same_target() -> None:
    # Same canonical ProcedureId and same exact revision: one shared budget,
    # regardless of how many times evidence records it.
    history = (
        _evidence("patch:1", target=_procedure_target(revision=4)),
        _evidence("patch:2", target=_procedure_target(revision=4)),
    )

    result = assess_repair_attempt(
        target=_procedure_target(revision=4),
        proposal=RepairProposalFingerprint("patch:3"),
        history=history,
        limits=_limits(total=2, per_proposal=9, no_progress=9),
    )

    assert result.decision is RepairBudgetDecision.STOP_TOTAL_LIMIT
    assert result.target_attempt_count == 2


def test_opaque_fingerprint_may_scope_finer_than_a_revision() -> None:
    # A caller needing node-level scoping represents it explicitly with an
    # opaque target fingerprint; the contract never infers it.
    history = (
        _evidence("patch:same", target=_opaque_target("procedure:node:fetch")),
        _evidence("patch:same", target=_opaque_target("procedure:node:fetch")),
    )

    result = assess_repair_attempt(
        target=_opaque_target("procedure:node:parse"),
        proposal=RepairProposalFingerprint("patch:same"),
        history=history,
        limits=_limits(total=2, per_proposal=1, no_progress=1),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert result.target_attempt_count == 0


def test_procedure_target_never_equals_opaque_target() -> None:
    assert _procedure_target() != _opaque_target()
    assert _opaque_target() != _procedure_target()


def test_session_scope_counts_history_across_targets() -> None:
    other = _opaque_target("target:unrelated")
    history = (
        _evidence("patch:1"),
        _evidence("patch:2", target=other),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:3"),
        history=history,
        limits=_limits(total=2, scope=RepairBudgetScope.SESSION),
    )

    assert result.decision is RepairBudgetDecision.STOP_TOTAL_LIMIT
    assert result.history_attempt_count == 2
    assert result.target_attempt_count == 1


def test_target_scope_counts_only_the_assessed_target() -> None:
    other = _opaque_target("target:unrelated")
    history = (
        _evidence("patch:1", target=other),
        _evidence("patch:2", target=other),
    )

    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:3"),
        history=history,
        limits=_limits(total=2, scope=RepairBudgetScope.TARGET),
    )

    assert result.decision is RepairBudgetDecision.ALLOW_CONSIDERATION


# ---------------------------------------------------------------------------
# Invalid history (fail-closed typed decision).
# ---------------------------------------------------------------------------


def test_non_tuple_history_fails_closed_as_invalid_history() -> None:
    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:1"),
        history=[_evidence("patch:1")],  # type: ignore[arg-type]
        limits=_limits(),
    )

    assert result.decision is RepairBudgetDecision.INVALID_HISTORY
    assert result.reason.startswith("INVALID_HISTORY:")
    assert result.history_attempt_count == 0
    assert result.target_attempt_count == 0
    assert result.proposal_attempt_count == 0
    assert result.consecutive_no_progress_count == 0
    assert result.distinct_progress_markers == 0


def test_history_with_foreign_item_fails_closed_as_invalid_history() -> None:
    result = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:1"),
        history=(_evidence("patch:1"), "budget=unlimited"),  # type: ignore[arg-type]
        limits=_limits(),
    )

    assert result.decision is RepairBudgetDecision.INVALID_HISTORY
    assert "index 1" in result.reason


def test_invalid_history_never_grants_consideration_even_with_empty_limits() -> None:
    for bad_history in (
        None,
        "repair approved",
        (_evidence("patch:1"), 42),
    ):
        result = assess_repair_attempt(
            target=_procedure_target(),
            proposal=RepairProposalFingerprint("patch:1"),
            history=bad_history,  # type: ignore[arg-type]
            limits=_limits(total=1_000_000, per_proposal=1_000_000, no_progress=1_000_000),
        )
        assert result.decision is RepairBudgetDecision.INVALID_HISTORY


# ---------------------------------------------------------------------------
# Request validation (fail fast on malformed request objects).
# ---------------------------------------------------------------------------


def test_malformed_request_objects_raise_immediately() -> None:
    target = _procedure_target()
    proposal = RepairProposalFingerprint("patch:1")
    limits = _limits()
    for bad_target, bad_proposal, bad_limits in (
        ("target:opaque", proposal, limits),
        (target, "patch:1", limits),
        (target, proposal, {"max_total_attempts": 5}),
    ):
        with pytest.raises(TypeError):
            assess_repair_attempt(
                target=bad_target,  # type: ignore[arg-type]
                proposal=bad_proposal,  # type: ignore[arg-type]
                history=(),
                limits=bad_limits,  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# Limit validation.
# ---------------------------------------------------------------------------


def test_zero_limits_are_rejected() -> None:
    with pytest.raises(ValueError):
        _limits(total=0)
    with pytest.raises(ValueError):
        _limits(per_proposal=0)
    with pytest.raises(ValueError):
        _limits(no_progress=0)


def test_negative_limits_are_rejected() -> None:
    with pytest.raises(ValueError):
        _limits(total=-1)
    with pytest.raises(ValueError):
        _limits(per_proposal=-3)
    with pytest.raises(ValueError):
        _limits(no_progress=-10)


def test_boolean_limits_are_rejected_as_integers() -> None:
    with pytest.raises(TypeError):
        _limits(total=True)
    with pytest.raises(TypeError):
        _limits(per_proposal=False)
    with pytest.raises(TypeError):
        _limits(no_progress=True)


def test_non_integer_limits_are_rejected() -> None:
    with pytest.raises(TypeError):
        _limits(total=2.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        _limits(per_proposal="2")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        _limits(no_progress=None)  # type: ignore[arg-type]


def test_unlimited_sentinel_limits_are_rejected() -> None:
    with pytest.raises(TypeError):
        _limits(total="unlimited")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        _limits(per_proposal=-1)  # -1 is an int and still no unlimited mode
    with pytest.raises(ValueError):
        _limits(no_progress=-1)


def test_absurdly_large_limits_are_rejected() -> None:
    with pytest.raises(OverflowError):
        _limits(total=1 << 64)
    with pytest.raises(OverflowError):
        _limits(per_proposal=(1 << 63))
    with pytest.raises(OverflowError):
        _limits(no_progress=(1 << 64) + 1)


def test_missing_scope_is_rejected() -> None:
    with pytest.raises(TypeError):
        RepairBudgetLimits(  # type: ignore[call-arg]
            max_total_attempts=5,
            max_attempts_per_proposal=2,
            max_consecutive_failures_without_progress=3,
        )


def test_invalid_scope_member_is_rejected() -> None:
    with pytest.raises(TypeError):
        _limits(scope="session")  # type: ignore[arg-type]


def test_limits_allow_no_unlimited_mode_by_construction() -> None:
    # Every limit field is required with no default and no default factory:
    # the record cannot even be constructed with an absent limit, and every
    # present value must be a finite positive int.
    for field in fields(RepairBudgetLimits):
        assert field.default is MISSING
        assert field.default_factory is MISSING


# ---------------------------------------------------------------------------
# Evidence and target validation.
# ---------------------------------------------------------------------------


def test_malformed_evidence_is_rejected_at_construction() -> None:
    target = _procedure_target()
    with pytest.raises(TypeError):
        RepairAttemptEvidence(
            target="target:opaque",  # type: ignore[arg-type]
            proposal=RepairProposalFingerprint("patch:1"),
            outcome=RepairAttemptOutcome.FAILED_VALIDATION,
        )
    with pytest.raises(TypeError):
        RepairAttemptEvidence(
            target=target,
            proposal="patch:1",  # type: ignore[arg-type]
            outcome=RepairAttemptOutcome.FAILED_VALIDATION,
        )
    with pytest.raises(TypeError):
        RepairAttemptEvidence(
            target=target,
            proposal=RepairProposalFingerprint("patch:1"),
            outcome="failed_validation",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        RepairAttemptEvidence(
            target=target,
            proposal=RepairProposalFingerprint("patch:1"),
            outcome=RepairAttemptOutcome.FAILED_VALIDATION,
            progress="progress:1",  # type: ignore[arg-type]
        )


def test_unrecognized_outcome_string_is_rejected_not_coerced() -> None:
    with pytest.raises(ValueError):
        RepairAttemptOutcome("repair approved")
    with pytest.raises(ValueError):
        RepairAttemptOutcome("VALIDATED")


def test_outcome_vocabulary_is_closed_and_matches_lifecycle() -> None:
    assert CANONICAL_REPAIR_ATTEMPT_OUTCOMES == (
        RepairAttemptOutcome.VALIDATED,
        RepairAttemptOutcome.FAILED_VALIDATION,
        RepairAttemptOutcome.FAILED_SHADOW,
        RepairAttemptOutcome.FAILED_APPLICATION,
        RepairAttemptOutcome.ABORTED,
        RepairAttemptOutcome.UNKNOWN,
    )


def test_target_requires_exactly_one_identification() -> None:
    with pytest.raises(RepairBudgetValidationError):
        RepairTarget()
    with pytest.raises(RepairBudgetValidationError):
        RepairTarget(
            procedure=ProcedureRepairTarget(procedure_id=_PROCEDURE, revision=1),
            opaque=RepairTargetFingerprint("target:also-opaque"),
        )


def test_procedure_target_rejects_bad_revisions_and_identities() -> None:
    with pytest.raises(TypeError):
        ProcedureRepairTarget(procedure_id=_PROCEDURE, revision=True)
    with pytest.raises(ValueError):
        ProcedureRepairTarget(procedure_id=_PROCEDURE, revision=0)
    with pytest.raises(ValueError):
        ProcedureRepairTarget(procedure_id=_PROCEDURE, revision=-2)
    with pytest.raises(RepairBudgetValidationError):
        ProcedureRepairTarget(procedure_id="not-a-procedure-id", revision=1)  # type: ignore[arg-type]


def test_procedure_target_requires_canonical_procedure_id_type() -> None:
    from agentx.core.ids import TaskId

    with pytest.raises(RepairBudgetValidationError):
        ProcedureRepairTarget(procedure_id=TaskId(uuid4()), revision=1)  # type: ignore[arg-type]


def test_fingerprints_and_markers_use_the_opaque_token_grammar() -> None:
    for token_type in (
        RepairProposalFingerprint,
        RepairProgressMarker,
        RepairTargetFingerprint,
    ):
        with pytest.raises(TypeError):
            token_type(42)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            token_type("")
        with pytest.raises(ValueError):
            token_type(" padded ")
        with pytest.raises(ValueError):
            token_type("token with spaces")
        with pytest.raises(ValueError):
            token_type("token-with-emoji-\N{SLIGHTLY SMILING FACE}")
        with pytest.raises(ValueError):
            token_type("x" * 257)
        assert token_type("sha256:9f2a").value == "sha256:9f2a"


# ---------------------------------------------------------------------------
# Determinism and immutability.
# ---------------------------------------------------------------------------


def test_same_inputs_produce_identical_assessments() -> None:
    history = (
        _evidence("patch:1"),
        _evidence("patch:2", RepairAttemptOutcome.FAILED_SHADOW),
        _evidence("patch:3", progress="progress:milestone-1"),
    )
    target = _procedure_target()
    proposal = RepairProposalFingerprint("patch:4")
    limits = _limits()

    first = assess_repair_attempt(target=target, proposal=proposal, history=history, limits=limits)
    second = assess_repair_attempt(target=target, proposal=proposal, history=history, limits=limits)

    assert first == second
    assert first.reason == second.reason


def test_records_are_immutable() -> None:
    evidence = _evidence("patch:1")
    limits = _limits()
    target = _procedure_target()
    result = assess_repair_attempt(
        target=target,
        proposal=RepairProposalFingerprint("patch:2"),
        history=(evidence,),
        limits=limits,
    )

    with pytest.raises(FrozenInstanceError):
        evidence.proposal = RepairProposalFingerprint("patch:other")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        limits.max_total_attempts = 99  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        target.opaque = None  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.decision = RepairBudgetDecision.STOP_TOTAL_LIMIT  # type: ignore[misc]


def test_assessment_counts_are_structurally_consistent() -> None:
    with pytest.raises(ValueError):
        RepairBudgetAssessment(
            decision=RepairBudgetDecision.ALLOW_CONSIDERATION,
            reason="x",
            history_attempt_count=1,
            target_attempt_count=2,
            proposal_attempt_count=0,
            consecutive_no_progress_count=0,
            distinct_progress_markers=0,
        )
    with pytest.raises(ValueError):
        RepairBudgetAssessment(
            decision=RepairBudgetDecision.INVALID_HISTORY,
            reason="x",
            history_attempt_count=1,
            target_attempt_count=0,
            proposal_attempt_count=0,
            consecutive_no_progress_count=0,
            distinct_progress_markers=0,
        )


# ---------------------------------------------------------------------------
# History order and no hidden reset.
# ---------------------------------------------------------------------------


def test_history_order_changes_the_no_progress_answer() -> None:
    marker = _evidence("patch:1", progress="progress:milestone-1")
    failures = (
        _evidence("patch:2"),
        _evidence("patch:3"),
    )

    early_progress = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:4"),
        history=(marker, *failures),
        limits=_limits(total=9, per_proposal=9, no_progress=2),
    )
    late_progress = assess_repair_attempt(
        target=_procedure_target(),
        proposal=RepairProposalFingerprint("patch:4"),
        history=(*failures, marker),
        limits=_limits(total=9, per_proposal=9, no_progress=2),
    )

    assert early_progress.decision is RepairBudgetDecision.STOP_NO_PROGRESS
    assert early_progress.consecutive_no_progress_count == 2
    assert late_progress.decision is RepairBudgetDecision.ALLOW_CONSIDERATION
    assert late_progress.consecutive_no_progress_count == 0


def test_reassessing_the_same_history_is_stable_no_counter_decays() -> None:
    history = (
        _evidence("patch:1"),
        _evidence("patch:1"),
    )
    target = _procedure_target()
    proposal = RepairProposalFingerprint("patch:1")
    limits = _limits(total=9, per_proposal=2, no_progress=9)

    for _ in range(5):
        result = assess_repair_attempt(
            target=target, proposal=proposal, history=history, limits=limits
        )
        assert result.decision is RepairBudgetDecision.STOP_REPEAT_LIMIT
        assert result.proposal_attempt_count == 2


def test_no_input_resets_the_total_ceiling() -> None:
    history = tuple(_evidence("patch:1", progress="progress:milestone-1") for _ in range(3))
    target = _procedure_target()
    proposal = RepairProposalFingerprint("patch:2")
    limits = _limits(total=3, per_proposal=9, no_progress=9)

    stopped = assess_repair_attempt(
        target=target, proposal=proposal, history=history, limits=limits
    )
    replayed = assess_repair_attempt(
        target=target,
        proposal=RepairProposalFingerprint("patch:1"),
        history=history,
        limits=limits,
    )

    assert stopped.decision is RepairBudgetDecision.STOP_TOTAL_LIMIT
    assert replayed.decision is RepairBudgetDecision.STOP_TOTAL_LIMIT


def test_evidence_carries_no_timestamp_or_uuid_reset_field() -> None:
    # The record structurally cannot represent "same content, newer time" as
    # distinct evidence: only target, proposal, outcome, and progress exist.
    assert {field.name for field in fields(RepairAttemptEvidence)} == {
        "target",
        "proposal",
        "outcome",
        "progress",
    }
    assert _evidence("patch:1") == _evidence("patch:1")
    assert _evidence("patch:1") != _evidence("patch:2")
