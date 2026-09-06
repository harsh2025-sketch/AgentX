"""Unit tests for evidence-driven C3.06 determinism/reasoning region classification."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.core.ids import EpisodeId, TaskId
from agentx.learning.causal_actions import (
    extract_causal_action_candidates,
)
from agentx.learning.irrelevant_actions import (
    analyze_irrelevant_actions,
)
from agentx.learning.parameter_extraction import extract_parameter_candidates
from agentx.learning.parameter_generalization import analyze_parameter_generalization
from agentx.learning.region_classification import (
    REGION_CLASSIFICATION_SCHEMA_VERSION,
    ActionClassification,
    ActionEvidenceReference,
    ClassifiedRegion,
    EvidenceSufficiency,
    RegionClassification,
    RegionClassificationAnalysis,
    RegionClassificationError,
    RegionClassificationReason,
    classify_regions,
    classify_trajectory_regions,
)
from agentx.learning.trajectory import (
    NormalizedTrajectory,
    normalize_trajectory,
)

_BASE = datetime(2026, 9, 6, tzinfo=UTC)
_CORRELATION_1 = UUID("11111111-1111-4111-8111-111111111111")
_CORRELATION_2 = UUID("22222222-2222-4222-8222-222222222222")
_CORRELATION_3 = UUID("33333333-3333-4333-8333-333333333333")
_NIL_UUID = UUID("00000000-0000-0000-0000-000000000000")
_DUMMY_SHA = "a" * 64
_TASK = TaskId.parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_EPISODE = EpisodeId.parse("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _experience(
    *,
    correlation_id: UUID = _CORRELATION_1,
    offset: int = 0,
    action_name: str = "example.action",
    data: dict[str, object] | None = None,
    outcome: CausalOutcome = CausalOutcome.VERIFIED,
    verification_passed: bool = True,
    has_verification: bool | None = None,
) -> CausalExperience:
    before_at = _BASE + timedelta(seconds=offset)
    if outcome is CausalOutcome.VERIFIED:
        verification = (
            VerificationPayload(passed=True, detail="verified")
            if has_verification is not False
            else None
        )
        verification_at = before_at + timedelta(seconds=4) if verification else None
        observation = ObservationPayload(value={"state": "observed", "offset": offset})
        state_after = ExperienceState(
            captured_at=before_at + timedelta(seconds=3),
            observation=ObservationPayload(value={"state": "after", "offset": offset}),
        )
    elif outcome is CausalOutcome.VERIFICATION_FAILED:
        verification = (
            VerificationPayload(passed=False, detail="verification failed")
            if has_verification is not False
            else None
        )
        verification_at = before_at + timedelta(seconds=4) if verification else None
        observation = ObservationPayload(value={"state": "observed", "offset": offset})
        state_after = ExperienceState(
            captured_at=before_at + timedelta(seconds=3),
            observation=ObservationPayload(value={"state": "after", "offset": offset}),
        )
    elif outcome is CausalOutcome.DENIED:
        verification = None
        verification_at = None
        observation = None
        state_after = None
    else:  # EXECUTION_FAILED, CANCELLED, TIMED_OUT
        verification = None
        verification_at = None
        observation = ObservationPayload(value={"state": "observed", "offset": offset})
        state_after = ExperienceState(
            captured_at=before_at + timedelta(seconds=3),
            observation=ObservationPayload(value={"state": "after", "offset": offset}),
        )

    return CausalExperience(
        task_id=_TASK,
        correlation_id=correlation_id,
        episode_id=_EPISODE,
        state_before=ExperienceState(
            captured_at=before_at,
            observation=ObservationPayload(value={"state": "before", "offset": offset}),
        ),
        action=ActionPayload(name=action_name, data=data or {}),
        action_at=before_at + timedelta(seconds=1),
        observation=observation,
        observation_at=before_at + timedelta(seconds=2) if observation else None,
        state_after=state_after,
        verification=verification,
        verification_at=verification_at,
        outcome=outcome,
        outcome_at=before_at + timedelta(seconds=5),
    )


def _trajectory(*experiences: CausalExperience) -> NormalizedTrajectory:
    return normalize_trajectory(experiences)


def test_single_verified_observation_is_insufficient_evidence() -> None:
    traj = _trajectory(_experience(offset=0, action_name="fs.read", data={"path": "/a.txt"}))
    result = classify_regions(traj)

    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.source_sequence == 1
    assert action.action_name == "fs.read"
    assert action.classification is RegionClassification.INSUFFICIENT_EVIDENCE
    assert action.reason is RegionClassificationReason.SINGLE_OBSERVATION
    assert action.evidence_sufficiency is EvidenceSufficiency.PARTIAL
    assert action.observation_count == 1
    assert len(action.evidence_references) == 1

    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.start_sequence == 1
    assert region.end_sequence == 1
    assert region.classification is RegionClassification.INSUFFICIENT_EVIDENCE
    assert region.reason is RegionClassificationReason.SINGLE_OBSERVATION


def test_repeated_verified_observations_classify_as_deterministic() -> None:
    traj1 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_1,
            offset=0,
            action_name="fs.read",
            data={"path": "/a.txt"},
        ),
        _experience(
            correlation_id=_CORRELATION_1,
            offset=10,
            action_name="fs.write",
            data={"path": "/b.txt"},
        ),
    )
    traj2 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_2,
            offset=0,
            action_name="fs.read",
            data={"path": "/a.txt"},
        ),
        _experience(
            correlation_id=_CORRELATION_2,
            offset=10,
            action_name="fs.write",
            data={"path": "/b.txt"},
        ),
    )

    result = classify_regions(traj1, corroborating=[traj2])

    assert len(result.actions) == 2
    for action in result.actions:
        assert action.classification is RegionClassification.DETERMINISTIC
        assert action.reason is RegionClassificationReason.REPEATED_VERIFIED_SUCCESS
        assert action.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT
        assert action.observation_count == 2
        assert len(action.evidence_references) == 2

    assert len(result.regions) == 1
    region = result.regions[0]
    assert region.start_sequence == 1
    assert region.end_sequence == 2
    assert region.classification is RegionClassification.DETERMINISTIC
    assert region.reason is RegionClassificationReason.REPEATED_VERIFIED_SUCCESS
    assert region.action_count == 2


def test_explicit_reasoning_step_is_reasoning_required() -> None:
    traj1 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_1,
            offset=0,
            action_name="cognition.plan",
            data={"goal": "sort"},
        ),
        _experience(
            correlation_id=_CORRELATION_1,
            offset=10,
            action_name="fs.write",
            data={"path": "/out.txt"},
        ),
    )
    traj2 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_2,
            offset=0,
            action_name="cognition.plan",
            data={"goal": "sort"},
        ),
        _experience(
            correlation_id=_CORRELATION_2,
            offset=10,
            action_name="fs.write",
            data={"path": "/out.txt"},
        ),
    )

    result = classify_regions(traj1, corroborating=[traj2])

    assert result.actions[0].classification is RegionClassification.REASONING_REQUIRED
    assert result.actions[0].reason is RegionClassificationReason.EXPLICIT_REASONING_STEP
    assert result.actions[1].classification is RegionClassification.DETERMINISTIC

    assert len(result.regions) == 2
    assert result.regions[0].classification is RegionClassification.REASONING_REQUIRED
    assert result.regions[1].classification is RegionClassification.DETERMINISTIC


def test_mixed_trajectory_partitions_into_regions() -> None:
    # Sequence:
    # 1. fs.read (verified repeated -> DETERMINISTIC)
    # 2. fs.stat (verified repeated -> DETERMINISTIC)
    # 3. reason (explicit reasoning -> REASONING_REQUIRED)
    # 4. fs.write (verified repeated -> DETERMINISTIC)
    traj1 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_1,
            offset=0,
            action_name="fs.read",
            data={"path": "/a"},
        ),
        _experience(
            correlation_id=_CORRELATION_1,
            offset=10,
            action_name="fs.stat",
            data={"path": "/a"},
        ),
        _experience(
            correlation_id=_CORRELATION_1,
            offset=20,
            action_name="reason",
            data={"prompt": "check"},
        ),
        _experience(
            correlation_id=_CORRELATION_1,
            offset=30,
            action_name="fs.write",
            data={"path": "/b"},
        ),
    )
    traj2 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_2,
            offset=0,
            action_name="fs.read",
            data={"path": "/a"},
        ),
        _experience(
            correlation_id=_CORRELATION_2,
            offset=10,
            action_name="fs.stat",
            data={"path": "/a"},
        ),
        _experience(
            correlation_id=_CORRELATION_2,
            offset=20,
            action_name="reason",
            data={"prompt": "check"},
        ),
        _experience(
            correlation_id=_CORRELATION_2,
            offset=30,
            action_name="fs.write",
            data={"path": "/b"},
        ),
    )

    result = classify_regions(traj1, corroborating=[traj2])

    assert len(result.regions) == 3
    # Region 1: steps 1..2 (DETERMINISTIC)
    assert result.regions[0].start_sequence == 1
    assert result.regions[0].end_sequence == 2
    assert result.regions[0].classification is RegionClassification.DETERMINISTIC
    assert result.regions[0].action_count == 2

    # Region 2: step 3 (REASONING_REQUIRED)
    assert result.regions[1].start_sequence == 3
    assert result.regions[1].end_sequence == 3
    assert result.regions[1].classification is RegionClassification.REASONING_REQUIRED
    assert result.regions[1].action_count == 1

    # Region 3: step 4 (DETERMINISTIC)
    assert result.regions[2].start_sequence == 4
    assert result.regions[2].end_sequence == 4
    assert result.regions[2].classification is RegionClassification.DETERMINISTIC
    assert result.regions[2].action_count == 1

    assert len(result.deterministic_actions) == 3
    assert len(result.reasoning_actions) == 1
    assert len(result.insufficient_evidence_actions) == 0


def test_failed_outcome_in_target_is_reasoning_required() -> None:
    traj = _trajectory(
        _experience(
            offset=0,
            action_name="fs.write",
            data={"path": "/test"},
            outcome=CausalOutcome.EXECUTION_FAILED,
        )
    )
    result = classify_regions(traj)

    assert result.actions[0].classification is RegionClassification.REASONING_REQUIRED
    assert result.actions[0].reason is RegionClassificationReason.UNVERIFIED_OR_FAILED_ATTEMPT


def test_missing_verification_payload_is_reasoning_required() -> None:
    traj = _trajectory(
        _experience(
            offset=0,
            action_name="fs.write",
            data={"path": "/test"},
            outcome=CausalOutcome.VERIFICATION_FAILED,
        )
    )
    result = classify_regions(traj)

    assert result.actions[0].classification is RegionClassification.REASONING_REQUIRED
    assert result.actions[0].reason is RegionClassificationReason.UNVERIFIED_OR_FAILED_ATTEMPT


def test_eliminated_action_is_insufficient_evidence() -> None:
    traj = _trajectory(
        _experience(
            offset=0,
            action_name="denied.action",
            outcome=CausalOutcome.DENIED,
        )
    )
    result = classify_regions(traj)

    assert result.actions[0].classification is RegionClassification.INSUFFICIENT_EVIDENCE
    assert result.actions[0].reason is RegionClassificationReason.ACTION_ELIMINATED
    assert result.actions[0].evidence_sufficiency is EvidenceSufficiency.INSUFFICIENT


def test_conflicting_outcomes_across_runs_is_reasoning_required() -> None:
    traj1 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_1,
            offset=0,
            action_name="network.fetch",
            outcome=CausalOutcome.VERIFIED,
        )
    )
    traj2 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_2,
            offset=0,
            action_name="network.fetch",
            outcome=CausalOutcome.EXECUTION_FAILED,
        )
    )

    result = classify_regions(traj1, corroborating=[traj2])

    assert result.actions[0].classification is RegionClassification.REASONING_REQUIRED
    assert result.actions[0].reason is RegionClassificationReason.CONFLICTING_OUTCOMES
    assert result.actions[0].observation_count == 2


def test_parameter_generalization_variation_causes_reasoning_required() -> None:
    traj1 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_1,
            offset=0,
            action_name="api.call",
            data={"port": 8000},
        ),
        _experience(
            correlation_id=_CORRELATION_1,
            offset=10,
            action_name="api.call",
            data={"port": 8080},
        ),
    )
    causal = extract_causal_action_candidates(traj1)
    retained = analyze_irrelevant_actions(causal)
    param_ext = extract_parameter_candidates(retained)
    generalization = analyze_parameter_generalization(param_ext)

    # Generalization shows observed variation for api.call.port
    result = classify_regions(traj1, generalization=generalization)

    for action in result.actions:
        assert action.classification is RegionClassification.REASONING_REQUIRED
        assert action.reason is RegionClassificationReason.UNRESOLVED_VARIATION


def test_type_sensitive_values_detected_as_variation() -> None:
    # Run 1: port is int 8080; Run 2: port is float 8080.0
    traj1 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_1,
            offset=0,
            action_name="srv.start",
            data={"port": 8080},
        )
    )
    traj2 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_2,
            offset=0,
            action_name="srv.start",
            data={"port": 8080.0},
        )
    )

    result = classify_regions(traj1, corroborating=[traj2])

    assert result.actions[0].classification is RegionClassification.REASONING_REQUIRED
    assert result.actions[0].reason is RegionClassificationReason.UNRESOLVED_VARIATION


def test_three_repeated_runs_deterministic() -> None:
    traj1 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_1,
            offset=0,
            action_name="calc.add",
            data={"x": 1, "y": 2},
        )
    )
    traj2 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_2,
            offset=0,
            action_name="calc.add",
            data={"x": 1, "y": 2},
        )
    )
    traj3 = _trajectory(
        _experience(
            correlation_id=_CORRELATION_3,
            offset=0,
            action_name="calc.add",
            data={"x": 1, "y": 2},
        )
    )

    result = classify_regions(traj1, corroborating=[traj2, traj3])

    assert result.actions[0].classification is RegionClassification.DETERMINISTIC
    assert result.actions[0].observation_count == 3
    assert len(result.actions[0].evidence_references) == 3


def test_classify_trajectory_regions_alias() -> None:
    traj1 = _trajectory(_experience(correlation_id=_CORRELATION_1, offset=0, action_name="step.1"))
    traj2 = _trajectory(_experience(correlation_id=_CORRELATION_2, offset=0, action_name="step.1"))

    result = classify_trajectory_regions(traj1, corroborating=[traj2])
    assert result.actions[0].classification is RegionClassification.DETERMINISTIC


def test_provenance_retention_and_source_references() -> None:
    traj = _trajectory(_experience(offset=0, action_name="fs.read", data={"path": "x"}))
    result = classify_regions(traj)

    action = result.actions[0]
    assert action.source_trajectory_id == traj.trajectory_id
    assert action.source_sequence == 1
    assert action.source_decision is not None
    assert action.source_candidate is not None
    assert action.source_step is not None
    assert action.source_step.sequence == 1


def test_deterministic_serialization_roundtrip() -> None:
    traj1 = _trajectory(
        _experience(correlation_id=_CORRELATION_1, offset=0, action_name="calc.add", data={"x": 1})
    )
    traj2 = _trajectory(
        _experience(correlation_id=_CORRELATION_2, offset=0, action_name="calc.add", data={"x": 1})
    )

    first = classify_regions(traj1, corroborating=[traj2])
    second = classify_regions(traj1, corroborating=[traj2])

    assert first == second
    assert first.to_dict() == second.to_dict()
    assert first.to_json() == second.to_json()
    assert first.schema_version == REGION_CLASSIFICATION_SCHEMA_VERSION == 1


def test_immutability_of_analysis_and_regions() -> None:
    traj = _trajectory(_experience(offset=0, action_name="read"))
    result = classify_regions(traj)

    with pytest.raises(FrozenInstanceError):
        result.actions = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.regions[0].classification = RegionClassification.DETERMINISTIC  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.actions[0].action_name = "other"  # type: ignore[misc]


def test_invalid_schema_version_and_types_fail_closed() -> None:
    traj = _trajectory(_experience(offset=0, action_name="read"))
    analysis = classify_regions(traj)

    with pytest.raises(TypeError, match="schema_version must be an integer"):
        RegionClassificationAnalysis(
            source_trajectory_id=analysis.source_trajectory_id,
            actions=analysis.actions,
            regions=analysis.regions,
            schema_version=True,
        )

    with pytest.raises(
        RegionClassificationError, match="unsupported region classification schema version"
    ):
        RegionClassificationAnalysis(
            source_trajectory_id=analysis.source_trajectory_id,
            actions=analysis.actions,
            regions=analysis.regions,
            schema_version=2,
        )

    with pytest.raises(TypeError, match="target must be a"):
        classify_regions(object())  # type: ignore[arg-type]


def test_deterministic_classification_cannot_have_single_observation() -> None:
    traj = _trajectory(_experience(offset=0, action_name="read"))
    ref = ActionEvidenceReference.from_step(trajectory_id=traj.trajectory_id, step=traj.steps[0])

    with pytest.raises(RegionClassificationError, match="at least 2 corroborating observations"):
        ActionClassification(
            source_trajectory_id=traj.trajectory_id,
            source_sequence=1,
            source_experience_sha256=traj.steps[0].source_experience_sha256,
            action_name="read",
            classification=RegionClassification.DETERMINISTIC,
            reason=RegionClassificationReason.REPEATED_VERIFIED_SUCCESS,
            evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
            observation_count=1,
            evidence_references=(ref,),
        )


def test_action_evidence_reference_validation() -> None:
    with pytest.raises(TypeError, match="trajectory_id must be a UUID"):
        ActionEvidenceReference(
            trajectory_id="not-a-uuid",  # type: ignore[arg-type]
            sequence=1,
            experience_sha256=_DUMMY_SHA,
            action_name="action",
            outcome=CausalOutcome.VERIFIED,
            verification_passed=True,
        )

    with pytest.raises(RegionClassificationError, match="must not be the nil UUID"):
        ActionEvidenceReference(
            trajectory_id=_NIL_UUID,
            sequence=1,
            experience_sha256=_DUMMY_SHA,
            action_name="action",
            outcome=CausalOutcome.VERIFIED,
            verification_passed=True,
        )

    with pytest.raises(TypeError, match="sequence must be an integer"):
        ActionEvidenceReference(
            trajectory_id=_CORRELATION_1,
            sequence=True,
            experience_sha256=_DUMMY_SHA,
            action_name="action",
            outcome=CausalOutcome.VERIFIED,
            verification_passed=True,
        )

    with pytest.raises(RegionClassificationError, match="sequence must be >= 1"):
        ActionEvidenceReference(
            trajectory_id=_CORRELATION_1,
            sequence=0,
            experience_sha256=_DUMMY_SHA,
            action_name="action",
            outcome=CausalOutcome.VERIFIED,
            verification_passed=True,
        )

    with pytest.raises(RegionClassificationError, match="experience_sha256 must be 64 lowercase"):
        ActionEvidenceReference(
            trajectory_id=_CORRELATION_1,
            sequence=1,
            experience_sha256="short",
            action_name="action",
            outcome=CausalOutcome.VERIFIED,
            verification_passed=True,
        )

    with pytest.raises(TypeError, match="action_name must be a non-empty string"):
        ActionEvidenceReference(
            trajectory_id=_CORRELATION_1,
            sequence=1,
            experience_sha256=_DUMMY_SHA,
            action_name="",
            outcome=CausalOutcome.VERIFIED,
            verification_passed=True,
        )

    with pytest.raises(TypeError, match="outcome must be a CausalOutcome"):
        ActionEvidenceReference(
            trajectory_id=_CORRELATION_1,
            sequence=1,
            experience_sha256=_DUMMY_SHA,
            action_name="action",
            outcome="verified",  # type: ignore[arg-type]
            verification_passed=True,
        )


def test_classified_region_validation() -> None:
    traj = _trajectory(_experience(offset=0, action_name="read"))
    analysis = classify_regions(traj)
    action = analysis.actions[0]

    with pytest.raises(TypeError, match="region_id must be a non-empty string"):
        ClassifiedRegion(
            region_id="",
            start_sequence=1,
            end_sequence=1,
            classification=RegionClassification.INSUFFICIENT_EVIDENCE,
            reason=RegionClassificationReason.SINGLE_OBSERVATION,
            evidence_sufficiency=EvidenceSufficiency.PARTIAL,
            actions=(action,),
        )

    with pytest.raises(RegionClassificationError, match="end_sequence must be >= start_sequence"):
        ClassifiedRegion(
            region_id="r1",
            start_sequence=2,
            end_sequence=1,
            classification=RegionClassification.INSUFFICIENT_EVIDENCE,
            reason=RegionClassificationReason.SINGLE_OBSERVATION,
            evidence_sufficiency=EvidenceSufficiency.PARTIAL,
            actions=(action,),
        )

    with pytest.raises(
        RegionClassificationError,
        match="first action sequence does not match region start_sequence",
    ):
        ClassifiedRegion(
            region_id="r1",
            start_sequence=2,
            end_sequence=2,
            classification=RegionClassification.INSUFFICIENT_EVIDENCE,
            reason=RegionClassificationReason.SINGLE_OBSERVATION,
            evidence_sufficiency=EvidenceSufficiency.PARTIAL,
            actions=(action,),
        )


def test_corroborating_divergent_lengths_and_action_names() -> None:
    # Target has 2 steps: fs.read, fs.write
    traj1 = _trajectory(
        _experience(correlation_id=_CORRELATION_1, offset=0, action_name="fs.read"),
        _experience(correlation_id=_CORRELATION_1, offset=10, action_name="fs.write"),
    )
    # Corroborating has 1 step: fs.read
    traj2 = _trajectory(
        _experience(correlation_id=_CORRELATION_2, offset=0, action_name="fs.read"),
    )
    result = classify_regions(traj1, corroborating=[traj2])

    assert result.actions[0].classification is RegionClassification.DETERMINISTIC
    assert result.actions[1].classification is RegionClassification.REASONING_REQUIRED
    assert result.actions[1].reason is RegionClassificationReason.UNRESOLVED_VARIATION


def test_classify_regions_from_raw_experiences_and_extractions() -> None:
    exp1 = _experience(correlation_id=_CORRELATION_1, offset=0, action_name="raw.action")
    exp2 = _experience(correlation_id=_CORRELATION_2, offset=0, action_name="raw.action")

    # Pass raw iterable of experiences
    res_raw = classify_regions([exp1], corroborating=[[exp2]])
    assert res_raw.actions[0].classification is RegionClassification.DETERMINISTIC

    # Pass CausalActionExtraction
    traj1 = normalize_trajectory([exp1])
    traj2 = normalize_trajectory([exp2])
    causal1 = extract_causal_action_candidates(traj1)
    causal2 = extract_causal_action_candidates(traj2)
    res_causal = classify_regions(causal1, corroborating=[causal2])
    assert res_causal.actions[0].classification is RegionClassification.DETERMINISTIC

    # Pass IrrelevantActionAnalysis
    irr1 = analyze_irrelevant_actions(causal1)
    irr2 = analyze_irrelevant_actions(causal2)
    res_irr = classify_regions(irr1, corroborating=[irr2])
    assert res_irr.actions[0].classification is RegionClassification.DETERMINISTIC
