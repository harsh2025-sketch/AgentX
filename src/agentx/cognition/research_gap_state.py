"""Campaign-level deterministic research-gap classification (AX-363).

A4.01 intentionally owns only the older SUFFICIENT/GAP contract. This module
composes over those canonical requirements while preserving richer evidence
states needed by research policy: known, partial, stale, contradictory,
unknown, and aggregate research-required.

Staleness and contradiction are explicit caller-supplied Hive evidence by
KnowledgeId. Record content is never parsed for state, authority, or truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.cognition.gap_detector import (
    KnowledgeGapAssessmentRequest,
    KnowledgeGapDetector,
    KnowledgeGapRequirement,
)
from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import KnowledgeRecord

__all__ = [
    "MAX_RESEARCH_GAP_RECORDS",
    "KnowledgeResearchGapResult",
    "KnowledgeResearchGapState",
    "KnowledgeResearchRequirementState",
    "ResearchGapClassifier",
]

MAX_RESEARCH_GAP_RECORDS: Final[int] = 512


class KnowledgeResearchGapState(StrEnum):
    KNOWN = "known"
    PARTIALLY_KNOWN = "partially_known"
    STALE = "stale"
    CONTRADICTORY = "contradictory"
    UNKNOWN = "unknown"
    RESEARCH_REQUIRED = "research_required"


@dataclass(frozen=True, slots=True)
class KnowledgeResearchRequirementState:
    requirement_id: str
    state: KnowledgeResearchGapState
    matching_knowledge_ids: tuple[KnowledgeId, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.requirement_id, str) or not self.requirement_id.strip():
            raise ValueError("requirement_id must be non-empty")
        if not isinstance(self.state, KnowledgeResearchGapState):
            raise TypeError("state must be a KnowledgeResearchGapState")
        if not isinstance(self.matching_knowledge_ids, tuple):
            raise TypeError("matching_knowledge_ids must be a tuple")
        if any(not isinstance(item, KnowledgeId) for item in self.matching_knowledge_ids):
            raise TypeError("matching_knowledge_ids must contain KnowledgeId values")


@dataclass(frozen=True, slots=True)
class KnowledgeResearchGapResult:
    state: KnowledgeResearchGapState
    requirements: tuple[KnowledgeResearchRequirementState, ...]
    research_required: bool

    def __post_init__(self) -> None:
        if not isinstance(self.state, KnowledgeResearchGapState):
            raise TypeError("state must be a KnowledgeResearchGapState")
        if not isinstance(self.requirements, tuple) or not self.requirements:
            raise ValueError("requirements must be a non-empty tuple")
        if any(
            not isinstance(item, KnowledgeResearchRequirementState) for item in self.requirements
        ):
            raise TypeError("requirements must contain KnowledgeResearchRequirementState values")
        if type(self.research_required) is not bool:
            raise TypeError("research_required must be bool")
        if self.research_required is not (
            self.state is KnowledgeResearchGapState.RESEARCH_REQUIRED
        ):
            raise ValueError("research_required must agree with aggregate state")


def _ids(value: object, *, field_name: str) -> frozenset[KnowledgeId]:
    if not isinstance(value, frozenset):
        raise TypeError(f"{field_name} must be a frozenset")
    if len(value) > MAX_RESEARCH_GAP_RECORDS:
        raise ValueError(f"{field_name} exceeds the bounded evidence limit")
    if any(not isinstance(item, KnowledgeId) for item in value):
        raise TypeError(f"{field_name} must contain only KnowledgeId values")
    return value


def _state_for(
    requirement: KnowledgeGapRequirement,
    evidence: tuple[KnowledgeRecord, ...],
    *,
    stale_ids: frozenset[KnowledgeId],
    contradictory_ids: frozenset[KnowledgeId],
) -> KnowledgeResearchRequirementState:
    candidates = tuple(
        record for record in evidence if record.knowledge_id in requirement.acceptable_knowledge_ids
    )
    matching = tuple(
        sorted(
            (record.knowledge_id for record in candidates if requirement.is_satisfied_by(record)),
            key=lambda item: item.to_str(),
        )
    )
    candidate_ids = frozenset(record.knowledge_id for record in candidates)
    relevant_ids = frozenset(matching) if matching else candidate_ids

    if relevant_ids & contradictory_ids:
        state = KnowledgeResearchGapState.CONTRADICTORY
    elif relevant_ids & stale_ids:
        state = KnowledgeResearchGapState.STALE
    elif matching:
        state = KnowledgeResearchGapState.KNOWN
    elif candidates:
        state = KnowledgeResearchGapState.PARTIALLY_KNOWN
    else:
        state = KnowledgeResearchGapState.UNKNOWN
    return KnowledgeResearchRequirementState(
        requirement_id=requirement.requirement_id,
        state=state,
        matching_knowledge_ids=matching,
    )


class ResearchGapClassifier:
    """Classify bounded explicit Hive evidence without performing research."""

    __slots__ = ()

    def classify(
        self,
        request: KnowledgeGapAssessmentRequest,
        *,
        stale_knowledge_ids: frozenset[KnowledgeId] = frozenset(),
        contradictory_knowledge_ids: frozenset[KnowledgeId] = frozenset(),
    ) -> KnowledgeResearchGapResult:
        if not isinstance(request, KnowledgeGapAssessmentRequest):
            raise TypeError("request must be a KnowledgeGapAssessmentRequest")
        if len(request.evidence) > MAX_RESEARCH_GAP_RECORDS:
            raise ValueError("evidence exceeds the bounded research-gap limit")
        stale_ids = _ids(stale_knowledge_ids, field_name="stale_knowledge_ids")
        contradictory_ids = _ids(
            contradictory_knowledge_ids,
            field_name="contradictory_knowledge_ids",
        )

        # Reuse A4.01 validation and canonical requirement/evidence integrity.
        KnowledgeGapDetector().assess(request)
        states = tuple(
            _state_for(
                requirement,
                request.evidence,
                stale_ids=stale_ids,
                contradictory_ids=contradictory_ids,
            )
            for requirement in request.requirements
        )
        if all(item.state is KnowledgeResearchGapState.KNOWN for item in states):
            aggregate = KnowledgeResearchGapState.KNOWN
        else:
            aggregate = KnowledgeResearchGapState.RESEARCH_REQUIRED
        return KnowledgeResearchGapResult(
            state=aggregate,
            requirements=states,
            research_required=aggregate is KnowledgeResearchGapState.RESEARCH_REQUIRED,
        )
