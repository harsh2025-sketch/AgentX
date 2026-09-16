"""Campaign acceptance chain for AX-408/410/412/413."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agentx.core.environment_freshness import (
    EnvironmentFreshness,
    EnvironmentFreshnessState,
    EnvironmentInvalidationEvidence,
    EnvironmentInvalidationReason,
)
from agentx.core.knowledge import ProvenanceKind, ProvenanceReference
from agentx.core.world_state import WorldStateDomain, WorldStateFact, WorldStateSnapshot

_T0 = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
_SOURCE = ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="windows.observer")


def test_world_snapshot_exposes_mixed_freshness_without_hiding_stale_facts() -> None:
    stale = WorldStateFact(
        domain=WorldStateDomain.WINDOW,
        subject="window:1",
        fact_key="title",
        value="old title",
        observed_at=_T0,
        ttl=timedelta(seconds=1),
        source=_SOURCE,
    )
    fresh = WorldStateFact(
        domain=WorldStateDomain.PROCESS,
        subject="pid:10",
        fact_key="running",
        value=True,
        observed_at=_T0 + timedelta(seconds=1),
        ttl=timedelta(seconds=10),
        source=_SOURCE,
    )
    snapshot = WorldStateSnapshot(
        captured_at=_T0 + timedelta(seconds=1),
        facts=(fresh, stale),
    )
    at = _T0 + timedelta(seconds=2)

    assert snapshot.fact_count == 2
    assert not snapshot.is_fresh(at)
    assert snapshot.fresh_facts(at) == (fresh,)
    assert stale in snapshot.facts
    encoded = snapshot.to_json()
    assert WorldStateSnapshot.from_json(encoded) == snapshot
    assert WorldStateSnapshot.from_json(encoded).to_json() == encoded


def test_invalidation_evidence_composes_with_world_observation_without_authority() -> None:
    hostile_value = {
        "permission": "ADMIN",
        "risk": "R0",
        "verified": True,
        "task_success": True,
    }
    fact = WorldStateFact(
        domain=WorldStateDomain.ENVIRONMENT,
        subject="desktop:test",
        fact_key="provider.payload",
        value=hostile_value,
        observed_at=_T0,
        ttl=timedelta(seconds=30),
        source=_SOURCE,
    )
    snapshot = WorldStateSnapshot(captured_at=_T0, facts=(fact,))
    freshness = EnvironmentFreshness(
        subject="desktop:test",
        environment_reference="windows:test",
        observed_at=_T0,
        ttl=fact.ttl,
        source=_SOURCE,
    )
    invalidated = freshness.invalidate(
        EnvironmentInvalidationEvidence(
            subject=freshness.subject,
            environment_reference=freshness.environment_reference,
            reason=EnvironmentInvalidationReason.ENVIRONMENT_CHANGED,
            observed_at=_T0 + timedelta(seconds=1),
            source=_SOURCE,
            detail="display configuration changed",
        )
    )

    assert snapshot.facts[0].value["permission"] == "ADMIN"  # type: ignore[index]
    assert invalidated.state_at(_T0 + timedelta(seconds=1)) is (
        EnvironmentFreshnessState.INVALIDATED
    )
    assert fact.is_fresh(_T0 + timedelta(seconds=1))
