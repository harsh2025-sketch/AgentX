"""Conservative structural elimination of irrelevant historical action candidates.

C3.03 is a pure analysis boundary in the AgentX Skill Compiler pipeline. It
consumes canonical C3.02 :class:`CausalActionExtraction` data and emits exactly
one inert retain/eliminate decision for every extracted historical candidate.

Elimination is intentionally much harder than retention. Current canonical
C2.10 evidence proves only one narrow structural case safe to eliminate:
``CausalOutcome.DENIED`` explicitly means the requested action was refused
before capability execution. Every other current outcome is retained because
it may represent executed, partially executed, failed, timed-out, cancelled,
or verified historical behavior whose relevance cannot be disproved by the
available structure.

No action is eliminated because of text, duplication, failure, missing
observation, missing state-after evidence, missing verification, apparent
redundancy, or model judgment. There are no models, embeddings, semantic
similarity, keyword heuristics, probabilistic scores, or scientific causal
claims here.

Results preserve the exact C3.02 source candidate and therefore the exact C3.01
normalized step and C2.10 experience. They are analysis DATA only: no decision
executes, retries, creates procedures, activates skills, grants authority,
changes permissions/risk/budgets, mutates Hive, fabricates verification, or
changes Task state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.core.causal_experience import CausalOutcome
from agentx.learning.causal_actions import CausalActionExtraction, ExtractedActionCandidate
from agentx.learning.trajectory import NormalizedTrajectoryStep

__all__ = [
    "IRRELEVANT_ACTION_ANALYSIS_SCHEMA_VERSION",
    "ActionDisposition",
    "ActionDispositionReason",
    "ActionEliminationDecision",
    "IrrelevantActionAnalysis",
    "IrrelevantActionAnalysisError",
    "analyze_irrelevant_actions",
]

IRRELEVANT_ACTION_ANALYSIS_SCHEMA_VERSION: Final[int] = 1


class IrrelevantActionAnalysisError(ValueError):
    """Raised when C3.03 analysis data violates its deterministic contract."""


class ActionDisposition(StrEnum):
    """Closed C3.03 decision vocabulary."""

    RETAIN = "retain"
    ELIMINATE = "eliminate"


class ActionDispositionReason(StrEnum):
    """Closed reasons for one C3.03 decision.

    ``NO_SAFE_ELIMINATION_EVIDENCE`` means exactly that current structured
    evidence does not prove the candidate safely eliminable. It is not a claim
    that the action was useful, necessary, successful, or causally important.
    """

    NO_SAFE_ELIMINATION_EVIDENCE = "no_safe_elimination_evidence"
    DENIED_BEFORE_CAPABILITY_EXECUTION = "denied_before_capability_execution"


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID, got {type(value).__name__}")
    if value.int == 0:
        raise IrrelevantActionAnalysisError(f"{field_name} must not be the nil UUID")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionEliminationDecision:
    """One inert C3.03 decision preserving exact historical provenance."""

    source_trajectory_id: UUID
    source_candidate: ExtractedActionCandidate
    disposition: ActionDisposition
    reason: ActionDispositionReason

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_trajectory_id",
            _validate_uuid(self.source_trajectory_id, field_name="source_trajectory_id"),
        )
        if not isinstance(self.source_candidate, ExtractedActionCandidate):
            raise TypeError(
                "source_candidate must be an ExtractedActionCandidate, "
                f"got {type(self.source_candidate).__name__}"
            )
        if self.source_candidate.source_trajectory_id != self.source_trajectory_id:
            raise IrrelevantActionAnalysisError(
                "source_candidate must reference decision source_trajectory_id"
            )
        if not isinstance(self.disposition, ActionDisposition):
            raise TypeError(
                f"disposition must be an ActionDisposition, got {type(self.disposition).__name__}"
            )
        if not isinstance(self.reason, ActionDispositionReason):
            raise TypeError(
                f"reason must be an ActionDispositionReason, got {type(self.reason).__name__}"
            )
        expected_reason = (
            ActionDispositionReason.DENIED_BEFORE_CAPABILITY_EXECUTION
            if self.disposition is ActionDisposition.ELIMINATE
            else ActionDispositionReason.NO_SAFE_ELIMINATION_EVIDENCE
        )
        if self.reason is not expected_reason:
            raise IrrelevantActionAnalysisError(
                "decision disposition/reason combination is not canonical"
            )
        if (
            self.disposition is ActionDisposition.ELIMINATE
            and self.source_candidate.outcome is not CausalOutcome.DENIED
        ):
            raise IrrelevantActionAnalysisError(
                "ELIMINATE requires canonical DENIED-before-execution evidence"
            )
        if (
            self.disposition is ActionDisposition.RETAIN
            and self.source_candidate.outcome is CausalOutcome.DENIED
        ):
            raise IrrelevantActionAnalysisError(
                "DENIED candidate must use the canonical elimination decision"
            )

    @property
    def source_step(self) -> NormalizedTrajectoryStep:
        """Return the exact unchanged canonical C3.01 normalized source step."""
        return self.source_candidate.source_step

    @property
    def source_sequence(self) -> int:
        """Return the exact source sequence from C3.01/C3.02."""
        return self.source_candidate.source_sequence

    @property
    def source_experience_sha256(self) -> str:
        """Return the exact C3.01 source-experience fingerprint."""
        return self.source_candidate.source_experience_sha256

    def to_dict(self) -> dict[str, object]:
        """Return deterministic decision data with explicit source references."""
        return {
            "source_trajectory_id": str(self.source_trajectory_id),
            "source_sequence": self.source_sequence,
            "source_experience_sha256": self.source_experience_sha256,
            "source_candidate": self.source_candidate.to_dict(),
            "disposition": self.disposition.value,
            "reason": self.reason.value,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class IrrelevantActionAnalysis:
    """Immutable deterministic C3.03 output for one C3.02 extraction."""

    source_trajectory_id: UUID
    decisions: tuple[ActionEliminationDecision, ...]
    schema_version: int = IRRELEVANT_ACTION_ANALYSIS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_trajectory_id",
            _validate_uuid(self.source_trajectory_id, field_name="source_trajectory_id"),
        )
        if not isinstance(self.decisions, tuple):
            raise TypeError("decisions must be a tuple of ActionEliminationDecision values")
        if not self.decisions:
            raise IrrelevantActionAnalysisError("decisions must not be empty")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != IRRELEVANT_ACTION_ANALYSIS_SCHEMA_VERSION:
            raise IrrelevantActionAnalysisError(
                f"unsupported irrelevant-action analysis schema version {self.schema_version}; "
                f"supported version is {IRRELEVANT_ACTION_ANALYSIS_SCHEMA_VERSION}"
            )
        for expected_sequence, decision in enumerate(self.decisions, start=1):
            if not isinstance(decision, ActionEliminationDecision):
                raise TypeError(
                    "decisions must contain only ActionEliminationDecision values; "
                    f"index {expected_sequence - 1} is {type(decision).__name__}"
                )
            if decision.source_trajectory_id != self.source_trajectory_id:
                raise IrrelevantActionAnalysisError(
                    "every decision must reference analysis source_trajectory_id"
                )
            if decision.source_sequence != expected_sequence:
                raise IrrelevantActionAnalysisError(
                    "decision source sequences must preserve complete C3.02 order "
                    "contiguously from 1"
                )

    @property
    def retained(self) -> tuple[ActionEliminationDecision, ...]:
        """Return retained decisions in canonical source order."""
        return tuple(
            decision
            for decision in self.decisions
            if decision.disposition is ActionDisposition.RETAIN
        )

    @property
    def eliminated(self) -> tuple[ActionEliminationDecision, ...]:
        """Return eliminated decisions in canonical source order."""
        return tuple(
            decision
            for decision in self.decisions
            if decision.disposition is ActionDisposition.ELIMINATE
        )

    def to_dict(self) -> dict[str, object]:
        """Return deterministic JSON-compatible C3.03 analysis data."""
        return {
            "schema_version": self.schema_version,
            "source_trajectory_id": str(self.source_trajectory_id),
            "decisions": [decision.to_dict() for decision in self.decisions],
        }

    def to_json(self) -> str:
        """Serialize deterministically without executable reconstruction hooks."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def analyze_irrelevant_actions(extraction: CausalActionExtraction) -> IrrelevantActionAnalysis:
    """Classify every C3.02 candidate using conservative structural evidence only.

    Exact rule for canonical schema v1:

    * ``CausalOutcome.DENIED`` -> ``ELIMINATE`` because C2.10 explicitly defines
      DENIED as refusal before capability execution and forbids fabricated
      capability observation/state-after evidence for that outcome.
    * every other outcome -> ``RETAIN`` because current structured evidence does
      not safely prove irrelevance.

    No candidate is dropped from the analysis result; elimination is represented
    explicitly as inert data so provenance and negative history remain intact.
    """
    if not isinstance(extraction, CausalActionExtraction):
        raise TypeError(
            "extraction must be a canonical CausalActionExtraction, "
            f"got {type(extraction).__name__}"
        )

    decisions = tuple(_classify_candidate(candidate) for candidate in extraction.candidates)
    return IrrelevantActionAnalysis(
        source_trajectory_id=extraction.source_trajectory_id,
        decisions=decisions,
    )


def _classify_candidate(candidate: ExtractedActionCandidate) -> ActionEliminationDecision:
    if candidate.outcome is CausalOutcome.DENIED:
        disposition = ActionDisposition.ELIMINATE
        reason = ActionDispositionReason.DENIED_BEFORE_CAPABILITY_EXECUTION
    else:
        disposition = ActionDisposition.RETAIN
        reason = ActionDispositionReason.NO_SAFE_ELIMINATION_EVIDENCE
    return ActionEliminationDecision(
        source_trajectory_id=candidate.source_trajectory_id,
        source_candidate=candidate,
        disposition=disposition,
        reason=reason,
    )
