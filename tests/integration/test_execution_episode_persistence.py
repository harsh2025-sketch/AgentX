"""Restart-durability integration for the M1.03 execution episode boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.episodes import EpisodeOutcome
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.ids import EpisodeId, TaskId
from agentx.core.tasks import Task, TaskStatus
from agentx.execution_episode import ExecutionEpisodeCapture, ExecutionEpisodeRequest
from agentx.hive.experience_memory import ExperienceMemory
from agentx.infrastructure.episode_store import DuplicateEpisodeError, EpisodeStore
from agentx.infrastructure.negative_experience_store import NegativeExperienceStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 9, 8, 8, 0, tzinfo=UTC)
_TASK_ID = TaskId(UUID(int=1001))
_CORRELATION = UUID(int=1002)
_EPISODE_ID = EpisodeId(UUID(int=1003))
_EVENT_IDS = (UUID(int=1004), UUID(int=1005), UUID(int=1006))


def _memory(path: Path) -> ExperienceMemory:
    database = SQLiteDatabase(path=path)
    return ExperienceMemory(
        episode_store=EpisodeStore(database=database),
        negative_experience_store=NegativeExperienceStore(database=database),
    )


def _request() -> ExecutionEpisodeRequest:
    source = CancellationSource()
    context = ExecutionContext(
        task_id=_TASK_ID,
        correlation_id=_CORRELATION,
        cancellation_token=source.token,
    )
    experience = CausalExperience(
        task_id=_TASK_ID,
        correlation_id=_CORRELATION,
        episode_id=_EPISODE_ID,
        state_before=ExperienceState(
            captured_at=_T0,
            observation=ObservationPayload(value={"exists": False}),
        ),
        action=ActionPayload(name="files.write@1.0.0", data={"path": "C:/tmp/note.txt"}),
        action_at=_T0 + timedelta(seconds=1),
        observation=ObservationPayload(value={"write_returned": True}),
        observation_at=_T0 + timedelta(seconds=2),
        state_after=ExperienceState(
            captured_at=_T0 + timedelta(seconds=3),
            observation=ObservationPayload(value={"exists": True}),
        ),
        verification=VerificationPayload(passed=True, detail="postcondition checked"),
        verification_at=_T0 + timedelta(seconds=4),
        outcome=CausalOutcome.VERIFIED,
        outcome_at=_T0 + timedelta(seconds=5),
    )
    return ExecutionEpisodeRequest(
        task=Task.create(
            "Persist verified execution through canonical memory",
            task_id=_TASK_ID,
            status=TaskStatus.SUCCEEDED,
            created_at=_T0,
        ),
        context=context,
        experience=experience,
        episode_id=_EPISODE_ID,
        recorded_at=_T0 + timedelta(seconds=10),
        supporting_event_ids=_EVENT_IDS,
    )


def test_recorded_episode_survives_complete_object_restart(tmp_path: Path) -> None:
    path = tmp_path / "agentx.sqlite3"
    request = _request()
    database = SQLiteDatabase(path=path)
    episode_store = EpisodeStore(database=database)
    memory = ExperienceMemory(
        episode_store=episode_store,
        negative_experience_store=NegativeExperienceStore(database=database),
    )
    capture = ExecutionEpisodeCapture(memory=memory)

    recorded = capture.record(request)
    expected = recorded.episode
    assert recorded.sequence == 1

    del capture, memory, episode_store, database, recorded

    reopened_database = SQLiteDatabase(path=path)
    reopened_store = EpisodeStore(database=reopened_database)
    reopened_memory = ExperienceMemory(
        episode_store=reopened_store,
        negative_experience_store=NegativeExperienceStore(database=reopened_database),
    )
    restored = reopened_memory.get_episode(_EPISODE_ID)

    assert restored == expected
    assert restored is not None
    assert restored.outcome is EpisodeOutcome.SUCCEEDED
    assert restored.task_id == _TASK_ID
    assert restored.correlation_id == _CORRELATION
    assert restored.started_at == _T0
    assert restored.ended_at == _T0 + timedelta(seconds=5)
    assert restored.created_at == _T0 + timedelta(seconds=10)
    assert restored.supporting_event_ids == _EVENT_IDS
    assert restored.summary == "verified: Persist verified execution through canonical memory"


def test_duplicate_episode_identity_preserves_episode_store_contract(tmp_path: Path) -> None:
    path = tmp_path / "agentx.sqlite3"
    request = _request()
    capture = ExecutionEpisodeCapture(memory=_memory(path))
    capture.record(request)

    with pytest.raises(DuplicateEpisodeError):
        ExecutionEpisodeCapture(memory=_memory(path)).record(request)

    restored = _memory(path).get_episode(_EPISODE_ID)
    assert restored is not None
    assert _memory(path).history() == (restored,)


def test_boundary_adds_no_migration_or_sidecar_persistence(tmp_path: Path) -> None:
    path = tmp_path / "agentx.sqlite3"
    ExecutionEpisodeCapture(memory=_memory(path)).record(_request())

    with SQLiteDatabase(path=path).connection() as connection:
        migrations = tuple(
            row["name"]
            for row in connection.execute(
                "SELECT name FROM agentx_schema_migrations ORDER BY version"
            ).fetchall()
        )
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            ).fetchall()
        }

    assert "create_episode_store" in migrations
    assert all("execution_episode" not in name for name in migrations)
    assert "agentx_episodes" in tables
    assert all("execution_episode" not in name for name in tables)
    assert not any(path.parent.glob("*.json"))
