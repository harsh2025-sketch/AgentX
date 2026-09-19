"""Explicit M16 privacy, retention, and deletion controls.

The controller operates only on canonical durable tables through the canonical
SQLite connection/transaction boundary. Content is never interpreted as
authority or returned in deletion reports. File deletion is separately scoped
to an explicit data root so M15 provenance or release telemetry files can be
removed without accepting traversal or arbitrary absolute paths.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final

from agentx.infrastructure.persistence import PersistenceError, SQLiteDatabase, transaction

__all__ = [
    "DeletionCount",
    "PrivacyDeletionReport",
    "PrivacyRetentionController",
    "PrivacyRetentionError",
    "RetentionCategory",
    "ScopedPrivacyFiles",
]


class PrivacyRetentionError(PersistenceError):
    """Raised when an explicit privacy operation cannot be completed safely."""


class RetentionCategory(StrEnum):
    """Canonical durable-data categories controlled by M16."""

    EVENTS = "events"
    KNOWLEDGE = "knowledge"
    EPISODES = "episodes"
    PROCEDURES = "procedures"
    NEGATIVE_EXPERIENCE = "negative_experience"
    ARTIFACTS = "artifacts"
    AUDIT = "audit"
    SCHEDULER_HISTORY = "scheduler_history"


@dataclass(frozen=True, slots=True)
class DeletionCount:
    """Count-only evidence for one category; payload data is never included."""

    category: RetentionCategory
    deleted_rows: int


@dataclass(frozen=True, slots=True)
class PrivacyDeletionReport:
    """Non-secret evidence from one transactional privacy deletion."""

    cutoff_utc: datetime | None
    counts: tuple[DeletionCount, ...]

    @property
    def total_deleted_rows(self) -> int:
        """Return the total rows removed across requested categories."""
        return sum(item.deleted_rows for item in self.counts)


_TABLES: Final[dict[RetentionCategory, tuple[str, str]]] = {
    RetentionCategory.EVENTS: ("agentx_event_journal", "recorded_at_utc"),
    RetentionCategory.KNOWLEDGE: ("agentx_knowledge", "created_at_utc"),
    RetentionCategory.EPISODES: ("agentx_episodes", "recorded_at_utc"),
    RetentionCategory.PROCEDURES: ("agentx_procedures", "created_at_utc"),
    RetentionCategory.NEGATIVE_EXPERIENCE: (
        "agentx_negative_experiences",
        "recorded_at_utc",
    ),
    RetentionCategory.ARTIFACTS: ("agentx_artifacts", "recorded_at_utc"),
    RetentionCategory.AUDIT: ("agentx_audit_records", "recorded_at_utc"),
    RetentionCategory.SCHEDULER_HISTORY: ("agentx_background_runs", "created_at_utc"),
}


@dataclass(frozen=True, slots=True)
class PrivacyRetentionController:
    """Apply explicit transactional expiry/deletion to canonical SQLite state."""

    database: SQLiteDatabase

    def delete_all(
        self,
        categories: Iterable[RetentionCategory],
    ) -> PrivacyDeletionReport:
        """Delete all rows in the explicitly requested data categories."""
        selected = _normalize_categories(categories)
        return self._delete(selected, cutoff=None)

    def expire_before(
        self,
        cutoff_utc: datetime,
        categories: Iterable[RetentionCategory],
    ) -> PrivacyDeletionReport:
        """Delete rows strictly older than an aware UTC cutoff."""
        if cutoff_utc.tzinfo is None or cutoff_utc.utcoffset() is None:
            raise PrivacyRetentionError("retention cutoff must be timezone-aware")
        selected = _normalize_categories(categories)
        return self._delete(selected, cutoff=cutoff_utc.astimezone(UTC))

    def _delete(
        self,
        categories: tuple[RetentionCategory, ...],
        *,
        cutoff: datetime | None,
    ) -> PrivacyDeletionReport:
        cutoff_text = None if cutoff is None else _sqlite_timestamp(cutoff)
        counts: list[DeletionCount] = []
        try:
            with self.database.connection() as connection:
                with transaction(connection):
                    for category in categories:
                        deleted = _delete_category(
                            connection,
                            category,
                            cutoff_text=cutoff_text,
                        )
                        counts.append(DeletionCount(category=category, deleted_rows=deleted))
        except sqlite3.Error as exc:
            raise PrivacyRetentionError("privacy deletion failed transactionally") from exc
        return PrivacyDeletionReport(cutoff_utc=cutoff, counts=tuple(counts))


@dataclass(frozen=True, slots=True)
class ScopedPrivacyFiles:
    """Delete privacy-sensitive local files only beneath one explicit data root."""

    data_root: Path

    def __post_init__(self) -> None:
        root = self.data_root.expanduser().resolve()
        object.__setattr__(self, "data_root", root)

    def delete(self, relative_path: str | Path) -> bool:
        """Delete one scoped file and reject absolute/traversal paths."""
        relative = Path(relative_path)
        if relative.is_absolute():
            raise PrivacyRetentionError("privacy file path must be relative to data_root")
        candidate = (self.data_root / relative).resolve()
        if not candidate.is_relative_to(self.data_root):
            raise PrivacyRetentionError("privacy file path escapes data_root")
        if not candidate.exists():
            return False
        if candidate.is_dir():
            raise PrivacyRetentionError("privacy file deletion does not recursively delete directories")
        candidate.unlink()
        return True


def _normalize_categories(
    categories: Iterable[RetentionCategory],
) -> tuple[RetentionCategory, ...]:
    selected = tuple(categories)
    if not selected:
        raise PrivacyRetentionError("at least one retention category is required")
    if any(not isinstance(item, RetentionCategory) for item in selected):
        raise PrivacyRetentionError("retention categories must use the canonical enum")
    if len(set(selected)) != len(selected):
        raise PrivacyRetentionError("retention categories must not contain duplicates")
    return tuple(sorted(selected, key=lambda item: item.value))


def _delete_category(
    connection: sqlite3.Connection,
    category: RetentionCategory,
    *,
    cutoff_text: str | None,
) -> int:
    table, timestamp_column = _TABLES[category]
    where = "" if cutoff_text is None else f" WHERE {timestamp_column} < ?"
    parameters: tuple[object, ...] = () if cutoff_text is None else (cutoff_text,)

    if category is RetentionCategory.KNOWLEDGE:
        rows = connection.execute(
            f"SELECT knowledge_id FROM {table}{where}",
            parameters,
        ).fetchall()
        identities = tuple(str(row["knowledge_id"]) for row in rows)
        if not identities:
            return 0
        placeholders = ", ".join("?" for _ in identities)
        connection.execute(
            "DELETE FROM agentx_knowledge_contradictions "
            f"WHERE first_knowledge_id IN ({placeholders}) "
            f"OR second_knowledge_id IN ({placeholders})",
            (*identities, *identities),
        )
        connection.execute(
            "DELETE FROM agentx_knowledge_supersessions "
            f"WHERE replacement_knowledge_id IN ({placeholders}) "
            f"OR superseded_knowledge_id IN ({placeholders})",
            (*identities, *identities),
        )
        connection.execute(
            f"DELETE FROM {table} WHERE knowledge_id IN ({placeholders})",
            identities,
        )
        return len(identities)

    row = connection.execute(
        f"SELECT COUNT(*) AS count FROM {table}{where}",
        parameters,
    ).fetchone()
    count = 0 if row is None else int(row["count"])
    if count:
        connection.execute(f"DELETE FROM {table}{where}", parameters)
    return count


def _sqlite_timestamp(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")
