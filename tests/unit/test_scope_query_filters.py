"""AX-124 exact structured filter coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
    ProvenanceReference,
    ScopeDimension,
)
from agentx.hive.scope_retrieval import ScopedKnowledgeQuery, ScopedKnowledgeRetrieval
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def test_structured_filters_conjoin_inside_exact_scope(tmp_path: Path) -> None:
    store = KnowledgeStore(SQLiteDatabase((tmp_path / "agentx.sqlite3").resolve()))
    scope = KnowledgeScope({ScopeDimension.PROJECT: "agentx"})
    wanted = KnowledgeRecord(
        knowledge_id=KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content="wanted",
        created_at=_T0,
        status=KnowledgeStatus.VERIFIED,
        scope=scope,
        provenance=ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="verifier"),
        verified_at=_T0,
    )
    wrong_type = KnowledgeRecord(
        knowledge_id=KnowledgeId.create(),
        knowledge_type=KnowledgeType.OBSERVATION,
        content="wrong type",
        created_at=_T0,
        scope=scope,
    )
    store.insert(wanted)
    store.insert(wrong_type)

    result = ScopedKnowledgeRetrieval(store).retrieve(
        ScopedKnowledgeQuery(
            scope=scope,
            knowledge_types=frozenset({KnowledgeType.FACT}),
            statuses=frozenset({KnowledgeStatus.VERIFIED}),
            provenance_kinds=frozenset({ProvenanceKind.SYSTEM}),
        )
    )

    assert result == (wanted,)
