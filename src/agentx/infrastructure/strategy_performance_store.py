"""SQLite-backed durable storage for canonical strategy-performance records.

A8.01 persists the measured history of execution-strategy performance as inert
data. StrategyPerformanceStore appends, reads, and deterministically queries
canonical :class:`~agentx.core.strategy_performance.StrategyPerformanceRecord`
values; it never selects a strategy, changes routing, implements bandits/RL,
caches results, optimizes costs, verifies outcomes, or grants authority.

Append-only semantics: rows are never updated or deleted, ``record_id`` is
globally unique, and each append returns its committed durable sequence.
Aggregation is provided as a convenience over the same deterministic filters
as :meth:`read`, delegating the statistics themselves to the pure core
primitives in ``agentx.core.strategy_performance`` so consumers and later
A8.02+ tasks can aggregate without depending on this adapter.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from agentx.core.causal_experience import CausalOutcome
from agentx.core.ids import (
    CapabilityId,
    EpisodeId,
    ProcedureId,
    StrategyPerformanceId,
    TaskId,
)
from agentx.core.strategy_performance import (
    ExecutionStrategyLevel,
    StrategyAggregationDimension,
    StrategyPerformanceAggregate,
    StrategyPerformanceGroup,
    StrategyPerformanceRecord,
    StrategyPerformanceValidationError,
    aggregate_strategy_records,
    group_strategy_records,
)
from agentx.infrastructure.persistence import PersistenceError, SQLiteDatabase, transaction

_STRATEGY_PERFORMANCE_TABLE: Final = "agentx_strategy_performance"


class StrategyPerformanceStoreError(PersistenceError):
    """Base error for durable StrategyPerformanceStore operations."""


class DuplicateStrategyPerformanceError(StrategyPerformanceStoreError):
    """Raised when a record_id already exists in the append-only store."""


class StrategyPerformanceStoreStorageError(StrategyPerformanceStoreError):
    """Raised when SQLite cannot complete a StrategyPerformanceStore statement."""


class CorruptStrategyPerformanceError(StrategyPerformanceStoreError):
    """Raised when a persisted row cannot reconstruct canonical data."""

    def __init__(self, *, sequence: int, record_id: str) -> None:
        self.sequence = sequence
        self.record_id = record_id
        super().__init__(
            f"Strategy-performance row sequence {sequence} for record_id {record_id!r} is corrupt"
        )


@dataclass(frozen=True, slots=True)
class StrategyPerformanceEntry:
    """One persisted strategy-performance record paired with its durable sequence."""

    sequence: int
    record: StrategyPerformanceRecord


@dataclass(frozen=True, slots=True)
class StrategyPerformanceStore:
    """Append and deterministically query measured strategy-performance history."""

    database: SQLiteDatabase

    def append(self, record: StrategyPerformanceRecord) -> int:
        """Atomically append one record and return its committed durable sequence."""
        if not isinstance(record, StrategyPerformanceRecord):
            raise TypeError(
                f"record must be a canonical StrategyPerformanceRecord, got {type(record).__name__}"
            )

        record_json = record.to_json()
        procedure_id = None if record.procedure_id is None else record.procedure_id.to_str()
        capability_id = None if record.capability_id is None else record.capability_id.to_str()
        task_id = None if record.task_id is None else record.task_id.to_str()
        episode_id = None if record.episode_id is None else record.episode_id.to_str()
        correlation_id = None if record.correlation_id is None else str(record.correlation_id)

        with self.database.connection() as connection:
            try:
                with transaction(connection):
                    cursor = connection.execute(
                        f"""
                        INSERT INTO {_STRATEGY_PERFORMANCE_TABLE}
                            (record_id, level, outcome, strategy_name, procedure_id,
                             capability_id, task_id, episode_id, correlation_id,
                             record_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.record_id.to_str(),
                            record.level.value,
                            record.outcome.value,
                            record.strategy_name,
                            procedure_id,
                            capability_id,
                            task_id,
                            episode_id,
                            correlation_id,
                            record_json,
                        ),
                    )
                    sequence = cursor.lastrowid
                    if sequence is None:
                        raise StrategyPerformanceStoreStorageError(
                            "SQLite did not return a sequence for the appended record"
                        )
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_CONSTRAINT_UNIQUE:
                    raise DuplicateStrategyPerformanceError(
                        f"Record {record.record_id} already exists in the StrategyPerformanceStore"
                    ) from exc
                raise StrategyPerformanceStoreStorageError(
                    f"Unable to append record {record.record_id}"
                ) from exc
            except sqlite3.Error as exc:
                raise StrategyPerformanceStoreStorageError(
                    f"Unable to append record {record.record_id}"
                ) from exc

        return sequence

    def get(self, record_id: StrategyPerformanceId) -> StrategyPerformanceRecord | None:
        """Return one record by canonical identity, or None when it is absent."""
        if not isinstance(record_id, StrategyPerformanceId):
            raise TypeError("record_id must be a StrategyPerformanceId")

        with self.database.connection() as connection:
            try:
                row = connection.execute(
                    f"""
                    SELECT sequence, record_id, level, outcome, strategy_name,
                           procedure_id, capability_id, task_id, episode_id,
                           correlation_id, record_json
                    FROM {_STRATEGY_PERFORMANCE_TABLE}
                    WHERE record_id = ?
                    """,
                    (record_id.to_str(),),
                ).fetchone()
            except sqlite3.Error as exc:
                raise StrategyPerformanceStoreStorageError(
                    f"Unable to get record {record_id}"
                ) from exc

        if row is None:
            return None
        return _decode_row(row).record

    def read(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        level: ExecutionStrategyLevel | None = None,
        outcome: CausalOutcome | None = None,
        strategy_name: str | None = None,
        procedure_id: ProcedureId | None = None,
        capability_id: CapabilityId | None = None,
        task_id: TaskId | None = None,
        episode_id: EpisodeId | None = None,
        correlation_id: UUID | None = None,
    ) -> tuple[StrategyPerformanceEntry, ...]:
        """Read history in ascending durable sequence order with exact filters.

        ``after_sequence`` is exclusive. Filters are exact canonical-field
        equalities; ``strategy_name`` matching is exact and trimmed (an empty
        or un-trimmed filter is rejected rather than silently broadened).
        Measured facts (cost, latency) are not SQL-filterable; select the
        population with :meth:`read` and summarize with the pure core
        aggregation primitives (or :meth:`aggregate`).
        """
        _validate_read_boundary(after_sequence=after_sequence, limit=limit)
        _validate_optional_level(level)
        _validate_optional_outcome(outcome)
        strategy_name = _validate_optional_filter_label(strategy_name)
        _validate_optional_procedure_id_filter(procedure_id)
        _validate_optional_capability_id_filter(capability_id)
        _validate_optional_task_id_filter(task_id)
        _validate_optional_episode_id_filter(episode_id)
        _validate_optional_correlation_id(correlation_id)

        clauses = ["sequence > ?"]
        parameters: list[int | str] = [after_sequence]
        if level is not None:
            clauses.append("level = ?")
            parameters.append(level.value)
        if outcome is not None:
            clauses.append("outcome = ?")
            parameters.append(outcome.value)
        if strategy_name is not None:
            clauses.append("strategy_name = ?")
            parameters.append(strategy_name)
        if procedure_id is not None:
            clauses.append("procedure_id = ?")
            parameters.append(procedure_id.to_str())
        if capability_id is not None:
            clauses.append("capability_id = ?")
            parameters.append(capability_id.to_str())
        if task_id is not None:
            clauses.append("task_id = ?")
            parameters.append(task_id.to_str())
        if episode_id is not None:
            clauses.append("episode_id = ?")
            parameters.append(episode_id.to_str())
        if correlation_id is not None:
            clauses.append("correlation_id = ?")
            parameters.append(str(correlation_id))

        sql = (
            "SELECT sequence, record_id, level, outcome, strategy_name, procedure_id, "
            "capability_id, task_id, episode_id, correlation_id, record_json "
            f"FROM {_STRATEGY_PERFORMANCE_TABLE} WHERE {' AND '.join(clauses)} "
            "ORDER BY sequence ASC"
        )
        if limit is not None:
            sql = f"{sql} LIMIT ?"
            parameters.append(limit)

        with self.database.connection() as connection:
            try:
                rows = connection.execute(sql, tuple(parameters)).fetchall()
            except sqlite3.Error as exc:
                raise StrategyPerformanceStoreStorageError(
                    "Unable to read StrategyPerformanceStore history"
                ) from exc

        return tuple(_decode_row(row) for row in rows)

    def aggregate(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        level: ExecutionStrategyLevel | None = None,
        outcome: CausalOutcome | None = None,
        strategy_name: str | None = None,
        procedure_id: ProcedureId | None = None,
        capability_id: CapabilityId | None = None,
        task_id: TaskId | None = None,
        episode_id: EpisodeId | None = None,
        correlation_id: UUID | None = None,
    ) -> StrategyPerformanceAggregate:
        """Aggregate the deterministic :meth:`read` selection of this store.

        A convenience over :meth:`read` + the pure core aggregation
        primitive; the statistics themselves are computed by
        ``agentx.core.strategy_performance``.
        """
        entries = self.read(
            after_sequence=after_sequence,
            limit=limit,
            level=level,
            outcome=outcome,
            strategy_name=strategy_name,
            procedure_id=procedure_id,
            capability_id=capability_id,
            task_id=task_id,
            episode_id=episode_id,
            correlation_id=correlation_id,
        )
        return aggregate_strategy_records(entry.record for entry in entries)

    def aggregate_grouped(
        self,
        *,
        dimension: StrategyAggregationDimension,
        after_sequence: int = 0,
        limit: int | None = None,
        level: ExecutionStrategyLevel | None = None,
        outcome: CausalOutcome | None = None,
        strategy_name: str | None = None,
        procedure_id: ProcedureId | None = None,
        capability_id: CapabilityId | None = None,
        task_id: TaskId | None = None,
        episode_id: EpisodeId | None = None,
        correlation_id: UUID | None = None,
    ) -> tuple[StrategyPerformanceGroup, ...]:
        """Group the deterministic :meth:`read` selection along one dimension."""
        if not isinstance(dimension, StrategyAggregationDimension):
            raise TypeError(
                f"dimension must be a StrategyAggregationDimension, got {type(dimension).__name__}"
            )
        entries = self.read(
            after_sequence=after_sequence,
            limit=limit,
            level=level,
            outcome=outcome,
            strategy_name=strategy_name,
            procedure_id=procedure_id,
            capability_id=capability_id,
            task_id=task_id,
            episode_id=episode_id,
            correlation_id=correlation_id,
        )
        return group_strategy_records((entry.record for entry in entries), dimension=dimension)


def _validate_read_boundary(*, after_sequence: int, limit: int | None) -> None:
    if (
        not isinstance(after_sequence, int)
        or isinstance(after_sequence, bool)
        or after_sequence < 0
    ):
        raise ValueError("after_sequence must be a non-negative integer")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
        raise ValueError("limit must be a positive integer or None")


def _validate_optional_level(level: ExecutionStrategyLevel | None) -> None:
    if level is not None and not isinstance(level, ExecutionStrategyLevel):
        raise TypeError("level must be an ExecutionStrategyLevel or None")


def _validate_optional_outcome(outcome: CausalOutcome | None) -> None:
    if outcome is not None and not isinstance(outcome, CausalOutcome):
        raise TypeError("outcome must be a CausalOutcome or None")


def _validate_optional_filter_label(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("strategy_name must be non-empty and trimmed when provided")
    return value


def _validate_optional_procedure_id_filter(value: ProcedureId | None) -> None:
    if value is not None and not isinstance(value, ProcedureId):
        raise TypeError("procedure_id must be a ProcedureId or None")


def _validate_optional_capability_id_filter(value: CapabilityId | None) -> None:
    if value is not None and not isinstance(value, CapabilityId):
        raise TypeError("capability_id must be a CapabilityId or None")


def _validate_optional_task_id_filter(value: TaskId | None) -> None:
    if value is not None and not isinstance(value, TaskId):
        raise TypeError("task_id must be a TaskId or None")


def _validate_optional_episode_id_filter(value: EpisodeId | None) -> None:
    if value is not None and not isinstance(value, EpisodeId):
        raise TypeError("episode_id must be an EpisodeId or None")


def _validate_optional_correlation_id(value: UUID | None) -> None:
    if value is None:
        return
    if not isinstance(value, UUID):
        raise TypeError("correlation_id must be a UUID or None")
    if value.int == 0:
        raise ValueError("correlation_id must not be the nil UUID")


def _decode_row(row: sqlite3.Row) -> StrategyPerformanceEntry:
    sequence_raw = row["sequence"]
    record_id_raw = row["record_id"]
    level_raw = row["level"]
    outcome_raw = row["outcome"]
    strategy_name_raw = row["strategy_name"]
    procedure_id_raw = row["procedure_id"]
    capability_id_raw = row["capability_id"]
    task_id_raw = row["task_id"]
    episode_id_raw = row["episode_id"]
    correlation_id_raw = row["correlation_id"]
    record_json_raw = row["record_json"]

    if not isinstance(sequence_raw, int) or isinstance(sequence_raw, bool) or sequence_raw <= 0:
        raise CorruptStrategyPerformanceError(sequence=0, record_id="<invalid>")
    if not isinstance(record_id_raw, str) or not record_id_raw:
        raise CorruptStrategyPerformanceError(sequence=sequence_raw, record_id="<invalid>")
    for _label, value in (
        ("level", level_raw),
        ("outcome", outcome_raw),
        ("record_json", record_json_raw),
    ):
        if not isinstance(value, str) or not value:
            raise CorruptStrategyPerformanceError(sequence=sequence_raw, record_id=record_id_raw)
    for value in (
        strategy_name_raw,
        procedure_id_raw,
        capability_id_raw,
        task_id_raw,
        episode_id_raw,
        correlation_id_raw,
    ):
        if value is not None and (not isinstance(value, str) or not value):
            raise CorruptStrategyPerformanceError(sequence=sequence_raw, record_id=record_id_raw)

    try:
        record = StrategyPerformanceRecord.from_json(record_json_raw)
    except StrategyPerformanceValidationError as exc:
        raise CorruptStrategyPerformanceError(
            sequence=sequence_raw,
            record_id=record_id_raw,
        ) from exc

    expected_procedure_id = None if record.procedure_id is None else record.procedure_id.to_str()
    expected_capability_id = None if record.capability_id is None else record.capability_id.to_str()
    expected_task_id = None if record.task_id is None else record.task_id.to_str()
    expected_episode_id = None if record.episode_id is None else record.episode_id.to_str()
    expected_correlation_id = None if record.correlation_id is None else str(record.correlation_id)
    if (
        record.record_id.to_str() != record_id_raw
        or record.level.value != level_raw
        or record.outcome.value != outcome_raw
        or record.strategy_name != strategy_name_raw
        or expected_procedure_id != procedure_id_raw
        or expected_capability_id != capability_id_raw
        or expected_task_id != task_id_raw
        or expected_episode_id != episode_id_raw
        or expected_correlation_id != correlation_id_raw
    ):
        raise CorruptStrategyPerformanceError(sequence=sequence_raw, record_id=record_id_raw)

    return StrategyPerformanceEntry(sequence=sequence_raw, record=record)


__all__ = [
    "CorruptStrategyPerformanceError",
    "DuplicateStrategyPerformanceError",
    "StrategyPerformanceEntry",
    "StrategyPerformanceStore",
    "StrategyPerformanceStoreError",
    "StrategyPerformanceStoreStorageError",
]
