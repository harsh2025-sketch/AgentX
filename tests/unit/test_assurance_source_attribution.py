"""AX-122 evidence-source attribution proof."""

from __future__ import annotations

from datetime import UTC, datetime

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import ProvenanceKind, ProvenanceReference
from agentx.core.knowledge_assurance import KnowledgeAssuranceMetadata
from agentx.core.provenance import EvidenceKind, EvidenceReference


def test_assurance_preserves_evidence_source_identity_and_observation_time() -> None:
    observed = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
    evidence = EvidenceReference(
        kind=EvidenceKind.ARTIFACT,
        reference="artifact-1",
        provenance=ProvenanceReference(
            kind=ProvenanceKind.REPOSITORY,
            reference="repo:commit:abc",
        ),
        observed_at=observed,
    )
    metadata = KnowledgeAssuranceMetadata(
        knowledge_id=KnowledgeId.create(),
        source_observed_at=observed,
        evidence=(evidence,),
    )

    restored = KnowledgeAssuranceMetadata.from_json(metadata.to_json())
    assert restored.evidence == (evidence,)
    assert restored.evidence[0].provenance.reference == "repo:commit:abc"
    assert restored.evidence[0].observed_at == observed
