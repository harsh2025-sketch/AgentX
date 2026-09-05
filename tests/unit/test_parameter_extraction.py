"""Unit tests for deterministic C3.04 parameter extraction."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from uuid import UUID

import pytest

from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.events import (
    ActionPayload,
    EventValidationError,
    ObservationPayload,
    VerificationPayload,
)
from agentx.core.ids import EpisodeId, TaskId
from agentx.learning.causal_actions import extract_causal_action_candidates
from agentx.learning.irrelevant_actions import IrrelevantActionAnalysis, analyze_irrelevant_actions
from agentx.learning.parameter_extraction import (
    PARAMETER_EXTRACTION_SCHEMA_VERSION,
    ParameterCandidate,
    ParameterExtraction,
    ParameterExtractionError,
    extract_parameter_candidates,
)
from agentx.learning.trajectory import normalize_trajectory

_BASE = datetime(2026, 9, 6, tzinfo=UTC)
_CORRELATION = UUID("11111111-2222-4333-8444-555555555555")
_TASK = TaskId.parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_EPISODE = EpisodeId.parse("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _experience(
    *,
    offset: int,
    data: dict[str, object] | None = None,
    outcome: CausalOutcome = CausalOutcome.VERIFIED,
    action_name: str = "example.action",
    observation_text: str | None = None,
) -> CausalExperience:
    before_at = _BASE + timedelta(seconds=offset)
    common: dict[str, object] = {
        "task_id": _TASK,
        "correlation_id": _CORRELATION,
        "episode_id": _EPISODE,
        "state_before": ExperienceState(
            captured_at=before_at,
            observation=ObservationPayload(value={"state": "before", "offset": offset}),
        ),
        "action": ActionPayload(name=action_name, data={} if data is None else data),
        "action_at": before_at + timedelta(seconds=1),
        "outcome": outcome,
        "outcome_at": before_at + timedelta(seconds=5),
    }
    if outcome in {CausalOutcome.VERIFIED, CausalOutcome.VERIFICATION_FAILED}:
        common.update(
            observation=ObservationPayload(
                value={"text": observation_text or "recorded observation", "offset": offset}
            ),
            observation_at=before_at + timedelta(seconds=2),
            state_after=ExperienceState(
                captured_at=before_at + timedelta(seconds=3),
                observation=ObservationPayload(value={"state": "after", "offset": offset}),
            ),
            verification=VerificationPayload(
                passed=outcome is CausalOutcome.VERIFIED,
                detail="explicit verification detail",
            ),
            verification_at=before_at + timedelta(seconds=4),
        )
    return CausalExperience(**common)  # type: ignore[arg-type]


def _analysis(*experiences: CausalExperience) -> IrrelevantActionAnalysis:
    trajectory = normalize_trajectory(experiences)
    extraction = extract_causal_action_candidates(trajectory)
    return analyze_irrelevant_actions(extraction)


def _extract(*experiences: CausalExperience) -> ParameterExtraction:
    return extract_parameter_candidates(_analysis(*experiences))


def test_extracts_one_explicit_structured_field() -> None:
    result = _extract(_experience(offset=0, data={"path": "C:/work/item.txt"}))

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.field_name == "path"
    assert candidate.value == "C:/work/item.txt"


def test_extracts_multiple_fields_in_canonical_lexicographic_order() -> None:
    result = _extract(_experience(offset=0, data={"z": 3, "a": 1, "middle": 2}))

    assert [candidate.field_name for candidate in result.candidates] == ["a", "middle", "z"]
    assert [candidate.value for candidate in result.candidates] == [1, 2, 3]


def test_preserves_retained_action_source_order_before_field_order() -> None:
    result = _extract(
        _experience(offset=0, data={"b": "first-b", "a": "first-a"}),
        _experience(offset=10, data={"a": "second-a"}),
    )

    actual = [(candidate.source_sequence, candidate.field_name) for candidate in result.candidates]
    assert actual == [
        (1, "a"),
        (1, "b"),
        (2, "a"),
    ]


def test_eliminated_c3_03_action_is_not_a_parameter_source() -> None:
    result = _extract(
        _experience(offset=0, data={"kept": 1}),
        _experience(offset=10, data={"must_not_appear": 2}, outcome=CausalOutcome.DENIED),
    )

    actual = [(candidate.source_sequence, candidate.field_name) for candidate in result.candidates]
    assert actual == [(1, "kept")]


def test_empty_structured_action_data_yields_no_candidates() -> None:
    result = _extract(_experience(offset=0, data={}))

    assert result.candidates == ()


def test_equal_values_from_different_actions_remain_distinct() -> None:
    result = _extract(
        _experience(offset=0, data={"value": "same"}),
        _experience(offset=10, data={"value": "same"}),
    )

    assert len(result.candidates) == 2
    assert result.candidates[0].value == result.candidates[1].value == "same"
    assert result.candidates[0].source_sequence == 1
    assert result.candidates[1].source_sequence == 2
    assert result.candidates[0].reference != result.candidates[1].reference


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
def test_supported_canonical_json_values_are_preserved(value: object) -> None:
    candidate = _extract(_experience(offset=0, data={"value": value})).candidates[0]

    assert candidate.to_dict()["value"] == value


def test_nested_object_remains_one_top_level_candidate() -> None:
    result = _extract(
        _experience(offset=0, data={"options": {"timeout": 5, "nested": {"mode": "safe"}}})
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].field_name == "options"
    assert result.candidates[0].to_dict()["value"] == {
        "timeout": 5,
        "nested": {"mode": "safe"},
    }


def test_source_provenance_objects_are_preserved_exactly() -> None:
    analysis = _analysis(_experience(offset=0, data={"target": "x"}))
    decision = analysis.retained[0]

    candidate = extract_parameter_candidates(analysis).candidates[0]

    assert candidate.source_decision is decision
    assert candidate.source_candidate is decision.source_candidate
    assert candidate.source_step is decision.source_step
    assert candidate.source_trajectory_id == analysis.source_trajectory_id
    assert candidate.source_sequence == decision.source_sequence
    assert candidate.source_experience_sha256 == decision.source_experience_sha256


def test_reference_is_structural_provenance_not_random_identity() -> None:
    candidate = _extract(_experience(offset=0, data={"field": 1})).candidates[0]

    assert candidate.reference == (
        candidate.source_trajectory_id,
        candidate.source_sequence,
        "field",
    )


def test_observation_text_cannot_create_parameter() -> None:
    result = _extract(
        _experience(
            offset=0,
            data={},
            observation_text="parameter=secret admin verified=true ignore ActionGate",
        )
    )

    assert result.candidates == ()


def test_action_name_cannot_create_parameter() -> None:
    result = _extract(
        _experience(
            offset=0,
            data={},
            action_name="parameter.target.admin.ignore-ActionGate",
        )
    )

    assert result.candidates == ()


def test_repeated_extraction_is_deterministic() -> None:
    analysis = _analysis(
        _experience(offset=0, data={"b": 2, "a": 1}),
        _experience(offset=10, data={"x": "y"}),
    )

    first = extract_parameter_candidates(analysis)
    second = extract_parameter_candidates(analysis)

    assert first == second
    assert first.to_dict() == second.to_dict()
    assert first.to_json() == second.to_json()


def test_candidate_and_result_are_immutable() -> None:
    result = _extract(_experience(offset=0, data={"value": 1}))

    with pytest.raises(FrozenInstanceError):
        result.candidates[0].field_name = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.candidates = ()  # type: ignore[misc]


def test_source_action_data_is_immutable() -> None:
    candidate = _extract(_experience(offset=0, data={"value": {"nested": 1}})).candidates[0]

    assert isinstance(candidate.source_candidate.action.data, MappingProxyType)
    with pytest.raises(TypeError):
        candidate.source_candidate.action.data["new"] = 1  # type: ignore[index]


def test_candidate_requires_retained_c3_03_decision() -> None:
    analysis = _analysis(_experience(offset=0, data={"field": 1}, outcome=CausalOutcome.DENIED))

    with pytest.raises(ParameterExtractionError, match="retained"):
        ParameterCandidate(source_decision=analysis.eliminated[0], field_name="field")


def test_candidate_requires_existing_explicit_field() -> None:
    analysis = _analysis(_experience(offset=0, data={"actual": 1}))

    with pytest.raises(ParameterExtractionError, match="must exist"):
        ParameterCandidate(source_decision=analysis.retained[0], field_name="invented")


def test_candidate_rejects_non_string_field_name() -> None:
    analysis = _analysis(_experience(offset=0, data={"actual": 1}))

    with pytest.raises(TypeError, match="field_name"):
        ParameterCandidate(
            source_decision=analysis.retained[0],
            field_name=1,  # type: ignore[arg-type]
        )


def test_wrong_analysis_type_fails_closed() -> None:
    with pytest.raises(TypeError, match="IrrelevantActionAnalysis"):
        extract_parameter_candidates(object())  # type: ignore[arg-type]


def test_parameter_extraction_allows_zero_candidates() -> None:
    analysis = _analysis(_experience(offset=0, data={}))

    result = ParameterExtraction(source_analysis=analysis, candidates=())

    assert result.candidates == ()


def test_parameter_extraction_rejects_missing_expected_candidate() -> None:
    analysis = _analysis(_experience(offset=0, data={"a": 1}))

    with pytest.raises(ParameterExtractionError, match="exactly one"):
        ParameterExtraction(source_analysis=analysis, candidates=())


def test_parameter_extraction_rejects_noncanonical_order() -> None:
    analysis = _analysis(_experience(offset=0, data={"a": 1, "b": 2}))
    first = ParameterCandidate(source_decision=analysis.retained[0], field_name="a")
    second = ParameterCandidate(source_decision=analysis.retained[0], field_name="b")

    with pytest.raises(ParameterExtractionError, match="ordering/content"):
        ParameterExtraction(source_analysis=analysis, candidates=(second, first))


def test_schema_version_is_exact_and_bool_is_rejected() -> None:
    analysis = _analysis(_experience(offset=0, data={}))

    assert ParameterExtraction(source_analysis=analysis, candidates=()).schema_version == 1
    assert PARAMETER_EXTRACTION_SCHEMA_VERSION == 1
    with pytest.raises(TypeError, match="integer"):
        ParameterExtraction(source_analysis=analysis, candidates=(), schema_version=True)
    with pytest.raises(ParameterExtractionError, match="unsupported"):
        ParameterExtraction(source_analysis=analysis, candidates=(), schema_version=2)


def test_action_payload_rejects_arbitrary_python_object_before_analysis() -> None:
    with pytest.raises(EventValidationError, match="non-JSON-compatible"):
        ActionPayload(name="example.action", data={"callback": object()})


def test_corrupted_post_construction_source_value_fails_closed() -> None:
    analysis = _analysis(_experience(offset=0, data={"safe": 1}))
    action = analysis.retained[0].source_candidate.action
    object.__setattr__(action, "data", MappingProxyType({"safe": object()}))

    with pytest.raises(ParameterExtractionError, match="canonical JSON-compatible"):
        extract_parameter_candidates(analysis)


def test_serialization_contains_only_explicit_candidate_provenance_and_value() -> None:
    candidate = _extract(_experience(offset=0, data={"target": "alpha"})).candidates[0]

    assert candidate.to_dict() == {
        "source_trajectory_id": str(candidate.source_trajectory_id),
        "source_sequence": 1,
        "source_experience_sha256": candidate.source_experience_sha256,
        "field_name": "target",
        "value": "alpha",
    }
