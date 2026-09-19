"""M16 privacy/data-retention acceptance on real canonical SQLite state."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentx.core.episodes import EpisodeOutcome, EpisodeRecord
from agentx.core.knowledge import KnowledgeRecord, KnowledgeType
from agentx.infrastructure.episode_store import EpisodeStore
from agentx.infrastructure.knowledge_store import KnowledgeStore
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.privacy import (
    PrivacyRetentionController,
    PrivacyRetentionError,
    RetentionCategory,
    ScopedPrivacyFiles,
)


def test_expiry_and_delete_survive_restart_without_payload_leak(tmp_path: Path) -> None:
    path = tmp_path / "privacy.sqlite3"
    database = SQLiteDatabase(path)
    knowledge_store = KnowledgeStore(database)
    episode_store = EpisodeStore(database)
    secret = "SECRET_SHOULD_NOT_APPEAR_IN_REPORT"
    old_at = datetime(2020, 1, 1, tzinfo=UTC)
    new_at = datetime(2030, 1, 1, tzinfo=UTC)

    old = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content=f"old {secret}",
        created_at=old_at,
    )
    recent = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="recent",
        created_at=new_at,
    )
    knowledge_store.insert(old)
    knowledge_store.insert(recent)
    episode = EpisodeRecord.create(
        outcome=EpisodeOutcome.SUCCEEDED,
        summary=f"episode {secret}",
        created_at=old_at,
    )
    episode_store.append(episode)

    controller = PrivacyRetentionController(database)
    expired = controller.expire_before(
        datetime(2025, 1, 1, tzinfo=UTC),
        (RetentionCategory.KNOWLEDGE,),
    )
    assert expired.total_deleted_rows == 1
    assert secret not in repr(expired)

    reopened = SQLiteDatabase(path)
    assert KnowledgeStore(reopened).get(old.knowledge_id) is None
    assert KnowledgeStore(reopened).get(recent.knowledge_id) == recent

    deleted = PrivacyRetentionController(reopened).delete_all((RetentionCategory.EPISODES,))
    assert deleted.total_deleted_rows == 1
    assert secret not in repr(deleted)
    with reopened.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM agentx_episodes").fetchone()[0] == 0


def test_scoped_file_privacy_deletion_rejects_traversal(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    extension_metadata = root / "extensions.json"
    extension_metadata.write_text('{"provenance": "sensitive"}', encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")

    files = ScopedPrivacyFiles(root)
    assert files.delete("extensions.json") is True
    assert not extension_metadata.exists()
    assert files.delete("extensions.json") is False

    with pytest.raises(PrivacyRetentionError, match="escapes"):
        files.delete("../outside.txt")
    assert outside.read_text(encoding="utf-8") == "keep"


def test_retention_cutoff_must_be_timezone_aware(tmp_path: Path) -> None:
    controller = PrivacyRetentionController(SQLiteDatabase(tmp_path / "privacy.sqlite3"))
    with pytest.raises(PrivacyRetentionError, match="timezone-aware"):
        controller.expire_before(
            datetime(2025, 1, 1),
            (RetentionCategory.KNOWLEDGE,),
        )
