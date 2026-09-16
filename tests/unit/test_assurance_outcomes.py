"""AX-123 explicit outcome coverage."""

from __future__ import annotations

from datetime import UTC, datetime

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import ProvenanceKind, ProvenanceReference
from agentx.core.knowledge_assurance import (
    KnowledgeAssuranceMetadata,
    KnowledgeConfidenceState,
    KnowledgeRevalidation,
    KnowledgeRevalidationOutcome,
    assurance_after_revalidation,
)
from agentx.core.provenance import EvidenceKind, EvidenceReference

_T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def _evidence(reference: str) -> EvidenceReference:
    return EvidenceReference(
        kind=EvidenceKind.OBSERVATION,
        reference=reference,
        provenance=ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="verifier"),
        observed_at=_T0,
    )


def _result(
    knowledge_id: KnowledgeId,
    outcome: KnowledgeRevalidationOutcome,
    *,
    related: KnowledgeId | None = None,
) -> KnowledgeRevalidation:
    from uuid import uuid4

    return KnowledgeRevalidation(
        revalidation_id=uuid4(),
        knowledge_id=knowledge_id,
        requested_at=_T0,
        completed_at=_T0,
        source="verifier",
        outcome=outcome,
        evidence=(_evidence(outcome.value),),
        related_knowledge_id=related,
    )


def test_contradiction_and_superseding_outcomes_are_distinct() -> None:
    identity = KnowledgeId.create()
    other = KnowledgeId.create()
    current = KnowledgeAssuranceMetadata(knowledge_id=identity)

    conflicted = assurance_after_revalidation(
        current,
        _result(identity, KnowledgeRevalidationOutcome.CONTRADICTION_DISCOVERED, related=other),
    )
    superseded = assurance_after_revalidation(
        current,
        _result(
            identity,
            KnowledgeRevalidationOutcome.SUPERSEDING_EVIDENCE_DISCOVERED,
            related=other,
        ),
    )

    assert conflicted.confidence_state is KnowledgeConfidenceState.CONFLICTED
    assert conflicted.contradiction_count == 1
    assert conflicted.superseded is False
    assert superseded.confidence_state is KnowledgeConfidenceState.SUPERSEDED
    assert superseded.superseded is True
    assert superseded.contradiction_count == 0
