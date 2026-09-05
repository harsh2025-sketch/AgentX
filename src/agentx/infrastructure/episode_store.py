"""SQLite-backed durable storage for canonical inert EpisodeRecord values.

EpisodeStore is distinct from EventJournal: it stores selected meaningful
historical experiences and never subscribes to, publishes, replays, or executes
canonical Events. Stored history remains data only and grants no authority.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from agentx.core.episodes import EpisodeRecord, EpisodeValidationError
from agentx.core.ids import EpisodeId, TaskId
from agentx.infrastructure.persistence import PersistenceError, SQLiteDatabase, transaction

_EPISODE_TABLE: Final = "agentx_episodes"


class EpisodeStoreError(PersistenceError):
    """Base error for durable EpisodeStore operations."""


class DuplicateEpisodeError(EpisodeStoreError):
    """Raised when an episode_id already exists in the append-only store."""


class EpisodeStoreStorageError(EpisodeStoreError):
    """Raised when SQLite cannot complete an EpisodeStore statement."""


class CorruptEpisodeError(EpisodeStoreError):
    """Raised when a persisted episode row cannot reconstruct canonical data."""

    def __init__(self, *, sequence: int, episode_id: str) -> None:
        self.sequence = sequence
        self.episode_id = episode_id
        super().__init__(
            f"Episode row sequence {sequence} for episode_id {episode_id!r} is corrupt"
        )


@dataclass(frozen=True, slots=True)
class EpisodeEntry:
    """One persisted episode paired with its durable store sequence."""

    sequence: int
    episode: EpisodeRecord


@dataclass(frozen=True, slots=True)
class EpisodeStore:
    """Append and deterministically read selected historical episodes from SQLite."""

    database: SQLiteDatabase

    def append(self, episode: EpisodeRecord) -> int:
        """Atomically append one episode and return its committed durable sequence."""
        if not isinstance(episode, EpisodeRecord):
            raise TypeError("episode must be a canonical EpisodeRecord")

        episode_json = episode.to_json()
        task_id = None if episode.task_id is None else episode.task_id.to_str()
        correlation_id = None if episode.correlation_id is None else str(episode.correlation_id)

        with self.database.connection() as connection:
            try:
                with transaction(connection):
                    cursor = connection.execute(
                        f"""
                        INSERT INTO {_EPISODE_TABLE}
                            (episode_id, task_id, correlation_id, episode_json)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            episode.episode_id.to_str(),
                            task_id,
                            correlation_id,
                            episode_json,
                        ),
                    )
                    sequence = cursor.lastrowid
                    if sequence is None:
                        raise EpisodeStoreStorageError(
                            "SQLite did not return a sequence for the appended episode"
                        )
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                    raise DuplicateEpisodeError(
                        f"Episode {episode.episode_id} already exists in the EpisodeStore"
                    ) from exc
                raise EpisodeStoreStorageError(
                    f"Unable to append episode {episode.episode_id}"
                ) from exc
            except sqlite3.Error as exc:
                raise EpisodeStoreStorageError(
                    f"Unable to append episode {episode.episode_id}"
                ) from exc

        return sequence

    def get(self, episode_id: EpisodeId) -> EpisodeRecord | None:
        """Return one episode by canonical identity, or None when it is absent."""
        if not isinstance(episode_id, EpisodeId):
            raise TypeError("episode_id must be an EpisodeId")

        with self.database.connection() as connection:
            try:
                row = connection.execute(
                    f"""
                    SELECT sequence, episode_id, task_id, correlation_id, episode_json
                    FROM {_EPISODE_TABLE}
                    WHERE episode_id = ?
                    """,
                    (episode_id.to_str(),),
                ).fetchone()
            except sqlite3.Error as exc:
                raise EpisodeStoreStorageError(f"Unable to get episode {episode_id}") from exc

        if row is None:
            return None
        return _decode_row(row).episode

    def read(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        task_id: TaskId | None = None,
        correlation_id: UUID | None = None,
    ) -> tuple[EpisodeEntry, ...]:
        """Read episodes in ascending durable sequence order.

        ``after_sequence`` is exclusive. Optional filters are intentionally
        restricted to stable TaskId and correlation UUID associations.
        """
        _validate_read_boundary(after_sequence=after_sequence, limit=limit)
        _validate_optional_task_id(task_id)
        _validate_optional_correlation_id(correlation_id)

        clauses = ["sequence > ?"]
        parameters: list[int | str] = [after_sequence]
        if task_id is not None:
            clauses.append("task_id = ?")
            parameters.append(task_id.to_str())
        if correlation_id is not None:
            clauses.append("correlation_id = ?")
            parameters.append(str(correlation_id))

        sql = (
            "SELECT sequence, episode_id, task_id, correlation_id, episode_json "
            f"FROM {_EPISODE_TABLE} WHERE {' AND '.join(clauses)} "
            "ORDER BY sequence ASC"
        )
        if limit is not None:
            sql = f"{sql} LIMIT ?"
            parameters.append(limit)

        with self.database.connection() as connection:
            try:
                rows = connection.execute(sql, tuple(parameters)).fetchall()
            except sqlite3.Error as exc:
                raise EpisodeStoreStorageError("Unable to read EpisodeStore history") from exc

        return tuple(_decode_row(row) for row in rows)


def _validate_read_boundary(*, after_sequence: int, limit: int | None) -> None:
    if (
        not isinstance(after_sequence, int)
        or isinstance(after_sequence, bool)
        or after_sequence < 0
    ):
        raise ValueError("after_sequence must be a non-negative integer")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
        raise ValueError("limit must be a positive integer or None")


def _validate_optional_task_id(value: TaskId | None) -> None:
    if value is not None and not isinstance(value, TaskId):
        raise TypeError("task_id must be a TaskId or None")


def _validate_optional_correlation_id(value: UUID | None) -> None:
    if value is None:
        return
    if not isinstance(value, UUID):
        raise TypeError("correlation_id must be a UUID or None")
    if value.int == 0:
        raise ValueError("correlation_id must not be the nil UUID")


def _decode_row(row: sqlite3.Row) -> EpisodeEntry:
    sequence_raw = row["sequence"]
    episode_id_raw = row["episode_id"]
    task_id_raw = row["task_id"]
    correlation_id_raw = row["correlation_id"]
    episode_json_raw = row["episode_json"]

    if not isinstance(sequence_raw, int) or isinstance(sequence_raw, bool) or sequence_raw <= 0:
        raise CorruptEpisodeError(sequence=0, episode_id="<invalid>")
    if not isinstance(episode_id_raw, str) or not episode_id_raw:
        raise CorruptEpisodeError(sequence=sequence_raw, episode_id="<invalid>")
    if task_id_raw is not None and (not isinstance(task_id_raw, str) or not task_id_raw):
        raise CorruptEpisodeError(sequence=sequence_raw, episode_id=episode_id_raw)
    if correlation_id_raw is not None and (
        not isinstance(correlation_id_raw, str) or not correlation_id_raw
    ):
        raise CorruptEpisodeError(sequence=sequence_raw, episode_id=episode_id_raw)
    if not isinstance(episode_json_raw, str) or not episode_json_raw:
        raise CorruptEpisodeError(sequence=sequence_raw, episode_id=episode_id_raw)

    try:
        episode = EpisodeRecord.from_json(episode_json_raw)
    except EpisodeValidationError as exc:
        raise CorruptEpisodeError(
            sequence=sequence_raw,
            episode_id=episode_id_raw,
        ) from exc

    expected_task_id = None if episode.task_id is None else episode.task_id.to_str()
    expected_correlation_id = (
        None if episode.correlation_id is None else str(episode.correlation_id)
    )
    if (
        episode.episode_id.to_str() != episode_id_raw
        or expected_task_id != task_id_raw
        or expected_correlation_id != correlation_id_raw
    ):
        raise CorruptEpisodeError(sequence=sequence_raw, episode_id=episode_id_raw)

    return EpisodeEntry(sequence=sequence_raw, episode=episode)


__all__ = [
    "CorruptEpisodeError",
    "DuplicateEpisodeError",
    "EpisodeEntry",
    "EpisodeStore",
    "EpisodeStoreError",
    "EpisodeStoreStorageError",
]
