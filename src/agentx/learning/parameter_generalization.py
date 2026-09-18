"""Conservative variation evidence over canonical C3.04 parameter observations.

C3.05 consumes one canonical target C3.04 :class:`ParameterExtraction` plus
optional independently normalized corroborating extractions and reports only
what those provenance-bearing historical observations demonstrate for an exact
structured ``(action.name, field_name)`` pair.

The evidence vocabulary is deliberately narrow:

- fewer than two observations -> insufficient observations;
- two or more canonically equal observations -> observed same value;
- two or more observations containing a canonical value difference -> observed
  variation.

None of those conclusions means a field is semantically a parameter, a
constant, required, optional, environment-dependent, deterministic, safe to
reuse, or suitable for a default. Field/action names and string contents are
inert data. Every contributing C3.04 ``ParameterCandidate`` remains attached as
provenance-bearing observation data; equal values are never deduplicated.

Results are analysis DATA only. This module performs no execution, procedure
synthesis, persistence, environment inspection, model/research call, authority
change, verification, task transition, or Hive mutation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.learning.parameter_extraction import ParameterCandidate, ParameterExtraction

__all__ = [
    "PARAMETER_GENERALIZATION_SCHEMA_VERSION",
    "ParameterGeneralization",
    "ParameterGeneralizationError",
    "ParameterObservationGroup",
    "ParameterVariationEvidence",
    "analyze_parameter_generalization",
]

PARAMETER_GENERALIZATION_SCHEMA_VERSION: Final[int] = 1


class ParameterGeneralizationError(ValueError):
    """Raised when C3.05 analysis data violates its deterministic contract."""


class ParameterVariationEvidence(StrEnum):
    """Observation-only conclusions supported by canonical C3.04 data."""

    INSUFFICIENT_OBSERVATIONS = "INSUFFICIENT_OBSERVATIONS"
    OBSERVED_SAME_VALUE = "OBSERVED_SAME_VALUE"
    OBSERVED_VARIATION = "OBSERVED_VARIATION"


def _canonical_value_tree(value: object) -> object:
    """Return a typed canonical tree used only for deterministic equality.

    Type tags prevent Python's ``True == 1`` and ``1 == 1.0`` numeric coercion
    from silently collapsing distinct canonical JSON structural types. Object
    members are sorted by their explicit string keys so insertion order cannot
    create false variation.
    """
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
            raise ParameterGeneralizationError(
                "non-finite float is not canonical JSON data"
            ) from exc
        return ["float", encoded]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, list):
        return ["array", [_canonical_value_tree(item) for item in value]]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ParameterGeneralizationError("canonical JSON objects require string keys")
        return [
            "object",
            [[key, _canonical_value_tree(value[key])] for key in sorted(value)],
        ]
    raise ParameterGeneralizationError(
        f"unsupported canonical parameter value type {type(value).__name__}"
    )


def _canonical_value_key(candidate: ParameterCandidate) -> str:
    """Return deterministic typed canonical JSON for one C3.04 observation."""
    serialized = candidate.to_dict().get("value")
    return json.dumps(
        _canonical_value_tree(serialized),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=False,
    )


def _group_key(candidate: ParameterCandidate) -> tuple[str, str]:
    """Return the exact structural grouping key without semantic inference."""
    return (candidate.source_candidate.action.name, candidate.field_name)


def _evidence_for(observations: tuple[ParameterCandidate, ...]) -> ParameterVariationEvidence:
    if len(observations) < 2:
        return ParameterVariationEvidence.INSUFFICIENT_OBSERVATIONS
    first_key = _canonical_value_key(observations[0])
    if all(_canonical_value_key(candidate) == first_key for candidate in observations[1:]):
        return ParameterVariationEvidence.OBSERVED_SAME_VALUE
    return ParameterVariationEvidence.OBSERVED_VARIATION


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterObservationGroup:
    """Variation evidence for one exact structured action/field pair."""

    action_name: str
    field_name: str
    observations: tuple[ParameterCandidate, ...]
    evidence: ParameterVariationEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.action_name, str) or not self.action_name:
            raise TypeError("action_name must be a non-empty string")
        if not isinstance(self.field_name, str):
            raise TypeError("field_name must be a string")
        if not isinstance(self.observations, tuple):
            raise TypeError("observations must be a tuple of ParameterCandidate values")
        if not self.observations:
            raise ParameterGeneralizationError("observation groups cannot be empty")
        for index, candidate in enumerate(self.observations):
            if not isinstance(candidate, ParameterCandidate):
                raise TypeError(
                    "observations must contain only ParameterCandidate values; "
                    f"index {index} is {type(candidate).__name__}"
                )
            if _group_key(candidate) != (self.action_name, self.field_name):
                raise ParameterGeneralizationError(
                    "all observations must match the group's exact action/field key"
                )
        if not isinstance(self.evidence, ParameterVariationEvidence):
            raise TypeError("evidence must be a ParameterVariationEvidence")
        expected = _evidence_for(self.observations)
        if self.evidence is not expected:
            raise ParameterGeneralizationError(
                f"evidence {self.evidence.value} does not match observed values; "
                f"expected {expected.value}"
            )

    @property
    def observation_count(self) -> int:
        """Return the number of provenance-bearing observations without deduplication."""
        return len(self.observations)

    @property
    def key(self) -> tuple[str, str]:
        """Return the exact structural grouping key."""
        return (self.action_name, self.field_name)

    def to_dict(self) -> dict[str, object]:
        """Return deterministic JSON-compatible analysis data with full provenance."""
        return {
            "action_name": self.action_name,
            "field_name": self.field_name,
            "evidence": self.evidence.value,
            "observation_count": self.observation_count,
            "observations": [candidate.to_dict() for candidate in self.observations],
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ParameterGeneralization:
    """Immutable deterministic C3.05 analysis for one canonical C3.04 output."""

    source_extraction: ParameterExtraction
    groups: tuple[ParameterObservationGroup, ...]
    supporting_extractions: tuple[ParameterExtraction, ...] = ()
    schema_version: int = PARAMETER_GENERALIZATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.source_extraction, ParameterExtraction):
            raise TypeError(
                "source_extraction must be a ParameterExtraction, "
                f"got {type(self.source_extraction).__name__}"
            )
        if not isinstance(self.groups, tuple):
            raise TypeError("groups must be a tuple of ParameterObservationGroup values")
        if not isinstance(self.corroborating_extractions, tuple):
            raise TypeError(
                "corroborating_extractions must be a tuple of ParameterExtraction values"
            )
        seen_trajectory_ids = {self.source_extraction.source_trajectory_id}
        for index, extraction in enumerate(self.corroborating_extractions):
            if not isinstance(extraction, ParameterExtraction):
                raise TypeError(
                    "corroborating_extractions must contain only ParameterExtraction values; "
                    f"index {index} is {type(extraction).__name__}"
                )
            trajectory_id = extraction.source_trajectory_id
            if trajectory_id in seen_trajectory_ids:
                raise ParameterGeneralizationError(
                    "corroborating_extractions must reference distinct non-target trajectories"
                )
            seen_trajectory_ids.add(trajectory_id)
        if not isinstance(self.supporting_extractions, tuple):
            raise TypeError("supporting_extractions must be a tuple of ParameterExtraction values")
        seen_trajectory_ids = {self.source_extraction.source_trajectory_id}
        for index, supporting in enumerate(self.supporting_extractions):
            if not isinstance(supporting, ParameterExtraction):
                raise TypeError(
                    "supporting_extractions must contain only ParameterExtraction values; "
                    f"index {index} is {type(supporting).__name__}"
                )
            trajectory_id = supporting.source_trajectory_id
            if trajectory_id in seen_trajectory_ids:
                raise ParameterGeneralizationError(
                    "supporting extractions must have distinct source trajectory identities"
                )
            seen_trajectory_ids.add(trajectory_id)
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != PARAMETER_GENERALIZATION_SCHEMA_VERSION:
            raise ParameterGeneralizationError(
                f"unsupported parameter generalization schema version {self.schema_version}; "
                f"supported version is {PARAMETER_GENERALIZATION_SCHEMA_VERSION}"
            )

        expected = _build_groups(
            self.source_extraction.candidates
            + tuple(
                candidate
                for supporting in self.supporting_extractions
                for candidate in supporting.candidates
            )
        )
        if self.groups != expected:
            raise ParameterGeneralizationError(
                "groups must exactly preserve deterministic C3.04 observation grouping and order"
            )

    @property
    def source_trajectory_id(self) -> UUID:
        """Return the canonical trajectory identity through C3.04 without reconstructing it."""
        return self.source_extraction.source_trajectory_id

    def to_dict(self) -> dict[str, object]:
        """Return deterministic JSON-compatible C3.05 analysis data."""
        return {
            "schema_version": self.schema_version,
            "source_trajectory_id": str(self.source_extraction.source_trajectory_id),
            "supporting_trajectory_ids": [
                str(item.source_trajectory_id) for item in self.supporting_extractions
            ],
            "corroborating_source_trajectory_ids": [
                str(extraction.source_trajectory_id)
                for extraction in self.corroborating_extractions
            ],
            "groups": [group.to_dict() for group in self.groups],
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


def _build_groups(
    candidates: tuple[ParameterCandidate, ...],
    *corroborating_candidates: tuple[ParameterCandidate, ...],
) -> tuple[ParameterObservationGroup, ...]:
    ordered_keys: list[tuple[str, str]] = []
    grouped: dict[tuple[str, str], list[ParameterCandidate]] = {}
    for source in (candidates, *corroborating_candidates):
        for candidate in source:
            if not isinstance(candidate, ParameterCandidate):
                raise TypeError(
                    "C3.04 candidates must contain only ParameterCandidate values, "
                    f"got {type(candidate).__name__}"
                )
            key = _group_key(candidate)
            if key not in grouped:
                grouped[key] = []
                ordered_keys.append(key)
            grouped[key].append(candidate)

    result: list[ParameterObservationGroup] = []
    for action_name, field_name in ordered_keys:
        observations = tuple(grouped[(action_name, field_name)])
        result.append(
            ParameterObservationGroup(
                action_name=action_name,
                field_name=field_name,
                observations=observations,
                evidence=_evidence_for(observations),
            )
        )
    return tuple(result)


def analyze_parameter_generalization(
    extraction: ParameterExtraction,
    *,
    corroborating: tuple[ParameterExtraction, ...] = (),
) -> ParameterGeneralization:
    """Produce conservative variation evidence from target and corroborating C3.04 data.

    Groups are created in first-observation order using only exact structured
    action-name/field-name equality. Target observations come first, followed
    by corroborating extractions in caller order. Values and provenance are
    never deduplicated. The target source_extraction remains the derivation anchor.
    """
    if not isinstance(extraction, ParameterExtraction):
        raise TypeError(
            f"extraction must be a ParameterExtraction, got {type(extraction).__name__}"
        )
    if not isinstance(corroborating, tuple):
        raise TypeError("corroborating must be a tuple of ParameterExtraction values")
    for index, item in enumerate(corroborating):
        if not isinstance(item, ParameterExtraction):
            raise TypeError(
                "corroborating must contain only ParameterExtraction values; "
                f"index {index} is {type(item).__name__}"
            )
    return ParameterGeneralization(
        source_extraction=extraction,
        corroborating_extractions=corroborating,
        groups=_build_groups(
            extraction.candidates,
            *tuple(item.candidates for item in corroborating),
        ),
    )
