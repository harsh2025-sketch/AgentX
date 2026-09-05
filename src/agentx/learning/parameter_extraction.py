"""Deterministic extraction of explicit parameter candidates from retained actions.

C3.04 consumes canonical C3.03 :class:`IrrelevantActionAnalysis` data and emits
inert parameter candidates from explicit top-level fields already present in a
retained action's canonical ``ActionPayload.data`` object.

This stage is deliberately narrow. A candidate means only that one retained
historical action explicitly recorded a structured field/value pair that a
later compiler stage may choose to analyze. It does not mean the value should
be a parameter, has a particular semantic meaning, is safe to reuse, is a
constant/default, is environment-independent, or grants any authority.

No values are inferred from action names, observations, verification detail,
errors, neighboring actions, external knowledge, environment variables,
filesystem state, models, embeddings, or natural language. Nested arrays and
objects remain single inert field values; C3.04 does not invent nested
parameter boundaries. Equal values from distinct source actions remain distinct
provenance-bearing candidates.

Results are analysis DATA only. Nothing here executes capabilities, creates or
activates procedures/skills, invokes models/research, grants permissions or
authority, changes risk/resources/emergency state, fabricates verification,
transitions tasks, mutates Hive, or persists data.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from agentx.core.events import EventValidationError, JsonValue
from agentx.learning.causal_actions import ExtractedActionCandidate
from agentx.learning.irrelevant_actions import (
    ActionDisposition,
    ActionEliminationDecision,
    IrrelevantActionAnalysis,
)
from agentx.learning.trajectory import NormalizedTrajectoryStep

__all__ = [
    "PARAMETER_EXTRACTION_SCHEMA_VERSION",
    "ParameterCandidate",
    "ParameterExtraction",
    "ParameterExtractionError",
    "extract_parameter_candidates",
]

PARAMETER_EXTRACTION_SCHEMA_VERSION: Final[int] = 1


class ParameterExtractionError(ValueError):
    """Raised when C3.04 analysis data violates its deterministic contract."""


def _canonical_json_value(decision: ActionEliminationDecision, field_name: str) -> JsonValue:
    """Read one source field through the canonical ActionPayload serializer.

    The C3.04 input should already be canonical. Reusing ``ActionPayload.to_dict``
    still makes this boundary fail closed if a source object has been corrupted
    after construction with an unsupported live Python value.
    """
    action = decision.source_candidate.action
    data = action.data
    if not isinstance(data, Mapping):
        raise ParameterExtractionError("source action data must remain a canonical JSON object")
    if field_name not in data:
        raise ParameterExtractionError("parameter field must exist in source action data")
    try:
        serialized = action.to_dict()["data"]
    except (EventValidationError, TypeError) as exc:
        raise ParameterExtractionError(
            "source action data is not canonical JSON-compatible data"
        ) from exc
    if not isinstance(serialized, dict):  # pragma: no cover - guaranteed by ActionPayload
        raise ParameterExtractionError("source action serialization did not produce a JSON object")
    return serialized[field_name]


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterCandidate:
    """One explicit structured field candidate linked to its canonical history.

    Only ``source_decision`` and ``field_name`` are stored. The exact action
    candidate, normalized step, and value are reached through that canonical
    C3.03/C3.02/C3.01 source chain rather than duplicated into a competing
    trajectory/action schema.
    """

    source_decision: ActionEliminationDecision
    field_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_decision, ActionEliminationDecision):
            raise TypeError(
                "source_decision must be an ActionEliminationDecision, "
                f"got {type(self.source_decision).__name__}"
            )
        if self.source_decision.disposition is not ActionDisposition.RETAIN:
            raise ParameterExtractionError("parameter candidates require a retained C3.03 decision")
        if not isinstance(self.field_name, str):
            raise TypeError(f"field_name must be a string, got {type(self.field_name).__name__}")
        _canonical_json_value(self.source_decision, self.field_name)

    @property
    def source_trajectory_id(self) -> UUID:
        """Return the exact canonical C3.01 trajectory identity."""
        return self.source_decision.source_trajectory_id

    @property
    def source_candidate(self) -> ExtractedActionCandidate:
        """Return the exact unchanged C3.02 action candidate."""
        return self.source_decision.source_candidate

    @property
    def source_step(self) -> NormalizedTrajectoryStep:
        """Return the exact unchanged C3.01 normalized step."""
        return self.source_decision.source_step

    @property
    def source_sequence(self) -> int:
        """Return the canonical source action sequence."""
        return self.source_decision.source_sequence

    @property
    def source_experience_sha256(self) -> str:
        """Return the canonical source-experience fingerprint."""
        return self.source_decision.source_experience_sha256

    @property
    def value(self) -> object:
        """Return the exact immutable structured value from canonical action data."""
        data = self.source_candidate.action.data
        if not isinstance(data, Mapping):  # defensive against post-construction corruption
            raise ParameterExtractionError("source action data must remain a canonical JSON object")
        try:
            return data[self.field_name]
        except KeyError as exc:  # defensive against post-construction corruption
            raise ParameterExtractionError(
                "parameter field no longer exists in source action data"
            ) from exc

    @property
    def reference(self) -> tuple[UUID, int, str]:
        """Return a deterministic provenance reference for this source field."""
        return (self.source_trajectory_id, self.source_sequence, self.field_name)

    def to_dict(self) -> dict[str, object]:
        """Return deterministic JSON-compatible candidate analysis data."""
        return {
            "source_trajectory_id": str(self.source_trajectory_id),
            "source_sequence": self.source_sequence,
            "source_experience_sha256": self.source_experience_sha256,
            "field_name": self.field_name,
            "value": _canonical_json_value(self.source_decision, self.field_name),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterExtraction:
    """Immutable deterministic C3.04 output for one C3.03 analysis result."""

    source_analysis: IrrelevantActionAnalysis
    candidates: tuple[ParameterCandidate, ...]
    schema_version: int = PARAMETER_EXTRACTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.source_analysis, IrrelevantActionAnalysis):
            raise TypeError(
                "source_analysis must be an IrrelevantActionAnalysis, "
                f"got {type(self.source_analysis).__name__}"
            )
        if not isinstance(self.candidates, tuple):
            raise TypeError("candidates must be a tuple of ParameterCandidate values")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != PARAMETER_EXTRACTION_SCHEMA_VERSION:
            raise ParameterExtractionError(
                f"unsupported parameter extraction schema version {self.schema_version}; "
                f"supported version is {PARAMETER_EXTRACTION_SCHEMA_VERSION}"
            )

        expected = tuple(
            (decision, field_name)
            for decision in self.source_analysis.decisions
            if decision.disposition is ActionDisposition.RETAIN
            for field_name in sorted(decision.source_candidate.action.data)
        )
        if len(self.candidates) != len(expected):
            raise ParameterExtractionError(
                "candidates must contain exactly one entry for every explicit field "
                "of every retained action"
            )
        expected_pairs = zip(self.candidates, expected, strict=True)
        for index, (candidate, expected_source) in enumerate(expected_pairs):
            if not isinstance(candidate, ParameterCandidate):
                raise TypeError(
                    "candidates must contain only ParameterCandidate values; "
                    f"index {index} is {type(candidate).__name__}"
                )
            expected_decision, expected_field = expected_source
            if (
                candidate.source_decision != expected_decision
                or candidate.field_name != expected_field
            ):
                raise ParameterExtractionError(
                    "candidate ordering/content must preserve C3.03 source order "
                    "and canonical field order"
                )

    @property
    def source_trajectory_id(self) -> UUID:
        """Return the exact canonical C3.01 trajectory identity."""
        return self.source_analysis.source_trajectory_id

    def to_dict(self) -> dict[str, object]:
        """Return deterministic JSON-compatible C3.04 analysis data."""
        return {
            "schema_version": self.schema_version,
            "source_trajectory_id": str(self.source_trajectory_id),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
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


def extract_parameter_candidates(analysis: IrrelevantActionAnalysis) -> ParameterExtraction:
    """Extract explicit top-level action-data fields from retained C3.03 decisions.

    Exact deterministic rule for schema version 1:

    1. Require canonical C3.03 ``IrrelevantActionAnalysis`` input.
    2. Iterate decisions in existing normalized source order.
    3. Skip decisions whose canonical C3.03 disposition is ``ELIMINATE``.
    4. For each ``RETAIN`` decision, iterate only explicit top-level
       ``ActionPayload.data`` keys in lexicographic key order.
    5. Emit exactly one candidate for each such field, preserving the exact
       C3.03 decision and therefore its C3.02 candidate/C3.01 normalized step.
    6. Do not inspect or infer from any other source.

    Empty structured action data legitimately yields no candidate. Nested
    objects/arrays remain one inert value attached to their explicit top-level
    field; no recursive parameterization or semantic naming occurs here.
    """
    if not isinstance(analysis, IrrelevantActionAnalysis):
        raise TypeError(
            f"analysis must be an IrrelevantActionAnalysis, got {type(analysis).__name__}"
        )

    candidates: list[ParameterCandidate] = []
    for decision in analysis.decisions:
        if decision.disposition is ActionDisposition.ELIMINATE:
            continue
        if decision.disposition is not ActionDisposition.RETAIN:  # defensive closed vocabulary
            raise ParameterExtractionError("unsupported C3.03 action disposition")
        data = decision.source_candidate.action.data
        if not isinstance(data, Mapping):
            raise ParameterExtractionError("source action data must remain a canonical JSON object")
        for field_name in sorted(data):
            candidates.append(
                ParameterCandidate(
                    source_decision=decision,
                    field_name=field_name,
                )
            )

    return ParameterExtraction(source_analysis=analysis, candidates=tuple(candidates))
