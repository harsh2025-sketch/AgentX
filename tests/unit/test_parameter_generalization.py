"""Unit tests for conservative C3.05 parameter generalization analysis."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.core.ids import EpisodeId, TaskId
from agentx.learning.causal_actions import extract_causal_action_candidates
from agentx.learning.irrelevant_actions import analyze_irrelevant_actions
from agentx.learning.parameter_extraction import ParameterExtraction, extract_parameter_candidates
from agentx.learning.parameter_generalization import (
    PARAMETER_GENERALIZATION_SCHEMA_VERSION,
    ParameterGeneralization,
    ParameterGeneralizationError,
    ParameterObservationGroup,
    ParameterVariationEvidence,
    analyze_parameter_generalization,
)
from agentx.learning.trajectory import normalize_trajectory

_BASE = datetime(2026, 9, 6, tzinfo=UTC)
_CORRELATION = UUID("11111111-2222-4333-8444-555555555555")
_TASK = TaskId.parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_EPISODE = EpisodeId.parse("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _experience(
    *,
    offset: int,
    data: dict[str, object],
    action_name: str = "example.action",
) -> CausalExperience:
    before_at = _BASE + timedelta(seconds=offset)
    return CausalExperience(
        task_id=_TASK,
        correlation_id=_CORRELATION,
        episode_id=_EPISODE,
        state_before=ExperienceState(
            captured_at=before_at,
            observation=ObservationPayload(value={"state": "before", "offset": offset}),
        ),
        action=ActionPayload(name=action_name, data=data),
        action_at=before_at + timedelta(seconds=1),
        observation=ObservationPayload(value={"state": "observed", "offset": offset}),
        observation_at=before_at + timedelta(seconds=2),
        state_after=ExperienceState(
            captured_at=before_at + timedelta(seconds=3),
            observation=ObservationPayload(value={"state": "after", "offset": offset}),
        ),
        verification=VerificationPayload(passed=True, detail="verified"),
        verification_at=before_at + timedelta(seconds=4),
        outcome=CausalOutcome.VERIFIED,
        outcome_at=before_at + timedelta(seconds=5),
    )


def _extraction(*experiences: CausalExperience) -> ParameterExtraction:
    trajectory = normalize_trajectory(experiences)
    causal = extract_causal_action_candidates(trajectory)
    retained = analyze_irrelevant_actions(causal)
    return extract_parameter_candidates(retained)


def _analyze(*experiences: CausalExperience) -> ParameterGeneralization:
    return analyze_parameter_generalization(_extraction(*experiences))


def test_zero_observations_produces_no_groups() -> None:
    result = _analyze(_experience(offset=0, data={}))

    assert result.groups == ()


def test_one_observation_is_insufficient() -> None:
    result = _analyze(_experience(offset=0, data={"value": "alpha"}))

    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.key == ("example.action", "value")
    assert group.observation_count == 1
    assert group.evidence is ParameterVariationEvidence.INSUFFICIENT_OBSERVATIONS


def test_repeated_equal_values_report_only_observed_same_value() -> None:
    result = _analyze(
        _experience(offset=0, data={"value": "same"}),
        _experience(offset=10, data={"value": "same"}),
        _experience(offset=20, data={"value": "same"}),
    )

    group = result.groups[0]
    assert group.observation_count == 3
    assert group.evidence is ParameterVariationEvidence.OBSERVED_SAME_VALUE
    assert not hasattr(group, "constant")
    assert not hasattr(group, "default")


def test_differing_values_report_observed_variation() -> None:
    result = _analyze(
        _experience(offset=0, data={"value": "alpha"}),
        _experience(offset=10, data={"value": "beta"}),
    )

    assert result.groups[0].evidence is ParameterVariationEvidence.OBSERVED_VARIATION


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        7,
        2.5,
        "plain text",
        [1, "two", False],
        {"nested": [1, 2], "flag": True},
    ],
)
def test_supported_equal_json_values_report_observed_same_value(value: object) -> None:
    result = _analyze(
        _experience(offset=0, data={"value": value}),
        _experience(offset=10, data={"value": value}),
    )

    assert result.groups[0].evidence is ParameterVariationEvidence.OBSERVED_SAME_VALUE


def test_bool_and_int_are_not_silently_equal() -> None:
    result = _analyze(
        _experience(offset=0, data={"value": True}),
        _experience(offset=10, data={"value": 1}),
    )

    assert result.groups[0].evidence is ParameterVariationEvidence.OBSERVED_VARIATION


def test_int_and_float_are_not_silently_equal() -> None:
    result = _analyze(
        _experience(offset=0, data={"value": 1}),
        _experience(offset=10, data={"value": 1.0}),
    )

    assert result.groups[0].evidence is ParameterVariationEvidence.OBSERVED_VARIATION


def test_nested_object_key_order_does_not_create_false_variation() -> None:
    result = _analyze(
        _experience(offset=0, data={"value": {"a": 1, "b": {"x": 2, "y": 3}}}),
        _experience(offset=10, data={"value": {"b": {"y": 3, "x": 2}, "a": 1}}),
    )

    assert result.groups[0].evidence is ParameterVariationEvidence.OBSERVED_SAME_VALUE


def test_array_order_remains_structurally_significant() -> None:
    result = _analyze(
        _experience(offset=0, data={"value": [1, 2]}),
        _experience(offset=10, data={"value": [2, 1]}),
    )

    assert result.groups[0].evidence is ParameterVariationEvidence.OBSERVED_VARIATION


def test_same_field_name_on_different_action_names_is_not_equated() -> None:
    result = _analyze(
        _experience(offset=0, action_name="action.one", data={"value": "same"}),
        _experience(offset=10, action_name="action.two", data={"value": "same"}),
    )

    assert [group.key for group in result.groups] == [
        ("action.one", "value"),
        ("action.two", "value"),
    ]
    assert all(
        group.evidence is ParameterVariationEvidence.INSUFFICIENT_OBSERVATIONS
        for group in result.groups
    )


def test_group_order_is_first_observation_order_and_deterministic() -> None:
    extraction = _extraction(
        _experience(offset=0, action_name="first.action", data={"z": 1, "a": 2}),
        _experience(offset=10, action_name="second.action", data={"a": 3}),
        _experience(offset=20, action_name="first.action", data={"a": 4}),
    )

    first = analyze_parameter_generalization(extraction)
    second = analyze_parameter_generalization(extraction)

    assert [group.key for group in first.groups] == [
        ("first.action", "a"),
        ("first.action", "z"),
        ("second.action", "a"),
    ]
    assert first == second
    assert first.to_dict() == second.to_dict()
    assert first.to_json() == second.to_json()


def test_equal_observations_keep_duplicate_provenance() -> None:
    extraction = _extraction(
        _experience(offset=0, data={"value": "same"}),
        _experience(offset=10, data={"value": "same"}),
    )

    result = analyze_parameter_generalization(extraction)
    group = result.groups[0]

    assert len(group.observations) == 2
    assert group.observations[0] is extraction.candidates[0]
    assert group.observations[1] is extraction.candidates[1]
    assert group.observations[0].reference != group.observations[1].reference


def test_source_extraction_and_candidates_are_preserved_exactly() -> None:
    extraction = _extraction(_experience(offset=0, data={"value": "alpha"}))

    result = analyze_parameter_generalization(extraction)

    assert result.source_extraction is extraction
    assert result.groups[0].observations[0] is extraction.candidates[0]
    assert result.source_trajectory_id == extraction.source_trajectory_id


def test_results_are_immutable() -> None:
    result = _analyze(_experience(offset=0, data={"value": "alpha"}))

    with pytest.raises(FrozenInstanceError):
        result.groups = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.groups[0].field_name = "other"  # type: ignore[misc]
    assert isinstance(result.groups, tuple)
    assert isinstance(result.groups[0].observations, tuple)


def test_manual_incorrect_evidence_fails_closed() -> None:
    extraction = _extraction(
        _experience(offset=0, data={"value": "same"}),
        _experience(offset=10, data={"value": "same"}),
    )

    with pytest.raises(ParameterGeneralizationError, match="does not match"):
        ParameterObservationGroup(
            action_name="example.action",
            field_name="value",
            observations=extraction.candidates,
            evidence=ParameterVariationEvidence.OBSERVED_VARIATION,
        )


def test_group_rejects_mixed_structural_keys() -> None:
    extraction = _extraction(
        _experience(offset=0, action_name="one", data={"value": 1}),
        _experience(offset=10, action_name="two", data={"value": 1}),
    )

    with pytest.raises(ParameterGeneralizationError, match="exact action/field"):
        ParameterObservationGroup(
            action_name="one",
            field_name="value",
            observations=extraction.candidates,
            evidence=ParameterVariationEvidence.OBSERVED_SAME_VALUE,
        )


def test_result_rejects_missing_or_reordered_groups() -> None:
    extraction = _extraction(
        _experience(offset=0, action_name="one", data={"value": 1}),
        _experience(offset=10, action_name="two", data={"value": 2}),
    )
    canonical = analyze_parameter_generalization(extraction)

    with pytest.raises(ParameterGeneralizationError, match="exactly preserve"):
        ParameterGeneralization(source_extraction=extraction, groups=())
    with pytest.raises(ParameterGeneralizationError, match="exactly preserve"):
        ParameterGeneralization(
            source_extraction=extraction,
            groups=tuple(reversed(canonical.groups)),
        )


def test_schema_version_is_exact_and_bool_rejected() -> None:
    extraction = _extraction(_experience(offset=0, data={}))

    result = analyze_parameter_generalization(extraction)
    assert result.schema_version == PARAMETER_GENERALIZATION_SCHEMA_VERSION == 1
    with pytest.raises(TypeError, match="integer"):
        ParameterGeneralization(source_extraction=extraction, groups=(), schema_version=True)
    with pytest.raises(ParameterGeneralizationError, match="unsupported"):
        ParameterGeneralization(source_extraction=extraction, groups=(), schema_version=2)


def test_wrong_input_type_fails_closed() -> None:
    with pytest.raises(TypeError, match="ParameterExtraction"):
        analyze_parameter_generalization(object())  # type: ignore[arg-type]


def test_serialization_retains_all_observation_provenance() -> None:
    result = _analyze(
        _experience(offset=0, data={"value": "same"}),
        _experience(offset=10, data={"value": "same"}),
    )

    payload = result.to_dict()
    groups = payload["groups"]
    assert isinstance(groups, list)
    group = groups[0]
    assert isinstance(group, dict)
    assert group["observation_count"] == 2
    assert len(group["observations"]) == 2
