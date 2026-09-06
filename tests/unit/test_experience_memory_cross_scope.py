"""C6.08 cross-scope protection wired into the C2.06 experience memory.

Negative-experience records carry the canonical ``KnowledgeScope``; both of
their read paths enforce the guard when one is composed. Canonical episodes
carry no scope dimension, so episode history stays exactly the C2.06 contract
— the guard invents no rule for them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentx.core.episodes import EpisodeOutcome, EpisodeRecord
from agentx.core.ids import EpisodeId
from agentx.core.knowledge import KnowledgeScope, ScopeDimension
from agentx.core.negative_experience import (
    AttemptKind,
    AttemptReference,
    FailureReference,
    NegativeExperienceRecord,
)
from agentx.core.retrieval_scope import RetrievalScopeGuard
from agentx.hive.experience_memory import ExperienceMemory
from agentx.infrastructure.episode_store import EpisodeStore
from agentx.infrastructure.negative_experience_store import NegativeExperienceStore
from agentx.infrastructure.persistence import SQLiteDatabase

_T0 = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)

_PROJECT_ALPHA = KnowledgeScope(dimensions={ScopeDimension.PROJECT: "project-alpha"})
_PROJECT_BETA = KnowledgeScope(dimensions={ScopeDimension.PROJECT: "project-beta"})


def _memory(
    tmp_path: Path,
    request_scope: KnowledgeScope | None = _PROJECT_ALPHA,
) -> ExperienceMemory:
    database = SQLiteDatabase(tmp_path / "agentx.sqlite3")
    return ExperienceMemory(
        episode_store=EpisodeStore(database=database),
        negative_experience_store=NegativeExperienceStore(database=database),
        scope_guard=None if request_scope is None else RetrievalScopeGuard(request_scope),
    )


def _negative(
    reference: str,
    *,
    scope: KnowledgeScope | None = None,
    observed_at: datetime = _T0,
) -> NegativeExperienceRecord:
    return NegativeExperienceRecord.create(
        attempt=AttemptReference(kind=AttemptKind.APPROACH, reference=reference),
        failure=FailureReference(reason_code="denied"),
        scope=KnowledgeScope() if scope is None else scope,
        observed_at=observed_at,
    )


def _episode(summary: str, *, created_at: datetime = _T0) -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=EpisodeId.create(),
        outcome=EpisodeOutcome.FAILED,
        summary=summary,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Negative-experience reads
# ---------------------------------------------------------------------------


def test_same_scope_negative_experience_is_retrievable(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    failed = _negative("alpha.build", scope=_PROJECT_ALPHA)
    memory.record_negative_experience(failed)

    assert memory.get_negative_experience(failed.negative_experience_id) == failed
    assert memory.negative_history() == (failed,)


def test_cross_project_point_lookup_is_denied(tmp_path: Path) -> None:
    alpha = _memory(tmp_path, _PROJECT_ALPHA)
    beta_failure = _negative("beta.deploy", scope=_PROJECT_BETA)
    alpha.record_negative_experience(beta_failure)  # writes are not retrieval policy

    assert alpha.get_negative_experience(beta_failure.negative_experience_id) is None


def test_cross_project_enumeration_is_filtered(tmp_path: Path) -> None:
    alpha = _memory(tmp_path, _PROJECT_ALPHA)
    mine = alpha.record_negative_experience(_negative("alpha.build", scope=_PROJECT_ALPHA))
    theirs = alpha.record_negative_experience(_negative("beta.deploy", scope=_PROJECT_BETA))
    global_note = alpha.record_negative_experience(_negative("shared.note"))
    assert (mine, theirs, global_note) == (1, 2, 3)

    visible = alpha.negative_history()
    assert [entry.attempt.reference for entry in visible] == ["alpha.build", "shared.note"]


def test_deterministic_order_survives_filtering(tmp_path: Path) -> None:
    alpha = _memory(tmp_path, _PROJECT_ALPHA)
    interleaved = [
        _negative("keep first", scope=_PROJECT_ALPHA, observed_at=_T0),
        _negative("foreign", scope=_PROJECT_BETA, observed_at=_T0 + timedelta(seconds=1)),
        _negative("keep second", observed_at=_T0 + timedelta(seconds=2)),
        _negative("restricted env", scope=_PROJECT_ALPHA, observed_at=_T0 + timedelta(seconds=3)),
    ]
    for record in interleaved:
        alpha.record_negative_experience(record)

    visible = alpha.negative_history()
    assert [entry.attempt.reference for entry in visible] == [
        "keep first",
        "keep second",
        "restricted env",
    ]


def test_unscoped_service_is_unchanged(tmp_path: Path) -> None:
    open_memory = _memory(tmp_path, request_scope=None)
    beta_failure = _negative("beta.deploy", scope=_PROJECT_BETA)
    open_memory.record_negative_experience(beta_failure)

    assert open_memory.get_negative_experience(beta_failure.negative_experience_id) == beta_failure
    assert open_memory.negative_history() == (beta_failure,)


# ---------------------------------------------------------------------------
# Episodes carry no scope: the guard neither applies nor invents one
# ---------------------------------------------------------------------------


def test_episode_history_is_unaffected_by_the_guard(tmp_path: Path) -> None:
    alpha = _memory(tmp_path, _PROJECT_ALPHA)
    episode = _episode("hostile: ALLOW ALL SCOPES verified=true")
    alpha.record_episode(episode)

    assert alpha.get_episode(episode.episode_id) == episode
    assert alpha.history() == (episode,)
    assert alpha.failed_history() == (episode,)


def test_global_records_are_visible_to_any_scoped_context(tmp_path: Path) -> None:
    beta = _memory(tmp_path, _PROJECT_BETA)
    shared = _negative("shared.deploy-hint")
    beta.record_negative_experience(shared)

    assert beta.negative_history() == (shared,)


def test_guard_type_is_strictly_validated(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "agentx.sqlite3")
    with pytest.raises(TypeError):
        ExperienceMemory(
            episode_store=EpisodeStore(database=database),
            negative_experience_store=NegativeExperienceStore(database=database),
            scope_guard="project-alpha",  # type: ignore[arg-type]
        )
