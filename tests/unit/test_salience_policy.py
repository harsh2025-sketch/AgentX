"""Tests for the C6.05 deterministic salience/archive policy.

Coverage: recent unverified, old verified, old superseded, important
failures, negative memory, conflicts, threshold boundaries, invalid
configuration, determinism, archive reason codes, and the structural
guarantee that archiving never deletes or mutates anything.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentx.core.episodes import EpisodeOutcome, EpisodeRecord
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
    ProvenanceReference,
    ScopeDimension,
)
from agentx.core.negative_experience import (
    AttemptKind,
    AttemptReference,
    FailureReference,
    NegativeExperienceRecord,
)
from agentx.hive.salience_policy import (
    DEFAULT_SALIENCE_CONFIG,
    SalienceClockError,
    SalienceConfigError,
    SalienceDecision,
    SalienceEvidence,
    SalienceEvidenceError,
    SaliencePolicy,
    SaliencePolicyConfig,
    SaliencePolicyError,
    SalienceReasonCode,
    SalienceSubject,
    SalienceSubjectKind,
    SalienceTier,
)
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)

_OFFICE_SCOPE = KnowledgeScope(dimensions={ScopeDimension.APPLICATION: "excel"})
_BROWSER_SCOPE = KnowledgeScope(dimensions={ScopeDimension.APPLICATION: "chrome"})
_WEB_PROVENANCE = ProvenanceReference(
    kind=ProvenanceKind.WEB, reference="https://example.invalid/claim"
)


class FakeClock:
    """Deterministic, mutable clock."""

    def __init__(self, start: datetime = _T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


def _policy(
    config: SaliencePolicyConfig | None = None,
    clock: FakeClock | None = None,
) -> tuple[SaliencePolicy, FakeClock]:
    active_clock = clock if clock is not None else FakeClock()
    return SaliencePolicy(config=config or SaliencePolicyConfig(), clock=active_clock), active_clock


def _knowledge(
    *,
    status: KnowledgeStatus = KnowledgeStatus.UNVERIFIED,
    created_at: datetime = _T0,
    verified_at: datetime | None = None,
    scope: KnowledgeScope | None = None,
    provenance: ProvenanceReference | None = None,
    content: str = "the invoice template lives in the finance share",
) -> KnowledgeRecord:
    """Build a knowledge record; status/verified_at are set post-construction
    through the canonical dataclass (tests construct directly, mirroring a
    store that restored persisted lifecycle state)."""
    record = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content=content,
        scope=scope,
        provenance=provenance,
        created_at=created_at,
    )
    return replace(record, status=status, verified_at=verified_at)


def _negative(
    *,
    observed_at: datetime = _T0,
    reason: str = "timeout",
) -> NegativeExperienceRecord:
    return NegativeExperienceRecord.create(
        attempt=AttemptReference(kind=AttemptKind.CAPABILITY, reference="browser.read_cell"),
        failure=FailureReference(reason_code=reason),
        observed_at=observed_at,
    )


def _episode(
    *,
    outcome: EpisodeOutcome = EpisodeOutcome.FAILED,
    created_at: datetime = _T0,
    ended_at: datetime | None = None,
) -> EpisodeRecord:
    return EpisodeRecord.create(
        outcome=outcome,
        summary="attempted to export the quarterly sheet",
        created_at=created_at,
        started_at=None if ended_at is None else created_at,
        ended_at=ended_at,
    )


# ---------------------------------------------------------------------------
# Configuration: canonical thresholds, invalid configuration
# ---------------------------------------------------------------------------


def test_default_config_is_canonical_and_valid() -> None:
    config = SaliencePolicyConfig()

    assert config.unverified_max_age == timedelta(days=30)
    assert config.degraded_max_age == timedelta(days=90)
    assert config.trusted_max_age == timedelta(days=365)
    assert config.mismatched_environment_max_age == timedelta(days=7)
    assert config.episode_max_age == timedelta(days=90)
    assert config.min_reuse_resistance == 2
    assert config == DEFAULT_SALIENCE_CONFIG


def test_module_default_policy_uses_canonical_config() -> None:
    policy = SaliencePolicy()

    assert policy.config == DEFAULT_SALIENCE_CONFIG


@pytest.mark.parametrize(
    "field_name",
    [
        "unverified_max_age",
        "degraded_max_age",
        "trusted_max_age",
        "mismatched_environment_max_age",
        "episode_max_age",
    ],
)
def test_zero_and_negative_thresholds_are_rejected(field_name: str) -> None:
    for bad in (timedelta(0), timedelta(seconds=-1)):
        payload: dict[str, object] = {field_name: bad}
        with pytest.raises(SalienceConfigError):
            SaliencePolicyConfig(**payload)  # type: ignore[arg-type]


def test_non_timedelta_threshold_is_rejected() -> None:
    with pytest.raises(TypeError):
        SaliencePolicyConfig(unverified_max_age=30)  # type: ignore[arg-type]


def test_non_monotonic_age_ladder_is_rejected() -> None:
    with pytest.raises(SalienceConfigError):
        SaliencePolicyConfig(
            unverified_max_age=timedelta(days=90),
            degraded_max_age=timedelta(days=30),
        )
    with pytest.raises(SalienceConfigError):
        SaliencePolicyConfig(
            trusted_max_age=timedelta(days=10),
            degraded_max_age=timedelta(days=30),
        )
    with pytest.raises(SalienceConfigError):
        SaliencePolicyConfig(
            unverified_max_age=timedelta(days=1),
            mismatched_environment_max_age=timedelta(days=2),
        )


def test_min_reuse_resistance_must_be_a_positive_int() -> None:
    with pytest.raises(SalienceConfigError):
        SaliencePolicyConfig(min_reuse_resistance=0)
    with pytest.raises(TypeError):
        SaliencePolicyConfig(min_reuse_resistance=True)
    with pytest.raises(TypeError):
        SaliencePolicyConfig(min_reuse_resistance=1.5)  # type: ignore[arg-type]


def test_policy_rejects_non_config_and_non_callable_clock() -> None:
    with pytest.raises(TypeError):
        SaliencePolicy(config=" archival ")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        SaliencePolicy(clock=42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Injected clock
# ---------------------------------------------------------------------------


def test_injected_clock_controls_evaluation_time() -> None:
    policy, clock = _policy()
    record = _knowledge(status=KnowledgeStatus.UNVERIFIED, created_at=_T0)

    clock.advance(timedelta(days=29))
    assert policy.evaluate_knowledge(record).is_active

    clock.advance(timedelta(days=2))
    assert policy.evaluate_knowledge(record).is_archival


def test_clock_must_return_timezone_aware_datetime() -> None:
    class NaiveClock:
        def __call__(self) -> datetime:
            return datetime(2026, 9, 1, 12, 0, 0)

    policy = SaliencePolicy(clock=NaiveClock())
    with pytest.raises(SalienceClockError):
        policy.evaluate_knowledge(_knowledge())

    class JunkClock:
        def __call__(self) -> datetime:
            return "now"  # type: ignore[return-value]

    policy = SaliencePolicy(clock=JunkClock())
    with pytest.raises(SalienceClockError):
        policy.evaluate_negative_experience(_negative())


def test_non_utc_clock_readings_are_normalized() -> None:
    offset_tz = timezone(timedelta(hours=3))

    class OffsetClock:
        def __call__(self) -> datetime:
            return datetime(2026, 9, 1, 15, 0, 0, tzinfo=offset_tz)

    policy = SaliencePolicy(clock=OffsetClock())
    decision = policy.evaluate_knowledge(_knowledge(created_at=_T0))

    assert decision.evaluated_at == _T0


# ---------------------------------------------------------------------------
# Knowledge records: recent unverified / old unverified
# ---------------------------------------------------------------------------


def test_recent_unverified_record_is_active() -> None:
    policy, _ = _policy()
    record = _knowledge(status=KnowledgeStatus.UNVERIFIED, created_at=_T0)

    decision = policy.evaluate_knowledge(record)

    assert decision.is_active
    assert decision.reason_codes == (SalienceReasonCode.RECENTLY_CREATED,)
    assert decision.subject == SalienceSubject(
        kind=SalienceSubjectKind.KNOWLEDGE, identifier=record.knowledge_id.to_str()
    )
    assert decision.factors.age == timedelta(0)
    assert decision.factors.effective_threshold == timedelta(days=30)


def test_old_unverified_record_archives_with_reason_code() -> None:
    policy, _clock = _policy(clock=FakeClock(_T0 + timedelta(days=31)))
    record = _knowledge(status=KnowledgeStatus.UNVERIFIED, created_at=_T0)

    decision = policy.evaluate_knowledge(record, SalienceEvidence(reuse_count=0))

    assert decision.is_archival
    assert decision.reason_codes == (SalienceReasonCode.STALE_UNVERIFIED,)
    assert decision.tier is SalienceTier.ARCHIVAL


# ---------------------------------------------------------------------------
# Knowledge records: verification status and provenance quality
# ---------------------------------------------------------------------------


def test_old_verified_record_with_provenance_resists_archival() -> None:
    policy, clock = _policy()
    verified_at = _T0 + timedelta(days=180)
    record = _knowledge(
        status=KnowledgeStatus.VERIFIED,
        created_at=_T0,
        verified_at=verified_at,
        provenance=_WEB_PROVENANCE,
    )

    clock.advance(timedelta(days=300))
    decision = policy.evaluate_knowledge(record)

    assert decision.is_active
    assert decision.reason_codes == (SalienceReasonCode.VERIFIED_RESISTANCE,)
    assert decision.factors.age == timedelta(days=120)
    assert decision.factors.effective_threshold == timedelta(days=365)
    assert decision.factors.provenance_cap_applied is False


def test_ancient_verified_record_archives_by_age() -> None:
    policy, clock = _policy()
    record = _knowledge(
        status=KnowledgeStatus.VERIFIED,
        created_at=_T0,
        verified_at=_T0,
        provenance=_WEB_PROVENANCE,
    )

    clock.advance(timedelta(days=366))
    decision = policy.evaluate_knowledge(record)

    assert decision.is_archival
    assert decision.reason_codes == (SalienceReasonCode.ARCHIVAL_BY_AGE,)


def test_verified_record_without_provenance_is_capped_at_degraded_threshold() -> None:
    policy, clock = _policy()
    record = _knowledge(
        status=KnowledgeStatus.VERIFIED,
        created_at=_T0,
        verified_at=_T0,
        provenance=None,
    )

    clock.advance(timedelta(days=91))
    decision = policy.evaluate_knowledge(record)

    assert decision.is_archival
    assert decision.reason_codes == (
        SalienceReasonCode.STALE_DEGRADED,
        SalienceReasonCode.PROVENANCE_ABSENT,
    )
    assert decision.factors.provenance_cap_applied is True
    assert decision.factors.effective_threshold == timedelta(days=90)


def test_provisional_and_supported_use_the_trusted_ladder() -> None:
    for status in (KnowledgeStatus.PROVISIONAL, KnowledgeStatus.SUPPORTED):
        policy, clock = _policy()
        record = _knowledge(status=status, created_at=_T0, provenance=_WEB_PROVENANCE)

        clock.advance(timedelta(days=364))
        assert policy.evaluate_knowledge(record).is_active

        clock.advance(timedelta(days=2))
        decision = policy.evaluate_knowledge(record)
        assert decision.is_archival
        assert decision.reason_codes == (SalienceReasonCode.ARCHIVAL_BY_AGE,)


def test_degraded_record_uses_the_degraded_threshold() -> None:
    policy, clock = _policy()
    record = _knowledge(
        status=KnowledgeStatus.DEGRADED,
        created_at=_T0,
        verified_at=_T0,
        provenance=_WEB_PROVENANCE,
    )

    clock.advance(timedelta(days=89))
    decision = policy.evaluate_knowledge(record)
    assert decision.is_active
    assert decision.reason_codes == (SalienceReasonCode.RECENTLY_CREATED,)

    clock.advance(timedelta(days=2))
    decision = policy.evaluate_knowledge(record)
    assert decision.is_archival
    assert decision.reason_codes == (SalienceReasonCode.STALE_DEGRADED,)


def test_age_counts_from_reverification_not_creation() -> None:
    policy, clock = _policy()
    record = _knowledge(
        status=KnowledgeStatus.VERIFIED,
        created_at=_T0,
        verified_at=_T0 + timedelta(days=300),
        provenance=_WEB_PROVENANCE,
    )

    clock.advance(timedelta(days=350))
    decision = policy.evaluate_knowledge(record)

    assert decision.is_active
    assert decision.factors.age == timedelta(days=50)


# ---------------------------------------------------------------------------
# Supersession and conflict
# ---------------------------------------------------------------------------


def test_old_superseded_record_archives_as_preserved_history() -> None:
    policy, _ = _policy()
    record = _knowledge(status=KnowledgeStatus.SUPERSEDED, created_at=_T0)

    decision = policy.evaluate_knowledge(record)

    assert decision.is_archival
    assert decision.reason_codes == (SalienceReasonCode.SUPERSEDED_HISTORY,)


def test_superseded_record_archives_even_when_still_referenced() -> None:
    policy, clock = _policy()
    record = _knowledge(status=KnowledgeStatus.SUPERSEDED, created_at=_T0)
    clock.advance(timedelta(days=5))

    decision = policy.evaluate_knowledge(record, SalienceEvidence(reuse_count=99))

    assert decision.is_archival
    assert decision.reason_codes == (SalienceReasonCode.SUPERSEDED_HISTORY,)


def test_recent_superseded_record_still_archives() -> None:
    policy, _ = _policy()
    record = _knowledge(status=KnowledgeStatus.SUPERSEDED, created_at=_T0)

    decision = policy.evaluate_knowledge(record)

    assert decision.is_archival


def test_conflicted_record_stays_active_and_visible() -> None:
    policy, clock = _policy()
    record = _knowledge(
        status=KnowledgeStatus.CONFLICTED,
        created_at=_T0,
        verified_at=_T0,
    )

    clock.advance(timedelta(days=1000))
    decision = policy.evaluate_knowledge(record)

    assert decision.is_active
    assert decision.reason_codes == (SalienceReasonCode.CONFLICT_VISIBLE,)
    assert decision.factors.effective_threshold is None


# ---------------------------------------------------------------------------
# Reuse / reference resistance
# ---------------------------------------------------------------------------


def test_reuse_at_threshold_resists_archival() -> None:
    policy, clock = _policy()
    record = _knowledge(status=KnowledgeStatus.UNVERIFIED, created_at=_T0)
    clock.advance(timedelta(days=400))

    decision = policy.evaluate_knowledge(record, SalienceEvidence(reuse_count=2))

    assert decision.is_active
    assert decision.reason_codes == (SalienceReasonCode.REUSE_RESISTANCE,)


def test_reuse_below_threshold_falls_through_to_age_rules() -> None:
    policy, clock = _policy()
    record = _knowledge(status=KnowledgeStatus.UNVERIFIED, created_at=_T0)
    clock.advance(timedelta(days=400))

    decision = policy.evaluate_knowledge(record, SalienceEvidence(reuse_count=1))

    assert decision.is_archival
    assert decision.reason_codes == (SalienceReasonCode.STALE_UNVERIFIED,)


def test_reused_recent_record_is_active_via_reuse_reason() -> None:
    policy, _ = _policy()
    record = _knowledge(status=KnowledgeStatus.UNVERIFIED, created_at=_T0)

    decision = policy.evaluate_knowledge(record, SalienceEvidence(reuse_count=7))

    assert decision.is_active
    assert decision.reason_codes == (SalienceReasonCode.REUSE_RESISTANCE,)


# ---------------------------------------------------------------------------
# Scope / environment relevance
# ---------------------------------------------------------------------------


def test_environment_mismatch_shortens_the_threshold() -> None:
    policy, clock = _policy()
    record = _knowledge(
        status=KnowledgeStatus.UNVERIFIED,
        created_at=_T0,
        scope=_OFFICE_SCOPE,
    )
    evidence = SalienceEvidence(current_environment=_BROWSER_SCOPE)

    clock.advance(timedelta(days=6))
    decision = policy.evaluate_knowledge(record, evidence)
    assert decision.is_active
    assert decision.reason_codes == (
        SalienceReasonCode.RECENTLY_CREATED,
        SalienceReasonCode.ENVIRONMENT_MISMATCH,
    )

    clock.advance(timedelta(days=2))
    decision = policy.evaluate_knowledge(record, evidence)
    assert decision.is_archival
    assert decision.reason_codes == (
        SalienceReasonCode.STALE_UNVERIFIED,
        SalienceReasonCode.ENVIRONMENT_MISMATCH,
    )
    assert decision.factors.environment_mismatch is True
    assert decision.factors.effective_threshold == timedelta(days=7)


def test_matching_environment_applies_the_normal_threshold() -> None:
    policy, clock = _policy()
    record = _knowledge(
        status=KnowledgeStatus.UNVERIFIED,
        created_at=_T0,
        scope=_OFFICE_SCOPE,
    )
    evidence = SalienceEvidence(current_environment=_OFFICE_SCOPE)

    clock.advance(timedelta(days=8))
    decision = policy.evaluate_knowledge(record, evidence)

    assert decision.is_active
    assert decision.reason_codes == (SalienceReasonCode.RECENTLY_CREATED,)
    assert decision.factors.environment_mismatch is False


def test_global_scope_never_mismatches_any_environment() -> None:
    policy, clock = _policy()
    record = _knowledge(status=KnowledgeStatus.UNVERIFIED, created_at=_T0, scope=KnowledgeScope())
    evidence = SalienceEvidence(current_environment=_BROWSER_SCOPE)

    clock.advance(timedelta(days=8))
    decision = policy.evaluate_knowledge(record, evidence)

    assert decision.is_active
    assert decision.factors.environment_mismatch is False


def test_partial_dimension_match_with_one_differing_dimension_is_a_mismatch() -> None:
    policy, clock = _policy()
    record_scope = KnowledgeScope(
        dimensions={
            ScopeDimension.APPLICATION: "excel",
            ScopeDimension.OPERATING_SYSTEM: "windows",
        }
    )
    current = KnowledgeScope(
        dimensions={
            ScopeDimension.APPLICATION: "chrome",
            ScopeDimension.OPERATING_SYSTEM: "windows",
        }
    )
    record = _knowledge(status=KnowledgeStatus.UNVERIFIED, created_at=_T0, scope=record_scope)
    clock.advance(timedelta(days=8))

    decision = policy.evaluate_knowledge(record, SalienceEvidence(current_environment=current))

    assert decision.is_archival
    assert decision.factors.environment_mismatch is True


def test_no_environment_supplied_applies_no_mismatch_factor() -> None:
    policy, clock = _policy()
    record = _knowledge(
        status=KnowledgeStatus.UNVERIFIED,
        created_at=_T0,
        scope=_OFFICE_SCOPE,
    )

    clock.advance(timedelta(days=8))
    decision = policy.evaluate_knowledge(record)

    assert decision.is_active
    assert decision.factors.environment_mismatch is False


# ---------------------------------------------------------------------------
# Negative memory and important failures
# ---------------------------------------------------------------------------


def test_negative_experience_is_protected_regardless_of_age() -> None:
    policy, clock = _policy()
    record = _negative(observed_at=_T0)

    clock.advance(timedelta(days=3650))
    decision = policy.evaluate_negative_experience(record)

    assert decision.is_active
    assert decision.reason_codes == (SalienceReasonCode.NEGATIVE_IMPORTANCE,)
    assert decision.subject == SalienceSubject(
        kind=SalienceSubjectKind.NEGATIVE_EXPERIENCE,
        identifier=record.negative_experience_id.to_str(),
    )


def test_important_failure_stays_active_even_under_tiny_thresholds() -> None:
    tiny = SaliencePolicyConfig(episode_max_age=timedelta(seconds=1))
    policy, clock = _policy(config=tiny)
    record = _negative(observed_at=_T0)
    clock.advance(timedelta(days=365))

    decision = policy.evaluate_negative_experience(record)

    assert decision.is_active
    assert decision.reason_codes == (SalienceReasonCode.NEGATIVE_IMPORTANCE,)


def test_failed_episode_is_protected() -> None:
    policy, clock = _policy()
    record = _episode(outcome=EpisodeOutcome.FAILED, created_at=_T0)

    clock.advance(timedelta(days=3650))
    decision = policy.evaluate_episode(record)

    assert decision.is_active
    assert decision.reason_codes == (SalienceReasonCode.FAILURE_IMPORTANCE,)


def test_old_successful_episode_archives_as_history() -> None:
    policy, clock = _policy()
    record = _episode(
        outcome=EpisodeOutcome.SUCCEEDED,
        created_at=_T0,
        ended_at=_T0 + timedelta(minutes=5),
    )

    clock.advance(timedelta(days=91))
    decision = policy.evaluate_episode(record)

    assert decision.is_archival
    assert decision.reason_codes == (SalienceReasonCode.EPISODE_HISTORY,)
    assert decision.factors.age == timedelta(days=91, minutes=-5)


def test_old_cancelled_and_partial_episodes_archive_as_history() -> None:
    for outcome in (EpisodeOutcome.CANCELLED, EpisodeOutcome.PARTIAL):
        policy, clock = _policy()
        record = _episode(outcome=outcome, created_at=_T0)
        clock.advance(timedelta(days=200))

        decision = policy.evaluate_episode(record)

        assert decision.is_archival
        assert decision.reason_codes == (SalienceReasonCode.EPISODE_HISTORY,)


def test_recent_episode_is_active() -> None:
    policy, clock = _policy()
    record = _episode(outcome=EpisodeOutcome.SUCCEEDED, created_at=_T0)

    clock.advance(timedelta(days=89))
    decision = policy.evaluate_episode(record)

    assert decision.is_active
    assert decision.reason_codes == (SalienceReasonCode.RECENTLY_CREATED,)


# ---------------------------------------------------------------------------
# Threshold boundaries (fail-closed toward the archival tier)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "provenance", "threshold"),
    [
        (KnowledgeStatus.UNVERIFIED, None, timedelta(days=30)),
        (KnowledgeStatus.DEGRADED, _WEB_PROVENANCE, timedelta(days=90)),
        (KnowledgeStatus.VERIFIED, _WEB_PROVENANCE, timedelta(days=365)),
    ],
)
def test_age_boundary_exactly_at_threshold_archives(
    status: KnowledgeStatus,
    provenance: ProvenanceReference | None,
    threshold: timedelta,
) -> None:
    policy, clock = _policy()
    record = _knowledge(status=status, created_at=_T0, verified_at=_T0, provenance=provenance)

    clock.advance(threshold)
    decision = policy.evaluate_knowledge(record)

    assert decision.is_archival


@pytest.mark.parametrize(
    ("status", "provenance", "threshold"),
    [
        (KnowledgeStatus.UNVERIFIED, None, timedelta(days=30)),
        (KnowledgeStatus.DEGRADED, _WEB_PROVENANCE, timedelta(days=90)),
        (KnowledgeStatus.VERIFIED, _WEB_PROVENANCE, timedelta(days=365)),
    ],
)
def test_age_one_microsecond_before_threshold_stays_active(
    status: KnowledgeStatus,
    provenance: ProvenanceReference | None,
    threshold: timedelta,
) -> None:
    policy, clock = _policy()
    record = _knowledge(status=status, created_at=_T0, verified_at=_T0, provenance=provenance)

    clock.advance(threshold - timedelta(microseconds=1))
    decision = policy.evaluate_knowledge(record)

    assert decision.is_active


def test_environment_mismatch_boundary() -> None:
    policy, clock = _policy()
    record = _knowledge(status=KnowledgeStatus.UNVERIFIED, created_at=_T0, scope=_OFFICE_SCOPE)
    evidence = SalienceEvidence(current_environment=_BROWSER_SCOPE)

    clock.advance(timedelta(days=7) - timedelta(microseconds=1))
    assert policy.evaluate_knowledge(record, evidence).is_active

    clock.advance(timedelta(microseconds=1))
    assert policy.evaluate_knowledge(record, evidence).is_archival


def test_episode_boundary_exactly_at_threshold_archives() -> None:
    policy, clock = _policy()
    record = _episode(outcome=EpisodeOutcome.SUCCEEDED, created_at=_T0)

    clock.advance(timedelta(days=90))
    decision = policy.evaluate_episode(record)

    assert decision.is_archival
    assert decision.reason_codes == (SalienceReasonCode.EPISODE_HISTORY,)


def test_negative_boundary_has_no_threshold_at_all() -> None:
    policy, clock = _policy()
    record = _negative(observed_at=_T0)

    clock.advance(timedelta(microseconds=1))
    decision = policy.evaluate_negative_experience(record)

    assert decision.is_active
    assert decision.factors.effective_threshold is None


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_inputs_produce_identical_decisions() -> None:
    policy, clock = _policy()
    record = _knowledge(
        status=KnowledgeStatus.VERIFIED,
        created_at=_T0,
        verified_at=_T0,
        provenance=_WEB_PROVENANCE,
        scope=_OFFICE_SCOPE,
    )
    evidence = SalienceEvidence(reuse_count=1, current_environment=_BROWSER_SCOPE)
    clock.advance(timedelta(days=10))

    first = policy.evaluate_knowledge(record, evidence)
    second = policy.evaluate_knowledge(record, evidence)

    assert first == second
    assert hash(first.subject) == hash(second.subject)


def test_decisions_are_reproducible_across_policy_instances() -> None:
    config = SaliencePolicyConfig()
    record = _knowledge(status=KnowledgeStatus.UNVERIFIED, created_at=_T0)
    first_policy, _first_clock = _policy(config=config, clock=FakeClock(_T0 + timedelta(days=31)))
    second_policy, _second_clock = _policy(config=config, clock=FakeClock(_T0 + timedelta(days=31)))

    assert first_policy.evaluate_knowledge(record) == second_policy.evaluate_knowledge(record)


def test_policy_and_decisions_are_frozen() -> None:
    policy, _ = _policy()
    decision = policy.evaluate_knowledge(_knowledge())

    with pytest.raises(FrozenInstanceError):
        policy.config = SaliencePolicyConfig()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.tier = SalienceTier.ARCHIVAL  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.reason_codes = tuple()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.factors.age = timedelta(0)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        SalienceSubject(kind=SalienceSubjectKind.KNOWLEDGE, identifier="x").identifier = "y"  # type: ignore[misc]


def test_future_timestamped_record_is_within_threshold() -> None:
    policy, _ = _policy()
    record = _knowledge(status=KnowledgeStatus.UNVERIFIED, created_at=_T0 + timedelta(days=1))

    decision = policy.evaluate_knowledge(record)

    assert decision.is_active
    assert decision.factors.age == timedelta(days=-1)


def test_decision_payload_is_plain_data() -> None:
    policy, _ = _policy()
    decision = policy.evaluate_knowledge(_knowledge())

    assert isinstance(decision, SalienceDecision)
    assert set(SalienceTier) == {SalienceTier.ACTIVE, SalienceTier.ARCHIVAL}
    assert decision.subject.identifier == decision.subject.identifier.strip()
    for code in decision.reason_codes:
        assert isinstance(code, SalienceReasonCode)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_evidence_validation() -> None:
    with pytest.raises(TypeError):
        SalienceEvidence(reuse_count=True)
    with pytest.raises(TypeError):
        SalienceEvidence(reuse_count=1.0)  # type: ignore[arg-type]
    with pytest.raises(SalienceEvidenceError):
        SalienceEvidence(reuse_count=-1)
    with pytest.raises(TypeError):
        SalienceEvidence(current_environment="excel")  # type: ignore[arg-type]


def test_wrong_record_types_are_rejected() -> None:
    policy, _ = _policy()
    with pytest.raises(TypeError):
        policy.evaluate_knowledge("the invoice template")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        policy.evaluate_negative_experience("timeout")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        policy.evaluate_episode("export")  # type: ignore[arg-type]


def test_salience_errors_form_one_contract_family() -> None:
    assert issubclass(SalienceConfigError, SaliencePolicyError)
    assert issubclass(SalienceClockError, SaliencePolicyError)
    assert issubclass(SalienceEvidenceError, SaliencePolicyError)
    assert issubclass(SaliencePolicyError, ValueError)


# ---------------------------------------------------------------------------
# Archive is not delete: composition over the canonical store
# ---------------------------------------------------------------------------


def test_archival_decisions_delete_nothing_and_change_no_lifecycle_state(
    tmp_path: Path,
) -> None:
    store = KnowledgeStore(SQLiteDatabase(tmp_path / "agentx.sqlite3"))
    records = [
        _knowledge(status=KnowledgeStatus.UNVERIFIED, content="stale claim one"),
        _knowledge(
            status=KnowledgeStatus.VERIFIED,
            verified_at=_T0,
            provenance=_WEB_PROVENANCE,
            content="maintained claim",
        ),
        _knowledge(status=KnowledgeStatus.SUPERSEDED, content="replaced claim"),
        _knowledge(status=KnowledgeStatus.CONFLICTED, content="contradicted claim"),
    ]
    for record in records:
        store.insert(record)
    before = store.list_records()

    policy, _clock = _policy(clock=FakeClock(_T0 + timedelta(days=400)))
    decisions = [policy.evaluate_knowledge(record) for record in records]

    assert any(decision.is_archival for decision in decisions)
    after = store.list_records()
    assert after == before
    assert len(after) == len(records)
    assert [stored.to_json() for stored in after] == [stored.to_json() for stored in before]
    for original, stored in zip(before, after, strict=True):
        assert original.status is stored.status
        assert original.verified_at == stored.verified_at


def test_policy_holds_no_store_and_outcome_vocabulary_has_no_delete() -> None:
    policy, _ = _policy()

    assert not any(
        hasattr(policy, name)
        for name in ("store", "database", "episode_store", "negative_store", "delete")
    )
    assert not hasattr(policy, "delete")
    assert {tier.value for tier in SalienceTier}.isdisjoint(
        {"delete", "deleted", "destroy", "purge", "erase", "drop"}
    )
    for code in SalienceReasonCode:
        assert "delete" not in code.value
        assert "purge" not in code.value
