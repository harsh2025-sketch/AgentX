"""Unit tests for deterministic C3.01 trajectory normalization."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.episodes import EpisodeOutcome, EpisodeRecord
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.core.ids import EpisodeId, TaskId
from agentx.learning.trajectory import (
    NORMALIZED_TRAJECTORY_SCHEMA_VERSION,
    NormalizedTrajectory,
    NormalizedTrajectoryStep,
    TrajectoryNormalizationError,
    TrajectoryValidationError,
    normalize_trajectory,
)

_BASE = datetime(2026, 9, 5, 9, 0, 0, tzinfo=UTC)
_CORRELATION = UUID("11111111-2222-4333-8444-555555555555")
_TASK = TaskId.parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_EPISODE = EpisodeId.parse("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _experience(
    *,
    offset: int,
    outcome: CausalOutcome = CausalOutcome.VERIFIED,
    task_id: TaskId | None = _TASK,
    episode_id: EpisodeId | None = _EPISODE,
    correlation_id: UUID = _CORRELATION,
    detail: str | None = None,
) -> CausalExperience:
    before_at = _BASE + timedelta(seconds=offset)
    action_at = before_at + timedelta(seconds=1)
    observation_at = before_at + timedelta(seconds=2)
    after_at = before_at + timedelta(seconds=3)
    verification_at = before_at + timedelta(seconds=4)
    outcome_at = before_at + timedelta(seconds=5)

    if outcome is CausalOutcome.VERIFIED:
        observation = ObservationPayload(value={"offset": offset, "status": "observed"})
        state_after = ExperienceState(
            captured_at=after_at,
            observation=ObservationPayload(value={"offset": offset, "state": "after"}),
        )
        verification = VerificationPayload(passed=True, detail="checked")
        return CausalExperience(
            task_id=task_id,
            correlation_id=correlation_id,
            episode_id=episode_id,
            state_before=ExperienceState(
                captured_at=before_at,
                observation=ObservationPayload(value={"offset": offset, "state": "before"}),
            ),
            action=ActionPayload(name=f"example.action.{offset}", data={"offset": offset}),
            action_at=action_at,
            observation=observation,
            observation_at=observation_at,
            state_after=state_after,
            verification=verification,
            verification_at=verification_at,
            outcome=outcome,
            outcome_at=outcome_at,
            outcome_detail=detail,
        )

    return CausalExperience(
        task_id=task_id,
        correlation_id=correlation_id,
        episode_id=episode_id,
        state_before=ExperienceState(
            captured_at=before_at,
            observation=ObservationPayload(value={"offset": offset, "state": "before"}),
        ),
        action=ActionPayload(name=f"example.action.{offset}", data={"offset": offset}),
        action_at=action_at,
        outcome=outcome,
        outcome_at=outcome_at,
        outcome_detail=detail,
    )


def _episode_record() -> EpisodeRecord:
    return EpisodeRecord(
        episode_id=_EPISODE,
        task_id=_TASK,
        correlation_id=_CORRELATION,
        created_at=_BASE,
        started_at=_BASE,
        ended_at=_BASE + timedelta(minutes=1),
        outcome=EpisodeOutcome.SUCCEEDED,
        summary="normalized source episode",
    )


def test_normalizes_one_canonical_experience_without_copying_schema() -> None:
    experience = _experience(offset=0)

    trajectory = normalize_trajectory([experience])

    assert isinstance(trajectory, NormalizedTrajectory)
    assert trajectory.correlation_id == _CORRELATION
    assert trajectory.task_id == _TASK
    assert trajectory.episode_id == _EPISODE
    assert len(trajectory.steps) == 1
    assert trajectory.steps[0].sequence == 1
    assert trajectory.steps[0].experience is experience
    assert isinstance(trajectory.steps[0].experience, CausalExperience)


def test_same_canonical_input_produces_equal_output_and_identity() -> None:
    first = _experience(offset=0)
    second = _experience(offset=10)

    left = normalize_trajectory([first, second])
    right = normalize_trajectory([first, second])

    assert left == right
    assert left.trajectory_id == right.trajectory_id
    assert left.to_json() == right.to_json()


def test_input_iteration_order_does_not_change_normalized_result() -> None:
    early = _experience(offset=0)
    late = _experience(offset=10)

    forward = normalize_trajectory([early, late])
    reverse = normalize_trajectory([late, early])

    assert reverse == forward
    assert [step.experience for step in reverse.steps] == [early, late]
    assert [step.sequence for step in reverse.steps] == [1, 2]


def test_chronology_uses_explicit_timestamps_then_canonical_tie_break() -> None:
    first = _experience(offset=0, detail="a")
    second = _experience(offset=0, detail="b")

    trajectory = normalize_trajectory([second, first])

    expected = sorted([first, second], key=lambda item: item.to_json())
    assert [step.experience for step in trajectory.steps] == expected


def test_duplicate_historical_records_are_preserved_not_eliminated() -> None:
    experience = _experience(offset=0)

    trajectory = normalize_trajectory([experience, experience])
    first_digest = trajectory.steps[0].source_experience_sha256
    second_digest = trajectory.steps[1].source_experience_sha256

    assert len(trajectory.steps) == 2
    assert trajectory.steps[0].experience == experience
    assert trajectory.steps[1].experience == experience
    assert first_digest == second_digest


def test_step_source_fingerprint_is_deterministic_and_content_bound() -> None:
    experience = _experience(offset=0)

    first = NormalizedTrajectoryStep.from_experience(sequence=1, experience=experience)
    second = NormalizedTrajectoryStep.from_experience(sequence=1, experience=experience)

    assert first == second
    assert len(first.source_experience_sha256) == 64
    assert first.source_experience_sha256 == second.source_experience_sha256


def test_mismatched_source_fingerprint_fails_closed() -> None:
    experience = _experience(offset=0)

    with pytest.raises(TrajectoryValidationError, match="does not match"):
        NormalizedTrajectoryStep(
            sequence=1,
            source_experience_sha256="0" * 64,
            experience=experience,
        )


def test_missing_optional_stages_remain_explicitly_absent() -> None:
    experience = _experience(offset=0, outcome=CausalOutcome.EXECUTION_FAILED)

    trajectory = normalize_trajectory([experience])
    encoded = trajectory.to_dict()["steps"]

    assert isinstance(encoded, list)
    nested = encoded[0]
    assert isinstance(nested, dict)
    canonical = nested["experience"]
    assert isinstance(canonical, dict)
    assert canonical["observation"] is None
    assert canonical["observation_at"] is None
    assert canonical["state_after"] is None
    assert canonical["verification"] is None
    assert canonical["verification_at"] is None
    assert trajectory.steps[0].experience.outcome is CausalOutcome.EXECUTION_FAILED


def test_verified_evidence_is_preserved_exactly() -> None:
    experience = _experience(offset=0)

    trajectory = normalize_trajectory([experience])
    normalized = trajectory.steps[0].experience

    assert normalized.verification == experience.verification
    assert normalized.verification is not None
    assert normalized.verification.passed is True
    assert normalized.outcome is CausalOutcome.VERIFIED


def test_failed_outcome_is_preserved_without_retry_semantics() -> None:
    experience = _experience(
        offset=0,
        outcome=CausalOutcome.EXECUTION_FAILED,
        detail="historical failure",
    )

    trajectory = normalize_trajectory([experience])

    assert trajectory.steps[0].experience.outcome is CausalOutcome.EXECUTION_FAILED
    assert trajectory.steps[0].experience.outcome_detail == "historical failure"


def test_episode_record_is_preserved_as_canonical_source_reference() -> None:
    episode = _episode_record()
    experience = _experience(offset=0)

    trajectory = normalize_trajectory([experience], episode=episode)

    assert trajectory.source_episode is episode
    assert trajectory.episode_id == episode.episode_id
    assert trajectory.task_id == episode.task_id
    assert trajectory.correlation_id == episode.correlation_id


def test_episode_record_can_supply_missing_task_and_episode_association() -> None:
    episode = _episode_record()
    experience = _experience(offset=0, task_id=None, episode_id=None)

    trajectory = normalize_trajectory([experience], episode=episode)

    assert trajectory.task_id == _TASK
    assert trajectory.episode_id == _EPISODE
    assert trajectory.steps[0].experience.task_id is None
    assert trajectory.steps[0].experience.episode_id is None


def test_episode_metadata_never_rewrites_missing_source_fields() -> None:
    episode = _episode_record()
    experience = _experience(offset=0, task_id=None, episode_id=None)

    trajectory = normalize_trajectory([experience], episode=episode)

    assert trajectory.steps[0].experience is experience
    assert trajectory.steps[0].experience.task_id is None
    assert trajectory.steps[0].experience.episode_id is None


def test_conflicting_correlation_ids_fail_closed() -> None:
    other = UUID("99999999-2222-4333-8444-555555555555")

    with pytest.raises(TrajectoryNormalizationError, match="correlation_id"):
        normalize_trajectory(
            [
                _experience(offset=0),
                _experience(offset=10, correlation_id=other),
            ]
        )


def test_conflicting_task_ids_fail_closed() -> None:
    other_task = TaskId.parse("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

    with pytest.raises(TrajectoryNormalizationError, match="task_id"):
        normalize_trajectory(
            [
                _experience(offset=0),
                _experience(offset=10, task_id=other_task),
            ]
        )


def test_conflicting_episode_ids_fail_closed() -> None:
    other_episode = EpisodeId.parse("dddddddd-dddd-4ddd-8ddd-dddddddddddd")

    with pytest.raises(TrajectoryNormalizationError, match="episode_id"):
        normalize_trajectory(
            [
                _experience(offset=0),
                _experience(offset=10, episode_id=other_episode),
            ]
        )


def test_conflicting_episode_record_fails_closed() -> None:
    episode = EpisodeRecord(
        episode_id=EpisodeId.parse("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        task_id=_TASK,
        correlation_id=_CORRELATION,
        created_at=_BASE,
        outcome=EpisodeOutcome.FAILED,
        summary="different episode",
    )

    with pytest.raises(TrajectoryNormalizationError, match="episode_id"):
        normalize_trajectory([_experience(offset=0)], episode=episode)


def test_empty_input_fails_closed() -> None:
    with pytest.raises(TrajectoryNormalizationError, match="at least one"):
        normalize_trajectory([])


def test_noncanonical_input_fails_closed() -> None:
    with pytest.raises(TypeError, match="CausalExperience"):
        normalize_trajectory(["not an experience"])  # type: ignore[list-item]


def test_non_episode_metadata_fails_closed() -> None:
    with pytest.raises(TypeError, match="EpisodeRecord"):
        normalize_trajectory([_experience(offset=0)], episode="bad")  # type: ignore[arg-type]


def test_contract_is_immutable() -> None:
    trajectory = normalize_trajectory([_experience(offset=0)])

    with pytest.raises(FrozenInstanceError):
        trajectory.task_id = None  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        trajectory.steps[0].sequence = 2  # type: ignore[misc]


def test_serialization_is_deterministic_and_keeps_c2_10_nested() -> None:
    trajectory = normalize_trajectory([_experience(offset=0)])

    first = trajectory.to_json()
    second = trajectory.to_json()
    decoded = json.loads(first)

    assert first == second
    assert decoded["schema_version"] == NORMALIZED_TRAJECTORY_SCHEMA_VERSION == 1
    assert decoded["steps"][0]["experience"]["schema_version"] == 1
    assert decoded["steps"][0]["experience"]["outcome"] == "verified"


def test_started_and_ended_at_are_derived_from_historical_evidence() -> None:
    early = _experience(offset=0)
    late = _experience(offset=20)

    trajectory = normalize_trajectory([late, early])

    assert trajectory.started_at == early.state_before.captured_at
    assert trajectory.ended_at == late.outcome_at


def test_trajectory_identity_changes_when_canonical_evidence_changes() -> None:
    baseline = normalize_trajectory([_experience(offset=0, detail="one")])
    changed = normalize_trajectory([_experience(offset=0, detail="two")])

    assert baseline.trajectory_id != changed.trajectory_id


def test_trajectory_identity_is_a_non_nil_uuid() -> None:
    trajectory = normalize_trajectory([_experience(offset=0)])

    assert isinstance(trajectory.trajectory_id, UUID)
    assert trajectory.trajectory_id.int != 0
