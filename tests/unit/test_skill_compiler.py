"""Unit tests for the N2.08 skill-compiler pipeline orchestrator.

The compiler is a composition layer: these tests pin the exact canonical stage
chain, the verified-input policy, deterministic repeatability, explicit failure
on malformed stage output, provenance linkage, and candidate-only semantics.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from agentx import skill_compiler
from agentx.core.causal_experience import CausalExperience, CausalOutcome, ExperienceState
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.core.ids import EpisodeId, ProcedureId, TaskId
from agentx.core.procedures import ProcedureStatus
from agentx.learning.causal_actions import extract_causal_action_candidates
from agentx.learning.irrelevant_actions import (
    ActionDisposition,
    analyze_irrelevant_actions,
)
from agentx.learning.parameter_extraction import extract_parameter_candidates
from agentx.learning.parameter_generalization import analyze_parameter_generalization
from agentx.learning.region_classification import classify_regions
from agentx.learning.trajectory import (
    NormalizedTrajectory,
    TrajectoryNormalizationError,
    normalize_trajectory,
)
from agentx.procedure_synthesis import (
    SynthesisBounds,
    SynthesisOutcome,
    SynthesisRejectionReason,
    synthesize_procedure_candidate,
)
from agentx.skill_compiler import (
    PIPELINE_STAGES,
    SKILL_COMPILER_SCHEMA_VERSION,
    CompilerRejectionReason,
    CompilerStage,
    EvidenceRole,
    EvidenceStatus,
    SkillCompilationOutcome,
    SkillCompilationResult,
    SkillCompilerError,
    compile_skill_candidate,
)

_BASE = datetime(2026, 9, 8, tzinfo=UTC)
_CORR_TARGET = UUID("a1111111-1111-4111-8111-111111111111")
_CORR_SUPPORT = UUID("a2222222-2222-4222-8222-222222222222")
_CORR_SUPPORT_2 = UUID("a3333333-3333-4333-8333-333333333333")
_TASK = TaskId.parse("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_EPISODE = EpisodeId.parse("abbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
_PROCEDURE_ID = ProcedureId.parse("accccccc-cccc-4ccc-8ccc-cccccccccccc")

type _Spec = tuple[str, dict[str, object], CausalOutcome]

_VERIFIED_SCRIPT: tuple[_Spec, ...] = (
    ("widget.open", {"target": "panel"}, CausalOutcome.VERIFIED),
    ("widget.press", {"button": "a"}, CausalOutcome.VERIFIED),
    ("widget.save", {"path": "report.txt"}, CausalOutcome.VERIFIED),
)


def _experience(
    *,
    correlation_id: UUID,
    offset: int,
    action_name: str,
    data: dict[str, object],
    outcome: CausalOutcome,
) -> CausalExperience:
    """Build one canonical C2.10 record for the requested historical outcome."""
    before_at = _BASE + timedelta(seconds=offset)
    verification: VerificationPayload | None = None
    verification_at: datetime | None = None
    observation: ObservationPayload | None = ObservationPayload(
        value={"state": "observed", "offset": offset}
    )
    observation_at: datetime | None = before_at + timedelta(seconds=2)
    state_after: ExperienceState | None = ExperienceState(
        captured_at=before_at + timedelta(seconds=3),
        observation=ObservationPayload(value={"state": "after", "offset": offset}),
    )
    if outcome is CausalOutcome.VERIFIED:
        verification = VerificationPayload(passed=True, detail="verified")
        verification_at = before_at + timedelta(seconds=4)
    elif outcome is CausalOutcome.VERIFICATION_FAILED:
        verification = VerificationPayload(passed=False, detail="verification failed")
        verification_at = before_at + timedelta(seconds=4)
    elif outcome is CausalOutcome.DENIED:
        observation = None
        observation_at = None
        state_after = None
    return CausalExperience(
        task_id=_TASK,
        correlation_id=correlation_id,
        episode_id=_EPISODE,
        state_before=ExperienceState(
            captured_at=before_at,
            observation=ObservationPayload(value={"state": "before", "offset": offset}),
        ),
        action=ActionPayload(name=action_name, data=dict(data)),
        action_at=before_at + timedelta(seconds=1),
        observation=observation,
        observation_at=observation_at,
        state_after=state_after,
        verification=verification,
        verification_at=verification_at,
        outcome=outcome,
        outcome_at=before_at + timedelta(seconds=5),
    )


def _run(
    specs: Iterable[_Spec] = _VERIFIED_SCRIPT,
    *,
    correlation_id: UUID = _CORR_TARGET,
) -> tuple[CausalExperience, ...]:
    return tuple(
        _experience(
            correlation_id=correlation_id,
            offset=index * 10,
            action_name=name,
            data=data,
            outcome=outcome,
        )
        for index, (name, data, outcome) in enumerate(specs)
    )


def _compile(
    specs: Iterable[_Spec] = _VERIFIED_SCRIPT,
    *,
    support: tuple[tuple[UUID, tuple[_Spec, ...]], ...] = ((_CORR_SUPPORT, _VERIFIED_SCRIPT),),
    revision: int = 1,
    bounds: SynthesisBounds | None = None,
) -> SkillCompilationResult:
    return compile_skill_candidate(
        _run(specs),
        corroborating=tuple(
            _run(script, correlation_id=correlation) for correlation, script in support
        ),
        procedure_id=_PROCEDURE_ID,
        revision=revision,
        bounds=bounds,
    )


# ---------------------------------------------------------------------------
# Stage chain.
# ---------------------------------------------------------------------------


def test_pipeline_stage_vocabulary_is_the_exact_canonical_chain() -> None:
    """The declared chain is exactly the eight-step conceptual pipeline."""
    assert PIPELINE_STAGES == (
        CompilerStage.NORMALIZE_TRAJECTORY,
        CompilerStage.EXTRACT_CAUSAL_ACTIONS,
        CompilerStage.ANALYZE_IRRELEVANT_ACTIONS,
        CompilerStage.EXTRACT_PARAMETERS,
        CompilerStage.GENERALIZE_PARAMETERS,
        CompilerStage.CLASSIFY_REGIONS,
        CompilerStage.SYNTHESIZE_CANDIDATE,
    )
    assert [stage.value for stage in PIPELINE_STAGES] == [
        "normalize_trajectory",
        "extract_causal_action_candidates",
        "analyze_irrelevant_actions",
        "extract_parameter_candidates",
        "analyze_parameter_generalization",
        "classify_regions",
        "synthesize_procedure_candidate",
    ]


def test_every_stage_runs_exactly_once_in_canonical_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No stage is skipped, reordered, duplicated, or replaced."""
    calls: list[str] = []

    def spy(name: str, real: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            calls.append(name)
            return real(*args, **kwargs)

        return wrapper

    for stage, real in (
        (CompilerStage.NORMALIZE_TRAJECTORY, normalize_trajectory),
        (CompilerStage.EXTRACT_CAUSAL_ACTIONS, extract_causal_action_candidates),
        (CompilerStage.ANALYZE_IRRELEVANT_ACTIONS, analyze_irrelevant_actions),
        (CompilerStage.EXTRACT_PARAMETERS, extract_parameter_candidates),
        (CompilerStage.GENERALIZE_PARAMETERS, analyze_parameter_generalization),
        (CompilerStage.CLASSIFY_REGIONS, classify_regions),
        (CompilerStage.SYNTHESIZE_CANDIDATE, synthesize_procedure_candidate),
    ):
        monkeypatch.setattr(skill_compiler, stage.value, spy(stage.value, real))

    result = _compile()

    # Target and corroborating runs each traverse the canonical evidence stages
    # needed to produce provenance-bearing C3.04 observations. Generalization,
    # classification and synthesis remain single target-level stages.
    assert calls == [
        "normalize_trajectory",
        "normalize_trajectory",
        "extract_causal_action_candidates",
        "analyze_irrelevant_actions",
        "extract_parameter_candidates",
        "extract_causal_action_candidates",
        "analyze_irrelevant_actions",
        "extract_parameter_candidates",
        "analyze_parameter_generalization",
        "classify_regions",
        "synthesize_procedure_candidate",
    ]
    assert result.stages == PIPELINE_STAGES


def test_compiler_delegates_and_duplicates_no_stage_logic() -> None:
    """Every stage output equals the canonical stage called directly."""
    target = _run()
    support = _run(correlation_id=_CORR_SUPPORT)

    trajectory = normalize_trajectory(target)
    extraction = extract_causal_action_candidates(trajectory)
    elimination = analyze_irrelevant_actions(extraction)
    parameters = extract_parameter_candidates(elimination)
    support_trajectory = normalize_trajectory(support)
    support_extraction = extract_causal_action_candidates(support_trajectory)
    support_elimination = analyze_irrelevant_actions(support_extraction)
    support_parameters = extract_parameter_candidates(support_elimination)
    generalization = analyze_parameter_generalization(
        parameters,
        corroborating=(support_parameters,),
    )
    regions = classify_regions(
        trajectory,
        corroborating=(support_trajectory,),
        generalization=generalization,
    )
    synthesis = synthesize_procedure_candidate(
        trajectory=trajectory,
        extraction=extraction,
        elimination=elimination,
        parameters=parameters,
        generalization=generalization,
        regions=regions,
        procedure_id=_PROCEDURE_ID,
        revision=1,
    )

    result = _compile()

    assert result.trajectory == trajectory
    assert result.extraction == extraction
    assert result.elimination == elimination
    assert result.parameters == parameters
    assert result.generalization == generalization
    assert result.regions == regions
    assert result.synthesis == synthesis


# ---------------------------------------------------------------------------
# Canonical verified trajectory.
# ---------------------------------------------------------------------------


def test_verified_trajectory_produces_a_candidate() -> None:
    """A verified target run corroborated by a verified run compiles."""
    result = _compile()

    assert result.outcome is SkillCompilationOutcome.CANDIDATE
    assert result.is_candidate is True
    assert result.rejection_reasons == ()
    assert result.rejection_details == ()
    assert result.synthesis.outcome is SynthesisOutcome.CANDIDATE
    assert result.graph is not None
    assert len(result.synthesis.included) == len(_VERIFIED_SCRIPT)
    assert result.schema_version == SKILL_COMPILER_SCHEMA_VERSION


def test_multiple_verified_examples_are_all_assessed() -> None:
    """Additional verified runs corroborate without changing candidate semantics."""
    result = _compile(
        support=(
            (_CORR_SUPPORT, _VERIFIED_SCRIPT),
            (_CORR_SUPPORT_2, _VERIFIED_SCRIPT),
        )
    )

    assert result.outcome is SkillCompilationOutcome.CANDIDATE
    assert len(result.evidence) == 3
    assert result.target_evidence.role is EvidenceRole.TARGET
    assert [run.role for run in result.corroborating_evidence] == [
        EvidenceRole.CORROBORATING,
        EvidenceRole.CORROBORATING,
    ]
    assert [run.run_index for run in result.evidence] == [0, 1, 2]
    assert all(run.admissible for run in result.evidence)


def test_single_run_without_corroboration_cannot_reach_a_candidate() -> None:
    """One observation never proves determinism, so synthesis fails closed."""
    result = _compile(support=())

    assert result.outcome is SkillCompilationOutcome.REJECTED
    assert CompilerRejectionReason.SYNTHESIS_REJECTED in result.rejection_reasons
    assert SynthesisRejectionReason.INSUFFICIENT_EVIDENCE in result.synthesis.rejection_reasons
    assert result.graph is None


def test_compilation_is_deterministically_repeatable() -> None:
    """Identical canonical input always produces an identical report."""
    first = _compile()
    second = _compile()

    assert first.to_json() == second.to_json()
    assert first.source_trajectory_id == second.source_trajectory_id
    assert first.to_dict() == second.to_dict()


# ---------------------------------------------------------------------------
# Verified-input policy.
# ---------------------------------------------------------------------------


def test_evidence_assessment_covers_every_step_without_deleting_history() -> None:
    """Failed and denied history is preserved as explicit assessment data."""
    script: tuple[_Spec, ...] = (
        ("widget.open", {"target": "panel"}, CausalOutcome.VERIFIED),
        ("junk.popup", {}, CausalOutcome.DENIED),
        ("widget.save", {"path": "report.txt"}, CausalOutcome.EXECUTION_FAILED),
    )
    result = _compile(script, support=((_CORR_SUPPORT, script),))

    statuses = [item.status for item in result.target_evidence.assessments]
    assert statuses == [
        EvidenceStatus.VERIFIED_SUCCESS,
        EvidenceStatus.DENIED,
        EvidenceStatus.EXECUTION_FAILED,
    ]
    outcomes = [item.outcome for item in result.target_evidence.assessments]
    assert outcomes == [
        CausalOutcome.VERIFIED,
        CausalOutcome.DENIED,
        CausalOutcome.EXECUTION_FAILED,
    ]
    assert len(result.trajectory.steps) == 3
    assert len(result.extraction.candidates) == 3
    assert len(result.elimination.decisions) == 3


def test_history_with_no_verified_success_is_refused() -> None:
    """Arbitrary non-success history is never admitted as skill evidence."""
    script: tuple[_Spec, ...] = (
        ("junk.popup", {}, CausalOutcome.DENIED),
        ("junk.retry", {}, CausalOutcome.TIMED_OUT),
    )
    result = _compile(script, support=((_CORR_SUPPORT, script),))

    assert result.outcome is SkillCompilationOutcome.REJECTED
    assert CompilerRejectionReason.NO_VERIFIED_SUCCESS_EVIDENCE in result.rejection_reasons
    assert result.target_evidence.admissible is False
    assert result.target_evidence.verified_success_count == 0
    assert result.graph is None


def test_denied_history_never_becomes_success_evidence() -> None:
    """A DENIED action stays an explicit C3.03 elimination, never an inclusion."""
    script: tuple[_Spec, ...] = (
        ("widget.open", {"target": "panel"}, CausalOutcome.VERIFIED),
        ("junk.popup", {}, CausalOutcome.DENIED),
        ("widget.save", {"path": "report.txt"}, CausalOutcome.VERIFIED),
    )
    result = _compile(script, support=((_CORR_SUPPORT, script),))

    denied_decision = result.elimination.decisions[1]
    assert denied_decision.disposition is ActionDisposition.ELIMINATE
    assert result.target_evidence.assessments[1].status is EvidenceStatus.DENIED
    assert result.target_evidence.assessments[1].verified_success is False
    assert [item.sequence for item in result.synthesis.excluded] == [2]
    assert 2 not in {item.sequence for item in result.synthesis.included}
    for included in result.synthesis.included:
        assessment = result.target_evidence.assessments[included.sequence - 1]
        assert assessment.status is EvidenceStatus.VERIFIED_SUCCESS


@pytest.mark.parametrize(
    "outcome",
    [
        CausalOutcome.EXECUTION_FAILED,
        CausalOutcome.VERIFICATION_FAILED,
        CausalOutcome.CANCELLED,
        CausalOutcome.TIMED_OUT,
    ],
)
def test_non_success_history_cannot_back_an_included_action(outcome: CausalOutcome) -> None:
    """Failed/unverified attempts never silently become reusable candidate steps."""
    script: tuple[_Spec, ...] = (
        ("widget.open", {"target": "panel"}, CausalOutcome.VERIFIED),
        ("widget.press", {"button": "a"}, outcome),
        ("widget.save", {"path": "report.txt"}, CausalOutcome.VERIFIED),
    )
    result = _compile(script, support=((_CORR_SUPPORT, script),))

    assert result.outcome is SkillCompilationOutcome.REJECTED
    assert CompilerRejectionReason.UNVERIFIED_INCLUDED_ACTION in result.rejection_reasons
    assert result.graph is None
    assert result.status is None
    assert any("sequence 2" in detail for detail in result.rejection_details)


def test_unverified_corroborating_run_is_refused() -> None:
    """Corroboration must itself be verified-success evidence."""
    hostile: tuple[_Spec, ...] = (
        ("junk.popup", {}, CausalOutcome.DENIED),
        ("junk.popup", {}, CausalOutcome.DENIED),
        ("junk.popup", {}, CausalOutcome.DENIED),
    )
    result = _compile(support=((_CORR_SUPPORT, hostile),))

    assert result.outcome is SkillCompilationOutcome.REJECTED
    assert CompilerRejectionReason.UNVERIFIED_CORROBORATING_EVIDENCE in result.rejection_reasons
    assert result.target_evidence.admissible is True
    assert result.corroborating_evidence[0].admissible is False


# ---------------------------------------------------------------------------
# Provenance linkage.
# ---------------------------------------------------------------------------


def test_provenance_linkage_is_preserved_across_every_stage() -> None:
    """Every stage output references the same canonical C3.01 identity."""
    result = _compile()
    trajectory_id = result.trajectory.trajectory_id

    assert result.source_trajectory_id == trajectory_id
    assert result.extraction.source_trajectory_id == trajectory_id
    assert result.elimination.source_trajectory_id == trajectory_id
    assert result.parameters.source_trajectory_id == trajectory_id
    assert result.generalization.source_trajectory_id == trajectory_id
    assert result.regions.source_trajectory_id == trajectory_id
    assert result.synthesis.source_trajectory_id == trajectory_id
    assert result.target_evidence.source_trajectory_id == trajectory_id
    assert result.target_evidence.correlation_id == _CORR_TARGET

    for step, assessment in zip(
        result.trajectory.steps, result.target_evidence.assessments, strict=True
    ):
        assert assessment.source_sequence == step.sequence
        assert assessment.source_experience_sha256 == step.source_experience_sha256
        assert assessment.action_name == step.experience.action.name


def test_included_actions_link_back_to_canonical_source_fingerprints() -> None:
    """Candidate inclusions carry the exact canonical experience fingerprints."""
    result = _compile()
    fingerprints = {step.source_experience_sha256 for step in result.trajectory.steps}

    assert result.synthesis.included
    for included in result.synthesis.included:
        assert included.source_experience_sha256 in fingerprints


# ---------------------------------------------------------------------------
# Malformed stage output fails explicitly.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stage",
    [
        CompilerStage.NORMALIZE_TRAJECTORY,
        CompilerStage.EXTRACT_CAUSAL_ACTIONS,
        CompilerStage.ANALYZE_IRRELEVANT_ACTIONS,
        CompilerStage.EXTRACT_PARAMETERS,
        CompilerStage.GENERALIZE_PARAMETERS,
        CompilerStage.CLASSIFY_REGIONS,
        CompilerStage.SYNTHESIZE_CANDIDATE,
    ],
)
def test_non_canonical_stage_output_fails_explicitly(
    monkeypatch: pytest.MonkeyPatch, stage: CompilerStage
) -> None:
    """A stage that stops returning canonical data stops the pipeline loudly."""

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        return object()

    monkeypatch.setattr(skill_compiler, stage.value, broken)

    with pytest.raises(SkillCompilerError, match=stage.value):
        _compile()


def test_stage_output_from_a_foreign_trajectory_fails_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical-but-unrelated stage output cannot silently replace provenance."""
    foreign = extract_causal_action_candidates(
        normalize_trajectory(_run(correlation_id=_CORR_SUPPORT_2))
    )

    def swapped(_trajectory: NormalizedTrajectory) -> Any:
        return foreign

    monkeypatch.setattr(skill_compiler, "extract_causal_action_candidates", swapped)

    with pytest.raises(SkillCompilerError, match="canonical source trajectory"):
        _compile()


def test_empty_history_fails_through_the_canonical_stage() -> None:
    """The compiler does not invent an empty trajectory; C3.01 refuses first."""
    with pytest.raises(TrajectoryNormalizationError):
        compile_skill_candidate(
            (),
            procedure_id=_PROCEDURE_ID,
            revision=1,
        )


# ---------------------------------------------------------------------------
# Candidate is not active.
# ---------------------------------------------------------------------------


def test_candidate_is_never_active_or_validated() -> None:
    """Construction success is not validation, eligibility, or activation."""
    result = _compile()

    assert result.status is not ProcedureStatus.ACTIVE
    assert result.status is not ProcedureStatus.RETIRED
    assert result.status is ProcedureStatus.CANDIDATE
    assert result.synthesis.status is ProcedureStatus.CANDIDATE
    assert ProcedureStatus.ACTIVE.value not in result.to_json()
    assert ProcedureStatus.RETIRED.value not in result.to_json()


def test_rejected_result_exposes_no_status_and_no_graph() -> None:
    """A refusal carries nothing that could be mistaken for a usable procedure."""
    result = _compile(support=())

    assert result.status is None
    assert result.graph is None
    assert result.is_candidate is False
    assert result.rejection_reasons


# ---------------------------------------------------------------------------
# Report contract.
# ---------------------------------------------------------------------------


def test_report_serializes_deterministically() -> None:
    """The report is inert JSON-compatible data with stable ordering."""
    result = _compile()
    payload = result.to_dict()

    assert payload["schema_version"] == SKILL_COMPILER_SCHEMA_VERSION
    assert payload["outcome"] == "candidate"
    assert payload["status"] == ProcedureStatus.CANDIDATE.value
    assert payload["procedure_id"] == _PROCEDURE_ID.to_str()
    assert payload["stages"] == [stage.value for stage in PIPELINE_STAGES]
    assert result.to_json() == result.to_json()


def test_result_rejects_a_tampered_stage_order() -> None:
    """The report contract itself refuses a non-canonical stage list."""
    result = _compile()

    with pytest.raises(SkillCompilerError, match="canonical pipeline order"):
        SkillCompilationResult(
            outcome=result.outcome,
            procedure_id=result.procedure_id,
            revision=result.revision,
            source_trajectory_id=result.source_trajectory_id,
            stages=PIPELINE_STAGES[:-1],
            evidence=result.evidence,
            trajectory=result.trajectory,
            extraction=result.extraction,
            elimination=result.elimination,
            parameters=result.parameters,
            generalization=result.generalization,
            regions=result.regions,
            synthesis=result.synthesis,
            rejection_reasons=(),
            rejection_details=(),
        )


def test_result_rejects_mismatched_provenance() -> None:
    """The report contract refuses stage outputs from different trajectories."""
    result = _compile()
    foreign = normalize_trajectory(_run(correlation_id=_CORR_SUPPORT_2))

    with pytest.raises(SkillCompilerError, match="one source trajectory"):
        SkillCompilationResult(
            outcome=result.outcome,
            procedure_id=result.procedure_id,
            revision=result.revision,
            source_trajectory_id=foreign.trajectory_id,
            stages=PIPELINE_STAGES,
            evidence=result.evidence,
            trajectory=result.trajectory,
            extraction=result.extraction,
            elimination=result.elimination,
            parameters=result.parameters,
            generalization=result.generalization,
            regions=result.regions,
            synthesis=result.synthesis,
            rejection_reasons=(),
            rejection_details=(),
        )


# ---------------------------------------------------------------------------
# Argument contract.
# ---------------------------------------------------------------------------


def test_procedure_identity_must_be_canonical() -> None:
    with pytest.raises(TypeError, match="procedure_id"):
        compile_skill_candidate(
            _run(),
            procedure_id="accccccc-cccc-4ccc-8ccc-cccccccccccc",  # type: ignore[arg-type]
            revision=1,
        )


def test_revision_must_be_a_positive_integer() -> None:
    with pytest.raises(TypeError, match="revision"):
        compile_skill_candidate(
            _run(),
            procedure_id=_PROCEDURE_ID,
            revision="1",  # type: ignore[arg-type]
        )
    with pytest.raises(SkillCompilerError, match="positive integer"):
        compile_skill_candidate(_run(), procedure_id=_PROCEDURE_ID, revision=0)


def test_bounds_and_episode_must_be_canonical() -> None:
    with pytest.raises(TypeError, match="bounds"):
        compile_skill_candidate(
            _run(),
            procedure_id=_PROCEDURE_ID,
            revision=1,
            bounds={"max_source_actions": 1},  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="episode"):
        compile_skill_candidate(
            _run(),
            procedure_id=_PROCEDURE_ID,
            revision=1,
            episode="episode",  # type: ignore[arg-type]
        )


def test_corroborating_must_be_an_iterable_of_runs() -> None:
    with pytest.raises(TypeError, match="corroborating"):
        compile_skill_candidate(
            _run(),
            corroborating=_run(),  # type: ignore[arg-type]
            procedure_id=_PROCEDURE_ID,
            revision=1,
        )


def test_bounds_are_forwarded_to_the_canonical_synthesis_stage() -> None:
    """Explicit compilation limits reach M4.01 unchanged and fail closed there."""
    result = _compile(bounds=SynthesisBounds(max_source_actions=1))

    assert result.outcome is SkillCompilationOutcome.REJECTED
    assert result.synthesis.rejection_reasons == (SynthesisRejectionReason.LIMIT_EXCEEDED,)
    assert result.synthesis.bounds.max_source_actions == 1
