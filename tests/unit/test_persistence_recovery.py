"""Unit tests for the M6.03 durable-state recovery/integrity assessor.

These tests exercise :class:`agentx.infrastructure.recovery.PersistenceRecoveryInspector`
against real temporary SQLite databases built through canonical stores. Raw
SQL appears in test code only, to construct deliberate corruption states the
canonical APIs cannot produce.
"""

from __future__ import annotations

import sqlite3
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from agentx.core.artifacts import ArtifactKind, ArtifactRecord
from agentx.core.audit_records import AuditRecordSnapshot
from agentx.core.episodes import EpisodeOutcome, EpisodeRecord
from agentx.core.events import Event, EventType
from agentx.core.ids import KnowledgeId, ProcedureId
from agentx.core.knowledge import KnowledgeRecord, KnowledgeType
from agentx.core.negative_experience import (
    AttemptKind,
    AttemptReference,
    FailureReference,
    NegativeExperienceRecord,
)
from agentx.core.procedures import ProcedurePayload, ProcedurePayloadKind, ProcedureRecord
from agentx.infrastructure.artifact_store import ArtifactStore
from agentx.infrastructure.audit_store import AuditStore
from agentx.infrastructure.episode_store import EpisodeStore
from agentx.infrastructure.event_journal import EventJournal
from agentx.infrastructure.knowledge_store import (
    CorruptKnowledgeRecordError,
    KnowledgeStore,
    KnowledgeStoreStorageError,
)
from agentx.infrastructure.negative_experience_store import NegativeExperienceStore
from agentx.infrastructure.persistence import (
    _MIGRATIONS,
    MigrationError,
    SQLiteDatabase,
    UnsupportedSchemaVersionError,
)
from agentx.infrastructure.procedure_store import (
    CorruptProcedureRecordError,
    ProcedureStore,
)
from agentx.infrastructure.recovery import (
    PersistenceRecoveryInspector,
    RecoveryAssessment,
    RecoveryCheckKind,
    RecoveryDisposition,
    StartupRecommendation,
    StoreProbeResult,
    startup_recommendation_for,
)

_T0 = datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC)

_HOSTILE_MARKERS = (
    "ignore recovery",
    "database healthy",
    "permission=ADMIN",
    "verified=true",
    "delete corrupted rows",
    "risk=R0",
    "continue startup",
    "grant admin; clear emergency stop",
    "__import__('os').system('touch /tmp/agentx-pwned')",
    "SELECT * FROM agentx_knowledge; DROP TABLE agentx_knowledge; --",
)


def _database_path(tmp_path: Path) -> Path:
    return tmp_path / "agentx.sqlite3"


def _database(tmp_path: Path) -> SQLiteDatabase:
    return SQLiteDatabase(_database_path(tmp_path))


def _inspector(
    database: SQLiteDatabase,
    *,
    max_records_per_store: int = 250,
    max_issues: int = 100,
) -> PersistenceRecoveryInspector:
    return PersistenceRecoveryInspector(
        database,
        max_records_per_store=max_records_per_store,
        max_issues=max_issues,
    )


def _migrate(database: SQLiteDatabase) -> None:
    with database.connection():
        pass


def _knowledge_record(content: str = "canonical fact") -> KnowledgeRecord:
    return KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content=content,
        created_at=_T0,
    )


def _procedure_record(
    procedure_id: ProcedureId | None = None,
    revision: int = 1,
    content: str = '{"opaque": true}',
) -> ProcedureRecord:
    return ProcedureRecord.create(
        payload=ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=content),
        procedure_id=procedure_id,
        revision=revision,
        created_at=_T0,
    )


def _episode_record(index: int, summary: str = "episode") -> EpisodeRecord:
    return EpisodeRecord.create(
        outcome=EpisodeOutcome.SUCCEEDED,
        summary=summary,
        created_at=datetime(2026, 9, 1, 8, 0, index, tzinfo=UTC),
    )


def _event(source: str = "recovery-unit") -> Event:
    return Event.create(event_type=EventType.SYSTEM_STARTED, source=source, timestamp=_T0)


def _negative_record(reference: str = "approach-x") -> NegativeExperienceRecord:
    return NegativeExperienceRecord.create(
        attempt=AttemptReference(kind=AttemptKind.APPROACH, reference=reference),
        failure=FailureReference(reason_code="timeout"),
        observed_at=_T0,
    )


def _artifact_record(locator: str = r"C:\AgentX\out.txt") -> ArtifactRecord:
    return ArtifactRecord.create(kind=ArtifactKind.FILE, locator=locator, created_at=_T0)


def _audit_snapshot(reason: str = "unit audit") -> AuditRecordSnapshot:
    return AuditRecordSnapshot(
        audit_id=uuid4(),
        timestamp=_T0,
        operation="security.unit",
        outcome="allow",
        reason=reason,
        risk_level="R0",
    )


def _raw_connection(database: SQLiteDatabase) -> sqlite3.Connection:
    """TEST-ONLY raw connection used to construct deliberate corruption."""
    connection = sqlite3.connect(str(database.path))
    connection.row_factory = sqlite3.Row
    return connection


def _raw_row(database: SQLiteDatabase, table: str, key_column: str, key: str) -> tuple[object, ...]:
    with _raw_connection(database) as connection:
        row = connection.execute(f"SELECT * FROM {table} WHERE {key_column} = ?", (key,)).fetchone()
    assert row is not None
    return tuple(row)


def _migration_rows(database: SQLiteDatabase) -> tuple[tuple[object, ...], ...]:
    with _raw_connection(database) as connection:
        rows = connection.execute(
            "SELECT version, name FROM agentx_schema_migrations ORDER BY version"
        ).fetchall()
    return tuple(tuple(row) for row in rows)


def _probe_by_store(assessment: RecoveryAssessment, store: str) -> StoreProbeResult:
    for probe in assessment.probes:
        if probe.store == store:
            return probe
    raise AssertionError(f"no probe for store {store!r}")


# ---------------------------------------------------------------------------
# Fresh / healthy / restart
# ---------------------------------------------------------------------------


def test_fresh_healthy_database_is_healthy(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.HEALTHY
    assert assessment.startup_recommendation is StartupRecommendation.CONTINUE
    assert assessment.issues == ()
    assert assessment.applied_schema_version == len(_MIGRATIONS)
    assert assessment.supported_schema_version == len(_MIGRATIONS)
    assert len(assessment.probes) == 7
    for probe in assessment.probes:
        assert probe.rows_present == 0
        assert probe.rows_checked == 0
        assert probe.scan_completed is True


def test_empty_database_file_without_schema_is_fresh_and_healthy(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    with sqlite3.connect(path):
        pass

    assessment = _inspector(_database(tmp_path)).assess()

    assert assessment.disposition is RecoveryDisposition.HEALTHY
    assert assessment.issues == ()
    assert assessment.probes == ()
    assert assessment.applied_schema_version == 0
    assert any("first connection will apply all migrations" in note for note in assessment.notes)


def test_restart_reassessment_stays_healthy(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    KnowledgeStore(database).insert(_knowledge_record(content="first run"))

    first = _inspector(database).assess()
    assert first.disposition is RecoveryDisposition.HEALTHY

    restarted = SQLiteDatabase(_database_path(tmp_path))
    KnowledgeStore(restarted).insert(_knowledge_record(content="second run"))
    second = _inspector(restarted).assess()

    assert second.disposition is RecoveryDisposition.HEALTHY
    probe = _probe_by_store(second, "knowledge_store")
    assert probe.rows_present == 2
    assert probe.rows_checked == 2
    assert probe.scan_completed is True


def test_migrated_database_with_all_stores_populated_is_healthy(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    KnowledgeStore(database).insert(_knowledge_record())
    ProcedureStore(database).insert(_procedure_record())
    EpisodeStore(database).append(_episode_record(1))
    EventJournal(database).append(_event())
    NegativeExperienceStore(database).append(_negative_record())
    ArtifactStore(database).register(_artifact_record())
    AuditStore(database).append(_audit_snapshot())

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.HEALTHY
    assert assessment.issues == ()
    expected = {
        "knowledge_store": 1,
        "procedure_store": 1,
        "episode_store": 1,
        "event_journal": 1,
        "negative_experience_store": 1,
        "artifact_store": 1,
        "audit_store": 1,
    }
    for probe in assessment.probes:
        assert probe.rows_checked == expected[probe.store]
        assert probe.scan_completed is True


# ---------------------------------------------------------------------------
# Migration integrity
# ---------------------------------------------------------------------------


def test_newer_schema_version_blocks_and_canonical_open_agrees(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    with _raw_connection(database) as connection:
        connection.execute(
            "INSERT INTO agentx_schema_migrations (version, name) VALUES (?, ?)",
            (len(_MIGRATIONS) + 1, "future_migration"),
        )

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.BLOCK_STARTUP
    assert assessment.startup_recommendation is StartupRecommendation.STOP_AND_ESCALATE
    assert [issue.kind for issue in assessment.issues] == [
        RecoveryCheckKind.SCHEMA_VERSION_NEWER_THAN_SUPPORTED
    ]
    assert assessment.applied_schema_version == len(_MIGRATIONS) + 1
    assert assessment.probes == ()
    with pytest.raises(UnsupportedSchemaVersionError), database.connection():
        pass


def test_migration_gap_blocks_and_canonical_open_agrees(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    with _raw_connection(database) as connection:
        connection.execute("DELETE FROM agentx_schema_migrations WHERE version = 3")

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.BLOCK_STARTUP
    assert assessment.issues[0].kind is RecoveryCheckKind.MIGRATION_HISTORY_INVALID
    with pytest.raises(MigrationError), database.connection():
        pass


def test_migration_name_mismatch_blocks_and_canonical_open_agrees(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    with _raw_connection(database) as connection:
        connection.execute(
            "UPDATE agentx_schema_migrations SET name = ? WHERE version = 4",
            ("renamed_migration",),
        )

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.BLOCK_STARTUP
    issue = assessment.issues[0]
    assert issue.kind is RecoveryCheckKind.MIGRATION_HISTORY_INVALID
    assert "version 4" in issue.detail
    with pytest.raises(MigrationError), database.connection():
        pass


def test_empty_migration_table_blocks(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    with _raw_connection(database) as connection:
        connection.execute("DELETE FROM agentx_schema_migrations")

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.BLOCK_STARTUP
    assert assessment.issues[0].kind is RecoveryCheckKind.MIGRATION_HISTORY_INVALID
    assert "no migration records" in assessment.issues[0].detail
    with pytest.raises(MigrationError), database.connection():
        pass


def test_missing_metadata_table_with_store_data_blocks(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    KnowledgeStore(database).insert(_knowledge_record())
    with _raw_connection(database) as connection:
        connection.execute("DROP TABLE agentx_schema_migrations")

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.BLOCK_STARTUP
    issue = assessment.issues[0]
    assert issue.kind is RecoveryCheckKind.MIGRATION_HISTORY_INVALID
    assert "metadata table is missing" in issue.detail


def test_older_schema_version_is_reported_with_deferred_probes(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    EpisodeStore(database).append(_episode_record(1))
    with _raw_connection(database) as connection:
        connection.execute("DROP TABLE agentx_artifacts")
        connection.execute("DROP TABLE agentx_audit_records")
        connection.execute("DROP TABLE agentx_knowledge_contradictions")
        connection.execute("DROP TABLE agentx_knowledge_supersessions")
        connection.execute("DROP TABLE agentx_negative_experiences")
        connection.execute("DELETE FROM agentx_schema_migrations WHERE version >= 6")

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.HEALTHY
    assert assessment.applied_schema_version == 5
    assert assessment.probes == ()
    assert any("older than supported" in note for note in assessment.notes)
    assert not any(
        issue.kind is RecoveryCheckKind.EXPECTED_TABLE_MISSING for issue in assessment.issues
    )


# ---------------------------------------------------------------------------
# Expected structure
# ---------------------------------------------------------------------------


def test_missing_expected_table_blocks_and_is_not_probed(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    KnowledgeStore(database).insert(_knowledge_record())
    with _raw_connection(database) as connection:
        connection.execute("DROP TABLE agentx_knowledge")

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.BLOCK_STARTUP
    missing = [
        issue
        for issue in assessment.issues
        if issue.kind is RecoveryCheckKind.EXPECTED_TABLE_MISSING
    ]
    assert len(missing) == 1
    assert missing[0].store == "knowledge_store"
    assert len(assessment.probes) == 6
    with pytest.raises(KnowledgeStoreStorageError):
        KnowledgeStore(database).get(KnowledgeId.parse(str(uuid4())))


# ---------------------------------------------------------------------------
# Corrupt rows through canonical store APIs
# ---------------------------------------------------------------------------


def test_corrupt_knowledge_row_is_detected_without_rewrite_or_deletion(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    store = KnowledgeStore(database)
    good = _knowledge_record(content="good claim")
    bad = _knowledge_record(content="bad claim")
    store.insert(good)
    store.insert(bad)
    bad_id = bad.knowledge_id.to_str()
    with _raw_connection(database) as connection:
        connection.execute(
            "UPDATE agentx_knowledge SET record_json = ? WHERE knowledge_id = ?",
            ("{definitely not json", bad_id),
        )
    before = _raw_row(database, "agentx_knowledge", "knowledge_id", bad_id)

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.DEGRADED_READ_ONLY
    assert assessment.startup_recommendation is StartupRecommendation.CONTINUE_DEGRADED
    assert len(assessment.issues) == 1
    issue = assessment.issues[0]
    assert issue.kind is RecoveryCheckKind.CORRUPT_RECORD
    assert issue.store == "knowledge_store"
    assert issue.identity == bad_id
    probe = _probe_by_store(assessment, "knowledge_store")
    assert probe.rows_present == 2
    assert probe.rows_checked == 1
    assert probe.scan_completed is True
    assert _raw_row(database, "agentx_knowledge", "knowledge_id", bad_id) == before
    with pytest.raises(CorruptKnowledgeRecordError):
        store.get(bad.knowledge_id)
    assert store.get(good.knowledge_id) is not None


def test_corrupt_procedure_row_is_detected_and_siblings_stay_readable(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    store = ProcedureStore(database)
    first = _procedure_record()
    store.insert(first)
    second = _procedure_record(
        procedure_id=first.procedure_id,
        revision=2,
        content='{"opaque": "second revision"}',
    )
    store.insert(second)
    with _raw_connection(database) as connection:
        connection.execute(
            "UPDATE agentx_procedures SET record_json = ? WHERE procedure_id = ? AND revision = 1",
            ("not-json", first.procedure_id.to_str()),
        )

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.DEGRADED_READ_ONLY
    issues = [
        issue for issue in assessment.issues if issue.kind is RecoveryCheckKind.CORRUPT_RECORD
    ]
    assert len(issues) == 1
    assert issues[0].store == "procedure_store"
    assert issues[0].identity is not None
    assert "revision 1" in issues[0].identity
    probe = _probe_by_store(assessment, "procedure_store")
    assert probe.rows_present == 2
    assert probe.rows_checked == 1
    assert probe.scan_completed is True
    with pytest.raises(CorruptProcedureRecordError):
        store.get(first.procedure_id, 1)
    assert store.get(first.procedure_id, 2) is not None


def test_corrupt_episode_row_is_detected_and_scan_continues(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    store = EpisodeStore(database)
    store.append(_episode_record(1))
    store.append(_episode_record(2))
    store.append(_episode_record(3))
    with _raw_connection(database) as connection:
        connection.execute(
            "UPDATE agentx_episodes SET episode_json = ? WHERE sequence = 2",
            ("{bad json",),
        )

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.DEGRADED_READ_ONLY
    issues = [
        issue for issue in assessment.issues if issue.kind is RecoveryCheckKind.CORRUPT_RECORD
    ]
    assert len(issues) == 1
    assert issues[0].store == "episode_store"
    probe = _probe_by_store(assessment, "episode_store")
    assert probe.rows_present == 3
    assert probe.rows_checked == 2
    assert probe.scan_completed is True


def test_corrupt_event_journal_row_is_detected(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    journal = EventJournal(database)
    journal.append(_event())
    journal.append(_event())
    with _raw_connection(database) as connection:
        connection.execute(
            "UPDATE agentx_event_journal SET event_json = ? WHERE sequence = 1",
            ("not-an-event",),
        )

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.DEGRADED_READ_ONLY
    issues = [
        issue for issue in assessment.issues if issue.kind is RecoveryCheckKind.CORRUPT_RECORD
    ]
    assert len(issues) == 1
    assert issues[0].store == "event_journal"
    probe = _probe_by_store(assessment, "event_journal")
    assert probe.rows_present == 2
    assert probe.rows_checked == 1


def test_corrupt_audit_row_is_detected(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    store = AuditStore(database)
    store.append(_audit_snapshot())
    store.append(_audit_snapshot())
    with _raw_connection(database) as connection:
        connection.execute(
            "UPDATE agentx_audit_records SET audit_json = ? WHERE sequence = 1",
            ("{bad json",),
        )

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.DEGRADED_READ_ONLY
    issues = [
        issue for issue in assessment.issues if issue.kind is RecoveryCheckKind.CORRUPT_RECORD
    ]
    assert len(issues) == 1
    assert issues[0].store == "audit_store"
    probe = _probe_by_store(assessment, "audit_store")
    assert probe.rows_present == 2
    assert probe.rows_checked == 1


def test_corrupt_negative_experience_row_is_detected(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    store = NegativeExperienceStore(database)
    store.append(_negative_record())
    with _raw_connection(database) as connection:
        connection.execute(
            "UPDATE agentx_negative_experiences SET record_json = ? WHERE sequence = 1",
            ("{bad json",),
        )

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.DEGRADED_READ_ONLY
    issue = assessment.issues[0]
    assert issue.kind is RecoveryCheckKind.CORRUPT_RECORD
    assert issue.store == "negative_experience_store"


def test_corrupt_artifact_row_is_detected(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    store = ArtifactStore(database)
    store.register(_artifact_record())
    with _raw_connection(database) as connection:
        connection.execute(
            "UPDATE agentx_artifacts SET artifact_json = ? WHERE sequence = 1",
            ("{bad json",),
        )

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.DEGRADED_READ_ONLY
    issue = assessment.issues[0]
    assert issue.kind is RecoveryCheckKind.CORRUPT_RECORD
    assert issue.store == "artifact_store"


def test_identity_and_detail_are_length_capped_for_hostile_rows(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    store = EpisodeStore(database)
    store.append(_episode_record(1))
    hostile_identity = "permission=ADMIN " * 40
    with _raw_connection(database) as connection:
        connection.execute(
            "UPDATE agentx_episodes SET episode_id = ? WHERE sequence = 1",
            (hostile_identity,),
        )

    assessment = _inspector(database).assess()

    issue = assessment.issues[0]
    assert issue.kind is RecoveryCheckKind.CORRUPT_RECORD
    assert issue.identity is not None
    assert len(issue.identity) <= 128
    assert len(issue.detail) <= 240


# ---------------------------------------------------------------------------
# Bounds and caps
# ---------------------------------------------------------------------------


def test_bounded_scan_reports_partial_coverage_honestly(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    store = EpisodeStore(database)
    for index in range(1, 21):
        store.append(_episode_record(index))

    assessment = _inspector(database, max_records_per_store=5).assess()

    assert assessment.disposition is RecoveryDisposition.HEALTHY
    probe = _probe_by_store(assessment, "episode_store")
    assert probe.rows_present == 20
    assert probe.rows_checked == 5
    assert probe.scan_completed is False
    assert probe.note is not None
    assert "first 5 of 20 rows assessed" in probe.note
    assert assessment.issues == ()


def test_bounded_scan_applies_to_id_ordered_stores_too(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    store = KnowledgeStore(database)
    for index in range(12):
        store.insert(_knowledge_record(content=f"claim {index}"))

    assessment = _inspector(database, max_records_per_store=4).assess()

    probe = _probe_by_store(assessment, "knowledge_store")
    assert probe.rows_present == 12
    assert probe.rows_checked == 4
    assert probe.scan_completed is False
    assert "first 4 of 12 rows assessed" in (probe.note or "")


def test_issue_cap_suppresses_but_counts_remaining_issues(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    store = KnowledgeStore(database)
    records = [_knowledge_record(content=f"claim {index}") for index in range(12)]
    for record in records:
        store.insert(record)
    with _raw_connection(database) as connection:
        for record in records:
            connection.execute(
                "UPDATE agentx_knowledge SET record_json = ? WHERE knowledge_id = ?",
                ("{bad json", record.knowledge_id.to_str()),
            )

    assessment = _inspector(database, max_issues=4, max_records_per_store=250).assess()

    assert assessment.disposition is RecoveryDisposition.DEGRADED_READ_ONLY
    assert len(assessment.issues) == 4
    assert assessment.suppressed_issue_count == 8
    assert all(issue.kind is RecoveryCheckKind.CORRUPT_RECORD for issue in assessment.issues)
    probe = _probe_by_store(assessment, "knowledge_store")
    assert probe.rows_present == 12
    assert probe.rows_checked == 0


# ---------------------------------------------------------------------------
# Determinism and immutability
# ---------------------------------------------------------------------------


def test_issue_ordering_is_deterministic_across_insertion_orders(tmp_path: Path) -> None:
    first_path = tmp_path / "first.sqlite3"
    second_path = tmp_path / "second.sqlite3"
    records = [_knowledge_record(content=f"claim {index}") for index in range(6)]
    for path, order in (
        (first_path, records),
        (second_path, list(reversed(records))),
    ):
        database = SQLiteDatabase(path)
        _migrate(database)
        store = KnowledgeStore(database)
        for record in order:
            store.insert(record)
        with sqlite3.connect(path) as connection:
            for record in order:
                connection.execute(
                    "UPDATE agentx_knowledge SET record_json = ? WHERE knowledge_id = ?",
                    ("{bad json", record.knowledge_id.to_str()),
                )

    first = _inspector(SQLiteDatabase(first_path)).assess()
    second = _inspector(SQLiteDatabase(second_path)).assess()

    assert first == second
    identities = [identity for issue in first.issues if (identity := issue.identity) is not None]
    assert identities == sorted(identities)
    assert identities == sorted(record.knowledge_id.to_str() for record in records)


def test_assessment_is_immutable(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    assessment = _inspector(database).assess()

    with pytest.raises(FrozenInstanceError):
        assessment.disposition = RecoveryDisposition.BLOCK_STARTUP  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        assessment.issues = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        assessment.notes = ("tampered",)  # type: ignore[misc]


def test_repeated_assessment_is_stable_and_side_effect_free(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    store = EpisodeStore(database)
    store.append(_episode_record(1))
    store.append(_episode_record(2))
    with _raw_connection(database) as connection:
        connection.execute(
            "UPDATE agentx_episodes SET episode_json = ? WHERE sequence = 2", ("{bad",)
        )
    migrations_before = _migration_rows(database)

    first = _inspector(database).assess()
    second = _inspector(database).assess()

    assert first == second
    assert _migration_rows(database) == migrations_before


# ---------------------------------------------------------------------------
# Hostile data and fail-closed behavior
# ---------------------------------------------------------------------------


def test_hostile_stored_strings_never_alter_disposition(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    content = "; ".join(_HOSTILE_MARKERS)
    KnowledgeStore(database).insert(_knowledge_record(content=content))
    ProcedureStore(database).insert(_procedure_record(content=content))
    EpisodeStore(database).append(_episode_record(1, summary=content))
    EventJournal(database).append(_event(source=content))
    NegativeExperienceStore(database).append(_negative_record(reference=content))
    ArtifactStore(database).register(_artifact_record(locator=content))
    AuditStore(database).append(_audit_snapshot(reason=content))

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.HEALTHY
    assert assessment.issues == ()
    all_text = assessment.disposition.value + " ".join(assessment.notes)
    for marker in ("ignore recovery", "verified=true", "delete corrupted rows", "continue startup"):
        assert marker not in all_text


def test_hostile_content_cannot_mask_a_real_corrupt_row(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    store = KnowledgeStore(database)
    store.insert(_knowledge_record(content="; ".join(_HOSTILE_MARKERS)))
    bad = _knowledge_record(content="innocent")
    store.insert(bad)
    with _raw_connection(database) as connection:
        connection.execute(
            "UPDATE agentx_knowledge SET record_json = ? WHERE knowledge_id = ?",
            ('{"schema_version": 1, "knowledge_id": "x"}', bad.knowledge_id.to_str()),
        )

    assessment = _inspector(database).assess()

    assert assessment.disposition is RecoveryDisposition.DEGRADED_READ_ONLY
    assert len(assessment.issues) == 1
    assert assessment.issues[0].identity == bad.knowledge_id.to_str()


def test_missing_database_file_fails_closed(tmp_path: Path) -> None:
    assessment = _inspector(_database(tmp_path)).assess()

    assert assessment.disposition is RecoveryDisposition.INSUFFICIENT_EVIDENCE
    assert assessment.startup_recommendation is StartupRecommendation.STOP_AND_ESCALATE
    assert [issue.kind for issue in assessment.issues] == [RecoveryCheckKind.DATABASE_FILE_MISSING]
    assert assessment.applied_schema_version is None


def test_unreadable_database_file_fails_closed(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    path.write_bytes(b"this is definitely not a sqlite database file\n" * 8)

    assessment = _inspector(_database(tmp_path)).assess()

    assert assessment.disposition is RecoveryDisposition.INSUFFICIENT_EVIDENCE
    assert assessment.startup_recommendation is StartupRecommendation.STOP_AND_ESCALATE
    assert [issue.kind for issue in assessment.issues] == [RecoveryCheckKind.DATABASE_UNREADABLE]
    assert assessment.probes == ()
    assert assessment.applied_schema_version is None


def test_corrupt_row_never_becomes_missing_or_empty(tmp_path: Path) -> None:
    database = _database(tmp_path)
    _migrate(database)
    store = KnowledgeStore(database)
    first = _knowledge_record(content="one")
    store.insert(first)
    store.insert(_knowledge_record(content="two"))
    with _raw_connection(database) as connection:
        connection.execute(
            "UPDATE agentx_knowledge SET record_json = ? WHERE knowledge_id = ?",
            ("{broken", first.knowledge_id.to_str()),
        )

    assessment = _inspector(database).assess()

    probe = _probe_by_store(assessment, "knowledge_store")
    assert probe.rows_present == 2
    assert probe.rows_checked == 1
    assert assessment.issues[0].kind is RecoveryCheckKind.CORRUPT_RECORD
    assert assessment.disposition is RecoveryDisposition.DEGRADED_READ_ONLY


def test_constructor_validation(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with pytest.raises(TypeError):
        PersistenceRecoveryInspector(database=database.path)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        PersistenceRecoveryInspector(database, max_records_per_store=0)
    with pytest.raises(ValueError):
        PersistenceRecoveryInspector(database, max_issues=0)


def test_recommendation_mapping_is_closed() -> None:
    assert startup_recommendation_for(RecoveryDisposition.HEALTHY) is StartupRecommendation.CONTINUE
    assert (
        startup_recommendation_for(RecoveryDisposition.DEGRADED_READ_ONLY)
        is StartupRecommendation.CONTINUE_DEGRADED
    )
    assert (
        startup_recommendation_for(RecoveryDisposition.BLOCK_STARTUP)
        is StartupRecommendation.STOP_AND_ESCALATE
    )
    assert (
        startup_recommendation_for(RecoveryDisposition.INSUFFICIENT_EVIDENCE)
        is StartupRecommendation.STOP_AND_ESCALATE
    )
