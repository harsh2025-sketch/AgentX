"""Serialization and validation tests for AX-122/123 contracts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import KnowledgeValidationError, ProvenanceKind, ProvenanceReference
from agentx.core.knowledge_assurance import (
    KnowledgeAssuranceMetadata,
    KnowledgeConfidenceState,
    KnowledgeRevalidation,
    KnowledgeRevalidationOutcome,
    KnowledgeRevalidationRequest,
)
from agentx.core.provenance import EvidenceKind, EvidenceReference

_T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def _evidence() -> EvidenceReference:
    return EvidenceReference(
        kind=EvidenceKind.OBSERVATION,
        reference="observation-1",
        provenance=ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="verifier"),
        observed_at=_T0,
    )


def test_assurance_json_round_trip_is_deterministic() -> None:
    metadata = KnowledgeAssuranceMetadata(
        knowledge_id=KnowledgeId.create(),
        source_observed_at=_T0,
        verification_count=2,
        failure_count=1,
        environment_valid=True,
        fresh_until=_T0 + timedelta(hours=1),
        last_verification=_T0,
        evidence=(_evidence(),),
        confidence_state=KnowledgeConfidenceState.SUPPORTED,
        contradiction_count=1,
        superseded=False,
    )

    encoded = metadata.to_json()

    assert KnowledgeAssuranceMetadata.from_json(encoded) == metadata
    assert KnowledgeAssuranceMetadata.from_json(encoded).to_json() == encoded


def test_revalidation_json_round_trip_is_deterministic() -> None:
    request = KnowledgeRevalidationRequest.create(
        knowledge_id=KnowledgeId.create(),
        source="verifier",
        requested_at=_T0,
    )
    result = KnowledgeRevalidation(
        revalidation_id=request.revalidation_id,
        knowledge_id=request.knowledge_id,
        requested_at=request.requested_at,
        completed_at=_T0 + timedelta(minutes=1),
        source=request.source,
        outcome=KnowledgeRevalidationOutcome.SUCCESS,
        evidence=(_evidence(),),
    )

    encoded = result.to_json()

    assert KnowledgeRevalidation.from_json(encoded) == result
    assert KnowledgeRevalidation.from_json(encoded).to_json() == encoded


def test_completed_revalidation_requires_attributable_evidence() -> None:
    request = KnowledgeRevalidationRequest.create(
        knowledge_id=KnowledgeId.create(),
        source="verifier",
        requested_at=_T0,
    )
    with pytest.raises(KnowledgeValidationError):
        KnowledgeRevalidation(
            revalidation_id=request.revalidation_id,
            knowledge_id=request.knowledge_id,
            requested_at=request.requested_at,
            completed_at=_T0,
            source=request.source,
            outcome=KnowledgeRevalidationOutcome.SUCCESS,
            evidence=(),
        )


def test_relationship_discovery_requires_related_record_identity() -> None:
    request = KnowledgeRevalidationRequest.create(
        knowledge_id=KnowledgeId.create(),
        source="verifier",
        requested_at=_T0,
    )
    with pytest.raises(KnowledgeValidationError):
        KnowledgeRevalidation(
            revalidation_id=request.revalidation_id,
            knowledge_id=request.knowledge_id,
            requested_at=request.requested_at,
            completed_at=_T0,
            source=request.source,
            outcome=KnowledgeRevalidationOutcome.CONTRADICTION_DISCOVERED,
            evidence=(_evidence(),),
        )
