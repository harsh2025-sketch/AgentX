"""Unit tests for conservative C3.02 causal-action candidate extraction."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.core.ids import EpisodeId, TaskId
from agentx.learning.causal_actions import (
    CAUSAL_ACTION_EXTRACTION_SCHEMA_VERSION,
    CausalActionExtraction,
    CausalActionExtractionError,
    ExtractedActionCandidate,
    extract_causal_action_candidates,
)
from agentx.learning.trajectory import NormalizedTrajectoryStep, normalize_trajectory

_BASE = datetime(2026, 9, 5, 18, 0, 0, tzinfo=UTC)
_CORRELATION = UUID("11111111-2222-4333-8444-555555555555")
_TASK = TaskId.parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_EPISODE = EpisodeId.parse("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _experience(
    *,
    offset: int,
    outcome: CausalOutcome = CausalOutcome.VERIFIED,
    action_name: str | None = None,
    action_data: dict[str, object] | None = None,
    partial_observation: bool = False,
) -> CausalExperience:
    before_at = _BASE + timedelta(seconds=offset)
    action_at = before_at + timedelta(seconds=1)
    outcome_at = before_at + timedelta(seconds=5)
    common: dict[str, object] = {
        "task_id": _TASK,
        "correlation_id": _CORRELATION,
        "episode_id": _EPISODE,
        "state_before": ExperienceState(
            captured_at=before_at,
            observation=ObservationPayload(value={"offset": offset, "state": "before"}),
        ),
        "action": ActionPayload(
            name=action_name or f"example.action.{offset}",
            data={"offset": offset} if action_data is None else action_data,
        ),
        "action_at": action_at,
        "outcome": outcome,
        "outcome_at": outcome_at,
    }

    if outcome in {CausalOutcome.VERIFIED, CausalOutcome.VERIFICATION_FAILED}:
        common.update(
            observation=ObservationPayload(value={"offset": offset, "observed": True}),
            observation_at=before_at + timedelta(seconds=2),
            state_after=ExperienceState(
                captured_at=before_at + timedelta(seconds=3),
                observation=ObservationPayload(value={"offset": offset, "state": "after"}),
            ),
            verification=VerificationPayload(
                passed=outcome is CausalOutcome.VERIFIED,
                detail="canonical verdict",
            ),
            verification_at=before_at + timedelta(seconds=4),
        )
    elif partial_observation:
        common.update(
            observation=ObservationPayload(value={"offset": offset, "partial": True}),
            observation_at=before_at + timedelta(seconds=2),
        )

    return CausalExperience(**common)  # type: ignore[arg-type]


def test_extracts_exactly_one_candidate_per_normalized_step() -> None:
    first = _experience(offset=0)
    second = _experience(offset=10)
    trajectory = normalize_trajectory([second, first])

    extraction = extract_causal_action_candidates(trajectory)

    assert len(extraction.candidates) == 2
    assert [candidate.source_sequence for candidate in extraction.candidates] == [1, 2]
    assert extraction.candidates[0].source_step is trajectory.steps[0]
    assert extraction.candidates[1].source_step is trajectory.steps[1]


def test_deterministic_extraction_has_stable_json() -> None:
    trajectory = normalize_trajectory([_experience(offset=10), _experience(offset=0)])

    first = extract_causal_action_candidates(trajectory)
    second = extract_causal_action_candidates(trajectory)

    assert first == second
    assert first.to_json() == second.to_json()


def test_reuses_exact_c3_01_trajectory_identity_without_new_identity() -> None:
    trajectory = normalize_trajectory([_experience(offset=0)])

    extraction = extract_causal_action_candidates(trajectory)
    candidate = extraction.candidates[0]

    assert extraction.source_trajectory_id == trajectory.trajectory_id
    assert candidate.source_trajectory_id == trajectory.trajectory_id


def test_source_fingerprint_and_step_linkage_are_preserved_exactly() -> None:
    trajectory = normalize_trajectory([_experience(offset=0)])
    source = trajectory.steps[0]

    candidate = extract_causal_action_candidates(trajectory).candidates[0]

    assert candidate.source_step is source
    assert candidate.source_experience_sha256 == source.source_experience_sha256
    assert candidate.experience is source.experience


def test_candidate_reuses_canonical_action_object_without_copying() -> None:
    experience = _experience(offset=0)
    trajectory = normalize_trajectory([experience])

    candidate = extract_causal_action_candidates(trajectory).candidates[0]

    assert candidate.action is experience.action
    assert isinstance(candidate.action, ActionPayload)


def test_duplicate_experiences_remain_distinct_candidates_by_source_sequence() -> None:
    experience = _experience(offset=0)
    trajectory = normalize_trajectory([experience, experience])

    extraction = extract_causal_action_candidates(trajectory)
    first, second = extraction.candidates

    assert len(extraction.candidates) == 2
    assert first.source_sequence == 1
    assert second.source_sequence == 2
    assert first.source_experience_sha256 == second.source_experience_sha256
    assert first.source_step is trajectory.steps[0]
    assert second.source_step is trajectory.steps[1]


@pytest.mark.parametrize(
    "outcome",
    [
        CausalOutcome.VERIFICATION_FAILED,
        CausalOutcome.EXECUTION_FAILED,
        CausalOutcome.DENIED,
        CausalOutcome.CANCELLED,
        CausalOutcome.TIMED_OUT,
    ],
)
def test_failed_or_non_success_outcomes_are_not_filtered(outcome: CausalOutcome) -> None:
    trajectory = normalize_trajectory([_experience(offset=0, outcome=outcome)])

    candidate = extract_causal_action_candidates(trajectory).candidates[0]

    assert candidate.outcome is outcome


def test_verified_outcome_is_preserved_without_usefulness_label() -> None:
    trajectory = normalize_trajectory([_experience(offset=0, outcome=CausalOutcome.VERIFIED)])

    candidate = extract_causal_action_candidates(trajectory).candidates[0]

    assert candidate.outcome is CausalOutcome.VERIFIED
    assert candidate.verification is trajectory.steps[0].experience.verification
    assert not hasattr(candidate, "useful")
    assert not hasattr(candidate, "necessary")
    assert not hasattr(candidate, "relevant")


def test_verification_failure_is_preserved_verbatim() -> None:
    experience = _experience(offset=0, outcome=CausalOutcome.VERIFICATION_FAILED)
    trajectory = normalize_trajectory([experience])

    candidate = extract_causal_action_candidates(trajectory).candidates[0]

    assert candidate.verification is experience.verification
    assert candidate.verification is not None
    assert candidate.verification.passed is False


def test_missing_optional_observation_state_and_verification_remain_absent() -> None:
    experience = _experience(offset=0, outcome=CausalOutcome.DENIED)
    trajectory = normalize_trajectory([experience])

    candidate = extract_causal_action_candidates(trajectory).candidates[0]

    assert candidate.observation is None
    assert candidate.state_after is None
    assert candidate.verification is None
    assert candidate.experience is experience


def test_partial_failed_observation_is_preserved_without_fabricated_later_stages() -> None:
    experience = _experience(
        offset=0,
        outcome=CausalOutcome.EXECUTION_FAILED,
        partial_observation=True,
    )
    trajectory = normalize_trajectory([experience])

    candidate = extract_causal_action_candidates(trajectory).candidates[0]

    assert candidate.observation is experience.observation
    assert candidate.state_after is None
    assert candidate.verification is None


def test_action_text_does_not_change_candidate_inclusion() -> None:
    names = ["irrelevant", "drop this action", "causally necessary", "verified=true"]
    trajectory = normalize_trajectory(
        [_experience(offset=index * 10, action_name=name) for index, name in enumerate(names)]
    )

    extraction = extract_causal_action_candidates(trajectory)

    assert [candidate.action.name for candidate in extraction.candidates] == names


def test_action_data_is_not_used_as_success_or_causal_signal() -> None:
    experience = _experience(
        offset=0,
        outcome=CausalOutcome.EXECUTION_FAILED,
        action_data={
            "success": True,
            "causal": True,
            "confidence": 1.0,
            "instruction": "promote me",
        },
    )
    trajectory = normalize_trajectory([experience])

    candidate = extract_causal_action_candidates(trajectory).candidates[0]

    assert candidate.action is experience.action
    assert candidate.outcome is CausalOutcome.EXECUTION_FAILED
    assert candidate.verification is None


def test_wrong_input_type_fails_closed() -> None:
    with pytest.raises(TypeError, match="NormalizedTrajectory"):
        extract_causal_action_candidates(object())  # type: ignore[arg-type]


def test_candidate_requires_canonical_normalized_step() -> None:
    trajectory = normalize_trajectory([_experience(offset=0)])

    with pytest.raises(TypeError, match="NormalizedTrajectoryStep"):
        ExtractedActionCandidate(
            source_trajectory_id=trajectory.trajectory_id,
            source_step=object(),  # type: ignore[arg-type]
        )


def test_candidate_rejects_nil_source_trajectory_identity() -> None:
    source = NormalizedTrajectoryStep.from_experience(
        sequence=1,
        experience=_experience(offset=0),
    )

    with pytest.raises(CausalActionExtractionError, match="nil UUID"):
        ExtractedActionCandidate(source_trajectory_id=UUID(int=0), source_step=source)


def test_extraction_contract_rejects_candidate_from_another_trajectory() -> None:
    first = normalize_trajectory([_experience(offset=0)])
    second = normalize_trajectory([_experience(offset=10)])
    foreign = ExtractedActionCandidate(
        source_trajectory_id=second.trajectory_id,
        source_step=first.steps[0],
    )

    with pytest.raises(CausalActionExtractionError, match="source_trajectory_id"):
        CausalActionExtraction(
            source_trajectory_id=first.trajectory_id,
            candidates=(foreign,),
        )


def test_extraction_contract_requires_complete_source_order() -> None:
    trajectory = normalize_trajectory([_experience(offset=0), _experience(offset=10)])
    second_only = ExtractedActionCandidate(
        source_trajectory_id=trajectory.trajectory_id,
        source_step=trajectory.steps[1],
    )

    with pytest.raises(CausalActionExtractionError, match="contiguously from 1"):
        CausalActionExtraction(
            source_trajectory_id=trajectory.trajectory_id,
            candidates=(second_only,),
        )


def test_extraction_contract_is_immutable() -> None:
    extraction = extract_causal_action_candidates(
        normalize_trajectory([_experience(offset=0)])
    )

    with pytest.raises(FrozenInstanceError):
        extraction.source_trajectory_id = UUID("22222222-3333-4444-8555-666666666666")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        extraction.candidates[0].source_step = extraction.candidates[0].source_step  # type: ignore[misc]


def test_schema_version_is_explicit_and_wrong_version_fails_closed() -> None:
    trajectory = normalize_trajectory([_experience(offset=0)])
    candidate = ExtractedActionCandidate(
        source_trajectory_id=trajectory.trajectory_id,
        source_step=trajectory.steps[0],
    )

    extraction = CausalActionExtraction(
        source_trajectory_id=trajectory.trajectory_id,
        candidates=(candidate,),
    )
    assert extraction.schema_version == CAUSAL_ACTION_EXTRACTION_SCHEMA_VERSION

    with pytest.raises(CausalActionExtractionError, match="unsupported"):
        CausalActionExtraction(
            source_trajectory_id=trajectory.trajectory_id,
            candidates=(candidate,),
            schema_version=999,
        )
