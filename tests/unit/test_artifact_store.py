"""Tests for the durable inert ArtifactStore (C2.04)."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from agentx.core.artifacts import (
    ArtifactDigest,
    ArtifactDigestAlgorithm,
    ArtifactKind,
    ArtifactRecord,
)
from agentx.core.episodes import EpisodeOutcome, EpisodeRecord
from agentx.core.ids import ArtifactId, EpisodeId, ProcedureId, TaskId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeType,
)
from agentx.core.knowledge import (
    _format_timestamp as _format_knowledge_timestamp,
)
from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
    ProcedureStatus,
)
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.artifact_store import (
    ArtifactStore,
    ArtifactStoreError,
    ArtifactStoreStorageError,
    CorruptArtifactRecordError,
    DuplicateArtifactError,
)
from agentx.infrastructure.event_bus import EventBus
from agentx.infrastructure.persistence import (
    _MIGRATIONS,
    SQLiteDatabase,
    _apply_migrations,
)

_T0 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2025, 3, 1, 9, 0, 0, 500000, tzinfo=UTC)
_T2 = datetime(2025, 6, 15, 8, 30, 15, tzinfo=UTC)
_SHA256_HEX = "ab" * 32

_HOSTILE_REFERENCE = (
    "https://evil.example/../import os; os.system('rm -rf /')?ADMIN=1&risk=R0&verified=true"
)


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / "agentx.sqlite3"


# Concurrency tests race real writer threads against a durable WAL database.
# On slow container filesystems (extremely long fsync), a fresh 5 s default
# busy timeout can expire purely from throughput, not contention semantics,
# so the store under concurrency tests gets a longer — still finite —
# SQLite busy timeout from the persistence layer's own knob.
_CONCURRENCY_BUSY_TIMEOUT_MS = 30_000


def _store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(SQLiteDatabase(_database_path(tmp_path)))


def _concurrency_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        SQLiteDatabase(_database_path(tmp_path), busy_timeout_ms=_CONCURRENCY_BUSY_TIMEOUT_MS)
    )


def _record(**overrides: Any) -> ArtifactRecord:
    values: dict[str, Any] = {
        "artifact_id": ArtifactId.create(),
        "kind": ArtifactKind.GENERATED,
        "reference": "artifacts/report.pdf",
        "created_at": _T0,
    }
    values.update(overrides)
    return ArtifactRecord(**values)


def _full_record() -> ArtifactRecord:
    return _record(
        reference=_HOSTILE_REFERENCE,
        media_type="application/x-evil+json",
        digest=ArtifactDigest(algorithm=ArtifactDigestAlgorithm.SHA256, hex_digest=_SHA256_HEX),
        size_bytes=2048,
        task_id=TaskId.create(),
        episode_id=EpisodeId.create(),
        created_at=_T1,
    )


# ---------------------------------------------------------------------------
# Fresh database / migrations
# ---------------------------------------------------------------------------


def test_fresh_database_creates_store_tables_via_migration(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.list_records() == ()

    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        names = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM agentx_schema_migrations ORDER BY version"
            ).fetchall()
        }
        versions = [
            row["version"]
            for row in connection.execute(
                "SELECT version FROM agentx_schema_migrations ORDER BY version"
            ).fetchall()
        ]
        artifact_table = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name = 'agentx_artifacts'"
        ).fetchone()
        audit_table = connection.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name = 'agentx_audit_log'"
        ).fetchone()
        indexes = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'index'"
            ).fetchall()
            if row["name"].startswith("agentx_artifacts")
        }

    # Identified by name, never by number: concurrent store migrations may be
    # resequenced at integration without touching this test.
    assert "create_artifact_store" in names
    assert "create_audit_store" in names
    assert "create_procedure_store" in names
    assert "create_episode_store" in names
    assert versions == list(range(1, len(versions) + 1))
    assert artifact_table is not None
    assert audit_table is not None
    assert {"agentx_artifacts_task_idx", "agentx_artifacts_episode_idx"} <= indexes


def test_current_main_database_migrates_forward_without_disturbing_data(
    tmp_path: Path,
) -> None:
    """A database at canonical main (migrations v1-v5 through C2.03) migrates
    forward: C2.04 appends only, rewrites nothing, and preserves stored data."""
    path = _database_path(tmp_path)
    knowledge = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT, content="pre-existing claim"
    )
    episode = EpisodeRecord.create(outcome=EpisodeOutcome.SUCCEEDED, summary="pre-existing episode")
    procedure = ProcedureRecord(
        procedure_id=ProcedureId.create(),
        revision=1,
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content="{}"),
        created_at=_T0,
        status=ProcedureStatus.CANDIDATE,
    )

    raw = sqlite3.connect(path, isolation_level=None)
    raw.row_factory = sqlite3.Row
    try:
        # Reproduce main exactly: the landed v1-v5 migrations only.
        _apply_migrations(raw, _MIGRATIONS[:5])
        raw.execute(
            "INSERT INTO agentx_knowledge (knowledge_id, created_at_utc, record_json) "
            "VALUES (?, ?, ?)",
            (
                knowledge.knowledge_id.to_str(),
                _format_knowledge_timestamp(knowledge.created_at),
                knowledge.to_json(),
            ),
        )
        raw.execute(
            "INSERT INTO agentx_episodes (episode_id, task_id, correlation_id, episode_json) "
            "VALUES (?, NULL, NULL, ?)",
            (episode.episode_id.to_str(), episode.to_json()),
        )
        raw.execute(
            "INSERT INTO agentx_procedures "
            "(procedure_id, revision, created_at_utc, status, record_json) VALUES (?, ?, ?, ?, ?)",
            (
                procedure.procedure_id.to_str(),
                procedure.revision,
                procedure.created_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                procedure.status.value,
                procedure.to_json(),
            ),
        )
    finally:
        raw.close()

    store = ArtifactStore(SQLiteDatabase(path))
    assert store.list_records() == ()  # fresh, empty, migrated

    artifact = _record()
    store.insert(artifact)
    assert store.get(artifact.artifact_id) == artifact

    # Pre-existing v1-v5 data is untouched and readable through its stores.
    from agentx.infrastructure.episode_store import EpisodeStore
    from agentx.infrastructure.knowledge_store import KnowledgeStore
    from agentx.infrastructure.procedure_store import ProcedureStore

    assert KnowledgeStore(SQLiteDatabase(path)).get(knowledge.knowledge_id) == knowledge
    assert EpisodeStore(SQLiteDatabase(path)).get(episode.episode_id) == episode
    assert ProcedureStore(SQLiteDatabase(path)).get(procedure.procedure_id, 1) == procedure


def test_store_errors_are_persistence_errors() -> None:
    from agentx.infrastructure.persistence import PersistenceError

    assert issubclass(ArtifactStoreError, PersistenceError)
    assert issubclass(DuplicateArtifactError, ArtifactStoreError)
    assert issubclass(ArtifactStoreStorageError, ArtifactStoreError)
    assert issubclass(CorruptArtifactRecordError, ArtifactStoreError)


# ---------------------------------------------------------------------------
# Insert / get / list
# ---------------------------------------------------------------------------


def test_insert_then_get_round_trips_exactly_including_all_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _full_record()

    store.insert(record)

    stored = store.get(record.artifact_id)

    assert stored == record
    assert stored is not None
    assert stored.reference == record.reference
    assert stored.digest == record.digest
    assert stored.size_bytes == record.size_bytes
    assert stored.task_id == record.task_id
    assert stored.episode_id == record.episode_id


def test_get_missing_returns_none_and_rejects_wrong_identity_types(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.get(ArtifactId.create()) is None
    with pytest.raises(TypeError, match="ArtifactId"):
        store.get(EpisodeId.create())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ArtifactId"):
        store.get(str(ArtifactId.create()))  # type: ignore[arg-type]


def test_insert_rejects_non_records_and_bad_filter_types(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(TypeError, match="canonical ArtifactRecord"):
        store.insert({"reference": "x"})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="task_id"):
        store.list_records(task_id="nope")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="episode_id"):
        store.list_records(episode_id=TaskId.create())  # type: ignore[arg-type]


def test_list_filters_are_correlation_lookups_not_joins(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = TaskId.create()
    episode = EpisodeId.create()
    correlated = _record(task_id=task, episode_id=episode)
    uncorrelated = _record()
    store.insert(uncorrelated)
    store.insert(correlated)

    assert store.list_records(task_id=task) == (correlated,)
    assert store.list_records(episode_id=episode) == (correlated,)
    assert store.list_records(task_id=task, episode_id=episode) == (correlated,)
    assert store.list_records(task_id=TaskId.create()) == ()
    assert set(store.list_records(kind=ArtifactKind.GENERATED)) == {correlated, uncorrelated}
    assert store.list_records(kind=ArtifactKind.OBSERVED) == ()


def test_correlated_ids_need_not_exist_anywhere(tmp_path: Path) -> None:
    """Correlation IDs are inert labels: no referential integrity, no joins."""
    store = _store(tmp_path)
    phantom = ArtifactRecord.create(
        kind=ArtifactKind.OBSERVED,
        reference="whatever",
        task_id=TaskId(UUID(int=7)),
        episode_id=EpisodeId(UUID(int=8)),
    )

    store.insert(phantom)

    assert store.get(phantom.artifact_id) == phantom


# ---------------------------------------------------------------------------
# Duplicate behavior and immutability
# ---------------------------------------------------------------------------


def test_duplicate_identity_fails_explicitly_and_original_is_preserved(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _record(reference="original")
    store.insert(record)

    hostile = ArtifactRecord(
        artifact_id=record.artifact_id,
        kind=ArtifactKind.OBSERVED,
        reference="file:///etc/shadow",
        created_at=_T2,
    )
    with pytest.raises(DuplicateArtifactError, match="already exists"):
        store.insert(hostile)

    assert store.get(record.artifact_id) == record
    assert store.list_records() == (record,)


def test_reinserting_the_identical_record_is_also_a_rejected_duplicate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _record()
    store.insert(record)

    with pytest.raises(DuplicateArtifactError):
        store.insert(record)

    assert store.list_records() == (record,)


def test_identical_content_under_distinct_ids_is_not_deduplicated(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = _record(reference="same/target.bin")
    second = _record(reference="same/target.bin")
    assert first.artifact_id != second.artifact_id

    store.insert(first)
    store.insert(second)

    assert set(store.list_records()) == {first, second}


def test_store_has_no_mutation_dereference_or_execution_surface(tmp_path: Path) -> None:
    store = _store(tmp_path)

    for forbidden in (
        "update",
        "delete",
        "remove",
        "replace",
        "upsert",
        "merge",
        "clear",
        "truncate",
        "open",
        "fetch",
        "download",
        "upload",
        "resolve",
        "dereference",
        "read_content",
        "execute",
        "import",
        "grant",
        "publish",
        "replay",
    ):
        assert not hasattr(store, forbidden), forbidden


def test_stored_rows_never_change_across_repeated_reads(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _full_record()
    store.insert(record)

    path = _database_path(tmp_path)
    for _ in range(3):
        store.get(record.artifact_id)
        store.list_records(task_id=record.task_id)

    raw = sqlite3.connect(path)
    try:
        rows = raw.execute("SELECT record_json FROM agentx_artifacts").fetchall()
    finally:
        raw.close()

    assert rows == [(record.to_json(),)]


# ---------------------------------------------------------------------------
# Deterministic ordering
# ---------------------------------------------------------------------------


def test_list_orders_by_artifact_id_independent_of_insertion_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ids = [
        ArtifactId(UUID("11111111-1111-4111-8111-111111111111")),
        ArtifactId(UUID("22222222-2222-4222-8222-222222222222")),
        ArtifactId(UUID("33333333-3333-4333-8333-333333333333")),
    ]
    records = [
        _record(artifact_id=id_, created_at=_T2 - timedelta(days=index))
        for index, id_ in enumerate(ids)
    ]
    for record in reversed(records):
        store.insert(record)

    listing = store.list_records()

    assert [record.artifact_id.to_str() for record in listing] == [id_.to_str() for id_ in ids]
    assert listing == store.list_records()  # stable across calls


def test_two_stores_with_different_insertion_orders_agree(tmp_path: Path) -> None:
    records = [_record() for _ in range(5)]
    forward_store = ArtifactStore(SQLiteDatabase(tmp_path / "forward" / "agentx.sqlite3"))
    backward_store = ArtifactStore(SQLiteDatabase(tmp_path / "backward" / "agentx.sqlite3"))
    for record in records:
        forward_store.insert(record)
    for record in sorted(records, key=lambda r: r.artifact_id.to_str(), reverse=True):
        backward_store.insert(record)

    assert forward_store.list_records() == backward_store.list_records()


# ---------------------------------------------------------------------------
# Restart durability
# ---------------------------------------------------------------------------


def test_records_survive_store_recreation(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    store = ArtifactStore(SQLiteDatabase(path))
    record = _full_record()
    store.insert(record)

    restarted = ArtifactStore(SQLiteDatabase(path))

    assert restarted.get(record.artifact_id) == record
    assert restarted.list_records() == (record,)
    with pytest.raises(DuplicateArtifactError):
        restarted.insert(record)


def test_records_survive_a_fresh_interpreter(tmp_path: Path) -> None:
    """Durability across process restarts, proven in a clean interpreter."""
    path = _database_path(tmp_path)
    store = _store(tmp_path)
    record = _record(reference="../../secrets", media_type="text/plain", size_bytes=7)
    store.insert(record)

    script = (
        "from pathlib import Path\n"
        "from agentx.core.ids import ArtifactId\n"
        "from agentx.core.artifacts import ArtifactKind, ArtifactRecord\n"
        "from agentx.infrastructure.persistence import SQLiteDatabase\n"
        "from agentx.infrastructure.artifact_store import ArtifactStore\n"
        f"store = ArtifactStore(SQLiteDatabase(Path({str(path)!r})))\n"
        f"record = store.get(ArtifactId.parse({record.artifact_id.to_str()!r}))\n"
        "assert record is not None\n"
        "assert record.reference == '../../secrets'\n"
        "assert record.media_type == 'text/plain'\n"
        "assert record.size_bytes == 7\n"
        "assert record.kind is ArtifactKind.GENERATED\n"
        "assert store.list_records() == (record,)\n"
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


# ---------------------------------------------------------------------------
# Corruption fails closed
# ---------------------------------------------------------------------------


def _corrupt(tmp_path: Path, sql: str, parameters: tuple[object, ...]) -> None:
    raw = sqlite3.connect(_database_path(tmp_path), isolation_level=None)
    try:
        raw.execute(sql, parameters)
    finally:
        raw.close()


def test_corrupt_json_fails_loudly_on_get_and_list(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _record()
    store.insert(record)

    _corrupt(
        tmp_path,
        "UPDATE agentx_artifacts SET record_json = '{not json' WHERE artifact_id = ?",
        (record.artifact_id.to_str(),),
    )

    with pytest.raises(CorruptArtifactRecordError) as excinfo:
        store.get(record.artifact_id)
    assert excinfo.value.artifact_id == record.artifact_id.to_str()
    with pytest.raises(CorruptArtifactRecordError):
        store.list_records()


def test_column_payload_identity_mismatch_is_corrupt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _record()
    other = _record(reference="other")
    store.insert(record)
    store.insert(other)

    _corrupt(
        tmp_path,
        "UPDATE agentx_artifacts SET record_json = ? WHERE artifact_id = ?",
        (other.to_json(), record.artifact_id.to_str()),
    )

    with pytest.raises(CorruptArtifactRecordError):
        store.get(record.artifact_id)


@pytest.mark.parametrize("column", ["kind", "created_at_utc", "task_id", "episode_id"])
def test_promoted_column_mismatches_are_corrupt(tmp_path: Path, column: str) -> None:
    store = _store(tmp_path)
    record = _record(
        task_id=TaskId.create(),
        episode_id=EpisodeId.create(),
    )
    store.insert(record)

    replacement = {
        "kind": "observed",
        "created_at_utc": "1999-01-01T00:00:00.000000Z",
        "task_id": TaskId.create().to_str(),
        "episode_id": EpisodeId.create().to_str(),
    }[column]

    _corrupt(
        tmp_path,
        f"UPDATE agentx_artifacts SET {column} = ? WHERE artifact_id = ?",
        (replacement, record.artifact_id.to_str()),
    )

    with pytest.raises(CorruptArtifactRecordError):
        store.get(record.artifact_id)
    with pytest.raises(CorruptArtifactRecordError):
        store.list_records()


def test_json_with_unknown_fields_or_unsupported_version_is_corrupt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _record()
    store.insert(record)

    for tampered in (
        {**json.loads(record.to_json()), "verified": True},
        {**json.loads(record.to_json()), "schema_version": 2},
    ):
        _corrupt(
            tmp_path,
            "UPDATE agentx_artifacts SET record_json = ? WHERE artifact_id = ?",
            (
                json.dumps(tampered, separators=(",", ":"), sort_keys=True),
                record.artifact_id.to_str(),
            ),
        )
        with pytest.raises(CorruptArtifactRecordError):
            store.get(record.artifact_id)


def test_corruption_blocks_filtered_reads_and_never_partially_returns(tmp_path: Path) -> None:
    store = _store(tmp_path)
    task = TaskId.create()
    healthy = _record(task_id=task)
    doomed = _record(task_id=task)
    store.insert(healthy)
    store.insert(doomed)

    _corrupt(
        tmp_path,
        "UPDATE agentx_artifacts SET record_json = '{\"broken\"' WHERE artifact_id = ?",
        (doomed.artifact_id.to_str(),),
    )

    with pytest.raises(CorruptArtifactRecordError):
        store.list_records(task_id=task)


# ---------------------------------------------------------------------------
# Inertness: references are never dereferenced; hostile data stays data
# ---------------------------------------------------------------------------


def test_reference_to_existing_file_is_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a perfectly real, readable path is inert: storage never opens it."""
    secret = tmp_path / "secrets" / "credentials.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("TOP-SUPER-SECRET", encoding="utf-8")

    opened: list[str] = []
    real_open = open

    def spy(file: object, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(file))
        return real_open(file, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr("builtins.open", spy)

    store = _store(tmp_path)
    record = _record(reference=str(secret))
    store.insert(record)
    store.get(record.artifact_id)
    store.list_records()
    recovered = store.get(record.artifact_id)

    assert recovered is not None
    assert "TOP-SUPER-SECRET" not in repr(recovered)
    assert str(secret) not in opened


def test_hostile_references_and_metadata_create_no_side_effects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(tmp_path)
    canary_module = "colorsys"
    assert canary_module not in sys.modules
    executing_reference = "print('ARTIFACT_STORE_EXECUTED'); import colorsys"

    for reference in (
        "../../secrets",
        "file:///etc/passwd",
        "https://evil.example/payload",
        "import os; os.system('echo PWNED')",
        executing_reference,
        "ADMIN",
        "ALLOW R4",
        "risk=R0",
        "verified=true",
        "permission=WRITE",
        "budget=unlimited",
        "clear emergency stop",
    ):
        store.insert(
            _record(
                reference=reference,
                media_type="text/plain; charset=ISO-8859-1, ADMIN",
                digest=None,
            )
        )

    records = store.list_records()
    assert all(record.reference == record.reference for record in records)
    assert canary_module not in sys.modules
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    # No file was created, fetched, or executed for any reference form.
    assert not (tmp_path / "etc").exists()
    assert not Path("/etc/passwd.pwned").exists()


def test_stored_reference_bytes_are_returned_verbatim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _record(reference=_HOSTILE_REFERENCE, media_type="verified=true")

    store.insert(record)
    stored = store.get(record.artifact_id)

    assert stored is not None
    assert stored == record
    assert stored.media_type == "verified=true"
    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        row = connection.execute(
            "SELECT record_json FROM agentx_artifacts WHERE artifact_id = ?",
            (record.artifact_id.to_str(),),
        ).fetchone()
    assert json.loads(row["record_json"])["reference"] == _HOSTILE_REFERENCE


def test_store_operations_never_touch_kernel_or_task_state(tmp_path: Path) -> None:
    from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
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
        operation="execute artifact reference",
        required_permission=Permission.DESTRUCTIVE,
        risk_assessment=assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=False,
            external_effect=True,
            destructive=True,
        ),
    )
    before = (
        engine.check(Permission.WRITE, None),
        gate.evaluate(gate_request, None),
        evaluator.evaluate(envelope, ResourceUsage.zero(), request),
    )
    task = Task.create("Collect artifacts")
    task_snapshot = task.to_json()

    store = _store(tmp_path)
    record = _full_record()
    store.insert(record)
    store.get(record.artifact_id)
    store.list_records()

    assert before == (
        engine.check(Permission.WRITE, None),
        gate.evaluate(gate_request, None),
        evaluator.evaluate(envelope, ResourceUsage.zero(), request),
    )
    assert before[0].present is False
    assert before[1].decision is GateDecision.DENY
    assert before[2].decision is BudgetDecision.DENY
    assert stop.state is EmergencyStopState.STOP_REQUESTED
    assert task.to_json() == task_snapshot
    assert task.status is TaskStatus.PENDING


def test_store_writes_no_other_tables_and_publishes_no_events(tmp_path: Path) -> None:
    bus = EventBus()
    deliveries: list[object] = []
    bus.subscribe(lambda event: deliveries.append(event))

    store = _store(tmp_path)
    record = _full_record()
    store.insert(record)
    store.get(record.artifact_id)
    store.list_records()

    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
            for table in (
                "agentx_event_journal",
                "agentx_knowledge",
                "agentx_episodes",
                "agentx_procedures",
                "agentx_audit_log",
            )
        }

    assert deliveries == []
    assert counts["agentx_event_journal"] == 0
    assert counts["agentx_knowledge"] == 0
    assert counts["agentx_episodes"] == 0
    assert counts["agentx_procedures"] == 0
    assert counts["agentx_audit_log"] == 0


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def _run_concurrently(
    worker_count: int, worker: Callable[[int], None]
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
        threading.Thread(target=run, name=f"artifact-store-worker-{index}", args=(index,))
        for index in range(worker_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
        assert not thread.is_alive()
    return outcomes


def test_concurrent_inserts_of_distinct_artifacts_all_succeed(tmp_path: Path) -> None:
    store = _concurrency_store(tmp_path)
    assert store.list_records() == ()  # migrate/WAL before racing writers
    worker_count = 8
    records = [_record(reference=f"race/{index}") for index in range(worker_count)]

    outcomes = _run_concurrently(worker_count, lambda index: store.insert(records[index]))

    assert outcomes == {index: None for index in range(worker_count)}
    listing = store.list_records()
    assert len(listing) == worker_count
    for record in records:
        assert store.get(record.artifact_id) == record


def test_concurrent_identical_identity_writes_have_exactly_one_winner(tmp_path: Path) -> None:
    store = _concurrency_store(tmp_path)
    assert store.list_records() == ()
    worker_count = 8
    artifact_id = ArtifactId.create()
    records = [
        _record(artifact_id=artifact_id, reference=f"racer {index}")
        for index in range(worker_count)
    ]

    outcomes = _run_concurrently(worker_count, lambda index: store.insert(records[index]))

    winners = [index for index, outcome in outcomes.items() if outcome is None]
    assert len(winners) == 1
    for index, outcome in outcomes.items():
        if index in winners:
            continue
        assert isinstance(outcome, DuplicateArtifactError), outcome
    assert store.list_records() == (records[winners[0]],)
