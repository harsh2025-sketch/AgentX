"""Durable SQLite storage for M12 scheduled and proactive work."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from agentx.core.scheduling import (
    BackgroundRunStatus,
    ScheduledTask,
    ScheduledTaskIntent,
    ScheduleKind,
    ScheduleStatus,
    ScheduleValidationError,
    next_recurring_due,
)
from agentx.core.tasks import Task
from agentx.infrastructure.persistence import PersistenceError, SQLiteDatabase


class ScheduleStoreError(PersistenceError):
    """Base durable M12 storage error."""


class DuplicateScheduleError(ScheduleStoreError):
    """A schedule identity already exists."""


class ScheduleNotFoundError(ScheduleStoreError):
    """The requested schedule or run does not exist."""


class ScheduleConflictError(ScheduleStoreError):
    """Concurrent state no longer matches the attempted transition."""


class CorruptScheduleError(ScheduleStoreError):
    """Persisted schedule data failed closed validation."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ScheduleClaim:
    schedule: ScheduledTask
    dispatch_id: UUID
    claimed_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class BackgroundRunRecord:
    dispatch_id: UUID
    schedule_id: UUID | None
    intent: ScheduledTaskIntent
    status: BackgroundRunStatus
    created_at: datetime
    updated_at: datetime
    task: Task | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class QuotaUsage:
    window_start: datetime
    resource_units: int
    model_calls: int
    machine_actions: int


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _fmt(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    text = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    return _utc(datetime.fromisoformat(text))


@contextmanager
def _write(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    if connection.in_transaction:
        raise ScheduleStoreError("nested transaction")
    try:
        connection.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as exc:
        raise ScheduleStoreError(f"unable to begin schedule-store transaction: {exc}") from exc
    try:
        yield connection
    except BaseException:
        try:
            connection.rollback()
        except sqlite3.Error as rollback_error:
            raise ScheduleStoreError(
                f"schedule-store rollback failed: {rollback_error}"
            ) from rollback_error
        raise
    else:
        try:
            connection.commit()
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except sqlite3.Error as rollback_error:
                raise ScheduleStoreError(
                    f"schedule-store commit and rollback failed: {rollback_error}"
                ) from rollback_error
            raise ScheduleStoreError(f"schedule-store commit failed: {exc}") from exc


@dataclass(frozen=True, slots=True)
class ScheduledTaskStore:
    database: SQLiteDatabase

    def create(self, schedule: ScheduledTask) -> UUID:
        if not isinstance(schedule, ScheduledTask):
            raise TypeError("schedule must be ScheduledTask")
        try:
            with self.database.connection() as conn, _write(conn):
                conn.execute(
                    "INSERT INTO agentx_scheduled_tasks "
                    "(schedule_id,status,next_due_utc,record_json) VALUES (?,?,?,?)",
                    (
                        str(schedule.schedule_id),
                        schedule.status.value,
                        _fmt(schedule.next_due_at),
                        schedule.to_json(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateScheduleError(str(schedule.schedule_id)) from exc
        return schedule.schedule_id

    def get(self, schedule_id: UUID) -> ScheduledTask | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT schedule_id,status,next_due_utc,record_json "
                "FROM agentx_scheduled_tasks WHERE schedule_id=?",
                (str(schedule_id),),
            ).fetchone()
        return None if row is None else _decode_schedule(row)

    def list(self) -> tuple[ScheduledTask, ...]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT schedule_id,status,next_due_utc,record_json "
                "FROM agentx_scheduled_tasks ORDER BY next_due_utc,schedule_id"
            ).fetchall()
        return tuple(_decode_schedule(row) for row in rows)

    def cancel(self, schedule_id: UUID, *, reason: str = "cancelled") -> ScheduledTask:
        current = self.get(schedule_id)
        if current is None:
            raise ScheduleNotFoundError(str(schedule_id))
        if current.status in {
            ScheduleStatus.COMPLETED,
            ScheduleStatus.FAILED,
            ScheduleStatus.CANCELLED,
            ScheduleStatus.EXPIRED,
        }:
            return current
        updated = replace(current, status=ScheduleStatus.CANCELLED, last_error=reason)
        self._replace(current, updated)
        return updated

    def claim_due(self, *, now: datetime, limit: int = 16) -> tuple[ScheduleClaim, ...]:
        moment = _utc(now)
        if type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError("limit must be 1..256")
        claims: list[ScheduleClaim] = []
        with self.database.connection() as conn, _write(conn):
            rows = conn.execute(
                "SELECT schedule_id,status,next_due_utc,record_json "
                "FROM agentx_scheduled_tasks "
                "WHERE status=? AND next_due_utc<=? "
                "ORDER BY next_due_utc,schedule_id LIMIT ?",
                (ScheduleStatus.ENABLED.value, _fmt(moment), limit),
            ).fetchall()
            for row in rows:
                current = _decode_schedule(row)
                if current.expires_at is not None and moment >= current.expires_at:
                    expired = replace(
                        current,
                        status=ScheduleStatus.EXPIRED,
                        last_error="schedule expired before dispatch",
                    )
                    _update(conn, current, expired)
                    continue
                dispatch_id = uuid4()
                claimed = replace(
                    current,
                    status=ScheduleStatus.DISPATCHING,
                    last_dispatch_id=dispatch_id,
                    last_error=None,
                )
                if _update(conn, current, claimed):
                    claims.append(
                        ScheduleClaim(
                            schedule=claimed,
                            dispatch_id=dispatch_id,
                            claimed_at=moment,
                        )
                    )
        return tuple(claims)

    def claim_one(self, schedule_id: UUID, *, now: datetime) -> ScheduleClaim | None:
        moment = _utc(now)
        with self.database.connection() as conn, _write(conn):
            row = conn.execute(
                "SELECT schedule_id,status,next_due_utc,record_json "
                "FROM agentx_scheduled_tasks WHERE schedule_id=?",
                (str(schedule_id),),
            ).fetchone()
            if row is None:
                raise ScheduleNotFoundError(str(schedule_id))
            current = _decode_schedule(row)
            if current.status is not ScheduleStatus.ENABLED or current.next_due_at > moment:
                return None
            dispatch_id = uuid4()
            claimed = replace(
                current,
                status=ScheduleStatus.DISPATCHING,
                last_dispatch_id=dispatch_id,
                last_error=None,
            )
            if not _update(conn, current, claimed):
                return None
            return ScheduleClaim(
                schedule=claimed,
                dispatch_id=dispatch_id,
                claimed_at=moment,
            )

    def complete_claim(
        self,
        claim: ScheduleClaim,
        *,
        succeeded: bool,
        completed_at: datetime,
        error: str | None = None,
    ) -> ScheduledTask:
        moment = _utc(completed_at)
        with self.database.connection() as conn, _write(conn):
            row = conn.execute(
                "SELECT schedule_id,status,next_due_utc,record_json "
                "FROM agentx_scheduled_tasks WHERE schedule_id=?",
                (str(claim.schedule.schedule_id),),
            ).fetchone()
            if row is None:
                raise ScheduleNotFoundError(str(claim.schedule.schedule_id))
            current = _decode_schedule(row)
            if current.status is ScheduleStatus.CANCELLED:
                return current
            if (
                current.status is not ScheduleStatus.DISPATCHING
                or current.last_dispatch_id != claim.dispatch_id
            ):
                raise ScheduleConflictError("claim is no longer current")
            count = current.run_count + 1
            detail = None if succeeded else (error or "dispatch was not verified")
            if current.kind is ScheduleKind.ONE_SHOT:
                status = ScheduleStatus.COMPLETED if succeeded else ScheduleStatus.FAILED
                updated = replace(
                    current,
                    status=status,
                    run_count=count,
                    last_error=detail,
                )
            elif current.max_runs is not None and count >= current.max_runs:
                updated = replace(
                    current,
                    status=(ScheduleStatus.COMPLETED if succeeded else ScheduleStatus.FAILED),
                    run_count=count,
                    last_error=detail,
                )
            else:
                assert current.interval is not None
                updated = replace(
                    current,
                    status=ScheduleStatus.ENABLED,
                    run_count=count,
                    next_due_at=next_recurring_due(
                        previous_due=current.next_due_at,
                        interval=current.interval,
                        now=moment,
                    ),
                    last_error=detail,
                )
            if not _update(conn, current, updated):
                raise ScheduleConflictError("schedule changed concurrently")
            return updated

    def mark_reconciliation_required(
        self,
        claim: ScheduleClaim,
        *,
        at: datetime,
        reason: str,
    ) -> ScheduledTask:
        with self.database.connection() as conn, _write(conn):
            row = conn.execute(
                "SELECT schedule_id,status,next_due_utc,record_json "
                "FROM agentx_scheduled_tasks WHERE schedule_id=?",
                (str(claim.schedule.schedule_id),),
            ).fetchone()
            if row is None:
                raise ScheduleNotFoundError(str(claim.schedule.schedule_id))
            current = _decode_schedule(row)
            if current.status is ScheduleStatus.CANCELLED:
                return current
            if (
                current.status is not ScheduleStatus.DISPATCHING
                or current.last_dispatch_id != claim.dispatch_id
            ):
                raise ScheduleConflictError("claim is no longer current")
            updated = replace(
                current,
                status=ScheduleStatus.RECONCILIATION_REQUIRED,
                last_error=f"{reason} at {_fmt(at)}",
            )
            if not _update(conn, current, updated):
                raise ScheduleConflictError("schedule changed concurrently")
            return updated

    def recover_interrupted_dispatches(
        self,
        *,
        recovered_at: datetime,
    ) -> tuple[ScheduledTask, ...]:
        recovered: list[ScheduledTask] = []
        with self.database.connection() as conn, _write(conn):
            rows = conn.execute(
                "SELECT schedule_id,status,next_due_utc,record_json "
                "FROM agentx_scheduled_tasks WHERE status=? ORDER BY schedule_id",
                (ScheduleStatus.DISPATCHING.value,),
            ).fetchall()
            for row in rows:
                current = _decode_schedule(row)
                updated = replace(
                    current,
                    status=ScheduleStatus.RECONCILIATION_REQUIRED,
                    last_error=f"interrupted at {_fmt(recovered_at)}",
                )
                if _update(conn, current, updated):
                    recovered.append(updated)
        return tuple(recovered)

    def recover_interrupted_runs(
        self,
        *,
        recovered_at: datetime,
    ) -> tuple[UUID, ...]:
        recovered: list[UUID] = []
        with self.database.connection() as conn, _write(conn):
            rows = conn.execute(
                "SELECT dispatch_id FROM agentx_background_runs "
                "WHERE status IN (?,?) ORDER BY dispatch_id",
                (
                    BackgroundRunStatus.RESERVED.value,
                    BackgroundRunStatus.RUNNING.value,
                ),
            ).fetchall()
            for row in rows:
                dispatch_id = UUID(row["dispatch_id"])
                cursor = conn.execute(
                    "UPDATE agentx_background_runs "
                    "SET status=?,detail=?,updated_at_utc=? "
                    "WHERE dispatch_id=? AND status IN (?,?)",
                    (
                        BackgroundRunStatus.UNCERTAIN.value,
                        "runtime restart occurred before a terminal verified outcome",
                        _fmt(recovered_at),
                        str(dispatch_id),
                        BackgroundRunStatus.RESERVED.value,
                        BackgroundRunStatus.RUNNING.value,
                    ),
                )
                if cursor.rowcount == 1:
                    recovered.append(dispatch_id)
        return tuple(recovered)

    def reserve_run(
        self,
        *,
        dispatch_id: UUID,
        schedule_id: UUID | None,
        intent: ScheduledTaskIntent,
        created_at: datetime,
    ) -> bool:
        encoded = json.dumps(
            intent.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        try:
            with self.database.connection() as conn, _write(conn):
                conn.execute(
                    "INSERT INTO agentx_background_runs "
                    "(dispatch_id,schedule_id,status,intent_json,task_json,detail,"
                    "created_at_utc,updated_at_utc) VALUES (?,?,?,?,NULL,NULL,?,?)",
                    (
                        str(dispatch_id),
                        None if schedule_id is None else str(schedule_id),
                        BackgroundRunStatus.RESERVED.value,
                        encoded,
                        _fmt(created_at),
                        _fmt(created_at),
                    ),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def update_run(
        self,
        *,
        dispatch_id: UUID,
        status: BackgroundRunStatus,
        at: datetime,
        task: Task | None = None,
        detail: str | None = None,
    ) -> None:
        with self.database.connection() as conn, _write(conn):
            cursor = conn.execute(
                "UPDATE agentx_background_runs "
                "SET status=?,task_json=?,detail=?,updated_at_utc=? WHERE dispatch_id=?",
                (
                    status.value,
                    None if task is None else task.to_json(),
                    detail,
                    _fmt(at),
                    str(dispatch_id),
                ),
            )
            if cursor.rowcount != 1:
                raise ScheduleNotFoundError(str(dispatch_id))

    def get_run(self, dispatch_id: UUID) -> BackgroundRunRecord | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM agentx_background_runs WHERE dispatch_id=?",
                (str(dispatch_id),),
            ).fetchone()
        if row is None:
            return None
        try:
            intent_raw = json.loads(row["intent_json"])
            if not isinstance(intent_raw, dict):
                raise ValueError("intent root")
            task_raw = row["task_json"]
            return BackgroundRunRecord(
                dispatch_id=UUID(row["dispatch_id"]),
                schedule_id=(None if row["schedule_id"] is None else UUID(row["schedule_id"])),
                intent=ScheduledTaskIntent.from_dict(intent_raw),
                status=BackgroundRunStatus(row["status"]),
                created_at=_parse(row["created_at_utc"]),
                updated_at=_parse(row["updated_at_utc"]),
                task=None if task_raw is None else Task.from_json(task_raw),
                detail=row["detail"],
            )
        except (ValueError, TypeError, ScheduleValidationError) as exc:
            raise CorruptScheduleError("background run record is corrupt") from exc

    def set_policy(self, key: str, value: object) -> None:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self.database.connection() as conn, _write(conn):
            conn.execute(
                "INSERT INTO agentx_m12_policy(policy_key,value_json) VALUES (?,?) "
                "ON CONFLICT(policy_key) DO UPDATE SET value_json=excluded.value_json",
                (key, encoded),
            )

    def get_policy(self, key: str) -> object | None:
        with self.database.connection() as conn:
            row = conn.execute(
                "SELECT value_json FROM agentx_m12_policy WHERE policy_key=?", (key,)
            ).fetchone()
        if row is None:
            return None
        try:
            value: object = json.loads(row["value_json"])
            return value
        except json.JSONDecodeError as exc:
            raise CorruptScheduleError("M12 policy is corrupt") from exc

    def consume_quota(
        self,
        *,
        scope: str,
        now: datetime,
        window: timedelta,
        resource_units: int,
        model_calls: int,
        machine_actions: int,
        maxima: tuple[int, int, int],
    ) -> bool:
        if any(type(value) is not int or value < 0 for value in maxima):
            raise ValueError("quota maxima must be non-negative ints")
        moment = _utc(now)
        with self.database.connection() as conn, _write(conn):
            row = conn.execute(
                "SELECT window_start_utc,resource_units,model_calls,machine_actions "
                "FROM agentx_m12_quota WHERE quota_scope=?",
                (scope,),
            ).fetchone()
            if row is None or moment >= _parse(row["window_start_utc"]) + window:
                usage = QuotaUsage(
                    window_start=moment,
                    resource_units=0,
                    model_calls=0,
                    machine_actions=0,
                )
            else:
                usage = QuotaUsage(
                    window_start=_parse(row["window_start_utc"]),
                    resource_units=int(row["resource_units"]),
                    model_calls=int(row["model_calls"]),
                    machine_actions=int(row["machine_actions"]),
                )
            candidate = QuotaUsage(
                window_start=usage.window_start,
                resource_units=usage.resource_units + resource_units,
                model_calls=usage.model_calls + model_calls,
                machine_actions=usage.machine_actions + machine_actions,
            )
            allowed = (
                candidate.resource_units <= maxima[0]
                and candidate.model_calls <= maxima[1]
                and candidate.machine_actions <= maxima[2]
            )
            chosen = candidate if allowed else usage
            conn.execute(
                "INSERT INTO agentx_m12_quota "
                "(quota_scope,window_start_utc,resource_units,model_calls,machine_actions) "
                "VALUES (?,?,?,?,?) ON CONFLICT(quota_scope) DO UPDATE SET "
                "window_start_utc=excluded.window_start_utc,"
                "resource_units=excluded.resource_units,"
                "model_calls=excluded.model_calls,"
                "machine_actions=excluded.machine_actions",
                (
                    scope,
                    _fmt(chosen.window_start),
                    chosen.resource_units,
                    chosen.model_calls,
                    chosen.machine_actions,
                ),
            )
        return allowed

    def _replace(self, expected: ScheduledTask, updated: ScheduledTask) -> None:
        with self.database.connection() as conn, _write(conn):
            if not _update(conn, expected, updated):
                raise ScheduleConflictError("schedule changed concurrently")


def _update(
    connection: sqlite3.Connection,
    expected: ScheduledTask,
    updated: ScheduledTask,
) -> bool:
    cursor = connection.execute(
        "UPDATE agentx_scheduled_tasks SET status=?,next_due_utc=?,record_json=? "
        "WHERE schedule_id=? AND status=? AND next_due_utc=? AND record_json=?",
        (
            updated.status.value,
            _fmt(updated.next_due_at),
            updated.to_json(),
            str(expected.schedule_id),
            expected.status.value,
            _fmt(expected.next_due_at),
            expected.to_json(),
        ),
    )
    return cursor.rowcount == 1


def _decode_schedule(row: sqlite3.Row) -> ScheduledTask:
    try:
        schedule = ScheduledTask.from_json(row["record_json"])
    except (ScheduleValidationError, TypeError, ValueError) as exc:
        raise CorruptScheduleError("scheduled task record is corrupt") from exc
    if (
        row["schedule_id"] != str(schedule.schedule_id)
        or row["status"] != schedule.status.value
        or row["next_due_utc"] != _fmt(schedule.next_due_at)
    ):
        raise CorruptScheduleError("schedule index disagrees with record")
    return schedule


__all__ = [
    "BackgroundRunRecord",
    "CorruptScheduleError",
    "DuplicateScheduleError",
    "QuotaUsage",
    "ScheduleClaim",
    "ScheduleConflictError",
    "ScheduleNotFoundError",
    "ScheduleStoreError",
    "ScheduledTaskStore",
]
