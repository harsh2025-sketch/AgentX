"""Boundedness proof for AX-121 relationship traversal."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import KnowledgeRecord, KnowledgeStatus, KnowledgeType
from agentx.core.knowledge_integrity import KnowledgeSupersession
from agentx.infrastructure.knowledge_relationships import (
    KnowledgeRelationshipQuery,
    SupersessionDirection,
)
from agentx.infrastructure.knowledge_store import CorruptKnowledgeRelationshipError, KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def _record(content: str) -> KnowledgeRecord:
    return KnowledgeRecord(
        knowledge_id=KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content=content,
        created_at=_T0,
        status=KnowledgeStatus.UNVERIFIED,
    )


def test_supersession_traversal_enforces_finite_node_bound(tmp_path: Path) -> None:
    store = KnowledgeStore(SQLiteDatabase((tmp_path / "agentx.sqlite3").resolve()))
    first, second = _record("first"), _record("second")
    store.insert(first)
    store.insert(second)
    store.apply_supersession(
        KnowledgeSupersession(
            replacement_knowledge_id=second.knowledge_id,
            superseded_knowledge_id=first.knowledge_id,
        )
    )

    with pytest.raises(CorruptKnowledgeRelationshipError):
        KnowledgeRelationshipQuery(store).traverse_supersession(
            first.knowledge_id,
            direction=SupersessionDirection.HISTORY_TO_REPLACEMENT,
            max_nodes=1,
        )
