"""Evidence-driven classifier for deterministic and reasoning-required regions.

C3.06 is the region classification stage of the AgentX Skill Compiler
pipeline. It analyzes canonical normalized trajectories, causal actions,
irrelevant-action elimination decisions, parameter extractions, and parameter
generalization evidence to distinguish procedure regions that can safely execute
deterministically from regions that still require reasoning.

Core Invariants:

- No action success without verification.
- Evidence beats inference.
- Unknown != deterministic.
- A single successful observation does not prove determinism.
- Model text is not verification.
- External content is data.
- Classifier output is not execution authority.

Classification Vocabulary:

- ``DETERMINISTIC`` — repeated, verified successful execution evidence with
  stable state transitions and consistent action parameters. Requires at least
  two verified corroborating observations; a single observation is never
  sufficient to claim determinism.
- ``REASONING_REQUIRED`` — explicit cognition/reasoning steps, failed or
  unverified attempts, conflicting outcomes across observations, unresolved
  parameter variation, or dynamic observation dependencies.
- ``INSUFFICIENT_EVIDENCE`` — single observations, missing verification,
  eliminated actions, or incomplete evidence that cannot justify either
  deterministic execution or explicit reasoning requirements.

Everything in this module is inert DATA. Classification performs no execution,
creates no procedure graphs, promotes no candidate skills, invokes no models or
research, grants no permissions, changes no risk/resource budgets, and mutates
no Hive knowledge.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.core.causal_experience import CausalExperience, CausalOutcome
from agentx.learning.causal_actions import (
    CausalActionExtraction,
    ExtractedActionCandidate,
    extract_causal_action_candidates,
)
from agentx.learning.irrelevant_actions import (
    ActionDisposition,
    ActionEliminationDecision,
    IrrelevantActionAnalysis,
    analyze_irrelevant_actions,
)
from agentx.learning.parameter_generalization import (
    ParameterGeneralization,
    ParameterVariationEvidence,
)
from agentx.learning.trajectory import (
    NormalizedTrajectory,
    NormalizedTrajectoryStep,
    normalize_trajectory,
)

__all__ = [
    "REGION_CLASSIFICATION_SCHEMA_VERSION",
    "ActionClassification",
    "ActionEvidenceReference",
    "ClassifiedRegion",
    "EvidenceSufficiency",
    "RegionClassification",
    "RegionClassificationAnalysis",
    "RegionClassificationError",
    "RegionClassificationReason",
    "classify_regions",
    "classify_trajectory_regions",
]

REGION_CLASSIFICATION_SCHEMA_VERSION: Final[int] = 1
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_EXPLICIT_REASONING_PREFIXES: Final[tuple[str, ...]] = (
    "cognition.",
    "reasoning.",
    "research.",
    "model.",
    "reason.",
    "research.",
    "plan.",
    "planner.",
)
_EXPLICIT_REASONING_NAMES: Final[frozenset[str]] = frozenset(
    {"reason", "research", "plan", "reflect", "decide", "analyze"}
)


class RegionClassificationError(ValueError):
    """Raised when region classification data violates its deterministic contract."""


class RegionClassification(StrEnum):
    """Closed explicit vocabulary for region and action determinism classification."""

    DETERMINISTIC = "deterministic"
    REASONING_REQUIRED = "reasoning_required"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class EvidenceSufficiency(StrEnum):
    """Evaluation of evidence completeness supporting a classification decision."""

    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class RegionClassificationReason(StrEnum):
    """Closed reason codes explaining the evidence-backed classification."""

    # Deterministic reasons (requires repeated verified success across multiple observations):
    REPEATED_VERIFIED_SUCCESS = "repeated_verified_success"

    # Reasoning-required reasons:
    EXPLICIT_REASONING_STEP = "explicit_reasoning_step"
    CONFLICTING_OUTCOMES = "conflicting_outcomes"
    DYNAMIC_OBSERVATION_DEPENDENCY = "dynamic_observation_dependency"
    UNVERIFIED_OR_FAILED_ATTEMPT = "unverified_or_failed_attempt"
    UNRESOLVED_VARIATION = "unresolved_variation"

    # Insufficient / unknown evidence reasons:
    SINGLE_OBSERVATION = "single_observation"
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
    MISSING_VERIFICATION = "missing_verification"
    MISSING_STATE_TRANSITION = "missing_state_transition"
    ACTION_ELIMINATED = "action_eliminated"
    NO_EVIDENCE = "no_evidence"


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID, got {type(value).__name__}")
    if value.int == 0:
        raise RegionClassificationError(f"{field_name} must not be the nil UUID")
    return value


def _canonical_value_tree(value: object) -> object:
    """Return a typed canonical tree preventing type coercion in comparisons."""
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        try:
            encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
        except ValueError as exc:
            raise RegionClassificationError("non-finite float is not canonical JSON data") from exc
        return ["float", encoded]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, list):
        return ["array", [_canonical_value_tree(item) for item in value]]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise RegionClassificationError("canonical JSON objects require string keys")
        return [
            "object",
            [[key, _canonical_value_tree(value[key])] for key in sorted(value)],
        ]
    raise RegionClassificationError(f"unsupported canonical value type {type(value).__name__}")


def _canonical_data_key(data: Mapping[str, object] | None) -> str:
    """Return a deterministic typed canonical representation of action payload data."""
    if data is None:
        return '["null"]'
    if not isinstance(data, Mapping):
        raise RegionClassificationError("action data must be a mapping")
    return json.dumps(
        _canonical_value_tree(dict(data)),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=False,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionEvidenceReference:
    """One immutable, provenance-bearing historical action observation reference."""

    trajectory_id: UUID
    sequence: int
    experience_sha256: str
    action_name: str
    outcome: CausalOutcome
    verification_passed: bool
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "trajectory_id",
            _validate_uuid(self.trajectory_id, field_name="trajectory_id"),
        )
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool):
            raise TypeError("sequence must be an integer")
        if self.sequence < 1:
            raise RegionClassificationError("sequence must be >= 1")
        if not isinstance(self.experience_sha256, str):
            raise TypeError("experience_sha256 must be a string")
        if not _SHA256_PATTERN.fullmatch(self.experience_sha256):
            raise RegionClassificationError(
                "experience_sha256 must be 64 lowercase hexadecimal characters"
            )
        if not isinstance(self.action_name, str) or not self.action_name:
            raise TypeError("action_name must be a non-empty string")
        if not isinstance(self.outcome, CausalOutcome):
            raise TypeError("outcome must be a CausalOutcome")
        if not isinstance(self.verification_passed, bool):
            raise TypeError("verification_passed must be a bool")
        if self.observed_at is not None and not isinstance(self.observed_at, datetime):
            raise TypeError("observed_at must be a datetime or None")

    @classmethod
    def from_step(
        cls, *, trajectory_id: UUID, step: NormalizedTrajectoryStep
    ) -> ActionEvidenceReference:
        if not isinstance(step, NormalizedTrajectoryStep):
            raise TypeError(f"step must be a NormalizedTrajectoryStep, got {type(step).__name__}")
        exp = step.experience
        verification_passed = exp.verification.passed if exp.verification is not None else False
        return cls(
            trajectory_id=trajectory_id,
            sequence=step.sequence,
            experience_sha256=step.source_experience_sha256,
            action_name=exp.action.name,
            outcome=exp.outcome,
            verification_passed=verification_passed,
            observed_at=exp.action_at,
        )

    @classmethod
    def from_candidate(cls, candidate: ExtractedActionCandidate) -> ActionEvidenceReference:
        if not isinstance(candidate, ExtractedActionCandidate):
            raise TypeError(
                f"candidate must be an ExtractedActionCandidate, got {type(candidate).__name__}"
            )
        return cls.from_step(
            trajectory_id=candidate.source_trajectory_id,
            step=candidate.source_step,
        )

    @classmethod
    def from_decision(cls, decision: ActionEliminationDecision) -> ActionEvidenceReference:
        if not isinstance(decision, ActionEliminationDecision):
            raise TypeError(
                f"decision must be an ActionEliminationDecision, got {type(decision).__name__}"
            )
        return cls.from_candidate(decision.source_candidate)

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible dictionary."""
        return {
            "trajectory_id": str(self.trajectory_id),
            "sequence": self.sequence,
            "experience_sha256": self.experience_sha256,
            "action_name": self.action_name,
            "outcome": self.outcome.value,
            "verification_passed": self.verification_passed,
            "observed_at": (None if self.observed_at is None else self.observed_at.isoformat()),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionClassification:
    """Immutable deterministic classification for one historical action step."""

    source_trajectory_id: UUID
    source_sequence: int
    source_experience_sha256: str
    action_name: str
    classification: RegionClassification
    reason: RegionClassificationReason
    evidence_sufficiency: EvidenceSufficiency
    observation_count: int
    evidence_references: tuple[ActionEvidenceReference, ...]
    source_decision: ActionEliminationDecision | None = None
    source_candidate: ExtractedActionCandidate | None = None
    source_step: NormalizedTrajectoryStep | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_trajectory_id",
            _validate_uuid(self.source_trajectory_id, field_name="source_trajectory_id"),
        )
        if not isinstance(self.source_sequence, int) or isinstance(self.source_sequence, bool):
            raise TypeError("source_sequence must be an integer")
        if self.source_sequence < 1:
            raise RegionClassificationError("source_sequence must be >= 1")
        if not isinstance(self.source_experience_sha256, str):
            raise TypeError("source_experience_sha256 must be a string")
        if not _SHA256_PATTERN.fullmatch(self.source_experience_sha256):
            raise RegionClassificationError(
                "source_experience_sha256 must be 64 lowercase hexadecimal characters"
            )
        if not isinstance(self.action_name, str) or not self.action_name:
            raise TypeError("action_name must be a non-empty string")
        if not isinstance(self.classification, RegionClassification):
            raise TypeError("classification must be a RegionClassification")
        if not isinstance(self.reason, RegionClassificationReason):
            raise TypeError("reason must be a RegionClassificationReason")
        if not isinstance(self.evidence_sufficiency, EvidenceSufficiency):
            raise TypeError("evidence_sufficiency must be an EvidenceSufficiency")
        if not isinstance(self.observation_count, int) or isinstance(self.observation_count, bool):
            raise TypeError("observation_count must be an integer")
        if self.observation_count < 1:
            raise RegionClassificationError("observation_count must be >= 1")
        if not isinstance(self.evidence_references, tuple):
            raise TypeError("evidence_references must be a tuple of ActionEvidenceReference values")
        if not self.evidence_references:
            raise RegionClassificationError("evidence_references must not be empty")
        for index, ref in enumerate(self.evidence_references):
            if not isinstance(ref, ActionEvidenceReference):
                raise TypeError(
                    "evidence_references must contain only ActionEvidenceReference values; "
                    f"index {index} is {type(ref).__name__}"
                )

        # Invariant: DETERMINISTIC requires at least 2 verified observations with
        # sufficient evidence.
        if self.classification is RegionClassification.DETERMINISTIC:
            if self.observation_count < 2:
                raise RegionClassificationError(
                    "DETERMINISTIC classification requires at least 2 corroborating observations"
                )
            if self.evidence_sufficiency is not EvidenceSufficiency.SUFFICIENT:
                raise RegionClassificationError(
                    "DETERMINISTIC classification requires SUFFICIENT evidence"
                )
            if self.reason is not RegionClassificationReason.REPEATED_VERIFIED_SUCCESS:
                raise RegionClassificationError(
                    "DETERMINISTIC classification requires REPEATED_VERIFIED_SUCCESS reason"
                )
            for ref in self.evidence_references:
                if ref.outcome is not CausalOutcome.VERIFIED or not ref.verification_passed:
                    raise RegionClassificationError(
                        "DETERMINISTIC classification requires all evidence references "
                        "to be verified"
                    )

        # Invariant: SINGLE_OBSERVATION reason implies exactly 1 observation
        # and INSUFFICIENT_EVIDENCE.
        if self.reason is RegionClassificationReason.SINGLE_OBSERVATION:
            if self.observation_count != 1:
                raise RegionClassificationError(
                    "SINGLE_OBSERVATION reason requires observation_count == 1"
                )
            if self.classification is not RegionClassification.INSUFFICIENT_EVIDENCE:
                raise RegionClassificationError(
                    "SINGLE_OBSERVATION reason requires INSUFFICIENT_EVIDENCE classification"
                )

        # Validate source provenance chain when provided
        if self.source_decision is not None:
            if not isinstance(self.source_decision, ActionEliminationDecision):
                raise TypeError(
                    "source_decision must be an ActionEliminationDecision or None, "
                    f"got {type(self.source_decision).__name__}"
                )
            if self.source_decision.source_trajectory_id != self.source_trajectory_id:
                raise RegionClassificationError(
                    "source_decision trajectory ID does not match action source_trajectory_id"
                )
            if self.source_decision.source_sequence != self.source_sequence:
                raise RegionClassificationError(
                    "source_decision sequence does not match action source_sequence"
                )

        if self.source_candidate is not None:
            if not isinstance(self.source_candidate, ExtractedActionCandidate):
                raise TypeError(
                    "source_candidate must be an ExtractedActionCandidate or None, "
                    f"got {type(self.source_candidate).__name__}"
                )
            if self.source_candidate.source_trajectory_id != self.source_trajectory_id:
                raise RegionClassificationError(
                    "source_candidate trajectory ID does not match action source_trajectory_id"
                )
            if self.source_candidate.source_sequence != self.source_sequence:
                raise RegionClassificationError(
                    "source_candidate sequence does not match action source_sequence"
                )

        if self.source_step is not None:
            if not isinstance(self.source_step, NormalizedTrajectoryStep):
                raise TypeError(
                    "source_step must be a NormalizedTrajectoryStep or None, "
                    f"got {type(self.source_step).__name__}"
                )
            if self.source_step.sequence != self.source_sequence:
                raise RegionClassificationError(
                    "source_step sequence does not match action source_sequence"
                )
            if self.source_step.source_experience_sha256 != self.source_experience_sha256:
                raise RegionClassificationError(
                    "source_step SHA-256 does not match action source_experience_sha256"
                )

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible dictionary."""
        return {
            "source_trajectory_id": str(self.source_trajectory_id),
            "source_sequence": self.source_sequence,
            "source_experience_sha256": self.source_experience_sha256,
            "action_name": self.action_name,
            "classification": self.classification.value,
            "reason": self.reason.value,
            "evidence_sufficiency": self.evidence_sufficiency.value,
            "observation_count": self.observation_count,
            "evidence_references": [ref.to_dict() for ref in self.evidence_references],
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ClassifiedRegion:
    """Contiguous slice of classified action steps sharing one classification."""

    region_id: str
    start_sequence: int
    end_sequence: int
    classification: RegionClassification
    reason: RegionClassificationReason
    evidence_sufficiency: EvidenceSufficiency
    actions: tuple[ActionClassification, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.region_id, str) or not self.region_id:
            raise TypeError("region_id must be a non-empty string")
        if not isinstance(self.start_sequence, int) or isinstance(self.start_sequence, bool):
            raise TypeError("start_sequence must be an integer")
        if self.start_sequence < 1:
            raise RegionClassificationError("start_sequence must be >= 1")
        if not isinstance(self.end_sequence, int) or isinstance(self.end_sequence, bool):
            raise TypeError("end_sequence must be an integer")
        if self.end_sequence < self.start_sequence:
            raise RegionClassificationError("end_sequence must be >= start_sequence")
        if not isinstance(self.classification, RegionClassification):
            raise TypeError("classification must be a RegionClassification")
        if not isinstance(self.reason, RegionClassificationReason):
            raise TypeError("reason must be a RegionClassificationReason")
        if not isinstance(self.evidence_sufficiency, EvidenceSufficiency):
            raise TypeError("evidence_sufficiency must be an EvidenceSufficiency")
        if not isinstance(self.actions, tuple):
            raise TypeError("actions must be a tuple of ActionClassification values")
        if not self.actions:
            raise RegionClassificationError("actions must not be empty")

        if self.actions[0].source_sequence != self.start_sequence:
            raise RegionClassificationError(
                "first action sequence does not match region start_sequence"
            )
        if self.actions[-1].source_sequence != self.end_sequence:
            raise RegionClassificationError(
                "last action sequence does not match region end_sequence"
            )

        for index, action in enumerate(self.actions):
            if not isinstance(action, ActionClassification):
                raise TypeError(
                    "actions must contain only ActionClassification values; "
                    f"index {index} is {type(action).__name__}"
                )
            expected_seq = self.start_sequence + index
            if action.source_sequence != expected_seq:
                raise RegionClassificationError("actions in region must be contiguous")
            if action.classification is not self.classification:
                raise RegionClassificationError(
                    f"all actions in region must share classification {self.classification.value}"
                )

    @property
    def action_count(self) -> int:
        """Return the number of action steps in this region."""
        return len(self.actions)

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible dictionary."""
        return {
            "region_id": self.region_id,
            "start_sequence": self.start_sequence,
            "end_sequence": self.end_sequence,
            "classification": self.classification.value,
            "reason": self.reason.value,
            "evidence_sufficiency": self.evidence_sufficiency.value,
            "action_count": self.action_count,
            "actions": [action.to_dict() for action in self.actions],
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class RegionClassificationAnalysis:
    """Immutable deterministic C3.06 analysis output for one target trajectory."""

    source_trajectory_id: UUID
    actions: tuple[ActionClassification, ...]
    regions: tuple[ClassifiedRegion, ...]
    schema_version: int = REGION_CLASSIFICATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_trajectory_id",
            _validate_uuid(self.source_trajectory_id, field_name="source_trajectory_id"),
        )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != REGION_CLASSIFICATION_SCHEMA_VERSION:
            raise RegionClassificationError(
                f"unsupported region classification schema version {self.schema_version}; "
                f"supported version is {REGION_CLASSIFICATION_SCHEMA_VERSION}"
            )
        if not isinstance(self.actions, tuple):
            raise TypeError("actions must be a tuple of ActionClassification values")
        if not self.actions:
            raise RegionClassificationError("actions must not be empty")
        if not isinstance(self.regions, tuple):
            raise TypeError("regions must be a tuple of ClassifiedRegion values")
        if not self.regions:
            raise RegionClassificationError("regions must not be empty")

        for expected_seq, action in enumerate(self.actions, start=1):
            if not isinstance(action, ActionClassification):
                raise TypeError(
                    "actions must contain only ActionClassification values; "
                    f"index {expected_seq - 1} is {type(action).__name__}"
                )
            if action.source_trajectory_id != self.source_trajectory_id:
                raise RegionClassificationError(
                    "every action must reference analysis source_trajectory_id"
                )
            if action.source_sequence != expected_seq:
                raise RegionClassificationError("action sequences must be contiguous from 1")

        # Validate regions form a contiguous, non-overlapping partition
        expected_start = 1
        for reg_index, region in enumerate(self.regions):
            if not isinstance(region, ClassifiedRegion):
                raise TypeError(
                    "regions must contain only ClassifiedRegion values; "
                    f"index {reg_index} is {type(region).__name__}"
                )
            if region.start_sequence != expected_start:
                raise RegionClassificationError(
                    f"region {region.region_id} start_sequence {region.start_sequence} "
                    f"does not match expected {expected_start}"
                )
            expected_start = region.end_sequence + 1

        if expected_start - 1 != len(self.actions):
            raise RegionClassificationError("regions do not cover all actions in the analysis")

        # Adjacent regions should not have the same classification (they should be merged)
        for i in range(len(self.regions) - 1):
            if self.regions[i].classification == self.regions[i + 1].classification:
                raise RegionClassificationError(
                    "adjacent regions must not share the same classification; "
                    "they should be merged into maximal contiguous regions"
                )

    @property
    def deterministic_actions(self) -> tuple[ActionClassification, ...]:
        """Return all deterministic actions in sequence order."""
        return tuple(
            a for a in self.actions if a.classification is RegionClassification.DETERMINISTIC
        )

    @property
    def reasoning_actions(self) -> tuple[ActionClassification, ...]:
        """Return all reasoning-required actions in sequence order."""
        return tuple(
            a for a in self.actions if a.classification is RegionClassification.REASONING_REQUIRED
        )

    @property
    def insufficient_evidence_actions(self) -> tuple[ActionClassification, ...]:
        """Return all actions with insufficient evidence in sequence order."""
        return tuple(
            a
            for a in self.actions
            if a.classification is RegionClassification.INSUFFICIENT_EVIDENCE
        )

    @property
    def deterministic_regions(self) -> tuple[ClassifiedRegion, ...]:
        """Return all deterministic regions in sequence order."""
        return tuple(
            r for r in self.regions if r.classification is RegionClassification.DETERMINISTIC
        )

    @property
    def reasoning_regions(self) -> tuple[ClassifiedRegion, ...]:
        """Return all reasoning-required regions in sequence order."""
        return tuple(
            r for r in self.regions if r.classification is RegionClassification.REASONING_REQUIRED
        )

    @property
    def insufficient_evidence_regions(self) -> tuple[ClassifiedRegion, ...]:
        """Return all insufficient-evidence regions in sequence order."""
        return tuple(
            r
            for r in self.regions
            if r.classification is RegionClassification.INSUFFICIENT_EVIDENCE
        )

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible dictionary."""
        return {
            "schema_version": self.schema_version,
            "source_trajectory_id": str(self.source_trajectory_id),
            "actions": [action.to_dict() for action in self.actions],
            "regions": [region.to_dict() for region in self.regions],
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


def _is_explicit_reasoning_action(action_name: str) -> bool:
    """Check whether an action explicitly represents cognition or reasoning."""
    lowered = action_name.lower()
    if lowered in _EXPLICIT_REASONING_NAMES:
        return True
    return any(lowered.startswith(prefix) for prefix in _EXPLICIT_REASONING_PREFIXES)


def _to_analysis(
    item: NormalizedTrajectory
    | CausalActionExtraction
    | IrrelevantActionAnalysis
    | Iterable[CausalExperience],
) -> IrrelevantActionAnalysis:
    """Resolve an input item to a canonical IrrelevantActionAnalysis."""
    if isinstance(item, IrrelevantActionAnalysis):
        return item
    if isinstance(item, CausalActionExtraction):
        return analyze_irrelevant_actions(item)
    if isinstance(item, NormalizedTrajectory):
        return analyze_irrelevant_actions(extract_causal_action_candidates(item))
    try:
        experiences = tuple(item)
    except TypeError as exc:
        raise TypeError(
            "target must be a NormalizedTrajectory, CausalActionExtraction, "
            f"IrrelevantActionAnalysis, or iterable of CausalExperience, got {type(item).__name__}"
        ) from exc
    if not experiences or not all(isinstance(e, CausalExperience) for e in experiences):
        raise TypeError("target must contain only CausalExperience values")
    trajectory = normalize_trajectory(experiences)
    return analyze_irrelevant_actions(extract_causal_action_candidates(trajectory))


def _build_regions(
    trajectory_id: UUID, actions: tuple[ActionClassification, ...]
) -> tuple[ClassifiedRegion, ...]:
    """Group contiguous actions with identical classification into maximal regions."""
    if not actions:
        return ()

    regions: list[ClassifiedRegion] = []
    current_actions: list[ActionClassification] = [actions[0]]
    current_class = actions[0].classification
    region_counter = 1

    for action in actions[1:]:
        if action.classification == current_class:
            current_actions.append(action)
        else:
            region_actions = tuple(current_actions)
            first_seq = region_actions[0].source_sequence
            last_seq = region_actions[-1].source_sequence
            reg_id = f"{trajectory_id}:region:{first_seq:03d}-{last_seq:03d}"
            # Select region reason: if all actions match, use that; otherwise representative
            reasons = {a.reason for a in region_actions}
            region_reason = (
                region_actions[0].reason
                if len(reasons) == 1
                else _primary_region_reason(region_actions)
            )
            # Select region sufficiency: lowest across actions
            sufficiencies = {a.evidence_sufficiency for a in region_actions}
            if EvidenceSufficiency.INSUFFICIENT in sufficiencies:
                reg_suff = EvidenceSufficiency.INSUFFICIENT
            elif EvidenceSufficiency.PARTIAL in sufficiencies:
                reg_suff = EvidenceSufficiency.PARTIAL
            else:
                reg_suff = EvidenceSufficiency.SUFFICIENT

            regions.append(
                ClassifiedRegion(
                    region_id=reg_id,
                    start_sequence=region_actions[0].source_sequence,
                    end_sequence=region_actions[-1].source_sequence,
                    classification=current_class,
                    reason=region_reason,
                    evidence_sufficiency=reg_suff,
                    actions=region_actions,
                )
            )
            region_counter += 1
            current_actions = [action]
            current_class = action.classification

    # Append the final region
    region_actions = tuple(current_actions)
    first_seq = region_actions[0].source_sequence
    last_seq = region_actions[-1].source_sequence
    reg_id = f"{trajectory_id}:region:{first_seq:03d}-{last_seq:03d}"
    reasons = {a.reason for a in region_actions}
    region_reason = (
        region_actions[0].reason if len(reasons) == 1 else _primary_region_reason(region_actions)
    )
    sufficiencies = {a.evidence_sufficiency for a in region_actions}
    if EvidenceSufficiency.INSUFFICIENT in sufficiencies:
        reg_suff = EvidenceSufficiency.INSUFFICIENT
    elif EvidenceSufficiency.PARTIAL in sufficiencies:
        reg_suff = EvidenceSufficiency.PARTIAL
    else:
        reg_suff = EvidenceSufficiency.SUFFICIENT

    regions.append(
        ClassifiedRegion(
            region_id=reg_id,
            start_sequence=region_actions[0].source_sequence,
            end_sequence=region_actions[-1].source_sequence,
            classification=current_class,
            reason=region_reason,
            evidence_sufficiency=reg_suff,
            actions=region_actions,
        )
    )

    return tuple(regions)


def _primary_region_reason(actions: Sequence[ActionClassification]) -> RegionClassificationReason:
    """Return the most informative reason code for a region with mixed action reasons."""
    classification = actions[0].classification
    reasons = [a.reason for a in actions]
    if classification is RegionClassification.REASONING_REQUIRED:
        if RegionClassificationReason.CONFLICTING_OUTCOMES in reasons:
            return RegionClassificationReason.CONFLICTING_OUTCOMES
        if RegionClassificationReason.UNRESOLVED_VARIATION in reasons:
            return RegionClassificationReason.UNRESOLVED_VARIATION
        if RegionClassificationReason.DYNAMIC_OBSERVATION_DEPENDENCY in reasons:
            return RegionClassificationReason.DYNAMIC_OBSERVATION_DEPENDENCY
        if RegionClassificationReason.EXPLICIT_REASONING_STEP in reasons:
            return RegionClassificationReason.EXPLICIT_REASONING_STEP
        return RegionClassificationReason.UNVERIFIED_OR_FAILED_ATTEMPT

    if classification is RegionClassification.INSUFFICIENT_EVIDENCE:
        if RegionClassificationReason.ACTION_ELIMINATED in reasons:
            return RegionClassificationReason.ACTION_ELIMINATED
        if RegionClassificationReason.MISSING_VERIFICATION in reasons:
            return RegionClassificationReason.MISSING_VERIFICATION
        if RegionClassificationReason.SINGLE_OBSERVATION in reasons:
            return RegionClassificationReason.SINGLE_OBSERVATION
        return RegionClassificationReason.INSUFFICIENT_OBSERVATIONS

    return RegionClassificationReason.REPEATED_VERIFIED_SUCCESS


def classify_regions(
    target: NormalizedTrajectory
    | CausalActionExtraction
    | IrrelevantActionAnalysis
    | Iterable[CausalExperience],
    *,
    corroborating: Iterable[
        NormalizedTrajectory
        | CausalActionExtraction
        | IrrelevantActionAnalysis
        | Iterable[CausalExperience]
    ]
    | None = None,
    generalization: ParameterGeneralization | None = None,
) -> RegionClassificationAnalysis:
    """Classify deterministic and reasoning-required regions from historical compiler evidence.

    Exact deterministic classification rule:

    1. Resolve ``target`` to canonical :class:`IrrelevantActionAnalysis`.
    2. Collect corroborating observations from ``corroborating`` analyses/trajectories.
    3. For each step $i$ in the target trajectory:
       a. If the action was eliminated (e.g. ``DENIED`` before execution):
          classify as ``INSUFFICIENT_EVIDENCE`` with reason ``ACTION_ELIMINATED``.
       b. If the action is an explicit reasoning/cognition step:
          classify as ``REASONING_REQUIRED`` with reason ``EXPLICIT_REASONING_STEP``.
       c. If the target action itself was unverified or failed:
          classify as ``REASONING_REQUIRED`` with reason ``UNVERIFIED_OR_FAILED_ATTEMPT``
          (or ``MISSING_VERIFICATION``).
       d. Collect matching observations from corroborating trajectories at sequence $i$.
       e. If corroborating runs show conflicting outcomes (e.g. verified vs failed)
          or divergent actions at sequence $i$:
          classify as ``REASONING_REQUIRED`` with reason ``CONFLICTING_OUTCOMES``
          or ``DYNAMIC_OBSERVATION_DEPENDENCY``.
       f. If parameter generalization reports ``OBSERVED_VARIATION`` for this action:
          classify as ``REASONING_REQUIRED`` with reason ``UNRESOLVED_VARIATION``.
       g. If 2 or more observations exist, ALL are verified, and parameters/outcomes match:
          classify as ``DETERMINISTIC`` with reason ``REPEATED_VERIFIED_SUCCESS``.
       h. If only 1 observation exists:
          classify as ``INSUFFICIENT_EVIDENCE`` with reason ``SINGLE_OBSERVATION``
          (per core invariant: a single observation never proves determinism).
       i. Otherwise:
          classify as ``INSUFFICIENT_EVIDENCE`` with reason ``INSUFFICIENT_OBSERVATIONS``.
    4. Group contiguous steps with matching classifications into maximal
       :class:`ClassifiedRegion` objects.
    """
    analysis = _to_analysis(target)
    corroborating_analyses: list[IrrelevantActionAnalysis] = []
    if corroborating is not None:
        try:
            for item in corroborating:
                corroborating_analyses.append(_to_analysis(item))
        except TypeError as exc:
            raise TypeError("corroborating must be an iterable of trajectories/analyses") from exc

    if generalization is not None and not isinstance(generalization, ParameterGeneralization):
        raise TypeError(
            "generalization must be a ParameterGeneralization or None, "
            f"got {type(generalization).__name__}"
        )

    # Build action variation lookup from generalization if provided
    action_variations: dict[str, bool] = {}
    if generalization is not None:
        for group in generalization.groups:
            if group.evidence is ParameterVariationEvidence.OBSERVED_VARIATION:
                action_variations[group.action_name] = True

    classified_actions: list[ActionClassification] = []

    for index, decision in enumerate(analysis.decisions, start=1):
        candidate = decision.source_candidate
        step = candidate.source_step
        exp = step.experience
        action_name = exp.action.name
        trajectory_id = analysis.source_trajectory_id

        # Target evidence reference
        target_ref = ActionEvidenceReference.from_decision(decision)
        refs: list[ActionEvidenceReference] = [target_ref]

        # Case 1: Eliminated action
        if (
            decision.disposition is ActionDisposition.ELIMINATE
            or exp.outcome is CausalOutcome.DENIED
        ):
            classified_actions.append(
                ActionClassification(
                    source_trajectory_id=trajectory_id,
                    source_sequence=index,
                    source_experience_sha256=step.source_experience_sha256,
                    action_name=action_name,
                    classification=RegionClassification.INSUFFICIENT_EVIDENCE,
                    reason=RegionClassificationReason.ACTION_ELIMINATED,
                    evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
                    observation_count=1,
                    evidence_references=(target_ref,),
                    source_decision=decision,
                    source_candidate=candidate,
                    source_step=step,
                )
            )
            continue

        # Case 2: Explicit reasoning / cognition action
        if _is_explicit_reasoning_action(action_name):
            classified_actions.append(
                ActionClassification(
                    source_trajectory_id=trajectory_id,
                    source_sequence=index,
                    source_experience_sha256=step.source_experience_sha256,
                    action_name=action_name,
                    classification=RegionClassification.REASONING_REQUIRED,
                    reason=RegionClassificationReason.EXPLICIT_REASONING_STEP,
                    evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
                    observation_count=1,
                    evidence_references=(target_ref,),
                    source_decision=decision,
                    source_candidate=candidate,
                    source_step=step,
                )
            )
            continue

        # Case 3: Target action is unverified or failed
        if (
            exp.outcome is not CausalOutcome.VERIFIED
            or exp.verification is None
            or not exp.verification.passed
        ):
            if exp.outcome in (
                CausalOutcome.EXECUTION_FAILED,
                CausalOutcome.VERIFICATION_FAILED,
                CausalOutcome.CANCELLED,
                CausalOutcome.TIMED_OUT,
            ):
                reason = RegionClassificationReason.UNVERIFIED_OR_FAILED_ATTEMPT
            else:
                reason = RegionClassificationReason.MISSING_VERIFICATION
            classified_actions.append(
                ActionClassification(
                    source_trajectory_id=trajectory_id,
                    source_sequence=index,
                    source_experience_sha256=step.source_experience_sha256,
                    action_name=action_name,
                    classification=RegionClassification.REASONING_REQUIRED,
                    reason=reason,
                    evidence_sufficiency=EvidenceSufficiency.PARTIAL,
                    observation_count=1,
                    evidence_references=(target_ref,),
                    source_decision=decision,
                    source_candidate=candidate,
                    source_step=step,
                )
            )
            continue

        # Collect matching corroborating observations
        conflicting = False
        divergent_behavior = False
        target_data_key = _canonical_data_key(exp.action.data)

        for corr in corroborating_analyses:
            if corr.source_trajectory_id == trajectory_id:
                # Same trajectory, skip self
                continue
            if index <= len(corr.decisions):
                corr_dec = corr.decisions[index - 1]
                corr_exp = corr_dec.source_candidate.experience
                corr_ref = ActionEvidenceReference.from_decision(corr_dec)

                if corr_exp.action.name == action_name:
                    refs.append(corr_ref)
                    # Check outcome consistency
                    if (
                        corr_exp.outcome is not CausalOutcome.VERIFIED
                        or corr_exp.verification is None
                        or not corr_exp.verification.passed
                    ):
                        conflicting = True
                    # Check parameter consistency
                    corr_data_key = _canonical_data_key(corr_exp.action.data)
                    if corr_data_key != target_data_key:
                        divergent_behavior = True
                else:
                    # Divergent action at sequence index across runs
                    divergent_behavior = True
            else:
                divergent_behavior = True

        total_observations = len(refs)

        # Check for unresolved variation from parameter generalization
        has_variation = action_variations.get(action_name, False) or divergent_behavior

        if conflicting:
            classification = RegionClassification.REASONING_REQUIRED
            reason = RegionClassificationReason.CONFLICTING_OUTCOMES
            sufficiency = EvidenceSufficiency.SUFFICIENT
        elif has_variation:
            classification = RegionClassification.REASONING_REQUIRED
            reason = RegionClassificationReason.UNRESOLVED_VARIATION
            sufficiency = EvidenceSufficiency.SUFFICIENT
        elif total_observations >= 2 and all(
            r.outcome is CausalOutcome.VERIFIED and r.verification_passed for r in refs
        ):
            classification = RegionClassification.DETERMINISTIC
            reason = RegionClassificationReason.REPEATED_VERIFIED_SUCCESS
            sufficiency = EvidenceSufficiency.SUFFICIENT
        elif total_observations == 1:
            classification = RegionClassification.INSUFFICIENT_EVIDENCE
            reason = RegionClassificationReason.SINGLE_OBSERVATION
            sufficiency = EvidenceSufficiency.PARTIAL
        else:
            classification = RegionClassification.INSUFFICIENT_EVIDENCE
            reason = RegionClassificationReason.INSUFFICIENT_OBSERVATIONS
            sufficiency = EvidenceSufficiency.INSUFFICIENT

        classified_actions.append(
            ActionClassification(
                source_trajectory_id=trajectory_id,
                source_sequence=index,
                source_experience_sha256=step.source_experience_sha256,
                action_name=action_name,
                classification=classification,
                reason=reason,
                evidence_sufficiency=sufficiency,
                observation_count=total_observations,
                evidence_references=tuple(refs),
                source_decision=decision,
                source_candidate=candidate,
                source_step=step,
            )
        )

    action_tuple = tuple(classified_actions)
    regions = _build_regions(analysis.source_trajectory_id, action_tuple)

    return RegionClassificationAnalysis(
        source_trajectory_id=analysis.source_trajectory_id,
        actions=action_tuple,
        regions=regions,
    )


def classify_trajectory_regions(
    trajectory: NormalizedTrajectory,
    *,
    corroborating: Iterable[NormalizedTrajectory] | None = None,
    generalization: ParameterGeneralization | None = None,
) -> RegionClassificationAnalysis:
    """Convenience alias for classifying regions from a NormalizedTrajectory."""
    if not isinstance(trajectory, NormalizedTrajectory):
        raise TypeError(
            f"trajectory must be a NormalizedTrajectory, got {type(trajectory).__name__}"
        )
    return classify_regions(
        target=trajectory,
        corroborating=corroborating,
        generalization=generalization,
    )
