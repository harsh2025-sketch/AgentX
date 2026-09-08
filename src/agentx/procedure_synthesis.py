"""Deterministic procedure-synthesis candidate builder (M4.01).

This module is a top-level composition root, deliberately placed at the
``agentx`` namespace root exactly like the A2.10 agent loop. It reads canonical
learning-analysis outputs and produces canonical :class:`ProcedureGraph` DATA
plus an explicit typed synthesis report. It exists because the canonical
architecture does NOT allow a ``learning -> procedures`` edge, so no canonical
subsystem may legally perform this composition; the manifest is NOT widened.

Conceptually::

    verified trajectory / causal analysis (C3.01-C3.03)
            + parameter generalization (C3.04-C3.05)
            + region classification (C3.06)
            v
    bounded synthesis analysis
            v
    canonical ProcedureGraph CANDIDATE + explicit synthesis report

Candidate is the key word. A synthesized graph is ``CANDIDATE / UNVALIDATED``:
it is never ``ACTIVE`` merely because construction succeeded and never
``VERIFIED`` merely because the source trajectory was verified. A verified
trajectory proves that trajectory worked in its original context; it does NOT
prove the generalized procedure works under new parameters or environment.

The builder never invents evidence. It only synthesizes what upstream typed
evidence supports:

- deterministic regions map to canonical ``ACTION`` graph nodes carrying the
  observed action data verbatim as inert params (no capability version is
  invented, so no A3.02 capability-identity claim is made);
- reasoning-required regions map to canonical ``REASON`` nodes built with the
  existing :class:`ReasonNodeSpec` contract;
- ``RESEARCH`` nodes are never emitted because the current C3.06 vocabulary
  has no research-required classification and research must never be inferred
  from text;
- ``BRANCH`` structure is never emitted because no current upstream typed
  evidence expresses branching;
- generalized parameters come only from ``OBSERVED_VARIATION`` groups;
  ``OBSERVED_SAME_VALUE`` groups are preserved as constants;
  single-observation fields are preserved verbatim with an explicit warning;
- eliminated (``DENIED``) actions are excluded only because the canonical C3.03
  disposition says so; anything retained but classified
  ``INSUFFICIENT_EVIDENCE`` fails closed to rejection.

The synthesizer builds DATA only. It never runs the graph, calls a
Capability/AgentLoop/model/research provider, touches filesystem/browser/
Windows, runs subprocesses, persists, or activates anything. Identical
canonical inputs always produce identical outputs: no randomness, no
wall-clock, no internally generated UUIDs, no model output, no environment
probing. Procedure identity (``ProcedureId`` + revision) is caller-supplied.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.core.causal_experience import CausalExperience
from agentx.core.events import ActionPayload
from agentx.core.ids import ProcedureId
from agentx.core.procedures import ProcedureStatus
from agentx.learning.causal_actions import CausalActionExtraction, ExtractedActionCandidate
from agentx.learning.irrelevant_actions import (
    ActionDisposition,
    ActionDispositionReason,
    ActionEliminationDecision,
    IrrelevantActionAnalysis,
)
from agentx.learning.parameter_extraction import ParameterCandidate, ParameterExtraction
from agentx.learning.parameter_generalization import (
    ParameterGeneralization,
    ParameterObservationGroup,
    ParameterVariationEvidence,
)
from agentx.learning.region_classification import (
    ActionClassification,
    ClassifiedRegion,
    RegionClassification,
    RegionClassificationAnalysis,
    RegionClassificationReason,
)
from agentx.learning.trajectory import NormalizedTrajectory, NormalizedTrajectoryStep
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureEdgeKind,
    ProcedureGraph,
    ProcedureGraphError,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.procedures.reason_research import ReasonNodeSpec

__all__ = [
    "DEFAULT_BOUNDS",
    "DEFAULT_MAX_EDGES",
    "DEFAULT_MAX_METADATA_BYTES",
    "DEFAULT_MAX_PARAMETERS",
    "DEFAULT_MAX_SOURCE_ACTIONS",
    "DEFAULT_MAX_SYNTHESIZED_NODES",
    "PROCEDURE_SYNTHESIS_SCHEMA_VERSION",
    "ExcludedAction",
    "IncludedAction",
    "SynthesisBounds",
    "SynthesisError",
    "SynthesisOutcome",
    "SynthesisRejectionReason",
    "SynthesisResult",
    "SynthesizedParameter",
    "synthesize_procedure_candidate",
]

PROCEDURE_SYNTHESIS_SCHEMA_VERSION: Final[int] = 1

DEFAULT_MAX_SOURCE_ACTIONS: Final[int] = 64
DEFAULT_MAX_SYNTHESIZED_NODES: Final[int] = 128
DEFAULT_MAX_EDGES: Final[int] = 256
DEFAULT_MAX_PARAMETERS: Final[int] = 64
DEFAULT_MAX_METADATA_BYTES: Final[int] = 65_536

_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9_]+")
_NON_TOKEN_RUN: Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")

_STANDING_LIMITATION: Final[str] = (
    "Candidate is unvalidated: verified trajectory evidence proves the observed "
    "trajectory worked in its original context; it does not prove the generalized "
    "procedure works under new parameters or environment."
)

_END_NODE_ID: Final[str] = "end"


class SynthesisError(ValueError):
    """Raised when synthesis data or caller identity violates its contract."""


class SynthesisOutcome(StrEnum):
    """Closed outcome vocabulary for one synthesis attempt."""

    CANDIDATE = "candidate"
    REJECTED = "rejected"


class SynthesisRejectionReason(StrEnum):
    """Closed reason codes explaining why synthesis produced no candidate."""

    NO_RETAINED_ACTIONS = "no_retained_actions"
    EVIDENCE_MISMATCH = "evidence_mismatch"
    FABRICATED_UPSTREAM_OBJECT = "fabricated_upstream_object"
    UNKNOWN_CLASSIFICATION = "unknown_classification"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNSUPPORTED_PARAMETER_GENERALIZATION = "unsupported_parameter_generalization"
    LIMIT_EXCEEDED = "limit_exceeded"
    GRAPH_INVARIANT_VIOLATION = "graph_invariant_violation"
    UNSUPPORTED_CYCLE = "unsupported_cycle"
    MALFORMED_EVIDENCE = "malformed_evidence"


@dataclass(frozen=True, slots=True, kw_only=True)
class SynthesisBounds:
    """Explicit compilation limits; synthesis fails closed when exceeded."""

    max_source_actions: int = DEFAULT_MAX_SOURCE_ACTIONS
    max_synthesized_nodes: int = DEFAULT_MAX_SYNTHESIZED_NODES
    max_edges: int = DEFAULT_MAX_EDGES
    max_parameters: int = DEFAULT_MAX_PARAMETERS
    max_metadata_bytes: int = DEFAULT_MAX_METADATA_BYTES

    def __post_init__(self) -> None:
        for field_name in (
            "max_source_actions",
            "max_synthesized_nodes",
            "max_edges",
            "max_parameters",
            "max_metadata_bytes",
        ):
            value = getattr(self, field_name)
            if type(value) is not int:
                raise TypeError(f"{field_name} must be an integer")
            if value < 1:
                raise SynthesisError(f"{field_name} must be at least 1")

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic JSON-compatible bounds representation."""
        return {
            "max_source_actions": self.max_source_actions,
            "max_synthesized_nodes": self.max_synthesized_nodes,
            "max_edges": self.max_edges,
            "max_parameters": self.max_parameters,
            "max_metadata_bytes": self.max_metadata_bytes,
        }


DEFAULT_BOUNDS: Final[SynthesisBounds] = SynthesisBounds()


def _validate_sequence(value: object) -> int:
    if type(value) is not int:
        raise TypeError("sequence must be an integer")
    if value < 1:
        raise SynthesisError("sequence must be >= 1")
    return value


def _validate_name(value: object, *, field_name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a string")
    if value == "" or value != value.strip():
        raise SynthesisError(f"{field_name} must be non-empty and trimmed")
    return value


def _validate_sha256(value: object) -> str:
    if type(value) is not str:
        raise TypeError("source_experience_sha256 must be a string")
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise SynthesisError("source_experience_sha256 must be 64 lowercase hex characters")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class IncludedAction:
    """One retained source action mapped to a candidate graph node."""

    sequence: int
    action_name: str
    node_id: str
    classification: RegionClassification
    classification_reason: RegionClassificationReason
    source_experience_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "sequence", _validate_sequence(self.sequence))
        object.__setattr__(
            self, "action_name", _validate_name(self.action_name, field_name="action_name")
        )
        object.__setattr__(self, "node_id", _validate_name(self.node_id, field_name="node_id"))
        if type(self.classification) is not RegionClassification:
            raise TypeError("classification must be a RegionClassification")
        if self.classification is RegionClassification.INSUFFICIENT_EVIDENCE:
            raise SynthesisError("included actions must be deterministic or reasoning-required")
        if type(self.classification_reason) is not RegionClassificationReason:
            raise TypeError("classification_reason must be a RegionClassificationReason")
        object.__setattr__(
            self, "source_experience_sha256", _validate_sha256(self.source_experience_sha256)
        )

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic JSON-compatible included-action record."""
        return {
            "sequence": self.sequence,
            "action_name": self.action_name,
            "node_id": self.node_id,
            "classification": self.classification.value,
            "classification_reason": self.classification_reason.value,
            "source_experience_sha256": self.source_experience_sha256,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ExcludedAction:
    """One eliminated source action excluded for its canonical reason."""

    sequence: int
    action_name: str
    source_experience_sha256: str
    reason: ActionDispositionReason

    def __post_init__(self) -> None:
        object.__setattr__(self, "sequence", _validate_sequence(self.sequence))
        object.__setattr__(
            self, "action_name", _validate_name(self.action_name, field_name="action_name")
        )
        object.__setattr__(
            self, "source_experience_sha256", _validate_sha256(self.source_experience_sha256)
        )
        if type(self.reason) is not ActionDispositionReason:
            raise TypeError("reason must be an ActionDispositionReason")

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic JSON-compatible excluded-action record."""
        return {
            "sequence": self.sequence,
            "action_name": self.action_name,
            "source_experience_sha256": self.source_experience_sha256,
            "reason": self.reason.value,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class SynthesizedParameter:
    """One generalized parameter supported by observed-variation evidence.

    Only ``OBSERVED_VARIATION`` groups generalize. The observed values are
    carried verbatim in observation order; nothing is inferred about defaults,
    ranges, or semantics beyond what upstream evidence states.
    """

    parameter_name: str
    action_name: str
    field_name: str
    evidence: ParameterVariationEvidence
    observation_count: int
    observed_values: tuple[object, ...]

    def __post_init__(self) -> None:
        name = _validate_name(self.parameter_name, field_name="parameter_name")
        if _TOKEN_PATTERN.fullmatch(name) is None:
            raise SynthesisError("parameter_name must contain only letters, digits, '_'")
        object.__setattr__(self, "parameter_name", name)
        object.__setattr__(
            self, "action_name", _validate_name(self.action_name, field_name="action_name")
        )
        if type(self.field_name) is not str:
            raise TypeError("field_name must be a string")
        if type(self.evidence) is not ParameterVariationEvidence:
            raise TypeError("evidence must be a ParameterVariationEvidence")
        if self.evidence is not ParameterVariationEvidence.OBSERVED_VARIATION:
            raise SynthesisError("generalized parameters require OBSERVED_VARIATION evidence")
        if type(self.observation_count) is not int:
            raise TypeError("observation_count must be an integer")
        if self.observation_count < 2:
            raise SynthesisError("generalized parameters require at least 2 observations")
        if type(self.observed_values) is not tuple:
            raise TypeError("observed_values must be a tuple")
        if len(self.observed_values) != self.observation_count:
            raise SynthesisError("observed_values must match observation_count exactly")
        for index, value in enumerate(self.observed_values):
            try:
                json.dumps(value, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise SynthesisError(
                    f"observed_values[{index}] is not canonical JSON data"
                ) from exc

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic JSON-compatible parameter record."""
        return {
            "parameter_name": self.parameter_name,
            "action_name": self.action_name,
            "field_name": self.field_name,
            "evidence": self.evidence.value,
            "observation_count": self.observation_count,
            "observed_values": list(self.observed_values),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class SynthesisResult:
    """Immutable typed outcome of one synthesis attempt.

    On ``CANDIDATE``, ``graph`` holds the canonical unvalidated candidate,
    ``status`` is exactly :attr:`ProcedureStatus.CANDIDATE`, and
    included/excluded records partition source sequences ``1..N``. On
    ``REJECTED``, ``graph`` is ``None``, ``status`` is ``None`` (nothing was
    produced, so no storage status applies), at least one closed-vocabulary
    reason plus deterministic details explain the refusal, and ``excluded``
    holds whatever eliminated actions were observed before rejection.
    ``status`` can never be ``ACTIVE`` or ``RETIRED``: synthesis performs no
    promotion or withdrawal.
    """

    outcome: SynthesisOutcome
    status: ProcedureStatus | None
    procedure_id: ProcedureId
    revision: int
    source_trajectory_id: UUID
    graph: ProcedureGraph | None
    included: tuple[IncludedAction, ...]
    excluded: tuple[ExcludedAction, ...]
    parameters: tuple[SynthesizedParameter, ...]
    reasoning_regions: tuple[ClassifiedRegion, ...]
    warnings: tuple[str, ...]
    rejection_reasons: tuple[SynthesisRejectionReason, ...]
    rejection_details: tuple[str, ...]
    bounds: SynthesisBounds
    schema_version: int = PROCEDURE_SYNTHESIS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.outcome) is not SynthesisOutcome:
            raise TypeError("outcome must be a SynthesisOutcome")
        if self.status is not None and type(self.status) is not ProcedureStatus:
            raise TypeError("status must be a ProcedureStatus or None")
        if self.status is not None and self.status is not ProcedureStatus.CANDIDATE:
            raise SynthesisError("synthesis status must be CANDIDATE or None; never ACTIVE")
        if type(self.procedure_id) is not ProcedureId:
            raise TypeError("procedure_id must be a ProcedureId")
        if type(self.revision) is not int:
            raise TypeError("revision must be an integer")
        if self.revision < 1:
            raise SynthesisError("revision must be a positive integer")
        if type(self.source_trajectory_id) is not UUID:
            raise TypeError("source_trajectory_id must be a UUID")
        if self.source_trajectory_id.int == 0:
            raise SynthesisError("source_trajectory_id must not be the nil UUID")
        if self.graph is not None and type(self.graph) is not ProcedureGraph:
            raise TypeError("graph must be a ProcedureGraph or None")
        if type(self.bounds) is not SynthesisBounds:
            raise TypeError("bounds must be SynthesisBounds")
        if type(self.schema_version) is not int:
            raise TypeError("schema_version must be an integer")
        if self.schema_version != PROCEDURE_SYNTHESIS_SCHEMA_VERSION:
            raise SynthesisError(
                f"unsupported synthesis schema version {self.schema_version}; "
                f"supported version is {PROCEDURE_SYNTHESIS_SCHEMA_VERSION}"
            )
        if type(self.included) is not tuple:
            raise TypeError("included must be a tuple of IncludedAction values")
        for included_item in self.included:
            if type(included_item) is not IncludedAction:
                raise TypeError("included must contain only IncludedAction values")
        if type(self.excluded) is not tuple:
            raise TypeError("excluded must be a tuple of ExcludedAction values")
        for excluded_item in self.excluded:
            if type(excluded_item) is not ExcludedAction:
                raise TypeError("excluded must contain only ExcludedAction values")
        if type(self.parameters) is not tuple:
            raise TypeError("parameters must be a tuple of SynthesizedParameter values")
        for parameter_item in self.parameters:
            if type(parameter_item) is not SynthesizedParameter:
                raise TypeError("parameters must contain only SynthesizedParameter values")
        if len({named.parameter_name for named in self.parameters}) != len(self.parameters):
            raise SynthesisError("parameter names must be unique within one result")
        if type(self.reasoning_regions) is not tuple:
            raise TypeError("reasoning_regions must be a tuple of ClassifiedRegion values")
        for region_item in self.reasoning_regions:
            if type(region_item) is not ClassifiedRegion:
                raise TypeError("reasoning_regions must contain only ClassifiedRegion values")
            if region_item.classification is not RegionClassification.REASONING_REQUIRED:
                raise SynthesisError("reasoning_regions must be reasoning-required regions")
        if type(self.warnings) is not tuple:
            raise TypeError("warnings must be a tuple of strings")
        for warning_item in self.warnings:
            if type(warning_item) is not str:
                raise TypeError("warnings must contain only strings")
        if type(self.rejection_reasons) is not tuple:
            raise TypeError("rejection_reasons must be a tuple of SynthesisRejectionReason values")
        for reason_item in self.rejection_reasons:
            if type(reason_item) is not SynthesisRejectionReason:
                raise TypeError(
                    "rejection_reasons must contain only SynthesisRejectionReason values"
                )
        if type(self.rejection_details) is not tuple:
            raise TypeError("rejection_details must be a tuple of strings")
        for detail_item in self.rejection_details:
            if type(detail_item) is not str:
                raise TypeError("rejection_details must contain only strings")

        sequences = [included.sequence for included in self.included]
        sequences.extend(excluded.sequence for excluded in self.excluded)
        if len(set(sequences)) != len(sequences):
            raise SynthesisError("included/excluded sequences must not repeat")

        if self.outcome is SynthesisOutcome.CANDIDATE:
            if self.graph is None:
                raise SynthesisError("CANDIDATE outcome requires a graph")
            if self.status is not ProcedureStatus.CANDIDATE:
                raise SynthesisError("CANDIDATE outcome requires CANDIDATE status")
            if self.rejection_reasons or self.rejection_details:
                raise SynthesisError("CANDIDATE outcome must carry no rejection")
            if not self.included:
                raise SynthesisError("CANDIDATE outcome requires included actions")
            if sorted(sequences) != list(range(1, len(sequences) + 1)):
                raise SynthesisError("candidate sequences must partition 1..N contiguously")
        else:
            if self.graph is not None:
                raise SynthesisError("REJECTED outcome must not carry a graph")
            if self.status is not None:
                raise SynthesisError("REJECTED outcome must not carry a status")
            if not self.rejection_reasons:
                raise SynthesisError("REJECTED outcome requires at least one reason")

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic JSON-compatible synthesis report."""
        return {
            "schema_version": self.schema_version,
            "outcome": self.outcome.value,
            "status": None if self.status is None else self.status.value,
            "procedure_id": self.procedure_id.to_str(),
            "revision": self.revision,
            "source_trajectory_id": str(self.source_trajectory_id),
            "graph": None if self.graph is None else self.graph.to_dict(),
            "included": [item.to_dict() for item in self.included],
            "excluded": [item.to_dict() for item in self.excluded],
            "parameters": [item.to_dict() for item in self.parameters],
            "reasoning_regions": [item.to_dict() for item in self.reasoning_regions],
            "warnings": list(self.warnings),
            "rejection_reasons": [item.value for item in self.rejection_reasons],
            "rejection_details": list(self.rejection_details),
            "bounds": self.bounds.to_dict(),
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


@dataclass(frozen=True, slots=True, kw_only=True)
class _Failure:
    """Internal fail-closed signal: a rejection reason plus a detail string."""

    reason: SynthesisRejectionReason
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class _RetainedActionEvidence:
    """Internal verified evidence bundle for one retained source action."""

    action_name: str
    action_data: dict[str, object]
    classification: RegionClassification
    classification_reason: RegionClassificationReason


def _sanitize_token(text: str) -> str:
    """Return a deterministic ``[a-z0-9_]+`` token derived from ``text``."""
    token = _NON_TOKEN_RUN.sub("_", text.lower()).strip("_")
    return token if token else "x"


def _step_node_id(sequence: int) -> str:
    return f"step-{sequence:03d}"


def _parameter_name(index: int, action_name: str, field_name: str) -> str:
    return f"p{index:03d}_{_sanitize_token(action_name)}_{_sanitize_token(field_name)}"


def _experience_fingerprint(experience: CausalExperience) -> str:
    return hashlib.sha256(experience.to_json().encode("utf-8")).hexdigest()


def _reject(
    *,
    procedure_id: ProcedureId,
    revision: int,
    source_trajectory_id: UUID,
    bounds: SynthesisBounds,
    reasons: tuple[SynthesisRejectionReason, ...],
    details: tuple[str, ...],
    excluded: tuple[ExcludedAction, ...] = (),
) -> SynthesisResult:
    result = SynthesisResult(
        outcome=SynthesisOutcome.REJECTED,
        status=None,
        procedure_id=procedure_id,
        revision=revision,
        source_trajectory_id=source_trajectory_id,
        graph=None,
        included=(),
        excluded=excluded,
        parameters=(),
        reasoning_regions=(),
        warnings=(),
        rejection_reasons=reasons,
        rejection_details=details,
        bounds=bounds,
    )
    try:
        size = len(result.to_json().encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise SynthesisError("rejection report is not canonical JSON data") from exc
    if size > bounds.max_metadata_bytes:
        raise SynthesisError(
            f"rejection metadata {size} bytes exceeds limit {bounds.max_metadata_bytes}"
        )
    return result


def _read_action_data(action: ActionPayload) -> dict[str, object]:
    data: object = action.data
    if not isinstance(data, Mapping):
        raise SynthesisError("action data must remain a canonical JSON object")
    raw = action.to_dict()["data"]
    if type(raw) is not dict:
        raise SynthesisError("action data must remain a canonical JSON object")
    plain: dict[str, object] = {}
    for key, value in raw.items():
        plain[key] = value
    return plain


def _read_observation_value(candidate: ParameterCandidate) -> object:
    decision = candidate.source_decision
    if type(decision) is not ActionEliminationDecision:
        raise SynthesisError("parameter observation decision is not canonical")
    action = decision.source_candidate.action
    if type(action) is not ActionPayload or not isinstance(action.data, Mapping):
        raise SynthesisError("parameter observation action is not canonical data")
    return candidate.to_dict()["value"]


def synthesize_procedure_candidate(
    *,
    trajectory: NormalizedTrajectory,
    extraction: CausalActionExtraction,
    elimination: IrrelevantActionAnalysis,
    parameters: ParameterExtraction,
    generalization: ParameterGeneralization,
    regions: RegionClassificationAnalysis,
    procedure_id: ProcedureId,
    revision: int,
    bounds: SynthesisBounds | None = None,
) -> SynthesisResult:
    """Build a canonical unvalidated procedure candidate from learning evidence.

    All six upstream analyses must be canonical typed outputs that agree on one
    source trajectory. Wrong Python types fail closed with :class:`TypeError`
    (no evidence-bearing result can be constructed from them); canonical but
    insufficient, contradictory, oversized, or malformed evidence fails closed
    to a ``REJECTED`` :class:`SynthesisResult` that names closed-vocabulary
    reasons. Success returns ``CANDIDATE`` with status ``CANDIDATE``.

    The function is pure: deterministic, side-effect free, and authority free.
    """
    if type(trajectory) is not NormalizedTrajectory:
        raise TypeError(
            f"trajectory must be a canonical NormalizedTrajectory, got {type(trajectory).__name__}"
        )
    if type(extraction) is not CausalActionExtraction:
        raise TypeError(
            "extraction must be a canonical CausalActionExtraction, "
            f"got {type(extraction).__name__}"
        )
    if type(elimination) is not IrrelevantActionAnalysis:
        raise TypeError(
            "elimination must be a canonical IrrelevantActionAnalysis, "
            f"got {type(elimination).__name__}"
        )
    if type(parameters) is not ParameterExtraction:
        raise TypeError(
            f"parameters must be a canonical ParameterExtraction, got {type(parameters).__name__}"
        )
    if type(generalization) is not ParameterGeneralization:
        raise TypeError(
            "generalization must be a canonical ParameterGeneralization, "
            f"got {type(generalization).__name__}"
        )
    if type(regions) is not RegionClassificationAnalysis:
        raise TypeError(
            "regions must be a canonical RegionClassificationAnalysis, "
            f"got {type(regions).__name__}"
        )
    if type(procedure_id) is not ProcedureId:
        raise TypeError(f"procedure_id must be a ProcedureId, got {type(procedure_id).__name__}")
    if type(revision) is not int:
        raise TypeError(f"revision must be an integer, got {type(revision).__name__}")
    if revision < 1:
        raise SynthesisError("revision must be a positive integer (first revision is 1)")
    if bounds is None:
        effective_bounds = DEFAULT_BOUNDS
    elif type(bounds) is not SynthesisBounds:
        raise TypeError(f"bounds must be SynthesisBounds or None, got {type(bounds).__name__}")
    else:
        effective_bounds = bounds

    trajectory_id = trajectory.trajectory_id
    step_count = len(trajectory.steps)
    if step_count > effective_bounds.max_source_actions:
        return _reject(
            procedure_id=procedure_id,
            revision=revision,
            source_trajectory_id=trajectory_id,
            bounds=effective_bounds,
            reasons=(SynthesisRejectionReason.LIMIT_EXCEEDED,),
            details=(
                f"source actions {step_count} exceed limit {effective_bounds.max_source_actions}",
            ),
        )

    try:
        mismatch = _check_evidence_agreement(
            trajectory=trajectory,
            extraction=extraction,
            elimination=elimination,
            parameters=parameters,
            generalization=generalization,
            regions=regions,
        )
    except (TypeError, ValueError, AttributeError) as exc:
        mismatch = f"evidence cross-check is unreadable: {type(exc).__name__}"
    if mismatch is not None:
        return _reject(
            procedure_id=procedure_id,
            revision=revision,
            source_trajectory_id=trajectory_id,
            bounds=effective_bounds,
            reasons=(SynthesisRejectionReason.EVIDENCE_MISMATCH,),
            details=(mismatch,),
        )

    for index, step in enumerate(trajectory.steps, start=1):
        if type(step) is not NormalizedTrajectoryStep:
            return _reject(
                procedure_id=procedure_id,
                revision=revision,
                source_trajectory_id=trajectory_id,
                bounds=effective_bounds,
                reasons=(SynthesisRejectionReason.EVIDENCE_MISMATCH,),
                details=(f"sequence {index}: trajectory step is not canonical",),
            )
        experience = step.experience
        if type(experience) is not CausalExperience:
            return _reject(
                procedure_id=procedure_id,
                revision=revision,
                source_trajectory_id=trajectory_id,
                bounds=effective_bounds,
                reasons=(SynthesisRejectionReason.FABRICATED_UPSTREAM_OBJECT,),
                details=(f"sequence {index}: source experience is not canonical",),
            )
        action = experience.action
        if type(action) is not ActionPayload:
            return _reject(
                procedure_id=procedure_id,
                revision=revision,
                source_trajectory_id=trajectory_id,
                bounds=effective_bounds,
                reasons=(SynthesisRejectionReason.FABRICATED_UPSTREAM_OBJECT,),
                details=(f"sequence {index}: action payload is not canonical",),
            )
        action_data: object = action.data
        if not isinstance(action_data, Mapping):
            return _reject(
                procedure_id=procedure_id,
                revision=revision,
                source_trajectory_id=trajectory_id,
                bounds=effective_bounds,
                reasons=(SynthesisRejectionReason.MALFORMED_EVIDENCE,),
                details=(f"sequence {index}: action data is not a canonical JSON object",),
            )
        try:
            fingerprint = _experience_fingerprint(experience)
        except (TypeError, ValueError, AttributeError):
            return _reject(
                procedure_id=procedure_id,
                revision=revision,
                source_trajectory_id=trajectory_id,
                bounds=effective_bounds,
                reasons=(SynthesisRejectionReason.MALFORMED_EVIDENCE,),
                details=(f"sequence {index}: source experience is not canonical data",),
            )
        if fingerprint != step.source_experience_sha256:
            return _reject(
                procedure_id=procedure_id,
                revision=revision,
                source_trajectory_id=trajectory_id,
                bounds=effective_bounds,
                reasons=(SynthesisRejectionReason.FABRICATED_UPSTREAM_OBJECT,),
                details=(
                    f"sequence {index}: source-experience fingerprint "
                    "does not match canonical experience",
                ),
            )

    # Integrity partition: excluded actions first, then evidence reads that
    # must succeed before any sufficiency judgment is made.
    excluded: list[ExcludedAction] = []
    retained_sequences: list[int] = []
    for sequence in range(1, step_count + 1):
        decision = elimination.decisions[sequence - 1]
        if type(decision) is not ActionEliminationDecision:
            return _reject(
                procedure_id=procedure_id,
                revision=revision,
                source_trajectory_id=trajectory_id,
                bounds=effective_bounds,
                reasons=(SynthesisRejectionReason.EVIDENCE_MISMATCH,),
                details=(f"sequence {sequence}: elimination decision is not canonical",),
            )
        disposition = decision.disposition
        if type(disposition) is not ActionDisposition:
            return _reject(
                procedure_id=procedure_id,
                revision=revision,
                source_trajectory_id=trajectory_id,
                bounds=effective_bounds,
                reasons=(SynthesisRejectionReason.EVIDENCE_MISMATCH,),
                details=(f"sequence {sequence}: elimination disposition is not canonical",),
            )
        action_name = _safe_action_name(trajectory.steps[sequence - 1].experience)
        if action_name is None:
            return _reject(
                procedure_id=procedure_id,
                revision=revision,
                source_trajectory_id=trajectory_id,
                bounds=effective_bounds,
                reasons=(SynthesisRejectionReason.MALFORMED_EVIDENCE,),
                details=(f"sequence {sequence}: action name is not canonical data",),
            )
        if disposition is ActionDisposition.ELIMINATE:
            reason = decision.reason
            if type(reason) is not ActionDispositionReason:
                return _reject(
                    procedure_id=procedure_id,
                    revision=revision,
                    source_trajectory_id=trajectory_id,
                    bounds=effective_bounds,
                    reasons=(SynthesisRejectionReason.EVIDENCE_MISMATCH,),
                    details=(f"sequence {sequence}: elimination reason is not canonical",),
                )
            excluded.append(
                ExcludedAction(
                    sequence=sequence,
                    action_name=action_name,
                    source_experience_sha256=trajectory.steps[
                        sequence - 1
                    ].source_experience_sha256,
                    reason=reason,
                )
            )
        else:
            retained_sequences.append(sequence)

    if not retained_sequences:
        return _reject(
            procedure_id=procedure_id,
            revision=revision,
            source_trajectory_id=trajectory_id,
            bounds=effective_bounds,
            reasons=(SynthesisRejectionReason.NO_RETAINED_ACTIONS,),
            details=("no retained actions: every extracted action was eliminated upstream",),
            excluded=tuple(excluded),
        )

    node_count = len(retained_sequences) + 1
    edge_count = len(retained_sequences)
    if node_count > effective_bounds.max_synthesized_nodes:
        return _reject(
            procedure_id=procedure_id,
            revision=revision,
            source_trajectory_id=trajectory_id,
            bounds=effective_bounds,
            reasons=(SynthesisRejectionReason.LIMIT_EXCEEDED,),
            details=(
                f"synthesized nodes {node_count} exceed limit "
                f"{effective_bounds.max_synthesized_nodes}",
            ),
            excluded=tuple(excluded),
        )
    if edge_count > effective_bounds.max_edges:
        return _reject(
            procedure_id=procedure_id,
            revision=revision,
            source_trajectory_id=trajectory_id,
            bounds=effective_bounds,
            reasons=(SynthesisRejectionReason.LIMIT_EXCEEDED,),
            details=(f"synthesized edges {edge_count} exceed limit {effective_bounds.max_edges}",),
            excluded=tuple(excluded),
        )

    group_failure = _check_generalization_groups(
        generalization=generalization,
        elimination=elimination,
        step_count=step_count,
    )
    if group_failure is not None:
        return _reject(
            procedure_id=procedure_id,
            revision=revision,
            source_trajectory_id=trajectory_id,
            bounds=effective_bounds,
            reasons=(group_failure.reason,),
            details=(group_failure.detail,),
            excluded=tuple(excluded),
        )

    variation_groups = [
        group
        for group in generalization.groups
        if group.evidence is ParameterVariationEvidence.OBSERVED_VARIATION
    ]
    if len(variation_groups) > effective_bounds.max_parameters:
        return _reject(
            procedure_id=procedure_id,
            revision=revision,
            source_trajectory_id=trajectory_id,
            bounds=effective_bounds,
            reasons=(SynthesisRejectionReason.LIMIT_EXCEEDED,),
            details=(
                f"generalized parameters {len(variation_groups)} exceed limit "
                f"{effective_bounds.max_parameters}",
            ),
            excluded=tuple(excluded),
        )

    # Per-action evidence reads and classification mapping, in source order.
    included: list[IncludedAction] = []
    nodes: list[ProcedureNode] = []
    insufficient: list[str] = []
    for sequence in retained_sequences:
        evidence = _read_retained_action(
            trajectory=trajectory,
            regions=regions,
            sequence=sequence,
        )
        if isinstance(evidence, _Failure):
            return _reject(
                procedure_id=procedure_id,
                revision=revision,
                source_trajectory_id=trajectory_id,
                bounds=effective_bounds,
                reasons=(evidence.reason,),
                details=(evidence.detail,),
                excluded=tuple(excluded),
            )
        if evidence.classification is RegionClassification.INSUFFICIENT_EVIDENCE:
            insufficient.append(
                f"sequence {sequence}: insufficient evidence "
                f"({evidence.classification_reason.value})"
            )
            continue
        node_id = _step_node_id(sequence)
        included.append(
            IncludedAction(
                sequence=sequence,
                action_name=evidence.action_name,
                node_id=node_id,
                classification=evidence.classification,
                classification_reason=evidence.classification_reason,
                source_experience_sha256=trajectory.steps[sequence - 1].source_experience_sha256,
            )
        )
        if evidence.classification is RegionClassification.DETERMINISTIC:
            node_outcome: ProcedureNode | _Failure = _build_action_node(
                generalization=generalization,
                sequence=sequence,
                node_id=node_id,
                action_name=evidence.action_name,
                action_data=evidence.action_data,
                classification_reason=evidence.classification_reason,
                experience_sha256=trajectory.steps[sequence - 1].source_experience_sha256,
            )
        else:
            node_outcome = _build_reason_node(
                regions=regions,
                sequence=sequence,
                node_id=node_id,
                action_name=evidence.action_name,
                classification_reason=evidence.classification_reason,
            )
        if isinstance(node_outcome, _Failure):
            return _reject(
                procedure_id=procedure_id,
                revision=revision,
                source_trajectory_id=trajectory_id,
                bounds=effective_bounds,
                reasons=(node_outcome.reason,),
                details=(node_outcome.detail,),
                excluded=tuple(excluded),
            )
        nodes.append(node_outcome)

    if insufficient:
        return _reject(
            procedure_id=procedure_id,
            revision=revision,
            source_trajectory_id=trajectory_id,
            bounds=effective_bounds,
            reasons=(SynthesisRejectionReason.INSUFFICIENT_EVIDENCE,),
            details=tuple(insufficient),
            excluded=tuple(excluded),
        )

    parameters_outcome = _build_synthesized_parameters(generalization)
    if isinstance(parameters_outcome, _Failure):
        return _reject(
            procedure_id=procedure_id,
            revision=revision,
            source_trajectory_id=trajectory_id,
            bounds=effective_bounds,
            reasons=(parameters_outcome.reason,),
            details=(parameters_outcome.detail,),
            excluded=tuple(excluded),
        )
    synthesized_parameters, single_observation_warnings = parameters_outcome

    try:
        # Plain graph-level END node without a payload: legal A3.01 structure
        # on its own (the A3.05 END contract validates a payload only when
        # asked to view one, and this composer claims no A3.05 payload).
        end_node = ProcedureNode(
            id=ProcedureNodeId(_END_NODE_ID),
            kind=ProcedureNodeKind.END,
            label="end",
            params={},
        )
        ordered_nodes = [*nodes, end_node]
        node_ids = [node.id.to_str() for node in ordered_nodes[:-1]]
        chained = [
            ProcedureEdge(
                source=ProcedureNodeId.parse(node_ids[index]),
                target=ProcedureNodeId.parse(node_ids[index + 1]),
                kind=ProcedureEdgeKind.NEXT,
            )
            for index in range(len(node_ids) - 1)
        ]
        chained.append(
            ProcedureEdge(
                source=ProcedureNodeId.parse(node_ids[-1]),
                target=ProcedureNodeId.parse(_END_NODE_ID),
                kind=ProcedureEdgeKind.NEXT,
            )
        )
        edges = tuple(chained)
        graph = ProcedureGraph(
            entry=ProcedureNodeId.parse(node_ids[0]),
            nodes=tuple(ordered_nodes),
            edges=edges,
        )
    except ProcedureGraphError as exc:
        return _reject(
            procedure_id=procedure_id,
            revision=revision,
            source_trajectory_id=trajectory_id,
            bounds=effective_bounds,
            reasons=(SynthesisRejectionReason.GRAPH_INVARIANT_VIOLATION,),
            details=(f"graph assembly violated canonical invariants: {exc}",),
            excluded=tuple(excluded),
        )

    reasoning_regions = tuple(
        region
        for region in regions.regions
        if region.classification is RegionClassification.REASONING_REQUIRED
    )
    warnings = [_STANDING_LIMITATION]
    for region in reasoning_regions:
        warnings.append(
            f"Region '{region.region_id}' (sequences {region.start_sequence}-"
            f"{region.end_sequence}) requires reasoning; mapped to REASON nodes "
            "without any determinism claim."
        )
    warnings.extend(single_observation_warnings)

    result = SynthesisResult(
        outcome=SynthesisOutcome.CANDIDATE,
        status=ProcedureStatus.CANDIDATE,
        procedure_id=procedure_id,
        revision=revision,
        source_trajectory_id=trajectory_id,
        graph=graph,
        included=tuple(included),
        excluded=tuple(excluded),
        parameters=synthesized_parameters,
        reasoning_regions=reasoning_regions,
        warnings=tuple(warnings),
        rejection_reasons=(),
        rejection_details=(),
        bounds=effective_bounds,
    )
    try:
        size = len(result.to_json().encode("utf-8"))
    except (TypeError, ValueError):
        return _reject(
            procedure_id=procedure_id,
            revision=revision,
            source_trajectory_id=trajectory_id,
            bounds=effective_bounds,
            reasons=(SynthesisRejectionReason.MALFORMED_EVIDENCE,),
            details=("candidate metadata is not canonical JSON data",),
            excluded=tuple(excluded),
        )
    if size > effective_bounds.max_metadata_bytes:
        return _reject(
            procedure_id=procedure_id,
            revision=revision,
            source_trajectory_id=trajectory_id,
            bounds=effective_bounds,
            reasons=(SynthesisRejectionReason.LIMIT_EXCEEDED,),
            details=(
                f"candidate metadata {size} bytes exceeds limit "
                f"{effective_bounds.max_metadata_bytes}",
            ),
            excluded=tuple(excluded),
        )
    return result


def _check_evidence_agreement(
    *,
    trajectory: NormalizedTrajectory,
    extraction: CausalActionExtraction,
    elimination: IrrelevantActionAnalysis,
    parameters: ParameterExtraction,
    generalization: ParameterGeneralization,
    regions: RegionClassificationAnalysis,
) -> str | None:
    """Return a mismatch detail, or ``None`` when all evidence agrees."""
    trajectory_id = trajectory.trajectory_id
    step_count = len(trajectory.steps)
    claims: tuple[tuple[str, object], ...] = (
        ("extraction", extraction.source_trajectory_id),
        ("elimination", elimination.source_trajectory_id),
        ("parameters", parameters.source_trajectory_id),
        ("generalization", generalization.source_trajectory_id),
        ("regions", regions.source_trajectory_id),
    )
    for stage, claimed in claims:
        if claimed != trajectory_id:
            return f"{stage} references trajectory {claimed} but trajectory is {trajectory_id}"
    counts: tuple[tuple[str, int], ...] = (
        ("extraction", len(extraction.candidates)),
        ("elimination", len(elimination.decisions)),
        ("regions", len(regions.actions)),
    )
    for stage, count in counts:
        if count != step_count:
            return f"{stage} covers {count} actions but trajectory has {step_count} steps"
    for candidate in extraction.candidates:
        if type(candidate) is not ExtractedActionCandidate:
            return "extraction contains a non-canonical candidate"
    for decision in elimination.decisions:
        if type(decision) is not ActionEliminationDecision:
            return "elimination contains a non-canonical decision"
    for parameter_candidate in parameters.candidates:
        if type(parameter_candidate) is not ParameterCandidate:
            return "parameters contains a non-canonical candidate"
    for group in generalization.groups:
        if type(group) is not ParameterObservationGroup:
            return "generalization contains a non-canonical group"
    for action in regions.actions:
        if type(action) is not ActionClassification:
            return "regions contains a non-canonical action classification"
    for region in regions.regions:
        if type(region) is not ClassifiedRegion:
            return "regions contains a non-canonical region"
    if parameters.source_analysis != elimination:
        return "parameter extraction was not built from the supplied elimination analysis"
    if generalization.source_extraction != parameters:
        return "parameter generalization was not built from the supplied parameter extraction"
    for index in range(step_count):
        candidate = extraction.candidates[index]
        if (
            type(candidate) is ExtractedActionCandidate
            and candidate.source_step != trajectory.steps[index]
        ):
            return f"sequence {index + 1}: extraction step does not match trajectory"
        expected_sha = trajectory.steps[index].source_experience_sha256
        decision = elimination.decisions[index]
        if (
            type(decision) is ActionEliminationDecision
            and decision.source_experience_sha256 != expected_sha
        ):
            return f"sequence {index + 1}: elimination evidence does not match trajectory"
        action = regions.actions[index]
        if type(action) is ActionClassification and action.source_experience_sha256 != expected_sha:
            return f"sequence {index + 1}: region evidence does not match trajectory"
    return None


def _check_generalization_groups(
    *,
    generalization: ParameterGeneralization,
    elimination: IrrelevantActionAnalysis,
    step_count: int,
) -> _Failure | None:
    """Validate group evidence enums, names, and observation provenance."""
    for index, group in enumerate(generalization.groups):
        if type(group.evidence) is not ParameterVariationEvidence:
            return _Failure(
                reason=SynthesisRejectionReason.UNSUPPORTED_PARAMETER_GENERALIZATION,
                detail=f"parameter group {index}: generalization evidence is not canonical",
            )
        if (
            type(group.action_name) is not str
            or group.action_name == ""
            or type(group.field_name) is not str
        ):
            return _Failure(
                reason=SynthesisRejectionReason.EVIDENCE_MISMATCH,
                detail=f"parameter group {index}: group key is not canonical",
            )
        for observation in group.observations:
            if type(observation) is not ParameterCandidate:
                return _Failure(
                    reason=SynthesisRejectionReason.EVIDENCE_MISMATCH,
                    detail=f"parameter group {index}: observation is not canonical",
                )
            try:
                sequence = observation.source_sequence
                decision = observation.source_decision
            except (TypeError, ValueError, AttributeError):
                return _Failure(
                    reason=SynthesisRejectionReason.EVIDENCE_MISMATCH,
                    detail=f"parameter group {index}: observation provenance is unreadable",
                )
            if (
                type(sequence) is not int
                or sequence < 1
                or sequence > step_count
                or type(decision) is not ActionEliminationDecision
                or elimination.decisions[sequence - 1].disposition is not ActionDisposition.RETAIN
            ):
                return _Failure(
                    reason=SynthesisRejectionReason.EVIDENCE_MISMATCH,
                    detail=(
                        f"parameter group {index}: observation does not reference retained evidence"
                    ),
                )
    return None


def _safe_action_name(experience: CausalExperience) -> str | None:
    action = experience.action
    if type(action) is not ActionPayload:
        return None
    name = action.name
    if type(name) is not str or name == "" or name != name.strip():
        return None
    return name


def _read_retained_action(
    *,
    trajectory: NormalizedTrajectory,
    regions: RegionClassificationAnalysis,
    sequence: int,
) -> _RetainedActionEvidence | _Failure:
    """Read one retained action's evidence or return a failure signal."""
    step = trajectory.steps[sequence - 1]
    action = step.experience.action
    if type(action) is not ActionPayload:
        return _Failure(
            reason=SynthesisRejectionReason.FABRICATED_UPSTREAM_OBJECT,
            detail=f"sequence {sequence}: action payload is not canonical",
        )
    name = _safe_action_name(step.experience)
    if name is None:
        return _Failure(
            reason=SynthesisRejectionReason.MALFORMED_EVIDENCE,
            detail=f"sequence {sequence}: action name is not canonical data",
        )
    try:
        data = _read_action_data(action)
    except (TypeError, ValueError, AttributeError):
        return _Failure(
            reason=SynthesisRejectionReason.MALFORMED_EVIDENCE,
            detail=f"sequence {sequence}: action data is not canonical JSON data",
        )
    classification_record = regions.actions[sequence - 1]
    classification = classification_record.classification
    classification_reason = classification_record.reason
    if (
        type(classification) is not RegionClassification
        or type(classification_reason) is not RegionClassificationReason
    ):
        return _Failure(
            reason=SynthesisRejectionReason.UNKNOWN_CLASSIFICATION,
            detail=f"sequence {sequence}: region classification is not canonical",
        )
    return _RetainedActionEvidence(
        action_name=name,
        action_data=data,
        classification=classification,
        classification_reason=classification_reason,
    )


def _build_action_node(
    *,
    generalization: ParameterGeneralization,
    sequence: int,
    node_id: str,
    action_name: str,
    action_data: dict[str, object],
    classification_reason: RegionClassificationReason,
    experience_sha256: str,
) -> ProcedureNode | _Failure:
    """Build one deterministic ACTION node or return a failure signal."""
    constants: dict[str, object] = {}
    node_parameters: dict[str, object] = {}
    single_observation_fields: list[str] = []
    for index, group in enumerate(generalization.groups):
        if group.action_name != action_name:
            continue
        try:
            values = [_read_observation_value(item) for item in group.observations]
        except (TypeError, ValueError, AttributeError):
            return _Failure(
                reason=SynthesisRejectionReason.MALFORMED_EVIDENCE,
                detail=f"sequence {sequence}: parameter evidence is not canonical data",
            )
        if not values:
            return _Failure(
                reason=SynthesisRejectionReason.MALFORMED_EVIDENCE,
                detail=f"sequence {sequence}: parameter evidence is not canonical data",
            )
        if group.evidence is ParameterVariationEvidence.OBSERVED_VARIATION:
            node_parameters[group.field_name] = {
                "parameter_name": _parameter_name(index, group.action_name, group.field_name),
                "observed_values": values,
                "observation_count": len(values),
            }
        elif group.evidence is ParameterVariationEvidence.OBSERVED_SAME_VALUE:
            constants[group.field_name] = values[0]
        else:
            single_observation_fields.append(group.field_name)
    try:
        return ProcedureNode(
            id=ProcedureNodeId(node_id),
            kind=ProcedureNodeKind.ACTION,
            label=f"step {sequence}",
            params={
                "action_name": action_name,
                "action_data": action_data,
                "classification": RegionClassification.DETERMINISTIC.value,
                "classification_reason": classification_reason.value,
                "constants": constants,
                "parameters": node_parameters,
                "single_observation_fields": sorted(single_observation_fields),
                "source_sequence": sequence,
                "source_experience_sha256": experience_sha256,
            },
        )
    except ProcedureGraphError:
        return _Failure(
            reason=SynthesisRejectionReason.GRAPH_INVARIANT_VIOLATION,
            detail=f"sequence {sequence}: node payload violates canonical contracts",
        )


def _build_reason_node(
    *,
    regions: RegionClassificationAnalysis,
    sequence: int,
    node_id: str,
    action_name: str,
    classification_reason: RegionClassificationReason,
) -> ProcedureNode | _Failure:
    """Build one reasoning-required REASON node or return a failure signal."""
    try:
        region_id = "unknown-region"
        for region in regions.regions:
            if region.start_sequence <= sequence <= region.end_sequence:
                region_id = region.region_id
                break
        objective = (
            f"Reasoning-required historical action '{action_name}' at sequence {sequence} "
            f"(region {region_id}, reason {classification_reason.value}). Historical evidence "
            "is preserved without any determinism claim; this node describes required "
            "reasoning, it does not execute it."
        )
        spec = ReasonNodeSpec(
            objective=objective,
            output_binding=f"step_{sequence:03d}_reasoning_output",
            input_references=(),
        )
        return spec.to_node(node_id=ProcedureNodeId(node_id), label=f"step {sequence}")
    except (TypeError, ValueError, AttributeError):
        return _Failure(
            reason=SynthesisRejectionReason.GRAPH_INVARIANT_VIOLATION,
            detail=f"sequence {sequence}: node payload violates canonical contracts",
        )


def _build_synthesized_parameters(
    generalization: ParameterGeneralization,
) -> tuple[tuple[SynthesizedParameter, ...], tuple[str, ...]] | _Failure:
    """Build report parameters and single-observation warnings from evidence."""
    synthesized: list[SynthesizedParameter] = []
    warnings: list[str] = []
    for index, group in enumerate(generalization.groups):
        if group.evidence is ParameterVariationEvidence.INSUFFICIENT_OBSERVATIONS:
            warnings.append(
                f"Action '{group.action_name}' field '{group.field_name}': single observation; "
                "value preserved verbatim without constant or parameter claim."
            )
            continue
        if group.evidence is not ParameterVariationEvidence.OBSERVED_VARIATION:
            continue
        try:
            values = tuple(_read_observation_value(item) for item in group.observations)
        except (TypeError, ValueError, AttributeError):
            return _Failure(
                reason=SynthesisRejectionReason.MALFORMED_EVIDENCE,
                detail="parameter observation evidence is not canonical JSON data",
            )
        try:
            synthesized.append(
                SynthesizedParameter(
                    parameter_name=_parameter_name(index, group.action_name, group.field_name),
                    action_name=group.action_name,
                    field_name=group.field_name,
                    evidence=group.evidence,
                    observation_count=len(values),
                    observed_values=values,
                )
            )
        except (TypeError, ValueError):
            return _Failure(
                reason=SynthesisRejectionReason.MALFORMED_EVIDENCE,
                detail="parameter observation evidence is not canonical JSON data",
            )
    return (tuple(synthesized), tuple(warnings))
