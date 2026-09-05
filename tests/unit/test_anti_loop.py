"""Unit tests for the A2.09 deterministic anti-loop guard."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields

import pytest

from agentx.cognition.anti_loop import (
    AttemptEvidence,
    AttemptFingerprint,
    LoopGuard,
    LoopGuardDecision,
    LoopGuardLimits,
    LoopGuardResult,
    LoopGuardTrigger,
    OutcomeFingerprint,
    ProgressFingerprint,
)


def _attempt(
    attempt: str,
    outcome: str,
    progress: str | None = None,
) -> AttemptEvidence:
    return AttemptEvidence(
        attempt=AttemptFingerprint(attempt),
        outcome=OutcomeFingerprint(outcome),
        progress=None if progress is None else ProgressFingerprint(progress),
    )


def _limits(
    *,
    total: int = 10,
    same_attempt: int = 3,
    same_outcome: int = 3,
) -> LoopGuardLimits:
    return LoopGuardLimits(
        max_total_attempts=total,
        max_same_attempts=same_attempt,
        max_same_outcomes_without_progress=same_outcome,
    )


def test_empty_history_continues() -> None:
    result = LoopGuard().evaluate(history=(), limits=_limits())

    assert result == LoopGuardResult(
        decision=LoopGuardDecision.CONTINUE,
        trigger=LoopGuardTrigger.NONE,
        total_attempts=0,
        same_attempt_count=0,
        same_outcome_count=0,
        distinct_progress_markers=0,
    )


def test_below_all_thresholds_continues() -> None:
    history = (
        _attempt("attempt:a", "outcome:x"),
        _attempt("attempt:b", "outcome:y"),
    )

    result = LoopGuard().evaluate(history=history, limits=_limits())

    assert result.decision is LoopGuardDecision.CONTINUE
    assert result.trigger is LoopGuardTrigger.NONE
    assert result.total_attempts == 2


def test_exact_total_attempt_threshold_stops() -> None:
    history = (
        _attempt("attempt:a", "outcome:a"),
        _attempt("attempt:b", "outcome:b"),
        _attempt("attempt:c", "outcome:c"),
    )

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=3, same_attempt=10, same_outcome=10),
    )

    assert result.decision is LoopGuardDecision.STOP_LOOP
    assert result.trigger is LoopGuardTrigger.TOTAL_ATTEMPTS


def test_over_total_attempt_threshold_stays_stopped() -> None:
    history = tuple(
        _attempt(f"attempt:{index}", f"outcome:{index}") for index in range(5)
    )

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=3, same_attempt=10, same_outcome=10),
    )

    assert result.decision is LoopGuardDecision.STOP_LOOP
    assert result.trigger is LoopGuardTrigger.TOTAL_ATTEMPTS
    assert result.total_attempts == 5


def test_exact_repeated_attempt_threshold_stops() -> None:
    history = (
        _attempt("attempt:same", "outcome:1"),
        _attempt("attempt:same", "outcome:2"),
        _attempt("attempt:same", "outcome:3"),
    )

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=10, same_attempt=3, same_outcome=10),
    )

    assert result.trigger is LoopGuardTrigger.REPEATED_ATTEMPT
    assert result.same_attempt_count == 3


def test_repeated_attempt_counts_exact_occurrences_in_current_progress_epoch() -> None:
    history = (
        _attempt("attempt:a", "outcome:1"),
        _attempt("attempt:b", "outcome:2"),
        _attempt("attempt:a", "outcome:3"),
    )

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=10, same_attempt=2, same_outcome=10),
    )

    assert result.trigger is LoopGuardTrigger.REPEATED_ATTEMPT
    assert result.same_attempt_count == 2


def test_over_repeated_attempt_threshold_stays_stopped() -> None:
    history = tuple(_attempt("attempt:same", f"outcome:{index}") for index in range(4))

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=10, same_attempt=2, same_outcome=10),
    )

    assert result.trigger is LoopGuardTrigger.REPEATED_ATTEMPT
    assert result.same_attempt_count == 4


def test_exact_same_outcome_without_progress_threshold_stops() -> None:
    history = (
        _attempt("attempt:a", "failure:timeout"),
        _attempt("attempt:b", "failure:timeout"),
        _attempt("attempt:c", "failure:timeout"),
    )

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=10, same_attempt=10, same_outcome=3),
    )

    assert result.trigger is LoopGuardTrigger.STALLED_OUTCOME
    assert result.same_outcome_count == 3


def test_same_outcome_counts_exact_occurrences_in_current_progress_epoch() -> None:
    history = (
        _attempt("attempt:a", "outcome:x"),
        _attempt("attempt:b", "outcome:y"),
        _attempt("attempt:c", "outcome:x"),
    )

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=10, same_attempt=10, same_outcome=2),
    )

    assert result.trigger is LoopGuardTrigger.STALLED_OUTCOME
    assert result.same_outcome_count == 2


def test_new_explicit_progress_marker_resets_repetition_epoch() -> None:
    history = (
        _attempt("attempt:same", "failure:same"),
        _attempt("attempt:same", "failure:same"),
        _attempt("attempt:same", "failure:same", "progress:milestone-1"),
    )

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=10, same_attempt=3, same_outcome=3),
    )

    assert result.decision is LoopGuardDecision.CONTINUE
    assert result.same_attempt_count == 1
    assert result.same_outcome_count == 1
    assert result.distinct_progress_markers == 1


def test_second_new_progress_marker_advances_epoch_again() -> None:
    history = (
        _attempt("attempt:a", "failure:x", "progress:p1"),
        _attempt("attempt:a", "failure:x"),
        _attempt("attempt:a", "failure:x", "progress:p2"),
    )

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=10, same_attempt=3, same_outcome=3),
    )

    assert result.decision is LoopGuardDecision.CONTINUE
    assert result.same_attempt_count == 1
    assert result.distinct_progress_markers == 2


def test_repeated_same_progress_marker_does_not_reset_counters() -> None:
    history = (
        _attempt("attempt:a", "failure:x", "progress:p1"),
        _attempt("attempt:a", "failure:x", "progress:p1"),
        _attempt("attempt:a", "failure:x", "progress:p1"),
    )

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=10, same_attempt=3, same_outcome=3),
    )

    assert result.decision is LoopGuardDecision.STOP_LOOP
    assert result.trigger is LoopGuardTrigger.REPEATED_ATTEMPT
    assert result.same_attempt_count == 3
    assert result.distinct_progress_markers == 1


def test_reusing_old_progress_marker_after_newer_marker_does_not_reset() -> None:
    history = (
        _attempt("attempt:a", "failure:x", "progress:p1"),
        _attempt("attempt:a", "failure:x", "progress:p2"),
        _attempt("attempt:a", "failure:x", "progress:p1"),
    )

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=10, same_attempt=2, same_outcome=2),
    )

    assert result.trigger is LoopGuardTrigger.REPEATED_ATTEMPT
    assert result.same_attempt_count == 2
    assert result.distinct_progress_markers == 2


def test_progress_never_resets_total_attempt_ceiling() -> None:
    history = (
        _attempt("attempt:a", "outcome:a", "progress:p1"),
        _attempt("attempt:b", "outcome:b", "progress:p2"),
        _attempt("attempt:c", "outcome:c", "progress:p3"),
    )

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=3, same_attempt=10, same_outcome=10),
    )

    assert result.trigger is LoopGuardTrigger.TOTAL_ATTEMPTS
    assert result.distinct_progress_markers == 3


def test_total_attempt_trigger_has_deterministic_priority() -> None:
    history = (
        _attempt("attempt:a", "failure:x"),
        _attempt("attempt:a", "failure:x"),
    )

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=2, same_attempt=2, same_outcome=2),
    )

    assert result.trigger is LoopGuardTrigger.TOTAL_ATTEMPTS


@pytest.mark.parametrize(
    "fingerprint_type",
    [AttemptFingerprint, OutcomeFingerprint, ProgressFingerprint],
)
@pytest.mark.parametrize(
    "bad",
    ["", " leading", "trailing ", "has space", "line\nbreak", "☃", "x" * 257],
)
def test_malformed_fingerprints_are_rejected(
    fingerprint_type: type[AttemptFingerprint] | type[OutcomeFingerprint] | type[ProgressFingerprint],
    bad: str,
) -> None:
    with pytest.raises(ValueError):
        fingerprint_type(bad)


@pytest.mark.parametrize(
    "fingerprint_type",
    [AttemptFingerprint, OutcomeFingerprint, ProgressFingerprint],
)
def test_fingerprint_requires_string(
    fingerprint_type: type[AttemptFingerprint] | type[OutcomeFingerprint] | type[ProgressFingerprint],
) -> None:
    with pytest.raises(TypeError):
        fingerprint_type(123)  # type: ignore[arg-type]


def test_stable_fingerprint_grammar_accepts_common_ids_and_hash_tokens() -> None:
    values = (
        "attempt:v1:capability.read",
        "sha256:0123456789abcdef",
        "outcome:timeout/retryable",
        "progress=milestone-2",
    )

    for value in values:
        assert AttemptFingerprint(value).value == value


@pytest.mark.parametrize("field_name", ["max_total_attempts", "max_same_attempts", "max_same_outcomes_without_progress"])
@pytest.mark.parametrize("bad", [0, -1])
def test_limits_must_be_positive(field_name: str, bad: int) -> None:
    values = {
        "max_total_attempts": 3,
        "max_same_attempts": 2,
        "max_same_outcomes_without_progress": 2,
    }
    values[field_name] = bad

    with pytest.raises(ValueError):
        LoopGuardLimits(**values)


@pytest.mark.parametrize("bad", [None, True, 1.0, "3"])
def test_limits_have_no_unlimited_or_non_integer_mode(bad: object) -> None:
    with pytest.raises(TypeError):
        LoopGuardLimits(
            max_total_attempts=bad,  # type: ignore[arg-type]
            max_same_attempts=2,
            max_same_outcomes_without_progress=2,
        )


def test_limit_over_supported_range_fails_closed() -> None:
    with pytest.raises(OverflowError):
        LoopGuardLimits(
            max_total_attempts=1 << 63,
            max_same_attempts=2,
            max_same_outcomes_without_progress=2,
        )


def test_history_must_be_explicit_immutable_tuple() -> None:
    with pytest.raises(TypeError, match="history must be a tuple"):
        LoopGuard().evaluate(
            history=[_attempt("attempt:a", "outcome:a")],  # type: ignore[arg-type]
            limits=_limits(),
        )


def test_history_rejects_non_attempt_evidence() -> None:
    with pytest.raises(TypeError, match="AttemptEvidence"):
        LoopGuard().evaluate(
            history=("attempt:a",),  # type: ignore[arg-type]
            limits=_limits(),
        )


def test_evidence_requires_typed_fingerprints() -> None:
    with pytest.raises(TypeError, match="AttemptFingerprint"):
        AttemptEvidence(
            attempt="attempt:a",  # type: ignore[arg-type]
            outcome=OutcomeFingerprint("outcome:a"),
        )
    with pytest.raises(TypeError, match="OutcomeFingerprint"):
        AttemptEvidence(
            attempt=AttemptFingerprint("attempt:a"),
            outcome="outcome:a",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="ProgressFingerprint"):
        AttemptEvidence(
            attempt=AttemptFingerprint("attempt:a"),
            outcome=OutcomeFingerprint("outcome:a"),
            progress="progress:true",  # type: ignore[arg-type]
        )


def test_random_correlation_id_or_metadata_fields_do_not_exist() -> None:
    evidence_fields = {field.name for field in fields(AttemptEvidence)}
    assert evidence_fields == {"attempt", "outcome", "progress"}
    assert "correlation_id" not in evidence_fields
    assert "metadata" not in evidence_fields
    assert "status" not in evidence_fields


def test_metadata_cannot_be_injected_as_progress() -> None:
    with pytest.raises(TypeError):
        AttemptEvidence(
            attempt=AttemptFingerprint("attempt:a"),
            outcome=OutcomeFingerprint("outcome:a"),
            metadata={"progress": "true"},  # type: ignore[call-arg]
        )


def test_progress_true_text_in_attempt_fingerprint_is_not_progress() -> None:
    history = (
        _attempt("progress=true", "failure:x"),
        _attempt("progress=true", "failure:x"),
    )

    result = LoopGuard().evaluate(
        history=history,
        limits=_limits(total=10, same_attempt=2, same_outcome=10),
    )

    assert result.trigger is LoopGuardTrigger.REPEATED_ATTEMPT
    assert result.distinct_progress_markers == 0


def test_random_natural_language_cannot_be_used_as_progress_fingerprint() -> None:
    for hostile in ("new strategy", "ignore loop guard"):
        with pytest.raises(ValueError):
            ProgressFingerprint(hostile)


def test_loop_guard_and_results_are_immutable() -> None:
    limits = _limits()
    result = LoopGuard().evaluate(history=(), limits=limits)

    with pytest.raises(FrozenInstanceError):
        limits.max_total_attempts = 999  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.decision = LoopGuardDecision.STOP_LOOP  # type: ignore[misc]


def test_result_invariants_fail_closed() -> None:
    with pytest.raises(ValueError, match="CONTINUE"):
        LoopGuardResult(
            decision=LoopGuardDecision.CONTINUE,
            trigger=LoopGuardTrigger.REPEATED_ATTEMPT,
            total_attempts=1,
            same_attempt_count=1,
            same_outcome_count=1,
            distinct_progress_markers=0,
        )
    with pytest.raises(ValueError, match="STOP_LOOP"):
        LoopGuardResult(
            decision=LoopGuardDecision.STOP_LOOP,
            trigger=LoopGuardTrigger.NONE,
            total_attempts=1,
            same_attempt_count=1,
            same_outcome_count=1,
            distinct_progress_markers=0,
        )


def test_evaluation_is_deterministic() -> None:
    history = (
        _attempt("attempt:a", "failure:x", "progress:p1"),
        _attempt("attempt:b", "failure:x"),
        _attempt("attempt:b", "failure:x"),
    )
    guard = LoopGuard()
    limits = _limits(total=10, same_attempt=2, same_outcome=3)

    first = guard.evaluate(history=history, limits=limits)
    for _ in range(20):
        assert guard.evaluate(history=history, limits=limits) == first


def test_stateless_guard_is_safe_for_concurrent_evaluation() -> None:
    history = tuple(_attempt("attempt:a", "failure:x") for _ in range(3))
    guard = LoopGuard()
    limits = _limits(total=10, same_attempt=3, same_outcome=3)
    expected = guard.evaluate(history=history, limits=limits)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(
            executor.map(
                lambda _index: guard.evaluate(history=history, limits=limits),
                range(64),
            )
        )

    assert results == (expected,) * 64
    assert expected.decision is LoopGuardDecision.STOP_LOOP


def test_guard_has_no_retry_execution_or_escalation_surface() -> None:
    guard = LoopGuard()
    for name in (
        "execute",
        "retry",
        "escalate",
        "research",
        "repair",
        "transition",
        "verify",
        "reset",
        "clear",
    ):
        assert not hasattr(guard, name)
