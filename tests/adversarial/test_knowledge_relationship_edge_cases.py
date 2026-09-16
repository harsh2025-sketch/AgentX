"""AX-120/121 relationship adversarial completion coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
    ProvenanceReference,
)
from agentx.core.knowledge_integrity import KnowledgeContradiction, KnowledgeSupersession
from agentx.infrastructure.knowledge_relationships import KnowledgeRelationshipQuery
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(SQLiteDatabase((tmp_path / "agentx.sqlite3").resolve()))


def _record(
    content: str,
    *,
    source: str,
    status: KnowledgeStatus = KnowledgeStatus.UNVERIFIED,
) -> KnowledgeRecord:
    return KnowledgeRecord(
        knowledge_id=KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content=content,
        created_at=_T0,
        status=status,
        provenance=ProvenanceReference(kind=ProvenanceKind.DOCUMENT, reference=source),
        verified_at=_T0 if status is KnowledgeStatus.VERIFIED else None,
    )


def test_verified_vs_verified_and_same_source_conflicts_preserve_both(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _record("A", source="same-source", status=KnowledgeStatus.VERIFIED)
    second = _record("not A", source="same-source", status=KnowledgeStatus.VERIFIED)
    store.insert(first)
    store.insert(second)
    store.record_contradiction(
        KnowledgeContradiction(
            first_knowledge_id=first.knowledge_id,
            second_knowledge_id=second.knowledge_id,
        )
    )

    view = KnowledgeRelationshipQuery(store).contradiction_views(first.knowledge_id)[0]

    assert {view.first.content, view.second.content} == {"A", "not A"}
    assert view.first.status is KnowledgeStatus.VERIFIED
    assert view.second.status is KnowledgeStatus.VERIFIED
    assert view.first.provenance is not None
    assert view.second.provenance is not None
    assert view.first.provenance.reference == view.second.provenance.reference == "same-source"


def test_contradiction_and_supersession_can_coexist_without_relationship_collapse(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    old = _record("old", source="source-old")
    replacement = _record("new", source="source-new")
    conflicting = _record("new is false", source="source-third")
    for record in (old, replacement, conflicting):
        store.insert(record)
    supersession = KnowledgeSupersession(
        replacement_knowledge_id=replacement.knowledge_id,
        superseded_knowledge_id=old.knowledge_id,
    )
    contradiction = KnowledgeContradiction(
        first_knowledge_id=replacement.knowledge_id,
        second_knowledge_id=conflicting.knowledge_id,
    )
    store.apply_supersession(supersession)
    store.record_contradiction(contradiction)

    query = KnowledgeRelationshipQuery(store)
    contradictions = query.contradiction_views(replacement.knowledge_id)
    supersessions = query.supersession_views(replacement.knowledge_id)

    assert tuple(item.relation for item in contradictions) == (contradiction,)
    assert tuple(item.relation for item in supersessions) == (supersession,)
    historical = store.get(old.knowledge_id)
    assert historical is not None and historical.status is KnowledgeStatus.SUPERSEDED
    assert store.get(conflicting.knowledge_id) == conflicting
