"""Regression coverage for campaign defects discovered by exact-head CI."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType

import pytest

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeType,
    ScopeDimension,
)
from agentx.core.knowledge_assurance import KnowledgeAssuranceMetadata
from agentx.core.provenance import (
    EvidenceKind,
    EvidenceReference,
)
from agentx.core.knowledge import ProvenanceKind, ProvenanceReference
from agentx.hive.scope_retrieval import (
    MAX_SCOPED_KNOWLEDGE_RESULTS,
    GlobalKnowledgePolicy,
    ScopedKnowledgeQuery,
    ScopedKnowledgeRetrieval,
    ScopedKnowledgeRetrievalError,
)
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def _freeze(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def test_assurance_from_dict_accepts_canonical_event_journal_freeze() -> None:
    record_id = KnowledgeId.create()
    metadata = KnowledgeAssuranceMetadata(
        knowledge_id=record_id,
        evidence=(
            EvidenceReference(
                kind=EvidenceKind.OBSERVATION,
                reference="observation:1",
                provenance=ProvenanceReference(
                    kind=ProvenanceKind.SYSTEM,
                    reference="verifier",
                ),
                observed_at=_T0,
            ),
        ),
    )
    frozen = _freeze(metadata.to_dict())

    assert isinstance(frozen, MappingProxyType)
    assert KnowledgeAssuranceMetadata.from_dict(frozen) == metadata


def test_scoped_retrieval_is_bounded_and_exact_scope_precedes_global(tmp_path: Path) -> None:
    store = KnowledgeStore(SQLiteDatabase((tmp_path / "agentx.sqlite3").resolve()))
    scope = KnowledgeScope({ScopeDimension.ENVIRONMENT: "prod"})
    exact = KnowledgeRecord(
        knowledge_id=KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content="exact",
        created_at=_T0,
        scope=scope,
    )
    global_record = KnowledgeRecord(
        knowledge_id=KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content="global",
        created_at=_T0,
        scope=KnowledgeScope(),
    )
    store.insert(global_record)
    store.insert(exact)

    result = ScopedKnowledgeRetrieval(store).retrieve(
        ScopedKnowledgeQuery(
            scope=scope,
            global_policy=GlobalKnowledgePolicy.INCLUDE,
            limit=1,
        )
    )

    assert result == (exact,)


@pytest.mark.parametrize("limit", [0, -1, True, MAX_SCOPED_KNOWLEDGE_RESULTS + 1])
def test_scoped_retrieval_rejects_unbounded_or_malformed_limits(limit: object) -> None:
    scope = KnowledgeScope({ScopeDimension.PROJECT: "agentx"})
    with pytest.raises(ScopedKnowledgeRetrievalError, match="limit"):
        ScopedKnowledgeQuery(scope=scope, limit=limit)  # type: ignore[arg-type]
