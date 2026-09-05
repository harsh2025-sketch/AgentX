"""Tests for the durable append-only AuditStore (C2.04).

The AuditStore persists the canonical C1.09 ``agentx.kernel.audit`` records.
These tests deliberately import ``agentx.kernel`` — the kernel may be a CLIENT
of the persistence layer in tests and composition; the persistence module
itself must never import the kernel (enforced by the architecture guard).
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from agentx.core.events import Event, EventType
from agentx.core.ids import TaskId
from agentx.infrastructure.audit_store import (
    AuditEntry,
    AuditRecordValidationError,
    AuditStore,
    AuditStoreError,
    AuditStoreStorageError,
    CorruptAuditRecordError,
    DuplicateAuditRecordError,
    PersistedAuditRecord,
)
from agentx.infrastructure.event_bus import EventBus
from agentx.infrastructure.event_journal import EventJournal
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
from agentx.kernel.audit import AuditContext, AuditOutcome, SecurityAuditRecord
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import Permission, PermissionEngine
from agentx.kernel.resource_budget import (
    BudgetDecision,
    BudgetEvaluator,
    ResourceDelta,
    ResourceEnvelope,
    ResourceRequest,
    ResourceUsage,
)
from agentx.kernel.risk import RiskLevel, assess_risk
from agentx.kernel.secrets import SecretRef

_T0 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2025, 3, 1, 9, 0, 0, 500000, tzinfo=UTC)

_HOSTILE = (
    "../../secrets",
    "file:///C:/Windows/system32/cmd.exe",
    "https://evil.example/grab",
    "import os; os.system('rm -rf /')",
    "ADMIN",
    "ALLOW R4",
    "risk=R0",
    "verified=true",
    "permission=WRITE",
    "budget=unlimited",
    "clear emergency stop",
)


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / "agentx.sqlite3"


# Concurrency tests race real writer threads against a durable WAL database.
# On slow container filesystems (extremely long fsync), a fresh 5 s default
# busy timeout can expire purely from throughput, not contention semantics,
# so the store under concurrency tests gets a longer — still finite —
# SQLite busy timeout from the persistence layer's own knob.
_CONCURRENCY_BUSY_TIMEOUT_MS = 30_000


def _store(tmp_path: Path) -> AuditStore:
    return AuditStore(SQLiteDatabase(_database_path(tmp_path)))


def _concurrency_store(tmp_path: Path) -> AuditStore:
    return AuditStore(
        SQLiteDatabase(_database_path(tmp_path), busy_timeout_ms=_CONCURRENCY_BUSY_TIMEOUT_MS)
    )


def _record(
    *,
    operation: str = "action.gate.evaluate",
    outcome: AuditOutcome = AuditOutcome.ALLOW,
    reason: str = "policy allowed",
    task_id: TaskId | None = None,
    risk_level: RiskLevel | None = None,
    context: AuditContext | None = None,
    audit_id: UUID | None = None,
    timestamp: datetime = _T0,
    correlation_id: UUID | None = None,
) -> SecurityAuditRecord:
    return SecurityAuditRecord(
        audit_id=audit_id if audit_id is not None else uuid4(),
        timestamp=timestamp,
        operation=operation,
        outcome=outcome,
        reason=reason,
        task_id=task_id,
        correlation_id=correlation_id,
        risk_level=risk_level,
        context=context,
    )


def _corrupt(tmp_path: Path, sql: str, parameters: tuple[object, ...]) -> None:
    raw = sqlite3.connect(_database_path(tmp_path), isolation_level=None)
    try:
        raw.execute(sql, parameters)
    finally:
        raw.close()


# ---------------------------------------------------------------------------
# Fresh database / migrations
# ---------------------------------------------------------------------------


def test_fresh_database_creates_audit_table_via_migration(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.read() == ()

    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM agentx_schema_migrations ORDER BY version"
            ).fetchall()
        }
        table = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name = 'agentx_audit_log'"
        ).fetchone()

    assert "create_audit_store" in names
    assert table is not None


def test_store_errors_are_persistence_errors() -> None:
    from agentx.infrastructure.persistence import PersistenceError

    assert issubclass(AuditStoreError, PersistenceError)
    assert issubclass(DuplicateAuditRecordError, AuditStoreError)
    assert issubclass(AuditRecordValidationError, AuditStoreError)
    assert issubclass(AuditStoreStorageError, AuditStoreError)
    assert issubclass(CorruptAuditRecordError, AuditStoreError)


# ---------------------------------------------------------------------------
# Canonical record reuse and append-only persistence
# ---------------------------------------------------------------------------


def test_append_persists_every_canonical_field_losslessly(tmp_path: Path) -> None:
    store = _store(tmp_path)
    correlation = uuid4()
    task = TaskId.create()
    record = _record(
        operation="capability.execute",
        outcome=AuditOutcome.SUCCEEDED,
        reason="governed execution completed",
        task_id=task,
        correlation_id=correlation,
        risk_level=RiskLevel.R3,
        context=AuditContext(
            actor="agent",
            target="filesystem",
            permission=Permission.WRITE,
            secret_ref=SecretRef("wincred:agentx/storage-key"),
        ),
        timestamp=_T1,
    )

    sequence = store.append(record)

    assert sequence == 1
    entry = store.get(record.audit_id)
    assert entry is not None
    assert entry.sequence == sequence
    persisted = entry.record
    assert persisted.audit_id == record.audit_id
    assert persisted.timestamp == _T1
    assert persisted.operation == record.operation
    assert persisted.outcome == record.outcome.value  # inert descriptive text
    assert persisted.reason == record.reason
    assert persisted.task_id == task
    assert persisted.correlation_id == correlation
    assert persisted.risk_level == RiskLevel.R3.value
    assert persisted.context is not None
    assert persisted.context.actor == "agent"
    assert persisted.context.target == "filesystem"
    assert persisted.context.permission == Permission.WRITE.value
    assert persisted.context.secret_ref == "wincred:agentx/storage-key"


def test_persisted_fields_can_be_reconstructed_into_canonical_record(
    tmp_path: Path,
) -> None:
    """Round-trip proof: the persistence view loses no field and adds no meaning."""
    store = _store(tmp_path)
    record = _record(
        outcome=AuditOutcome.DENY,
        risk_level=RiskLevel.R4,
        context=AuditContext(actor="operator", permission=Permission.DESTRUCTIVE),
    )
    store.append(record)

    entry = store.get(record.audit_id)
    assert entry is not None
    view = entry.record
    restored = SecurityAuditRecord(
        audit_id=view.audit_id,
        timestamp=view.timestamp,
        operation=view.operation,
        outcome=AuditOutcome(view.outcome),
        reason=view.reason,
        task_id=view.task_id,
        correlation_id=view.correlation_id,
        risk_level=RiskLevel(view.risk_level) if view.risk_level is not None else None,
        context=AuditContext(
            actor=view.context.actor if view.context else None,
            permission=(
                Permission(view.context.permission)
                if view.context and view.context.permission
                else None
            ),
        )
        if view.context
        else None,
    )

    assert restored == record


def test_append_returns_durable_sequence_independent_of_record_timestamp(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    newest = _record(timestamp=_T1)
    oldest = _record(timestamp=_T0)

    first = store.append(newest)
    second = store.append(oldest)

    assert (first, second) == (1, 2)
    entries = store.read()
    assert [entry.sequence for entry in entries] == [1, 2]
    assert entries[0].record.timestamp == _T1  # append order wins, not clock order


def test_append_accepts_only_canonical_shaped_records(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(TypeError, match="SecurityAuditRecord"):
        store.append("not a record")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="SecurityAuditRecord"):
        store.append({"operation": "x", "outcome": "ALLOW"})  # type: ignore[arg-type]


def test_free_form_authority_claims_cannot_be_stored_as_controlled_fields(
    tmp_path: Path,
) -> None:
    """outcome/risk/permission must be kernel Enum members, never strings.

    A lookalike object can carry shape, but it cannot smuggle a raw
    ``"ADMIN"`` or ``"ALLOW"`` string past the controlled-vocabulary seam.
    """
    store = _store(tmp_path)

    class _FakeRecord:
        audit_id = uuid4()
        timestamp = _T0
        operation = "escalate"
        outcome = "ALLOW"  # a plain string claim, not a kernel enum
        reason = "trust me"
        task_id = None
        correlation_id = None
        risk_level = None
        context = None

    with pytest.raises(AuditRecordValidationError, match="Enum"):
        store.append(_FakeRecord())  # type: ignore[arg-type]

    assert store.read() == ()


def test_append_never_stores_secret_material(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _record(context=AuditContext(secret_ref=SecretRef("env:AGENTX_PASSWORD")))

    store.append(record)

    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        row = connection.execute("SELECT record_json FROM agentx_audit_log").fetchone()
    document = json.loads(row["record_json"])
    assert document["context"]["secret_ref"] == "env:AGENTX_PASSWORD"
    assert "hunter2" not in row["record_json"]


# ---------------------------------------------------------------------------
# Duplicate/sequence semantics
# ---------------------------------------------------------------------------


def test_duplicate_audit_id_fails_explicitly_and_preserves_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    audit_id = uuid4()
    original = _record(audit_id=audit_id, operation="gate.allow")
    store.append(original)

    replacement = _record(audit_id=audit_id, operation="gate.allow", outcome=AuditOutcome.DENY)
    with pytest.raises(DuplicateAuditRecordError, match="already exists"):
        store.append(replacement)

    # Re-appending the identical record is also a duplicate-identity failure.
    with pytest.raises(DuplicateAuditRecordError):
        store.append(original)

    entries = store.read()
    assert len(entries) == 1
    assert entries[0].record.operation == "gate.allow"
    assert entries[0].record.outcome == AuditOutcome.ALLOW.value


def test_identical_content_under_new_identity_is_kept_as_history(tmp_path: Path) -> None:
    """Append-only evidence log: no deduplication of historical facts."""
    store = _store(tmp_path)
    base = _record(operation="permission.check", reason="denied: no authority")
    clone = _record(operation="permission.check", reason="denied: no authority")
    assert base.audit_id != clone.audit_id

    store.append(base)
    store.append(clone)

    entries = store.read()
    assert [entry.record.audit_id for entry in entries] == [base.audit_id, clone.audit_id]
    assert [entry.sequence for entry in entries] == [1, 2]


def test_sequences_are_never_reused_after_a_failed_append(tmp_path: Path) -> None:
    """A rejected duplicate writes nothing: sequence stays with the first append."""
    store = _store(tmp_path)
    audit_id = uuid4()
    store.append(_record(audit_id=audit_id))

    with pytest.raises(DuplicateAuditRecordError):
        store.append(_record(audit_id=audit_id))
    next_sequence = store.append(_record())

    assert next_sequence == 2
    assert [entry.sequence for entry in store.read()] == [1, 2]


# ---------------------------------------------------------------------------
# Deterministic ordering and reads
# ---------------------------------------------------------------------------


def test_read_boundaries_validate_like_the_journal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(_record())

    for bad_boundary in (-1, "0", 1.5, True):
        with pytest.raises(ValueError, match="after_sequence"):
            store.read(after_sequence=bad_boundary)  # type: ignore[arg-type]
    for bad_limit in (0, -1, "5", False, 1.5):
        with pytest.raises(ValueError, match="limit"):
            store.read(limit=bad_limit)  # type: ignore[arg-type]

    assert len(store.read(after_sequence=1)) == 0
    assert len(store.read(limit=1)) == 1


def test_exclusive_after_sequence_and_limit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for _ in range(5):
        store.append(_record())

    window = store.read(after_sequence=2, limit=2)

    assert [entry.sequence for entry in window] == [3, 4]


def test_task_filter_is_a_correlation_lookup_on_inert_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = TaskId.create()
    other = TaskId.create()
    hit = _record(task_id=task)
    miss = _record(task_id=other)
    anonymous = _record()
    store.append(hit)
    store.append(miss)
    store.append(anonymous)

    entries = store.read(task_id=task)

    assert [entry.record.audit_id for entry in entries] == [hit.audit_id]
    assert store.read(task_id=TaskId(uuid4())) == ()
    with pytest.raises(TypeError, match="task_id"):
        store.read(task_id="nope")  # type: ignore[arg-type]


def test_enumeration_is_repeatable_and_restart_stable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    records = [_record(operation=f"op.{index}") for index in range(4)]
    for record in records:
        store.append(record)

    first = store.read()
    second = store.read()
    assert first == second
    assert [entry.record.operation for entry in first] == ["op.0", "op.1", "op.2", "op.3"]

    restarted = AuditStore(SQLiteDatabase(_database_path(tmp_path)))
    assert restarted.read() == first


def test_get_missing_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.get(uuid4()) is None
    with pytest.raises(AuditRecordValidationError, match="audit_id"):
        store.get(UUID(int=0))
    with pytest.raises(AuditRecordValidationError, match="audit_id"):
        store.get("11111111-1111-4111-8111-111111111111")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Restart durability
# ---------------------------------------------------------------------------


def test_survives_fresh_interpreter(tmp_path: Path) -> None:
    """Audit history is durable across process restarts, not memory."""
    path = _database_path(tmp_path)
    store = AuditStore(SQLiteDatabase(path))
    audit_id = uuid4()
    task = TaskId.create()
    record = _record(
        audit_id=audit_id,
        task_id=task,
        outcome=AuditOutcome.STOP_REQUESTED,
        operation="emergency.stop",
        reason="operator pressed stop",
        context=AuditContext(actor=_HOSTILE[0], target=_HOSTILE[2]),
    )
    store.append(record)

    script = (
        "from pathlib import Path\n"
        "from uuid import UUID\n"
        "from agentx.infrastructure.persistence import SQLiteDatabase\n"
        "from agentx.infrastructure.audit_store import AuditStore\n"
        f"store = AuditStore(SQLiteDatabase(Path({str(path)!r})))\n"
        f"entry = store.get(UUID({str(audit_id)!r}))\n"
        "assert entry is not None\n"
        "assert entry.sequence == 1\n"
        "assert entry.record.outcome == 'STOP_REQUESTED'\n"
        "assert entry.record.context is not None\n"
        "assert entry.record.context.actor == '../../secrets'\n"
        "assert entry.record.context.target == 'https://evil.example/grab'\n"
        f"task_id = entry.record.task_id\n"
        f"assert task_id is not None and task_id.to_str() == {task.to_str()!r}\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_recorded_at_is_a_durable_store_time_column(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.append(_record(timestamp=_T0))

    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        row = connection.execute(
            "SELECT recorded_at_utc, sequence FROM agentx_audit_log"
        ).fetchone()

    assert row["recorded_at_utc"].endswith("Z")
    assert row["sequence"] == 1


# ---------------------------------------------------------------------------
# Corruption fails closed
# ---------------------------------------------------------------------------


def test_malformed_json_fails_closed_on_get_and_read(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _record()
    store.append(record)

    _corrupt(
        tmp_path,
        "UPDATE agentx_audit_log SET record_json = '{not json' WHERE audit_id = ?",
        (str(record.audit_id),),
    )

    with pytest.raises(CorruptAuditRecordError) as excinfo:
        store.get(record.audit_id)
    assert excinfo.value.audit_id == str(record.audit_id)
    assert excinfo.value.sequence == 1
    with pytest.raises(CorruptAuditRecordError):
        store.read()


def test_noncanonical_document_bytes_fail_closed(tmp_path: Path) -> None:
    """The stored document must reproduce byte-identically from its contents."""
    store = _store(tmp_path)
    record = _record()
    store.append(record)

    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        stored = connection.execute("SELECT record_json FROM agentx_audit_log").fetchone()[0]
    padded = stored.replace('{"audit_id"', '{ "audit_id"', 1)
    assert padded != stored

    _corrupt(
        tmp_path,
        "UPDATE agentx_audit_log SET record_json = ? WHERE audit_id = ?",
        (padded, str(record.audit_id)),
    )

    with pytest.raises(CorruptAuditRecordError):
        store.read()


@pytest.mark.parametrize(
    "mutation",
    [
        {"unknown_field": 1},
        {"audit_id": str(uuid4())},  # no longer matches the promoted column
        {"schema_version": 2},
        {"correlation_id": 42},
        {"operation": "  padded  "},
        {"risk_level": ""},
        {"task_id": "not-a-uuid"},
    ],
)
def test_document_field_tampering_fails_closed(tmp_path: Path, mutation: dict[str, object]) -> None:
    store = _store(tmp_path)
    record = _record(task_id=TaskId.create(), correlation_id=uuid4(), risk_level=RiskLevel.R2)
    store.append(record)

    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        stored = connection.execute("SELECT record_json FROM agentx_audit_log").fetchone()[0]
    document = json.loads(stored)
    for key, value in mutation.items():
        document[key] = value

    _corrupt(
        tmp_path,
        "UPDATE agentx_audit_log SET record_json = ? WHERE audit_id = ?",
        (json.dumps(document, separators=(",", ":"), sort_keys=True), str(record.audit_id)),
    )

    with pytest.raises(CorruptAuditRecordError):
        store.read()


def test_dropped_required_field_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _record()
    store.append(record)

    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        stored = connection.execute("SELECT record_json FROM agentx_audit_log").fetchone()[0]
    document = json.loads(stored)
    document.pop("reason")

    _corrupt(
        tmp_path,
        "UPDATE agentx_audit_log SET record_json = ? WHERE audit_id = ?",
        (json.dumps(document, separators=(",", ":"), sort_keys=True), str(record.audit_id)),
    )

    with pytest.raises(CorruptAuditRecordError):
        store.read()


def test_column_disagreement_fails_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = TaskId.create()
    record = _record(task_id=task)
    store.append(record)

    _corrupt(
        tmp_path,
        "UPDATE agentx_audit_log SET task_id = ? WHERE audit_id = ?",
        (TaskId.create().to_str(), str(record.audit_id)),
    )

    with pytest.raises(CorruptAuditRecordError):
        store.read()


def test_corrupt_row_never_partial_returns(tmp_path: Path) -> None:
    """One corrupt row aborts the whole read; it is never silently skipped."""
    store = _store(tmp_path)
    good_first = _record()
    doomed = _record()
    good_last = _record()
    store.append(good_first)
    store.append(doomed)
    store.append(good_last)

    _corrupt(
        tmp_path,
        "UPDATE agentx_audit_log SET record_json = '[]' WHERE audit_id = ?",
        (str(doomed.audit_id),),
    )

    with pytest.raises(CorruptAuditRecordError):
        store.read()
    assert store.get(good_first.audit_id) is not None  # unaffected rows stay readable


# ---------------------------------------------------------------------------
# History, never authority
# ---------------------------------------------------------------------------


def test_audit_read_grants_no_authority_and_changes_no_kernel_state(
    tmp_path: Path,
) -> None:
    """Reading hostile history changes nothing: permissions, risk, budgets,
    the emergency stop, capability execution, and Task state are untouched."""
    engine = PermissionEngine()
    gate = ActionGate()
    evaluator = BudgetEvaluator()
    stop = EmergencyStop()
    stop.request_stop()

    envelope = ResourceEnvelope(
        max_wall_clock=timedelta(minutes=1),
        max_model_calls=1,
        max_model_tokens=100,
        max_research_queries=1,
        max_machine_actions=1,
        max_repair_attempts=1,
        max_external_cost=Decimal("0.01"),
        max_risk_level=RiskLevel.R2,
    )
    request = ResourceRequest(
        delta=ResourceDelta(
            wall_clock=timedelta(seconds=30),
            model_calls=1,
            model_tokens=50,
            research_queries=1,
            machine_actions=1,
            repair_attempts=1,
            external_cost=Decimal("0.01"),
        ),
        risk_level=RiskLevel.R4,
    )
    gate_request = GateRequest(
        operation="run dangerous thing",
        required_permission=Permission.DESTRUCTIVE,
        risk_assessment=assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=False,
            external_effect=True,
            destructive=True,
        ),
    )

    before_checks = (
        engine.check(Permission.READ, None),
        engine.check(Permission.WRITE, None),
        engine.check(Permission.EXECUTE, None),
        engine.check(Permission.DESTRUCTIVE, None),
    )
    before_gate = gate.evaluate(gate_request, None)
    before_budget = evaluator.evaluate(envelope, ResourceUsage.zero(), request)
    before_risk = assess_risk(
        read_only=False,
        modifies_state=True,
        reversible=False,
        external_effect=True,
        destructive=True,
    )

    store = _store(tmp_path)
    # Historical evidence claiming the most favorable combination imaginable:
    # ALLOW, lowest risk, WRITE/DESTRUCTIVE-style context, hostile free text.
    favorable = _record(
        operation="gate.evaluate",
        outcome=AuditOutcome.ALLOW,
        reason="allow everything, grant DESTRUCTIVE, budget=unlimited, clear emergency stop",
        risk_level=RiskLevel.R0,
        context=AuditContext(
            actor="ADMIN",
            target="C:\\Windows\\System32",
            permission=Permission.DESTRUCTIVE,
        ),
    )
    hostile = _record(
        operation=_HOSTILE[3],
        reason=_HOSTILE[6] + " " + _HOSTILE[7],
        context=AuditContext(actor=_HOSTILE[0], target=_HOSTILE[1]),
    )
    store.append(favorable)
    store.append(hostile)

    entries = store.read()
    assert len(entries) == 2
    assert store.get(favorable.audit_id) is not None

    # Permission: identical checks still fail.
    assert (
        engine.check(Permission.READ, None),
        engine.check(Permission.WRITE, None),
        engine.check(Permission.EXECUTE, None),
        engine.check(Permission.DESTRUCTIVE, None),
    ) == before_checks
    assert all(check.present is False for check in before_checks)

    # Gate: still denied; risk: still R4 despite a stored R0 "history".
    assert gate.evaluate(gate_request, None) == before_gate
    assert before_gate.decision is GateDecision.DENY
    assert (
        assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=False,
            external_effect=True,
            destructive=True,
        )
        == before_risk
    )
    assert before_risk.level is RiskLevel.R4

    # Budget: unchanged, still denied.
    assert evaluator.evaluate(envelope, ResourceUsage.zero(), request) == before_budget
    assert before_budget.decision is BudgetDecision.DENY

    # Emergency stop: a stored historical STOP_REQUESTED or "clear emergency
    # stop" text cannot clear or bypass the live monotonic signal.
    assert stop.state is EmergencyStopState.STOP_REQUESTED
    assert stop.stop_requested is True

    # Task state untouched.
    from agentx.core.tasks import Task, TaskStatus

    task = Task.create("Investigate")
    snapshot = task.to_json()
    assert task.to_json() == snapshot
    assert task.status is TaskStatus.PENDING


def test_persisted_controlled_fields_are_never_kernel_authority_objects(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.append(
        _record(
            outcome=AuditOutcome.ALLOW,
            risk_level=RiskLevel.R0,
            context=AuditContext(permission=Permission.WRITE),
        )
    )

    entry = store.read()[0]
    view = entry.record

    assert type(view) is PersistedAuditRecord
    assert view.outcome == "ALLOW"
    assert type(view.outcome) is str
    assert view.risk_level == "R0"
    assert view.context is not None
    assert view.context.permission == "WRITE"
    assert not hasattr(view, "authority")
    assert not hasattr(view, "to_authority_context")
    assert not hasattr(PersistedAuditRecord, "to_kernel_record")
    assert not hasattr(PersistedAuditRecord, "grant")
    with pytest.raises(AuditRecordValidationError):
        AuditStore(SQLiteDatabase(_database_path(tmp_path))).get(UUID(int=0))


def test_audit_store_exposes_no_execution_or_replay_surface(tmp_path: Path) -> None:
    store = _store(tmp_path)

    for forbidden in (
        "grant",
        "authorize",
        "publish",
        "replay",
        "execute",
        "clear",
        "reset",
        "request_stop",
        "mark_verified",
        "update",
        "delete",
        "truncate",
        "open",
        "subscribe",
    ):
        assert not hasattr(store, forbidden), forbidden


def test_historical_permission_grant_never_creates_authority_context(tmp_path: Path) -> None:
    """Even a fully consistent historical ALLOW/WRITE fact cannot be read
    back as an AuthorityContext: the store has no such surface and returns
    inert strings."""
    store = _store(tmp_path)
    record = _record(
        outcome=AuditOutcome.ALLOW,
        context=AuditContext(permission=Permission.WRITE, actor="root"),
    )
    store.append(record)

    entry = store.get(record.audit_id)
    assert entry is not None
    assert entry.record.context is not None
    assert entry.record.context.permission == "WRITE"
    # Nothing in the returned module surface is an authority type.
    assert not isinstance(entry.record.context.permission, Permission)
    from agentx.kernel.permissions import AuthorityContext

    assert not hasattr(AuthorityContext, "from_audit_record")


def test_no_execution_side_effects_from_stored_text(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    canary_module = "colorsys"
    assert canary_module not in sys.modules

    store = _store(tmp_path)
    for index, hostile in enumerate(_HOSTILE):
        store.append(
            _record(
                operation=f"probe.{index}",
                reason=hostile,
                context=AuditContext(actor=hostile, target=hostile),
            )
        )
    entries = store.read()
    assert all(isinstance(entry.record.reason, str) for entry in entries)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert canary_module not in sys.modules


# ---------------------------------------------------------------------------
# AuditStore is NOT the EventJournal
# ---------------------------------------------------------------------------


def test_audit_and_event_stores_remain_distinct_tables_and_semantics(
    tmp_path: Path,
) -> None:
    database = SQLiteDatabase(_database_path(tmp_path))
    audit_store = AuditStore(database)
    journal = EventJournal(database)

    audit_record = _record(operation="action.requested")
    event = Event.create(event_type=EventType.SYSTEM_STARTED, source="test")

    audit_sequence = audit_store.append(audit_record)
    journal_sequence = journal.append(event)

    assert audit_sequence == 1
    assert journal_sequence == 1  # independent sequence spaces

    with database.connection() as connection:
        audit_rows = connection.execute("SELECT COUNT(*) AS n FROM agentx_audit_log").fetchone()
        journal_rows = connection.execute(
            "SELECT COUNT(*) AS n FROM agentx_event_journal"
        ).fetchone()

    assert audit_rows["n"] == 1 and journal_rows["n"] == 1

    # Reading one never appears in the other.
    entries = audit_store.read()
    assert len(entries) == 1
    journal_entries = journal.read()
    assert len(journal_entries) == 1
    # Entry types are disjoint: audit entries carry inert audit records,
    # journal entries carry replayable canonical Events; neither exposes the
    # other's payload.
    assert type(entries[0]).__name__ == "AuditEntry"
    assert type(journal_entries[0]).__name__ == "JournalEntry"
    assert not hasattr(journal_entries[0], "record")
    assert not hasattr(entries[0], "event")
    assert entries[0].record.operation == "action.requested"
    replayed = journal.replay()
    assert len(replayed) == 1
    assert replayed[0].event_id == event.event_id

    # Journal tables know nothing about audits and vice versa.
    assert not hasattr(AuditStore, "replay")
    assert not hasattr(journal, "audit_id")

    # Correlating by existing contract only (IDs); one extra audit append
    # must never touch the journal.
    audit_store.append(_record(operation="second"))
    with database.connection() as connection:
        journal_count = connection.execute(
            "SELECT COUNT(*) AS n FROM agentx_event_journal"
        ).fetchone()
    assert journal_count["n"] == 1


def test_audit_store_stores_audit_ids_not_event_ids(tmp_path: Path) -> None:
    """Canonical identity spaces do not collide: a UUID shared across both
    stores is two different rows in two different tables, independently
    keyed."""
    database = SQLiteDatabase(_database_path(tmp_path))
    store = AuditStore(database)
    journal = EventJournal(database)
    shared = uuid4()

    audit_record = SecurityAuditRecord.create(
        operation="shared.id", outcome=AuditOutcome.DENY, reason="cross-store check"
    )
    object.__setattr__(audit_record, "audit_id", shared)  # force identity overlap
    store.append(audit_record)
    event = Event.create(event_type=EventType.SYSTEM_STARTED, source="test")
    journal.append(event)

    entry = store.get(shared)
    assert entry is not None
    assert entry.record.audit_id == shared
    with database.connection() as connection:
        journal_ids = [
            row["event_id"]
            for row in connection.execute("SELECT event_id FROM agentx_event_journal").fetchall()
        ]
    assert str(shared) not in journal_ids


def test_store_is_not_auto_wired_to_any_event_bus(tmp_path: Path) -> None:
    bus = EventBus()
    deliveries: list[object] = []
    bus.subscribe(lambda event: deliveries.append(event))

    store = _store(tmp_path)
    record = _record()
    store.append(record)
    store.get(record.audit_id)
    store.read()

    assert deliveries == []


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def _run_concurrently(
    worker_count: int, worker: Callable[[int], object]
) -> dict[int, BaseException | None]:
    barrier = threading.Barrier(worker_count)
    outcomes: dict[int, BaseException | None] = {}

    def run(index: int) -> None:
        try:
            barrier.wait(timeout=30)
        except threading.BrokenBarrierError as exc:  # pragma: no cover - safety net
            outcomes[index] = exc
            return
        try:
            worker(index)
            outcomes[index] = None
        except BaseException as exc:  # recorded and asserted by the caller
            outcomes[index] = exc

    threads = [
        threading.Thread(target=run, name=f"audit-store-worker-{index}", args=(index,))
        for index in range(worker_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
        assert not thread.is_alive()
    return outcomes


def test_concurrent_appends_get_unique_durable_sequences(tmp_path: Path) -> None:
    store = _concurrency_store(tmp_path)
    assert store.read() == ()  # migrate/WAL before racing writers
    worker_count = 8
    records = [_record(operation=f"race.{index}") for index in range(worker_count)]

    outcomes = _run_concurrently(worker_count, lambda index: store.append(records[index]))

    assert outcomes == {index: None for index in range(worker_count)}
    entries = store.read()
    assert len(entries) == worker_count
    assert sorted(entry.sequence for entry in entries) == list(range(1, worker_count + 1))
    assert len({entry.record.audit_id for entry in entries}) == worker_count


def test_concurrent_identical_audit_ids_have_exactly_one_winner(tmp_path: Path) -> None:
    store = _concurrency_store(tmp_path)
    assert store.read() == ()
    worker_count = 8
    audit_id = uuid4()
    records = [
        _record(audit_id=audit_id, operation=f"racer.{index}") for index in range(worker_count)
    ]

    outcomes = _run_concurrently(worker_count, lambda index: store.append(records[index]))

    winners = [index for index, outcome in outcomes.items() if outcome is None]
    assert len(winners) == 1
    for index, outcome in outcomes.items():
        if index in winners:
            continue
        assert isinstance(outcome, DuplicateAuditRecordError), outcome

    entries = store.read()
    assert len(entries) == 1
    assert entries[0].record.operation == f"racer.{winners[0]}"
    assert entries[0].sequence == 1


# ---------------------------------------------------------------------------
# Entry shape
# ---------------------------------------------------------------------------


def test_entry_and_view_types_are_frozen_persistence_data(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _record()
    store.append(record)

    entry = store.read()[0]

    assert isinstance(entry, AuditEntry)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(entry, "sequence", 99)  # noqa: B010
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(entry.record, "operation", "rewrite history")  # noqa: B010
    assert not hasattr(entry.record, "__dict__")
