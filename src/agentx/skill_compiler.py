"""Deterministic skill-compiler pipeline orchestrator (N2.08).

This module is the canonical *top-level* composition layer for the AgentX
skill compiler. Like the A2.10 agent loop and the M4.01 synthesis builder it
lives directly under the ``agentx`` namespace root, which the canonical
boundary manifest deliberately treats as "not a subsystem". That placement is
required: the canonical manifest has no ``LEARNING -> PROCEDURES`` edge, so no
canonical subsystem may legally chain the learning stages into a procedure
candidate. The manifest in ``agentx._architecture`` is NOT widened by N2.08.

The orchestrator composes the *existing* canonical stages, in one explicit and
exact order::

    verified causal experiences (C2.10)
        1. trajectory normalization ............ C3.01 normalize_trajectory
        2. causal-action extraction ............ C3.02 extract_causal_action_candidates
        3. irrelevant-action analysis .......... C3.03 analyze_irrelevant_actions
        4. parameter extraction ................ C3.04 extract_parameter_candidates
        5. parameter generalization ............ C3.05 analyze_parameter_generalization
        6. determinism / reasoning regions ..... C3.06 classify_regions
        7. procedure synthesis candidate ....... M4.01 synthesize_procedure_candidate

No stage logic is reimplemented, copied, inlined, reordered, or skipped, and
no alternative representation of any canonical contract is introduced. Every
stage is invoked through its exact canonical API and every canonical stage
output is carried through unchanged; this module only sequences the calls,
checks structural linkage between them, applies an explicit verified-input
policy, and reports the result.

Verified-input policy
---------------------

Arbitrary history is not skill evidence. The compiler applies two explicit
gates around the canonical stages:

``P1`` (admission)
    Every supplied run - the target run and each corroborating run - must
    record at least one canonical verified-success experience, i.e. an
    experience whose ``CausalOutcome`` is ``VERIFIED`` with a present, passing
    canonical ``VerificationPayload``. A run with no such evidence can never
    produce a candidate.

``P2`` (inclusion)
    Every action the canonical M4.01 builder *includes* in a candidate must be
    backed by canonical verified-success evidence in the target run. Failed,
    verification-failed, cancelled, timed-out, denied, or otherwise incomplete
    history therefore cannot silently become a reusable Procedure candidate.

Non-success history is never deleted, filtered, reordered, or rewritten on the
way into any stage. The early canonical stages intentionally preserve failed
evidence for analysis (C3.03 records ``DENIED`` actions as explicit eliminated
decisions; C3.06 classifies unverified attempts ``REASONING_REQUIRED``), and
that distinction is preserved here. The compiler additionally reports an
explicit :class:`EvidenceAssessment` for every source experience of every run,
so nothing is dropped silently.

Candidate is not active
-----------------------

The output of this pipeline is a *candidate*. It does not mean validated,
eligible, active, trusted, safe, authorized, or executable. The pipeline never
calls procedure validation, never performs a lifecycle transition, never
touches ``ProcedureStore``, never activates or promotes anything, never
executes a Capability, never invokes the kernel, a model, or research, never
persists, and never mutates Task/Permission/ActionGate/risk/budget/stop state.
Historical action, observation, and verification *text* is inert data: it can
never grant authority, force a deterministic classification, or activate a
procedure.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.core.causal_experience import CausalExperience, CausalOutcome
from agentx.core.episodes import EpisodeRecord
from agentx.core.ids import ProcedureId
from agentx.core.procedures import ProcedureStatus
from agentx.learning.causal_actions import (
    CausalActionExtraction,
    extract_causal_action_candidates,
)
from agentx.learning.irrelevant_actions import (
    IrrelevantActionAnalysis,
    analyze_irrelevant_actions,
)
from agentx.learning.parameter_extraction import (
    ParameterExtraction,
    extract_parameter_candidates,
)
from agentx.learning.parameter_generalization import (
    ParameterGeneralization,
    analyze_parameter_generalization,
)
from agentx.learning.region_classification import (
    RegionClassificationAnalysis,
    classify_regions,
)
from agentx.learning.trajectory import NormalizedTrajectory, normalize_trajectory
from agentx.procedure_synthesis import (
    SynthesisBounds,
    SynthesisOutcome,
    SynthesisResult,
    synthesize_procedure_candidate,
)
from agentx.procedures.graph import ProcedureGraph

__all__ = [
    "PIPELINE_STAGES",
    "SKILL_COMPILER_SCHEMA_VERSION",
    "CompilerRejectionReason",
    "CompilerStage",
    "EvidenceAssessment",
    "EvidenceRole",
    "EvidenceStatus",
    "RunEvidence",
    "SkillCompilationOutcome",
    "SkillCompilationResult",
    "SkillCompilerError",
    "compile_skill_candidate",
]

SKILL_COMPILER_SCHEMA_VERSION: Final[int] = 1


class SkillCompilerError(ValueError):
    """Raised when the pipeline cannot be composed from canonical stage data.

    This is an *explicit* failure: a stage returned a non-canonical object, a
    stage output lost its provenance linkage, or a caller supplied arguments
    that no canonical stage could accept. The compiler never guesses, never
    repairs stage output, and never falls back to a partial result.
    """


class CompilerStage(StrEnum):
    """Closed, ordered vocabulary naming each canonical stage invocation."""

    NORMALIZE_TRAJECTORY = "normalize_trajectory"
    EXTRACT_CAUSAL_ACTIONS = "extract_causal_action_candidates"
    ANALYZE_IRRELEVANT_ACTIONS = "analyze_irrelevant_actions"
    EXTRACT_PARAMETERS = "extract_parameter_candidates"
    GENERALIZE_PARAMETERS = "analyze_parameter_generalization"
    CLASSIFY_REGIONS = "classify_regions"
    SYNTHESIZE_CANDIDATE = "synthesize_procedure_candidate"


PIPELINE_STAGES: Final[tuple[CompilerStage, ...]] = (
    CompilerStage.NORMALIZE_TRAJECTORY,
    CompilerStage.EXTRACT_CAUSAL_ACTIONS,
    CompilerStage.ANALYZE_IRRELEVANT_ACTIONS,
    CompilerStage.EXTRACT_PARAMETERS,
    CompilerStage.GENERALIZE_PARAMETERS,
    CompilerStage.CLASSIFY_REGIONS,
    CompilerStage.SYNTHESIZE_CANDIDATE,
)


class EvidenceStatus(StrEnum):
    """Closed vocabulary describing one source experience as skill evidence.

    ``VERIFIED_SUCCESS`` is the only status that may back an action included in
    a candidate. ``INCOMPLETE`` is the fail-closed status for an experience
    that claims ``VERIFIED`` but no longer carries the complete canonical
    verification evidence chain.
    """

    VERIFIED_SUCCESS = "verified_success"
    VERIFICATION_FAILED = "verification_failed"
    EXECUTION_FAILED = "execution_failed"
    DENIED = "denied"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INCOMPLETE = "incomplete"


class EvidenceRole(StrEnum):
    """Role one supplied run plays in the compilation."""

    TARGET = "target"
    CORROBORATING = "corroborating"


class SkillCompilationOutcome(StrEnum):
    """Closed outcome vocabulary for one compilation attempt."""

    CANDIDATE = "candidate"
    REJECTED = "rejected"


class CompilerRejectionReason(StrEnum):
    """Closed reason codes explaining why no candidate was produced."""

    NO_VERIFIED_SUCCESS_EVIDENCE = "no_verified_success_evidence"
    UNVERIFIED_CORROBORATING_EVIDENCE = "unverified_corroborating_evidence"
    UNVERIFIED_INCLUDED_ACTION = "unverified_included_action"
    SYNTHESIS_REJECTED = "synthesis_rejected"


_OUTCOME_STATUS: Final[dict[CausalOutcome, EvidenceStatus]] = {
    CausalOutcome.VERIFICATION_FAILED: EvidenceStatus.VERIFICATION_FAILED,
    CausalOutcome.EXECUTION_FAILED: EvidenceStatus.EXECUTION_FAILED,
    CausalOutcome.DENIED: EvidenceStatus.DENIED,
    CausalOutcome.CANCELLED: EvidenceStatus.CANCELLED,
    CausalOutcome.TIMED_OUT: EvidenceStatus.TIMED_OUT,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceAssessment:
    """Inert verified-input assessment of one canonical source experience.

    The assessment adds no new evidence and changes no history. It records the
    canonical C3.01 provenance of one step plus the closed-vocabulary judgment
    the verified-input policy makes about it.
    """

    source_sequence: int
    source_experience_sha256: str
    action_name: str
    outcome: CausalOutcome
    status: EvidenceStatus

    def __post_init__(self) -> None:
        if type(self.source_sequence) is not int:
            raise TypeError("source_sequence must be an integer")
        if self.source_sequence < 1:
            raise SkillCompilerError("source_sequence must be >= 1")
        if type(self.source_experience_sha256) is not str:
            raise TypeError("source_experience_sha256 must be a string")
        if type(self.action_name) is not str:
            raise TypeError("action_name must be a string")
        if type(self.outcome) is not CausalOutcome:
            raise TypeError("outcome must be a CausalOutcome")
        if type(self.status) is not EvidenceStatus:
            raise TypeError("status must be an EvidenceStatus")

    @property
    def verified_success(self) -> bool:
        """Whether this experience is canonical verified-success skill evidence."""
        return self.status is EvidenceStatus.VERIFIED_SUCCESS

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic JSON-compatible assessment record."""
        return {
            "source_sequence": self.source_sequence,
            "source_experience_sha256": self.source_experience_sha256,
            "action_name": self.action_name,
            "outcome": self.outcome.value,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class RunEvidence:
    """Verified-input assessment of one complete normalized run.

    Nothing is removed here. Every normalized step of the run is represented,
    including eliminated and failed history, so the distinction the early
    canonical stages preserve survives into the compiler report.
    """

    role: EvidenceRole
    run_index: int
    source_trajectory_id: UUID
    correlation_id: UUID
    assessments: tuple[EvidenceAssessment, ...]

    def __post_init__(self) -> None:
        if type(self.role) is not EvidenceRole:
            raise TypeError("role must be an EvidenceRole")
        if type(self.run_index) is not int:
            raise TypeError("run_index must be an integer")
        if self.run_index < 0:
            raise SkillCompilerError("run_index must be >= 0")
        if type(self.source_trajectory_id) is not UUID:
            raise TypeError("source_trajectory_id must be a UUID")
        if type(self.correlation_id) is not UUID:
            raise TypeError("correlation_id must be a UUID")
        if type(self.assessments) is not tuple:
            raise TypeError("assessments must be a tuple of EvidenceAssessment values")
        if not self.assessments:
            raise SkillCompilerError("assessments must not be empty")
        for expected, assessment in enumerate(self.assessments, start=1):
            if type(assessment) is not EvidenceAssessment:
                raise TypeError("assessments must contain only EvidenceAssessment values")
            if assessment.source_sequence != expected:
                raise SkillCompilerError("assessment sequences must be contiguous from 1")

    @property
    def verified_success_count(self) -> int:
        """Number of canonical verified-success experiences in this run."""
        return sum(1 for item in self.assessments if item.verified_success)

    @property
    def non_success(self) -> tuple[EvidenceAssessment, ...]:
        """Assessments that are not canonical verified-success evidence."""
        return tuple(item for item in self.assessments if not item.verified_success)

    @property
    def admissible(self) -> bool:
        """Whether policy ``P1`` admits this run as skill evidence at all."""
        return self.verified_success_count > 0

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic JSON-compatible run-evidence record."""
        return {
            "role": self.role.value,
            "run_index": self.run_index,
            "source_trajectory_id": str(self.source_trajectory_id),
            "correlation_id": str(self.correlation_id),
            "admissible": self.admissible,
            "verified_success_count": self.verified_success_count,
            "assessments": [item.to_dict() for item in self.assessments],
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class SkillCompilationResult:
    """Immutable deterministic report for one skill-compiler pipeline run.

    Every canonical stage output is carried verbatim so downstream readers use
    the canonical contracts rather than a compiler-local copy. ``stages`` names
    the exact stages that ran, in order; it always equals
    :data:`PIPELINE_STAGES` because no stage is ever skipped.

    ``CANDIDATE`` means only that construction succeeded under the
    verified-input policy. It is not validation, eligibility, activation,
    trust, safety, authorization, or executability.
    """

    outcome: SkillCompilationOutcome
    procedure_id: ProcedureId
    revision: int
    source_trajectory_id: UUID
    stages: tuple[CompilerStage, ...]
    evidence: tuple[RunEvidence, ...]
    trajectory: NormalizedTrajectory
    extraction: CausalActionExtraction
    elimination: IrrelevantActionAnalysis
    parameters: ParameterExtraction
    generalization: ParameterGeneralization
    regions: RegionClassificationAnalysis
    synthesis: SynthesisResult
    rejection_reasons: tuple[CompilerRejectionReason, ...]
    rejection_details: tuple[str, ...]
    schema_version: int = SKILL_COMPILER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.outcome) is not SkillCompilationOutcome:
            raise TypeError("outcome must be a SkillCompilationOutcome")
        if type(self.procedure_id) is not ProcedureId:
            raise TypeError("procedure_id must be a ProcedureId")
        if type(self.revision) is not int:
            raise TypeError("revision must be an integer")
        if self.revision < 1:
            raise SkillCompilerError("revision must be a positive integer")
        if type(self.source_trajectory_id) is not UUID:
            raise TypeError("source_trajectory_id must be a UUID")
        if type(self.stages) is not tuple:
            raise TypeError("stages must be a tuple of CompilerStage values")
        if self.stages != PIPELINE_STAGES:
            raise SkillCompilerError("stages must be exactly the canonical pipeline order")
        if type(self.evidence) is not tuple:
            raise TypeError("evidence must be a tuple of RunEvidence values")
        if not self.evidence:
            raise SkillCompilerError("evidence must describe at least the target run")
        for index, run in enumerate(self.evidence):
            if type(run) is not RunEvidence:
                raise TypeError("evidence must contain only RunEvidence values")
            if run.run_index != index:
                raise SkillCompilerError("evidence run_index values must be contiguous from 0")
            expected_role = EvidenceRole.TARGET if index == 0 else EvidenceRole.CORROBORATING
            if run.role is not expected_role:
                raise SkillCompilerError("exactly the first evidence run is the target run")
        if type(self.trajectory) is not NormalizedTrajectory:
            raise TypeError("trajectory must be a canonical NormalizedTrajectory")
        if type(self.extraction) is not CausalActionExtraction:
            raise TypeError("extraction must be a canonical CausalActionExtraction")
        if type(self.elimination) is not IrrelevantActionAnalysis:
            raise TypeError("elimination must be a canonical IrrelevantActionAnalysis")
        if type(self.parameters) is not ParameterExtraction:
            raise TypeError("parameters must be a canonical ParameterExtraction")
        if type(self.generalization) is not ParameterGeneralization:
            raise TypeError("generalization must be a canonical ParameterGeneralization")
        if type(self.regions) is not RegionClassificationAnalysis:
            raise TypeError("regions must be a canonical RegionClassificationAnalysis")
        if type(self.synthesis) is not SynthesisResult:
            raise TypeError("synthesis must be a canonical SynthesisResult")
        if type(self.rejection_reasons) is not tuple:
            raise TypeError("rejection_reasons must be a tuple of CompilerRejectionReason values")
        for reason in self.rejection_reasons:
            if type(reason) is not CompilerRejectionReason:
                raise TypeError("rejection_reasons must contain only CompilerRejectionReason")
        if type(self.rejection_details) is not tuple:
            raise TypeError("rejection_details must be a tuple of strings")
        for detail in self.rejection_details:
            if type(detail) is not str:
                raise TypeError("rejection_details must contain only strings")
        if type(self.schema_version) is not int:
            raise TypeError("schema_version must be an integer")
        if self.schema_version != SKILL_COMPILER_SCHEMA_VERSION:
            raise SkillCompilerError(
                f"unsupported skill compiler schema version {self.schema_version}; "
                f"supported version is {SKILL_COMPILER_SCHEMA_VERSION}"
            )

        linked = (
            self.trajectory.trajectory_id,
            self.extraction.source_trajectory_id,
            self.elimination.source_trajectory_id,
            self.parameters.source_trajectory_id,
            self.generalization.source_trajectory_id,
            self.regions.source_trajectory_id,
            self.synthesis.source_trajectory_id,
            self.evidence[0].source_trajectory_id,
        )
        if any(value != self.source_trajectory_id for value in linked):
            raise SkillCompilerError("every stage output must reference one source trajectory")
        if self.synthesis.procedure_id != self.procedure_id:
            raise SkillCompilerError("synthesis procedure_id must match the compilation identity")
        if self.synthesis.revision != self.revision:
            raise SkillCompilerError("synthesis revision must match the compilation identity")

        if self.outcome is SkillCompilationOutcome.CANDIDATE:
            if self.rejection_reasons or self.rejection_details:
                raise SkillCompilerError("CANDIDATE outcome must carry no rejection")
            if self.synthesis.outcome is not SynthesisOutcome.CANDIDATE:
                raise SkillCompilerError("CANDIDATE outcome requires a synthesized candidate")
            if self.synthesis.status is not ProcedureStatus.CANDIDATE:
                raise SkillCompilerError("CANDIDATE outcome requires CANDIDATE synthesis status")
        elif not self.rejection_reasons:
            raise SkillCompilerError("REJECTED outcome requires at least one reason")

    @property
    def status(self) -> ProcedureStatus | None:
        """Canonical storage status of the output: ``CANDIDATE`` or nothing.

        This is never ``ACTIVE`` and never ``RETIRED``: the compiler performs
        no promotion, no validation, and no lifecycle transition.
        """
        if self.outcome is not SkillCompilationOutcome.CANDIDATE:
            return None
        return ProcedureStatus.CANDIDATE

    @property
    def graph(self) -> ProcedureGraph | None:
        """Canonical unvalidated candidate graph, or ``None`` when rejected."""
        if self.outcome is not SkillCompilationOutcome.CANDIDATE:
            return None
        return self.synthesis.graph

    @property
    def is_candidate(self) -> bool:
        """Whether a candidate was constructed. Never means active or eligible."""
        return self.outcome is SkillCompilationOutcome.CANDIDATE

    @property
    def target_evidence(self) -> RunEvidence:
        """Verified-input assessment of the target run."""
        return self.evidence[0]

    @property
    def corroborating_evidence(self) -> tuple[RunEvidence, ...]:
        """Verified-input assessments of the corroborating runs, in supplied order."""
        return self.evidence[1:]

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic JSON-compatible compilation report."""
        return {
            "schema_version": self.schema_version,
            "outcome": self.outcome.value,
            "status": None if self.status is None else self.status.value,
            "procedure_id": self.procedure_id.to_str(),
            "revision": self.revision,
            "source_trajectory_id": str(self.source_trajectory_id),
            "stages": [stage.value for stage in self.stages],
            "evidence": [run.to_dict() for run in self.evidence],
            "synthesis": self.synthesis.to_dict(),
            "rejection_reasons": [reason.value for reason in self.rejection_reasons],
            "rejection_details": list(self.rejection_details),
        }

    def to_json(self) -> str:
        """Serialize the report deterministically without executable hooks."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _require_stage_output[T](value: object, expected: type[T], stage: CompilerStage) -> T:
    """Fail explicitly when a canonical stage did not return its canonical type."""
    if type(value) is not expected:
        raise SkillCompilerError(
            f"stage '{stage.value}' returned {type(value).__name__}, "
            f"expected canonical {expected.__name__}"
        )
    return value


def _require_linkage(actual: object, expected: UUID, stage: CompilerStage) -> None:
    """Fail explicitly when a stage output lost its canonical provenance linkage."""
    if type(actual) is not UUID or actual != expected:
        raise SkillCompilerError(
            f"stage '{stage.value}' output does not reference the canonical source trajectory"
        )


def _assess_experience(experience: object) -> EvidenceStatus:
    """Judge one canonical experience against the verified-success definition.

    ``CausalExperience`` already refuses to construct a ``VERIFIED`` record
    without a passing verification. Re-reading the evidence chain here is a
    fail-closed guard so a post-construction mutation cannot smuggle an
    unverified record in as success evidence.
    """
    if type(experience) is not CausalExperience:
        raise SkillCompilerError("normalized step does not carry a canonical CausalExperience")
    outcome = experience.outcome
    if type(outcome) is not CausalOutcome:
        raise SkillCompilerError("source experience outcome is not a canonical CausalOutcome")
    if outcome is CausalOutcome.VERIFIED:
        verification = experience.verification
        if (
            verification is None
            or verification.passed is not True
            or experience.observation is None
            or experience.state_after is None
        ):
            return EvidenceStatus.INCOMPLETE
        return EvidenceStatus.VERIFIED_SUCCESS
    status = _OUTCOME_STATUS.get(outcome)
    if status is None:  # pragma: no cover - closed vocabulary guard
        raise SkillCompilerError("source experience outcome is not a canonical CausalOutcome")
    return status


def _assess_run(
    trajectory: NormalizedTrajectory, *, role: EvidenceRole, run_index: int
) -> RunEvidence:
    """Assess every normalized step of one run without dropping any of them."""
    assessments: list[EvidenceAssessment] = []
    for step in trajectory.steps:
        experience = step.experience
        status = _assess_experience(experience)
        action_name = experience.action.name
        if type(action_name) is not str:
            raise SkillCompilerError("source action name is not canonical data")
        assessments.append(
            EvidenceAssessment(
                source_sequence=step.sequence,
                source_experience_sha256=step.source_experience_sha256,
                action_name=action_name,
                outcome=experience.outcome,
                status=status,
            )
        )
    return RunEvidence(
        role=role,
        run_index=run_index,
        source_trajectory_id=trajectory.trajectory_id,
        correlation_id=trajectory.correlation_id,
        assessments=tuple(assessments),
    )


def _corroborating_runs(
    corroborating: Iterable[Iterable[CausalExperience]],
) -> tuple[tuple[CausalExperience, ...], ...]:
    """Materialize supplied corroborating runs without reordering or filtering."""
    if isinstance(corroborating, str | bytes | CausalExperience | NormalizedTrajectory):
        raise TypeError("corroborating must be an iterable of causal-experience runs")
    try:
        return tuple(tuple(run) for run in corroborating)
    except TypeError as exc:
        raise TypeError("corroborating must be an iterable of causal-experience runs") from exc


def compile_skill_candidate(
    experiences: Iterable[CausalExperience],
    *,
    corroborating: Iterable[Iterable[CausalExperience]] = (),
    procedure_id: ProcedureId,
    revision: int,
    episode: EpisodeRecord | None = None,
    bounds: SynthesisBounds | None = None,
) -> SkillCompilationResult:
    """Run the canonical skill-compiler stages and report a candidate or refusal.

    ``experiences`` is the target run of canonical C2.10 causal experiences.
    ``corroborating`` supplies additional independent runs; the canonical C3.06
    classifier needs at least one corroborating verified run before it will
    call any region deterministic, and M4.01 refuses to synthesize from
    single-observation evidence.

    All seven canonical stages always run, in :data:`PIPELINE_STAGES` order.
    The verified-input policy is applied to the canonical stage outputs, never
    by filtering, reordering, or rewriting the history handed to a stage.

    Returns a :class:`SkillCompilationResult`. A ``CANDIDATE`` outcome is an
    unvalidated, inactive, unauthorized construction result; it is never stored,
    validated, promoted, or executed by this function. Malformed or
    non-canonical stage output raises :class:`SkillCompilerError`.
    """
    if type(procedure_id) is not ProcedureId:
        raise TypeError(f"procedure_id must be a ProcedureId, got {type(procedure_id).__name__}")
    if type(revision) is not int:
        raise TypeError(f"revision must be an integer, got {type(revision).__name__}")
    if revision < 1:
        raise SkillCompilerError("revision must be a positive integer (first revision is 1)")
    if episode is not None and not isinstance(episode, EpisodeRecord):
        raise TypeError(f"episode must be an EpisodeRecord or None, got {type(episode).__name__}")
    if bounds is not None and type(bounds) is not SynthesisBounds:
        raise TypeError(f"bounds must be SynthesisBounds or None, got {type(bounds).__name__}")

    corroborating_runs = _corroborating_runs(corroborating)

    # Stage 1 - C3.01 trajectory normalization (target and every support run).
    stage = CompilerStage.NORMALIZE_TRAJECTORY
    trajectory = _require_stage_output(
        normalize_trajectory(experiences, episode=episode), NormalizedTrajectory, stage
    )
    support_trajectories = tuple(
        _require_stage_output(normalize_trajectory(run), NormalizedTrajectory, stage)
        for run in corroborating_runs
    )
    trajectory_id = trajectory.trajectory_id

    evidence = (
        _assess_run(trajectory, role=EvidenceRole.TARGET, run_index=0),
        *(
            _assess_run(support, role=EvidenceRole.CORROBORATING, run_index=index)
            for index, support in enumerate(support_trajectories, start=1)
        ),
    )

    # Stage 2 - C3.02 causal-action extraction.
    stage = CompilerStage.EXTRACT_CAUSAL_ACTIONS
    extraction = _require_stage_output(
        extract_causal_action_candidates(trajectory), CausalActionExtraction, stage
    )
    _require_linkage(extraction.source_trajectory_id, trajectory_id, stage)

    # Stage 3 - C3.03 irrelevant-action analysis.
    stage = CompilerStage.ANALYZE_IRRELEVANT_ACTIONS
    elimination = _require_stage_output(
        analyze_irrelevant_actions(extraction), IrrelevantActionAnalysis, stage
    )
    _require_linkage(elimination.source_trajectory_id, trajectory_id, stage)

    # Stage 4 - C3.04 parameter extraction.
    stage = CompilerStage.EXTRACT_PARAMETERS
    parameters = _require_stage_output(
        extract_parameter_candidates(elimination), ParameterExtraction, stage
    )
    _require_linkage(parameters.source_trajectory_id, trajectory_id, stage)

    # Stage 5 - C3.05 parameter generalization. Corroborating verified
    # trajectories contribute provenance-bearing parameter observations through
    # the same canonical C3.02 -> C3.04 stages; no free-form/model data enters.
    stage = CompilerStage.GENERALIZE_PARAMETERS
    supporting_parameters = tuple(
        _require_stage_output(
            extract_parameter_candidates(
                _require_stage_output(
                    analyze_irrelevant_actions(
                        _require_stage_output(
                            extract_causal_action_candidates(support),
                            CausalActionExtraction,
                            stage,
                        )
                    ),
                    IrrelevantActionAnalysis,
                    stage,
                )
            ),
            ParameterExtraction,
            stage,
        )
        for support in support_trajectories
    )
    generalization = _require_stage_output(
        analyze_parameter_generalization(
            parameters,
            corroborating=supporting_parameters,
        ),
        ParameterGeneralization,
        stage,
    )
    _require_linkage(generalization.source_trajectory_id, trajectory_id, stage)

    # Stage 6 - C3.06 determinism / reasoning-region classification.
    stage = CompilerStage.CLASSIFY_REGIONS
    regions = _require_stage_output(
        classify_regions(
            trajectory,
            corroborating=support_trajectories or None,
            generalization=generalization,
        ),
        RegionClassificationAnalysis,
        stage,
    )
    _require_linkage(regions.source_trajectory_id, trajectory_id, stage)

    # Stage 7 - M4.01 procedure synthesis candidate builder.
    stage = CompilerStage.SYNTHESIZE_CANDIDATE
    synthesis = _require_stage_output(
        synthesize_procedure_candidate(
            trajectory=trajectory,
            extraction=extraction,
            elimination=elimination,
            parameters=parameters,
            generalization=generalization,
            regions=regions,
            procedure_id=procedure_id,
            revision=revision,
            bounds=bounds,
        ),
        SynthesisResult,
        stage,
    )
    _require_linkage(synthesis.source_trajectory_id, trajectory_id, stage)

    reasons, details = _apply_verified_input_policy(evidence=evidence, synthesis=synthesis)
    outcome = SkillCompilationOutcome.CANDIDATE if not reasons else SkillCompilationOutcome.REJECTED
    return SkillCompilationResult(
        outcome=outcome,
        procedure_id=procedure_id,
        revision=revision,
        source_trajectory_id=trajectory_id,
        stages=PIPELINE_STAGES,
        evidence=evidence,
        trajectory=trajectory,
        extraction=extraction,
        elimination=elimination,
        parameters=parameters,
        generalization=generalization,
        regions=regions,
        synthesis=synthesis,
        rejection_reasons=reasons,
        rejection_details=details,
    )


def _apply_verified_input_policy(
    *,
    evidence: tuple[RunEvidence, ...],
    synthesis: SynthesisResult,
) -> tuple[tuple[CompilerRejectionReason, ...], tuple[str, ...]]:
    """Decide candidacy from canonical stage output; never rewrite that output."""
    reasons: list[CompilerRejectionReason] = []
    details: list[str] = []

    target = evidence[0]
    if not target.admissible:
        reasons.append(CompilerRejectionReason.NO_VERIFIED_SUCCESS_EVIDENCE)
        details.append(
            "target run records no canonical verified-success experience; "
            "observed statuses: "
            + ", ".join(sorted({item.status.value for item in target.assessments}))
        )
    for run in evidence[1:]:
        if run.admissible:
            continue
        if CompilerRejectionReason.UNVERIFIED_CORROBORATING_EVIDENCE not in reasons:
            reasons.append(CompilerRejectionReason.UNVERIFIED_CORROBORATING_EVIDENCE)
        details.append(
            f"corroborating run {run.run_index} records no canonical verified-success experience"
        )

    if synthesis.outcome is not SynthesisOutcome.CANDIDATE:
        reasons.append(CompilerRejectionReason.SYNTHESIS_REJECTED)
        for reason in synthesis.rejection_reasons:
            details.append(f"synthesis rejected: {reason.value}")
        details.extend(synthesis.rejection_details)
        return _dedupe(reasons), tuple(details)

    by_sequence = {item.source_sequence: item for item in target.assessments}
    for included in synthesis.included:
        assessment = by_sequence.get(included.sequence)
        if assessment is None:
            raise SkillCompilerError(
                f"synthesis included sequence {included.sequence} with no source evidence"
            )
        if assessment.verified_success:
            continue
        if CompilerRejectionReason.UNVERIFIED_INCLUDED_ACTION not in reasons:
            reasons.append(CompilerRejectionReason.UNVERIFIED_INCLUDED_ACTION)
        details.append(
            f"sequence {included.sequence}: included action is backed by "
            f"'{assessment.status.value}' evidence, not canonical verified success"
        )
    return _dedupe(reasons), tuple(details)


def _dedupe(reasons: list[CompilerRejectionReason]) -> tuple[CompilerRejectionReason, ...]:
    return tuple(dict.fromkeys(reasons))
