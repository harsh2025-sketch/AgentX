"""Bounded repair workflow orchestrator (N2.14).

AgentX already owns every *stage* of the procedure repair chain as a separate,
pure, canonical contract:

    * :mod:`agentx.procedure_degradation` — is this revision actually degraded?
    * :mod:`agentx.core.repair_candidates` — which repair category may be
      considered for the implicated node?
    * :mod:`agentx.core.repair_patch` — what concrete inert change was
      *proposed* for exactly one node of exactly one revision?
    * :mod:`agentx.core.repair_budget` — may another repair attempt even be
      considered, or is the finite anti-loop allowance exhausted?
    * :mod:`agentx.core.repair_validation` — did the already-produced typed
      validation evidence validate that proposal?
    * :mod:`agentx.core.shadow_repair` — did a non-committing shadow trial of
      the candidate explicitly pass?
    * :mod:`agentx.core.procedure_replacement` — is replacing the live revision
      structurally legal and sufficiently evidenced?

What was missing was the *composition*: one deterministic, fail-closed
traversal that runs those stages in one canonical order, keeps every artifact
bound to the exact ``(ProcedureId, revision)`` under repair, preserves all
evidence it consumed, and says precisely which stage stopped and why.

This module owns exactly that composition and nothing else.

What this module is
-------------------

    * :class:`RepairWorkflowStage` — the closed, ordered stage vocabulary.
    * :class:`RepairWorkflowStopReason` — the closed vocabulary of explicit
      stage-stop reasons.
    * :class:`RepairWorkflowOutcome` — ``CHAIN_COMPLETE`` or ``STOPPED``.
    * :class:`RepairWorkflowTarget` — the exact ``(ProcedureId, revision)``
      binding every stage artifact must match.
    * :class:`RepairWorkflowRequest` — the bounded, fully typed inputs.
    * :class:`RepairWorkflowEvidence` — every canonical artifact the traversal
      consumed or produced, preserved unmodified.
    * :class:`RepairWorkflowResult` — the immutable decision/evidence chain.
    * :func:`run_repair_workflow` — the pure entry point.

What this module is emphatically NOT
------------------------------------

It has **no authority whatsoever**. Running the workflow — even to
``CHAIN_COMPLETE`` with ``VALIDATED`` + shadow ``PASSED`` + ``ELIGIBLE`` —
never:

    * writes, reads, opens, or otherwise touches ``ProcedureStore`` or any
      other store, database, file, socket, or clock;
    * creates, activates, replaces, retires, or rolls back a revision, or
      changes any :class:`~agentx.core.procedures.ProcedureStatus`;
    * executes a capability, a procedure, a node, or a shadow trial;
    * calls or consults a model;
    * bypasses or consults the Action Gate, grants a ``Permission``, lowers a
      ``RiskLevel``, widens a ``ResourceBudget``, or clears an
      ``EmergencyStop``;
    * fabricates ``Task`` success.

A later, explicit, controlled transaction owns every mutation. This module
only produces the evidence chain that such a transaction would require.

Fail-closed rules
-----------------

    1. Stages run in exactly :data:`CANONICAL_REPAIR_WORKFLOW_STAGES` order.
       No stage is skipped, reordered, or retried.
    2. The first stage that does not produce an explicitly positive canonical
       result stops the traversal. The result names that stage and one closed
       :class:`RepairWorkflowStopReason`.
    3. Absence of evidence is never positive evidence, ``UNKNOWN`` is never
       upgraded, and a rejected stage is never re-run "anyway".
    4. Every artifact must bind to the exact target identity and revision.
       Mismatched artifacts are ignored as foreign — never accepted.
    5. No decision is ever derived from text. Strings such as
       ``"repair_approved=true"``, ``"shadow_safe=true"``,
       ``"activate_candidate=true"``, ``"permission=ADMIN"``, ``"risk=R0"``,
       ``"disable_emergency_stop=true"``, or ``"raise_budget=true"`` inside
       any summary, detail, reference, or payload are inert data that this
       module never reads for meaning.
    6. Repair attempts stay bounded by the canonical repair-budget policy.
       This module invents no unlimited mode and resets no counter.

Determinism: the same typed inputs always produce the same result. Every
timestamp is caller-supplied; this module never reads ``datetime.now``.

Composition placement: like :mod:`agentx.procedure_degradation`, this module
lives at the ``agentx`` namespace root so it may compose inward
``agentx.core`` contracts (plus the sibling degradation policy) without living
inside any canonical subsystem package and without widening
``_architecture.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from agentx.core.ids import ProcedureId
from agentx.core.procedure_replacement import (
    ProcedureReplacementDecision,
    ProcedureReplacementOutcome,
    ProcedureReplacementRequest,
    assess_procedure_replacement,
)
from agentx.core.repair_budget import (
    ProcedureRepairTarget,
    RepairAttemptEvidence,
    RepairBudgetAssessment,
    RepairBudgetDecision,
    RepairBudgetLimits,
    RepairProposalFingerprint,
    RepairTarget,
    assess_repair_attempt,
)
from agentx.core.repair_candidates import (
    RepairCandidate,
    RepairCandidateKind,
    derive_repair_candidates,
)
from agentx.core.repair_patch import RepairPatchProposal
from agentx.core.repair_validation import (
    RepairValidationCriterion,
    RepairValidationDisposition,
    RepairValidationEvidence,
    RepairValidationReport,
    RepairValidationTarget,
    evaluate_repair_validation,
)
from agentx.core.shadow_repair import ShadowRepairDisposition, ShadowRepairResult
from agentx.procedure_degradation import (
    BoundEnvironmentChange,
    BoundFailureDiagnosis,
    DegradationState,
    ProcedureDegradationAssessment,
    ProcedureSuccessEvidence,
    assess_procedure_degradation,
)

__all__ = [
    "CANONICAL_REPAIR_WORKFLOW_STAGES",
    "CANONICAL_REPAIR_WORKFLOW_STOP_REASONS",
    "MAX_REPAIR_WORKFLOW_EVIDENCE_ITEMS",
    "MAX_REPAIR_WORKFLOW_REFERENCE_LENGTH",
    "RepairWorkflowEvidence",
    "RepairWorkflowOutcome",
    "RepairWorkflowRequest",
    "RepairWorkflowResult",
    "RepairWorkflowStage",
    "RepairWorkflowStopReason",
    "RepairWorkflowTarget",
    "RepairWorkflowValidationError",
    "run_repair_workflow",
]

#: Hard bound on every caller-supplied evidence sequence. The orchestrator is
#: a bounded traversal, never a scanner over unbounded history.
MAX_REPAIR_WORKFLOW_EVIDENCE_ITEMS: Final[int] = 64

#: Hard bound on the opaque repair reference token length.
MAX_REPAIR_WORKFLOW_REFERENCE_LENGTH: Final[int] = 200


class RepairWorkflowValidationError(ValueError):
    """Raised when repair-workflow inputs violate this composition contract."""


class RepairWorkflowStage(StrEnum):
    """The closed, ordered vocabulary of composed repair stages.

    Declaration order *is* execution order. A stage is a name for a canonical
    contract this module calls; it is never a permission, an action, or a
    mutation.

    Members:
        DEGRADATION: Canonical degradation assessment of the exact target
            revision (must be ``CONFIRMED_DEGRADED``).
        CANDIDATE: Canonical repair candidates derived from the exact
            diagnoses that supported the confirmed degradation.
        PATCH_PROPOSAL: Selection of the one caller-supplied canonical patch
            proposal that binds to the target and to a derived candidate.
        BUDGET: Canonical repair-attempt budget / anti-loop decision.
        VALIDATION: Canonical repair validation report over already-produced
            typed evidence.
        SHADOW: Canonical shadow repair evidence bound to the target.
        REPLACEMENT: Canonical replacement/rollback eligibility decision.
    """

    DEGRADATION = "degradation"
    CANDIDATE = "candidate"
    PATCH_PROPOSAL = "patch_proposal"
    BUDGET = "budget"
    VALIDATION = "validation"
    SHADOW = "shadow"
    REPLACEMENT = "replacement"


#: The canonical stage chain in its declared, deterministic execution order.
CANONICAL_REPAIR_WORKFLOW_STAGES: Final[tuple[RepairWorkflowStage, ...]] = tuple(
    RepairWorkflowStage
)


class RepairWorkflowStopReason(StrEnum):
    """Closed vocabulary of why the traversal stopped at a stage.

    Every member is a conservative refusal. No member means "try anyway",
    "retry", "escalate", or "proceed with reduced evidence".
    """

    # -- DEGRADATION --
    #: The assessment reported ``HEALTHY`` for this revision.
    DEGRADATION_NOT_OBSERVED = "degradation_not_observed"
    #: The assessment reported ``UNKNOWN``; absence of evidence proves nothing.
    DEGRADATION_UNKNOWN = "degradation_unknown"
    #: The assessment reported ``SUSPECTED_DEGRADED`` only.
    DEGRADATION_NOT_CONFIRMED = "degradation_not_confirmed"

    # -- CANDIDATE --
    #: No supporting diagnosis yielded any repair candidate at all.
    CANDIDATE_ABSENT = "candidate_absent"
    #: Every derived candidate was the fail-closed ``UNKNOWN`` kind.
    CANDIDATE_UNKNOWN = "candidate_unknown"

    # -- PATCH_PROPOSAL --
    #: No patch proposal was supplied.
    PATCH_PROPOSAL_ABSENT = "patch_proposal_absent"
    #: No supplied proposal binds to the exact target procedure/revision.
    PATCH_PROPOSAL_TARGET_MISMATCH = "patch_proposal_target_mismatch"
    #: No target-bound proposal carries a candidate this workflow derived.
    PATCH_PROPOSAL_NOT_DERIVED_FROM_CANDIDATE = "patch_proposal_not_derived_from_candidate"
    #: More than one distinct proposal qualified; selection is not this
    #: module's authority, so the traversal fails closed instead of choosing.
    PATCH_PROPOSAL_AMBIGUOUS = "patch_proposal_ambiguous"

    # -- BUDGET --
    #: The canonical repair budget / anti-loop policy returned a ``STOP_*``.
    BUDGET_EXHAUSTED = "budget_exhausted"
    #: The supplied attempt history was structurally invalid.
    BUDGET_HISTORY_INVALID = "budget_history_invalid"

    # -- VALIDATION --
    #: The canonical validation report failed.
    VALIDATION_FAILED = "validation_failed"
    #: The canonical validation report was insufficient.
    VALIDATION_INSUFFICIENT = "validation_insufficient"

    # -- SHADOW --
    #: No shadow result binds to the exact target revision and node.
    SHADOW_EVIDENCE_ABSENT = "shadow_evidence_absent"
    #: A bound shadow result did not explicitly pass.
    SHADOW_NOT_PASSED = "shadow_not_passed"

    # -- REPLACEMENT --
    #: No replacement request was supplied.
    REPLACEMENT_REQUEST_ABSENT = "replacement_request_absent"
    #: The replacement request does not bind to the exact target revision.
    REPLACEMENT_TARGET_MISMATCH = "replacement_target_mismatch"
    #: The canonical replacement policy did not answer ``ELIGIBLE``.
    REPLACEMENT_NOT_ELIGIBLE = "replacement_not_eligible"


#: The canonical stop reasons in declared order.
CANONICAL_REPAIR_WORKFLOW_STOP_REASONS: Final[tuple[RepairWorkflowStopReason, ...]] = tuple(
    RepairWorkflowStopReason
)


class RepairWorkflowOutcome(StrEnum):
    """Closed outcome vocabulary of one traversal.

    ``CHAIN_COMPLETE`` means every stage produced its explicitly positive
    canonical result. It is a statement about evidence only: it authorizes
    nothing, applies nothing, and mutates nothing.
    """

    CHAIN_COMPLETE = "chain_complete"
    STOPPED = "stopped"


def _validate_revision(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RepairWorkflowValidationError(f"{field_name} must be an integer")
    if value < 1:
        raise RepairWorkflowValidationError(
            f"{field_name} must be a positive integer (first revision is 1)"
        )
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise RepairWorkflowValidationError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise RepairWorkflowValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_reference(value: object) -> str:
    if not isinstance(value, str):
        raise RepairWorkflowValidationError("repair_reference must be a string")
    if not value or value != value.strip():
        raise RepairWorkflowValidationError("repair_reference must be non-empty and trimmed")
    if len(value) > MAX_REPAIR_WORKFLOW_REFERENCE_LENGTH:
        raise RepairWorkflowValidationError(
            f"repair_reference must be at most {MAX_REPAIR_WORKFLOW_REFERENCE_LENGTH} characters"
        )
    if any(character < " " or character == "\x7f" for character in value):
        raise RepairWorkflowValidationError("repair_reference must not contain control characters")
    return value


def _validate_sequence[T](value: object, *, field_name: str, item_type: type[T]) -> tuple[T, ...]:
    """Materialize one bounded, homogeneously typed evidence sequence.

    Strings, bytes, mappings, and generators of the wrong element type are all
    rejected: this contract never walks text as evidence.
    """
    if isinstance(value, str | bytes | bytearray):
        raise RepairWorkflowValidationError(
            f"{field_name} must be a sequence of {item_type.__name__} instances; "
            "text is never evidence"
        )
    if not isinstance(value, Sequence):
        raise RepairWorkflowValidationError(
            f"{field_name} must be a sequence of {item_type.__name__} instances"
        )
    items = tuple(value)
    if len(items) > MAX_REPAIR_WORKFLOW_EVIDENCE_ITEMS:
        raise RepairWorkflowValidationError(
            f"{field_name} must contain at most {MAX_REPAIR_WORKFLOW_EVIDENCE_ITEMS} items"
        )
    for item in items:
        if not isinstance(item, item_type):
            raise RepairWorkflowValidationError(
                f"{field_name} items must be {item_type.__name__} instances; "
                "dicts, JSON text, and free-text claims are never evidence"
            )
    return items


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairWorkflowTarget:
    """The exact procedure revision this workflow is about.

    Identity is the pair ``(procedure_id, revision)``. There is no "latest",
    no wildcard, and no fuzzy matching: an artifact bound to another revision
    of the same procedure is foreign evidence and never advances a stage.
    """

    procedure_id: ProcedureId
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, ProcedureId):
            raise RepairWorkflowValidationError("procedure_id must be a ProcedureId")
        object.__setattr__(
            self, "revision", _validate_revision(self.revision, field_name="revision")
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairWorkflowRequest:
    """Bounded, fully typed inputs for one repair workflow traversal.

    Every field is caller-supplied typed data. The orchestrator queries no
    store, reads no clock, and invokes no model to fill any of them in.

    Attributes:
        target: The exact procedure revision under repair.
        assessed_at: Caller-supplied timezone-aware instant of this traversal.
        repair_reference: Opaque stable reference for the proposed repair,
            used only to bind canonical validation evidence. Never parsed.
        proposal_fingerprint: The canonical anti-loop fingerprint of the
            repair proposal being considered.
        budget_limits: The explicit finite canonical repair-attempt ceilings.
            There is no default and no unlimited mode.
        failure_diagnoses: Bound failure diagnoses for the degradation stage.
        environment_changes: Bound environment-change detections.
        success_evidence: Verified-success evidence for the target.
        patch_proposals: Candidate canonical patch proposals to select from.
        repair_attempt_history: Historical repair attempts for the budget
            stage.
        validation_evidence: Already-produced typed validation evidence.
        required_validation_criteria: Optional explicit required criteria; when
            omitted the canonical default required set is used. An empty set is
            rejected by the canonical validation contract.
        shadow_results: Canonical shadow repair trial records.
        replacement_request: The canonical replacement/rollback request whose
            eligibility is assessed last.
    """

    target: RepairWorkflowTarget
    assessed_at: datetime
    repair_reference: str
    proposal_fingerprint: RepairProposalFingerprint
    budget_limits: RepairBudgetLimits
    failure_diagnoses: tuple[BoundFailureDiagnosis, ...] = ()
    environment_changes: tuple[BoundEnvironmentChange, ...] = ()
    success_evidence: tuple[ProcedureSuccessEvidence, ...] = ()
    patch_proposals: tuple[RepairPatchProposal, ...] = ()
    repair_attempt_history: tuple[RepairAttemptEvidence, ...] = ()
    validation_evidence: tuple[RepairValidationEvidence, ...] = ()
    required_validation_criteria: tuple[RepairValidationCriterion, ...] | None = None
    shadow_results: tuple[ShadowRepairResult, ...] = ()
    replacement_request: ProcedureReplacementRequest | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, RepairWorkflowTarget):
            raise RepairWorkflowValidationError("target must be a RepairWorkflowTarget")
        object.__setattr__(
            self, "assessed_at", _validate_timestamp(self.assessed_at, field_name="assessed_at")
        )
        object.__setattr__(self, "repair_reference", _validate_reference(self.repair_reference))
        if not isinstance(self.proposal_fingerprint, RepairProposalFingerprint):
            raise RepairWorkflowValidationError(
                "proposal_fingerprint must be a RepairProposalFingerprint"
            )
        if not isinstance(self.budget_limits, RepairBudgetLimits):
            raise RepairWorkflowValidationError("budget_limits must be RepairBudgetLimits")
        object.__setattr__(
            self,
            "failure_diagnoses",
            _validate_sequence(
                self.failure_diagnoses,
                field_name="failure_diagnoses",
                item_type=BoundFailureDiagnosis,
            ),
        )
        object.__setattr__(
            self,
            "environment_changes",
            _validate_sequence(
                self.environment_changes,
                field_name="environment_changes",
                item_type=BoundEnvironmentChange,
            ),
        )
        object.__setattr__(
            self,
            "success_evidence",
            _validate_sequence(
                self.success_evidence,
                field_name="success_evidence",
                item_type=ProcedureSuccessEvidence,
            ),
        )
        object.__setattr__(
            self,
            "patch_proposals",
            _validate_sequence(
                self.patch_proposals,
                field_name="patch_proposals",
                item_type=RepairPatchProposal,
            ),
        )
        object.__setattr__(
            self,
            "repair_attempt_history",
            _validate_sequence(
                self.repair_attempt_history,
                field_name="repair_attempt_history",
                item_type=RepairAttemptEvidence,
            ),
        )
        object.__setattr__(
            self,
            "validation_evidence",
            _validate_sequence(
                self.validation_evidence,
                field_name="validation_evidence",
                item_type=RepairValidationEvidence,
            ),
        )
        if self.required_validation_criteria is not None:
            object.__setattr__(
                self,
                "required_validation_criteria",
                _validate_sequence(
                    self.required_validation_criteria,
                    field_name="required_validation_criteria",
                    item_type=RepairValidationCriterion,
                ),
            )
        object.__setattr__(
            self,
            "shadow_results",
            _validate_sequence(
                self.shadow_results,
                field_name="shadow_results",
                item_type=ShadowRepairResult,
            ),
        )
        if self.replacement_request is not None and not isinstance(
            self.replacement_request, ProcedureReplacementRequest
        ):
            raise RepairWorkflowValidationError(
                "replacement_request must be a ProcedureReplacementRequest or None"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairWorkflowEvidence:
    """Every canonical artifact the traversal consumed, preserved unmodified.

    Artifacts are stored exactly as the canonical contracts produced them: no
    summarization, no re-typing, no text reinterpretation, and no dropping of
    a stage's evidence because a later stage stopped.
    """

    degradation_assessment: ProcedureDegradationAssessment | None = None
    candidates: tuple[RepairCandidate, ...] = ()
    patch_proposal: RepairPatchProposal | None = None
    budget_assessment: RepairBudgetAssessment | None = None
    validation_report: RepairValidationReport | None = None
    shadow_results: tuple[ShadowRepairResult, ...] = ()
    replacement_decision: ProcedureReplacementDecision | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairWorkflowResult:
    """The immutable decision/evidence chain of one traversal.

    The result is DATA. ``outcome is CHAIN_COMPLETE`` states only that every
    composed canonical stage produced its explicitly positive result for this
    exact revision. It is not permission, not activation, not replacement, not
    rollback, and not verification of any task.

    Attributes:
        procedure_id: The exact procedure identity the traversal was bound to.
        revision: The exact revision the traversal was bound to.
        outcome: ``CHAIN_COMPLETE`` or ``STOPPED``.
        completed_stages: Stages that produced their positive canonical result,
            in canonical order.
        stopped_at_stage: The stage that stopped the traversal, or ``None``.
        stop_reason: The closed reason that stage stopped, or ``None``.
        reasons: Deterministic, machine-generated explanation lines built only
            from canonical enum values and identities — never from caller text.
        evidence: Every artifact the traversal consumed.
        assessed_at: The caller-supplied traversal instant (UTC-normalized).
    """

    procedure_id: ProcedureId
    revision: int
    outcome: RepairWorkflowOutcome
    completed_stages: tuple[RepairWorkflowStage, ...]
    stopped_at_stage: RepairWorkflowStage | None
    stop_reason: RepairWorkflowStopReason | None
    reasons: tuple[str, ...]
    evidence: RepairWorkflowEvidence
    assessed_at: datetime
    stage_order: tuple[RepairWorkflowStage, ...] = field(default=CANONICAL_REPAIR_WORKFLOW_STAGES)

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, ProcedureId):
            raise RepairWorkflowValidationError("procedure_id must be a ProcedureId")
        object.__setattr__(
            self, "revision", _validate_revision(self.revision, field_name="revision")
        )
        if not isinstance(self.outcome, RepairWorkflowOutcome):
            raise RepairWorkflowValidationError("outcome must be a RepairWorkflowOutcome")
        if not isinstance(self.evidence, RepairWorkflowEvidence):
            raise RepairWorkflowValidationError("evidence must be a RepairWorkflowEvidence")
        object.__setattr__(
            self, "assessed_at", _validate_timestamp(self.assessed_at, field_name="assessed_at")
        )
        stages = tuple(self.completed_stages)
        expected_prefix = CANONICAL_REPAIR_WORKFLOW_STAGES[: len(stages)]
        if stages != expected_prefix:
            raise RepairWorkflowValidationError(
                "completed_stages must be a prefix of the canonical stage order"
            )
        object.__setattr__(self, "completed_stages", stages)
        object.__setattr__(self, "reasons", tuple(self.reasons))
        if self.outcome is RepairWorkflowOutcome.CHAIN_COMPLETE:
            if self.stopped_at_stage is not None or self.stop_reason is not None:
                raise RepairWorkflowValidationError(
                    "a complete chain must not carry a stop stage or stop reason"
                )
            if stages != CANONICAL_REPAIR_WORKFLOW_STAGES:
                raise RepairWorkflowValidationError(
                    "a complete chain must have completed every canonical stage"
                )
        else:
            if not isinstance(self.stopped_at_stage, RepairWorkflowStage):
                raise RepairWorkflowValidationError(
                    "a stopped chain must name the stage that stopped it"
                )
            if not isinstance(self.stop_reason, RepairWorkflowStopReason):
                raise RepairWorkflowValidationError(
                    "a stopped chain must name one canonical stop reason"
                )
            if self.stopped_at_stage in stages:
                raise RepairWorkflowValidationError(
                    "the stopping stage must not be recorded as completed"
                )
        object.__setattr__(self, "stage_order", CANONICAL_REPAIR_WORKFLOW_STAGES)

    @property
    def is_complete(self) -> bool:
        """Whether every canonical stage produced its positive result.

        True never means authorized, applied, activated, replaced, rolled
        back, or verified. A later explicit transaction owns all mutation.
        """
        return self.outcome is RepairWorkflowOutcome.CHAIN_COMPLETE


def _stop(
    *,
    request: RepairWorkflowRequest,
    completed: tuple[RepairWorkflowStage, ...],
    stage: RepairWorkflowStage,
    reason: RepairWorkflowStopReason,
    reasons: list[str],
    evidence: RepairWorkflowEvidence,
) -> RepairWorkflowResult:
    lines = [*reasons, f"stopped at stage {stage.value}: {reason.value}"]
    return RepairWorkflowResult(
        procedure_id=request.target.procedure_id,
        revision=request.target.revision,
        outcome=RepairWorkflowOutcome.STOPPED,
        completed_stages=completed,
        stopped_at_stage=stage,
        stop_reason=reason,
        reasons=tuple(lines),
        evidence=evidence,
        assessed_at=request.assessed_at,
    )


_DEGRADATION_STOP: Final[dict[DegradationState, RepairWorkflowStopReason]] = {
    DegradationState.HEALTHY: RepairWorkflowStopReason.DEGRADATION_NOT_OBSERVED,
    DegradationState.UNKNOWN: RepairWorkflowStopReason.DEGRADATION_UNKNOWN,
    DegradationState.SUSPECTED_DEGRADED: RepairWorkflowStopReason.DEGRADATION_NOT_CONFIRMED,
}

_VALIDATION_STOP: Final[dict[RepairValidationDisposition, RepairWorkflowStopReason]] = {
    RepairValidationDisposition.FAILED: RepairWorkflowStopReason.VALIDATION_FAILED,
    RepairValidationDisposition.INSUFFICIENT: RepairWorkflowStopReason.VALIDATION_INSUFFICIENT,
}


def run_repair_workflow(request: RepairWorkflowRequest) -> RepairWorkflowResult:
    """Run the bounded canonical repair stage chain and return its evidence.

    The traversal is pure, deterministic, and fail-closed. It composes, in
    exactly :data:`CANONICAL_REPAIR_WORKFLOW_STAGES` order:

        confirmed degradation evidence
        -> repair candidate
        -> repair patch proposal
        -> repair budget / anti-loop decision
        -> repair validation evidence
        -> shadow repair evidence
        -> replacement eligibility result

    Each stage delegates entirely to its canonical contract; no stage logic is
    re-implemented, re-weighted, or second-guessed here. The first stage whose
    canonical result is not explicitly positive stops the traversal, and the
    returned :class:`RepairWorkflowResult` names that stage plus one closed
    :class:`RepairWorkflowStopReason` while preserving every artifact already
    gathered.

    Running this function performs no mutation of any kind: no store write, no
    lifecycle transition, no activation, no rollback, no capability execution,
    no Action Gate interaction, no permission grant, no risk reduction, no
    budget widening, no emergency-stop clearing, and no model call.
    """
    if not isinstance(request, RepairWorkflowRequest):
        raise RepairWorkflowValidationError("request must be a RepairWorkflowRequest")

    target = request.target
    reasons: list[str] = [
        f"target procedure {target.procedure_id.to_str()} revision {target.revision}"
    ]
    completed: tuple[RepairWorkflowStage, ...] = ()

    # ---------------------------------------------------------------- stage 1
    assessment = assess_procedure_degradation(
        assessed_at=request.assessed_at,
        procedure_id=target.procedure_id,
        revision=target.revision,
        failure_diagnoses=request.failure_diagnoses,
        environment_changes=request.environment_changes,
        success_evidence=request.success_evidence,
    )
    evidence = RepairWorkflowEvidence(degradation_assessment=assessment)
    if assessment.state is not DegradationState.CONFIRMED_DEGRADED:
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.DEGRADATION,
            reason=_DEGRADATION_STOP[assessment.state],
            reasons=reasons,
            evidence=evidence,
        )
    completed += (RepairWorkflowStage.DEGRADATION,)
    reasons.append(f"degradation state {assessment.state.value}")

    # ---------------------------------------------------------------- stage 2
    derived: list[RepairCandidate] = []
    for index in assessment.supporting_failure_indices:
        bound = request.failure_diagnoses[index]
        derived.extend(
            derive_repair_candidates(diagnosis=bound.diagnosis, proposed_at=request.assessed_at)
        )
    actionable = tuple(
        candidate
        for candidate in derived
        if candidate.kind is not RepairCandidateKind.UNKNOWN
        and candidate.diagnosis.procedure_id == target.procedure_id
    )
    evidence = RepairWorkflowEvidence(degradation_assessment=assessment, candidates=tuple(derived))
    if not derived:
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.CANDIDATE,
            reason=RepairWorkflowStopReason.CANDIDATE_ABSENT,
            reasons=reasons,
            evidence=evidence,
        )
    if not actionable:
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.CANDIDATE,
            reason=RepairWorkflowStopReason.CANDIDATE_UNKNOWN,
            reasons=reasons,
            evidence=evidence,
        )
    completed += (RepairWorkflowStage.CANDIDATE,)
    reasons.append(f"derived {len(actionable)} actionable repair candidate(s)")

    # ---------------------------------------------------------------- stage 3
    if not request.patch_proposals:
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.PATCH_PROPOSAL,
            reason=RepairWorkflowStopReason.PATCH_PROPOSAL_ABSENT,
            reasons=reasons,
            evidence=evidence,
        )
    target_bound = tuple(
        proposal
        for proposal in request.patch_proposals
        if proposal.target_procedure_id == target.procedure_id
        and proposal.target_revision == target.revision
    )
    if not target_bound:
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.PATCH_PROPOSAL,
            reason=RepairWorkflowStopReason.PATCH_PROPOSAL_TARGET_MISMATCH,
            reasons=reasons,
            evidence=evidence,
        )
    # Provenance match is by the exact canonical diagnosis the candidate
    # embeds plus an actionable candidate kind. Candidate record timestamps
    # are bookkeeping and are deliberately not part of this binding.
    supporting_diagnoses = tuple(candidate.diagnosis for candidate in actionable)
    qualified = tuple(
        proposal
        for proposal in target_bound
        if proposal.candidate.kind is not RepairCandidateKind.UNKNOWN
        and proposal.candidate.diagnosis in supporting_diagnoses
    )
    if not qualified:
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.PATCH_PROPOSAL,
            reason=RepairWorkflowStopReason.PATCH_PROPOSAL_NOT_DERIVED_FROM_CANDIDATE,
            reasons=reasons,
            evidence=evidence,
        )
    distinct = {proposal.to_json() for proposal in qualified}
    if len(distinct) > 1:
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.PATCH_PROPOSAL,
            reason=RepairWorkflowStopReason.PATCH_PROPOSAL_AMBIGUOUS,
            reasons=reasons,
            evidence=evidence,
        )
    proposal = qualified[0]
    evidence = RepairWorkflowEvidence(
        degradation_assessment=assessment,
        candidates=tuple(derived),
        patch_proposal=proposal,
    )
    completed += (RepairWorkflowStage.PATCH_PROPOSAL,)
    reasons.append(f"selected patch proposal of kind {proposal.kind.value}")

    # ---------------------------------------------------------------- stage 4
    budget_assessment = assess_repair_attempt(
        target=RepairTarget(
            procedure=ProcedureRepairTarget(
                procedure_id=target.procedure_id, revision=target.revision
            )
        ),
        proposal=request.proposal_fingerprint,
        history=request.repair_attempt_history,
        limits=request.budget_limits,
    )
    evidence = RepairWorkflowEvidence(
        degradation_assessment=assessment,
        candidates=tuple(derived),
        patch_proposal=proposal,
        budget_assessment=budget_assessment,
    )
    if budget_assessment.decision is not RepairBudgetDecision.ALLOW_CONSIDERATION:
        budget_reason = (
            RepairWorkflowStopReason.BUDGET_HISTORY_INVALID
            if budget_assessment.decision is RepairBudgetDecision.INVALID_HISTORY
            else RepairWorkflowStopReason.BUDGET_EXHAUSTED
        )
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.BUDGET,
            reason=budget_reason,
            reasons=reasons,
            evidence=evidence,
        )
    completed += (RepairWorkflowStage.BUDGET,)
    reasons.append(f"budget decision {budget_assessment.decision.value}")

    # ---------------------------------------------------------------- stage 5
    validation_report = evaluate_repair_validation(
        repair_reference=request.repair_reference,
        target=RepairValidationTarget(
            procedure_id=target.procedure_id,
            procedure_revision=target.revision,
            procedure_node_id=proposal.target_node_id,
        ),
        evidence=request.validation_evidence,
        evaluated_at=request.assessed_at,
        required_criteria=request.required_validation_criteria,
    )
    evidence = RepairWorkflowEvidence(
        degradation_assessment=assessment,
        candidates=tuple(derived),
        patch_proposal=proposal,
        budget_assessment=budget_assessment,
        validation_report=validation_report,
    )
    if validation_report.disposition is not RepairValidationDisposition.VALIDATED:
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.VALIDATION,
            reason=_VALIDATION_STOP[validation_report.disposition],
            reasons=reasons,
            evidence=evidence,
        )
    completed += (RepairWorkflowStage.VALIDATION,)
    reasons.append(f"validation disposition {validation_report.disposition.value}")

    # ---------------------------------------------------------------- stage 6
    bound_shadow = tuple(
        result
        for result in request.shadow_results
        if result.procedure_id == target.procedure_id
        and result.source_revision == target.revision
        and result.target_node_id == proposal.target_node_id
    )
    evidence = RepairWorkflowEvidence(
        degradation_assessment=assessment,
        candidates=tuple(derived),
        patch_proposal=proposal,
        budget_assessment=budget_assessment,
        validation_report=validation_report,
        shadow_results=bound_shadow,
    )
    if not bound_shadow:
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.SHADOW,
            reason=RepairWorkflowStopReason.SHADOW_EVIDENCE_ABSENT,
            reasons=reasons,
            evidence=evidence,
        )
    if any(result.disposition is not ShadowRepairDisposition.PASSED for result in bound_shadow):
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.SHADOW,
            reason=RepairWorkflowStopReason.SHADOW_NOT_PASSED,
            reasons=reasons,
            evidence=evidence,
        )
    completed += (RepairWorkflowStage.SHADOW,)
    reasons.append(f"{len(bound_shadow)} bound shadow trial(s) passed")

    # ---------------------------------------------------------------- stage 7
    replacement_request = request.replacement_request
    if replacement_request is None:
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.REPLACEMENT,
            reason=RepairWorkflowStopReason.REPLACEMENT_REQUEST_ABSENT,
            reasons=reasons,
            evidence=evidence,
        )
    active = replacement_request.active_revision
    if active.procedure_id != target.procedure_id or active.revision != target.revision:
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.REPLACEMENT,
            reason=RepairWorkflowStopReason.REPLACEMENT_TARGET_MISMATCH,
            reasons=reasons,
            evidence=evidence,
        )
    replacement_decision = assess_procedure_replacement(replacement_request)
    evidence = RepairWorkflowEvidence(
        degradation_assessment=assessment,
        candidates=tuple(derived),
        patch_proposal=proposal,
        budget_assessment=budget_assessment,
        validation_report=validation_report,
        shadow_results=bound_shadow,
        replacement_decision=replacement_decision,
    )
    if replacement_decision.outcome is not ProcedureReplacementOutcome.ELIGIBLE:
        return _stop(
            request=request,
            completed=completed,
            stage=RepairWorkflowStage.REPLACEMENT,
            reason=RepairWorkflowStopReason.REPLACEMENT_NOT_ELIGIBLE,
            reasons=reasons,
            evidence=evidence,
        )
    completed += (RepairWorkflowStage.REPLACEMENT,)
    reasons.append(f"replacement outcome {replacement_decision.outcome.value}")
    reasons.append(
        "chain complete: evidence only; no store, lifecycle, authority, budget, "
        "risk, or emergency-stop state was changed"
    )

    return RepairWorkflowResult(
        procedure_id=target.procedure_id,
        revision=target.revision,
        outcome=RepairWorkflowOutcome.CHAIN_COMPLETE,
        completed_stages=completed,
        stopped_at_stage=None,
        stop_reason=None,
        reasons=tuple(reasons),
        evidence=evidence,
        assessed_at=request.assessed_at,
    )
