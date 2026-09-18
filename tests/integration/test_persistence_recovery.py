"""Integration tests for M6.03 durable-state recovery assessment.

Integration flow (real temporary SQLite database + canonical stores):

1. create the database and write canonical records through the public stores;
2. close everything, reopen with a fresh ``SQLiteDatabase`` handle, assess —
   the result is healthy and matches what was written;
3. deliberately tamper durable state (TEST CODE ONLY, raw SQL) to simulate
   corruption;
4. assess again and prove: corruption is detected, the record remains present
   and corrupt, no autonomous rewrite happened, and a restart does not
   magically erase the evidence.
"""

from __future__ import annotations

import sqlite3
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
)
from agentx.infrastructure.negative_experience_store import NegativeExperienceStore
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import (
    CorruptProcedureRecordError,
    ProcedureStore,
)
from agentx.infrastructure.recovery import (
    PersistenceRecoveryInspector,
    RecoveryAssessment,
    RecoveryCheckKind,
    RecoveryDisposition,
    RecoveryIssue,
    StartupRecommendation,
)

_T0 = datetime(2026, 9, 2, 9, 30, 0, tzinfo=UTC)


def _path(tmp_path: Path) -> Path:
    return tmp_path / "agentx.sqlite3"


def _write_canonical_state(database: SQLiteDatabase) -> dict[str, str]:
    """Populate every canonical store and return identity handles."""
    knowledge = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="integration knowledge claim",
        created_at=_T0,
    )
    KnowledgeStore(database).insert(knowledge)

    procedure = ProcedureRecord.create(
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content='{"opaque": "integration"}',
        ),
        created_at=_T0,
    )
    ProcedureStore(database).insert(procedure)

    episode = EpisodeRecord.create(
        outcome=EpisodeOutcome.SUCCEEDED,
        summary="integration episode",
        created_at=_T0,
    )
    EpisodeStore(database).append(episode)

    event = Event.create(
        event_type=EventType.SYSTEM_STARTED,
        source="recovery-integration",
        timestamp=_T0,
    )
    EventJournal(database).append(event)

    negative = NegativeExperienceRecord.create(
        attempt=AttemptReference(kind=AttemptKind.APPROACH, reference="integration-approach"),
        failure=FailureReference(reason_code="timeout"),
        observed_at=_T0,
    )
    NegativeExperienceStore(database).append(negative)

    artifact = ArtifactRecord.create(
        kind=ArtifactKind.FILE,
        locator=r"C:\AgentX\integration.txt",
        created_at=_T0,
    )
    ArtifactStore(database).register(artifact)

    audit = AuditRecordSnapshot(
        audit_id=uuid4(),
        timestamp=_T0,
        operation="security.integration",
        outcome="allow",
        reason="integration audit",
        risk_level="R0",
    )
    AuditStore(database).append(audit)

    return {
        "knowledge_id": knowledge.knowledge_id.to_str(),
        "procedure_id": procedure.procedure_id.to_str(),
        "episode_id": episode.episode_id.to_str(),
        "event_id": str(event.event_id),
        "negative_experience_id": negative.negative_experience_id.to_str(),
        "artifact_id": artifact.artifact_id.to_str(),
        "audit_id": str(audit.audit_id),
    }


def _raw_tamper(path: Path, sql: str, parameters: tuple[object, ...] = ()) -> None:
    """TEST-ONLY tampering helper; production code never does this."""
    with sqlite3.connect(path) as connection:
        connection.execute(sql, parameters)


def _raw_value(path: Path, sql: str, parameters: tuple[object, ...] = ()) -> object:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(sql, parameters).fetchone()
    assert row is not None
    return row[0]


def _corrupt_issues(assessment: RecoveryAssessment) -> list[RecoveryIssue]:
    return [issue for issue in assessment.issues if issue.kind is RecoveryCheckKind.CORRUPT_RECORD]


def test_full_lifecycle_write_reopen_assess_is_healthy(tmp_path: Path) -> None:
    path = _path(tmp_path)
    identities = _write_canonical_state(SQLiteDatabase(path))

    # Restart: drop every handle and open a brand-new SQLiteDatabase.
    assessment = PersistenceRecoveryInspector(SQLiteDatabase(path)).assess()

    assert assessment.disposition is RecoveryDisposition.HEALTHY
    assert assessment.startup_recommendation is StartupRecommendation.CONTINUE
    assert assessment.issues == ()
    assert assessment.applied_schema_version == 9
    by_store = {probe.store: probe for probe in assessment.probes}
    assert by_store["knowledge_store"].rows_checked == 1
    assert by_store["procedure_store"].rows_checked == 1
    assert by_store["episode_store"].rows_checked == 1
    assert by_store["event_journal"].rows_checked == 1
    assert by_store["negative_experience_store"].rows_checked == 1
    assert by_store["artifact_store"].rows_checked == 1
    assert by_store["audit_store"].rows_checked == 1
    # The canonical store still returns exactly what was written.
    knowledge = KnowledgeStore(SQLiteDatabase(path)).get(
        KnowledgeId.parse(identities["knowledge_id"])
    )
    assert knowledge is not None
    assert knowledge.content == "integration knowledge claim"


def test_corruption_is_detected_and_survives_restart_without_rewrite(
    tmp_path: Path,
) -> None:
    path = _path(tmp_path)
    identities = _write_canonical_state(SQLiteDatabase(path))

    # Tamper with durable state (TEST CODE ONLY): a knowledge row, an episode
    # row, and a procedure row become undecodable.
    _raw_tamper(
        path,
        "UPDATE agentx_knowledge SET record_json = ? WHERE knowledge_id = ?",
        ("{corrupt knowledge json", identities["knowledge_id"]),
    )
    _raw_tamper(
        path,
        "UPDATE agentx_episodes SET episode_json = ? WHERE episode_id = ?",
        ("{corrupt episode json", identities["episode_id"]),
    )
    _raw_tamper(
        path,
        "UPDATE agentx_procedures SET record_json = ? WHERE procedure_id = ? AND revision = 1",
        ("{corrupt procedure json", identities["procedure_id"]),
    )

    assessment = PersistenceRecoveryInspector(SQLiteDatabase(path)).assess()

    assert assessment.disposition is RecoveryDisposition.DEGRADED_READ_ONLY
    assert assessment.startup_recommendation is StartupRecommendation.CONTINUE_DEGRADED
    corrupt = _corrupt_issues(assessment)
    assert len(corrupt) == 3
    stores = {issue.store for issue in corrupt}
    assert stores == {"knowledge_store", "episode_store", "procedure_store"}
    identity_by_store = {
        issue.store: issue.identity for issue in corrupt if issue.identity is not None
    }
    assert identity_by_store["knowledge_store"] == identities["knowledge_id"]
    assert identity_by_store["episode_store"] == identities["episode_id"]
    assert "revision 1" in identity_by_store["procedure_store"]

    # Evidence remains: records are still present and still corrupt, with no
    # autonomous rewrite and no deletion.
    assert (
        _raw_value(
            path,
            "SELECT record_json FROM agentx_knowledge WHERE knowledge_id = ?",
            (identities["knowledge_id"],),
        )
        == "{corrupt knowledge json"
    )
    assert (
        _raw_value(
            path,
            "SELECT episode_json FROM agentx_episodes WHERE episode_id = ?",
            (identities["episode_id"],),
        )
        == "{corrupt episode json"
    )

    # Restart again: the evidence is not erased by reopening or by another
    # assessment.
    restarted = PersistenceRecoveryInspector(SQLiteDatabase(path)).assess()
    assert restarted == assessment

    # Canonical stores still fail closed on the corrupt rows.
    with pytest.raises(CorruptKnowledgeRecordError):
        KnowledgeStore(SQLiteDatabase(path)).get(KnowledgeId.parse(identities["knowledge_id"]))
    with pytest.raises(CorruptProcedureRecordError):
        ProcedureStore(SQLiteDatabase(path)).get(ProcedureId.parse(identities["procedure_id"]), 1)
    # Healthy rows in the same stores stay reachable.
    healthy_procedure = _write_one_more_procedure(path)
    assert healthy_procedure is not None


def _write_one_more_procedure(path: Path) -> object:
    record = ProcedureRecord.create(
        payload=ProcedurePayload(
            kind=ProcedurePayloadKind.CANONICAL_JSON,
            content='{"opaque": "still healthy"}',
        ),
        created_at=_T0,
    )
    ProcedureStore(SQLiteDatabase(path)).insert(record)
    return ProcedureStore(SQLiteDatabase(path)).get(record.procedure_id, 1)


def test_tampered_migration_history_blocks_after_restart(tmp_path: Path) -> None:
    path = _path(tmp_path)
    _write_canonical_state(SQLiteDatabase(path))
    _raw_tamper(path, "DELETE FROM agentx_schema_migrations WHERE version = 7")

    assessment = PersistenceRecoveryInspector(SQLiteDatabase(path)).assess()

    assert assessment.disposition is RecoveryDisposition.BLOCK_STARTUP
    assert assessment.startup_recommendation is StartupRecommendation.STOP_AND_ESCALATE
    assert any(
        issue.kind is RecoveryCheckKind.MIGRATION_HISTORY_INVALID for issue in assessment.issues
    )
    # The migration metadata table still physically holds the tampered state:
    # no assessment rewrote it.
    assert _raw_value(path, "SELECT COUNT(*) FROM agentx_schema_migrations") == 8
