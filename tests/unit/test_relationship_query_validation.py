"""AX-120/121 relationship query validation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import KnowledgeRecord, KnowledgeStatus, KnowledgeType
from agentx.infrastructure.knowledge_relationships import (
    KnowledgeRelationshipQuery,
    SupersessionDirection,
)
from agentx.infrastructure.knowledge_store import KnowledgeNotFoundError, KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)


def test_relationship_queries_reject_missing_target_and_bad_bound(tmp_path: Path) -> None:
    store = KnowledgeStore(SQLiteDatabase((tmp_path / "agentx.sqlite3").resolve()))
    query = KnowledgeRelationshipQuery(store)
    with pytest.raises(KnowledgeNotFoundError):
        query.contradiction_views(KnowledgeId.create())

    record = KnowledgeRecord(
        knowledge_id=KnowledgeId.create(),
        knowledge_type=KnowledgeType.FACT,
        content="claim",
        created_at=_T0,
        status=KnowledgeStatus.UNVERIFIED,
    )
    store.insert(record)
    with pytest.raises(ValueError):
        query.traverse_supersession(
            record.knowledge_id,
            direction=SupersessionDirection.HISTORY_TO_REPLACEMENT,
            max_nodes=0,
        )
