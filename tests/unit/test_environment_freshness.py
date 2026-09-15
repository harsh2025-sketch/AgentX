"""AX-412/413 freshness, invalidation and refresh semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentx.core.environment_freshness import (
    EnvironmentFreshness,
    EnvironmentFreshnessState,
    EnvironmentInvalidationEvidence,
    EnvironmentInvalidationReason,
)
from agentx.core.knowledge import ProvenanceKind, ProvenanceReference

_T0 = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
_SOURCE = ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="world.provider")


def _freshness() -> EnvironmentFreshness:
    return EnvironmentFreshness(
        subject="window:42",
        environment_reference="windows:test",
        observed_at=_T0,
        ttl=timedelta(seconds=10),
        source=_SOURCE,
    )


def test_freshness_edges_fail_closed_exactly_at_expiry() -> None:
    freshness = _freshness()

    assert freshness.state_at(_T0 - timedelta(microseconds=1)) is (
        EnvironmentFreshnessState.NOT_YET_OBSERVED
    )
    assert freshness.state_at(_T0) is EnvironmentFreshnessState.FRESH
    assert freshness.state_at(_T0 + timedelta(seconds=10) - timedelta(microseconds=1)) is (
        EnvironmentFreshnessState.FRESH
    )
    assert freshness.state_at(_T0 + timedelta(seconds=10)) is EnvironmentFreshnessState.STALE


def test_invalidation_is_attributable_and_takes_effect_at_observed_instant() -> None:
    freshness = _freshness()
    invalidated_at = _T0 + timedelta(seconds=2)
    evidence = EnvironmentInvalidationEvidence(
        subject=freshness.subject,
        environment_reference=freshness.environment_reference,
        reason=EnvironmentInvalidationReason.ENVIRONMENT_CHANGED,
        observed_at=invalidated_at,
        source=_SOURCE,
        detail="display topology changed",
    )
    invalidated = freshness.invalidate(evidence)

    assert invalidated.state_at(invalidated_at - timedelta(microseconds=1)) is (
        EnvironmentFreshnessState.FRESH
    )
    assert invalidated.state_at(invalidated_at) is EnvironmentFreshnessState.INVALIDATED
    assert invalidated.invalidations == (evidence,)


def test_refresh_creates_new_generation_without_rewriting_old_evidence() -> None:
    original = _freshness().invalidate(
        EnvironmentInvalidationEvidence(
            subject="window:42",
            environment_reference="windows:test",
            reason=EnvironmentInvalidationReason.PROVIDER_RESTARTED,
            observed_at=_T0 + timedelta(seconds=1),
            source=_SOURCE,
        )
    )
    refreshed_at = _T0 + timedelta(seconds=2)
    refreshed = original.refresh(
        observed_at=refreshed_at,
        ttl=timedelta(seconds=5),
        source=_SOURCE,
    )

    assert original.state_at(refreshed_at) is EnvironmentFreshnessState.INVALIDATED
    assert refreshed.state_at(refreshed_at) is EnvironmentFreshnessState.FRESH
    assert refreshed.invalidations == ()
    assert refreshed.generation == 1
    assert refreshed.previous_observed_at == _T0


def test_freshness_serialization_is_deterministic_and_round_trips() -> None:
    evidence = EnvironmentInvalidationEvidence(
        subject="window:42",
        environment_reference="windows:test",
        reason=EnvironmentInvalidationReason.EXPLICIT_INVALIDATION,
        observed_at=_T0 + timedelta(seconds=1),
        source=_SOURCE,
        detail="explicit stale evidence",
    )
    freshness = _freshness().invalidate(evidence)
    encoded = freshness.to_json()

    decoded = EnvironmentFreshness.from_json(encoded)
    assert decoded == freshness
    assert decoded.to_json() == encoded
