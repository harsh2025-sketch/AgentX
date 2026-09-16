"""Bounded durable scope retrieval refuses partial candidate universes."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentx.core.knowledge import KnowledgeRecord, KnowledgeScope, KnowledgeType, ScopeDimension
from agentx.hive.scope_retrieval import (
    ScopedKnowledgeQuery,
    ScopedKnowledgeRetrieval,
    ScopedKnowledgeRetrievalError,
)
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase


class ScopedKnowledgeScanTests(unittest.TestCase):
    def test_scan_ceiling_is_enforced_before_scope_filtering(self) -> None:
        with TemporaryDirectory() as directory:
            store = KnowledgeStore(SQLiteDatabase(Path(directory) / "knowledge.sqlite3"))
            target = KnowledgeScope({ScopeDimension.PROJECT: "target"})
            other = KnowledgeScope({ScopeDimension.PROJECT: "other"})
            wanted = KnowledgeRecord.create(
                knowledge_type=KnowledgeType.FACT, content="wanted", scope=target
            )
            store.insert(wanted)
            for number in range(3):
                store.insert(
                    KnowledgeRecord.create(
                        knowledge_type=KnowledgeType.FACT, content=str(number), scope=other
                    )
                )
            self.assertEqual(len(store.scan_records(limit=2)), 2)
            query = ScopedKnowledgeQuery(scope=target)
            with self.assertRaisesRegex(ScopedKnowledgeRetrievalError, "complete bounded"):
                ScopedKnowledgeRetrieval(store, max_scan=3).retrieve(query)
            self.assertEqual(ScopedKnowledgeRetrieval(store, max_scan=4).retrieve(query), (wanted,))
            # The canonical legacy list contract is preserved for its existing users.
            self.assertEqual(len(store.list_records()), 4)


if __name__ == "__main__":
    unittest.main()
