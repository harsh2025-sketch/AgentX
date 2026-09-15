"""Integration-owned bounded Hive-first research lookup (AX-364).

The composition checks explicitly identified canonical knowledge through the
existing durable retrieval implementation before external research is
considered. It performs no provider call and grants no authority. The module
lives at the package integration layer so ``agentx.cognition`` does not depend
outward on ``agentx.infrastructure``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from agentx.cognition.gap_detector import KnowledgeGapAssessmentRequest, KnowledgeGapRequirement
from agentx.cognition.research_gap_state import (
    KnowledgeResearchGapResult,
    ResearchGapClassifier,
)
from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import KnowledgeRecord, KnowledgeScope
from agentx.infrastructure.knowledge_retrieval import (
    KnowledgeRetrieval,
    KnowledgeRetrievalQuery,
)

__all__ = [
    "MAX_HIVE_FIRST_LOOKUP_IDS",
    "HiveFirstResearchDecision",
    "HiveFirstResearchLookup",
]

MAX_HIVE_FIRST_LOOKUP_IDS: Final[int] = 256


@dataclass(frozen=True, slots=True)
class HiveFirstResearchDecision:
    """Immutable evidence-only decision from one bounded Hive-first lookup."""

    records: tuple[KnowledgeRecord, ...]
    retrieved_knowledge_ids: tuple[KnowledgeId, ...]
    gap: KnowledgeResearchGapResult
    external_research_required: bool

    def __post_init__(self) -> None:
        if not isinstance(self.records, tuple) or any(
            not isinstance(item, KnowledgeRecord) for item in self.records
        ):
            raise TypeError("records must be a tuple of KnowledgeRecord values")
        if not isinstance(self.retrieved_knowledge_ids, tuple) or any(
            not isinstance(item, KnowledgeId) for item in self.retrieved_knowledge_ids
        ):
            raise TypeError("retrieved_knowledge_ids must contain KnowledgeId values")
        if not isinstance(self.gap, KnowledgeResearchGapResult):
            raise TypeError("gap must be a KnowledgeResearchGapResult")
        if type(self.external_research_required) is not bool:
            raise TypeError("external_research_required must be bool")
        if self.external_research_required is not self.gap.research_required:
            raise ValueError("external_research_required must agree with gap evidence")


def _candidate_ids(requirements: tuple[KnowledgeGapRequirement, ...]) -> tuple[KnowledgeId, ...]:
    ids = {item for requirement in requirements for item in requirement.acceptable_knowledge_ids}
    if len(ids) > MAX_HIVE_FIRST_LOOKUP_IDS:
        raise ValueError("Hive-first lookup exceeds the explicit knowledge-id bound")
    return tuple(sorted(ids, key=lambda item: item.to_str()))


class HiveFirstResearchLookup:
    """Consult canonical Hive evidence before external research is permitted.

    This composition surface reads only. A result saying external research is
    required is a descriptive gap classification, not permission to contact a
    provider or execute any action.
    """

    __slots__ = ("_retrieval",)

    def __init__(self, retrieval: KnowledgeRetrieval) -> None:
        if not isinstance(retrieval, KnowledgeRetrieval):
            raise TypeError("retrieval must be a KnowledgeRetrieval")
        self._retrieval = retrieval

    def assess(
        self,
        *,
        requirements: tuple[KnowledgeGapRequirement, ...],
        query: KnowledgeRetrievalQuery,
        stale_knowledge_ids: frozenset[KnowledgeId] = frozenset(),
        contradictory_knowledge_ids: frozenset[KnowledgeId] = frozenset(),
    ) -> HiveFirstResearchDecision:
        if not isinstance(requirements, tuple) or not requirements:
            raise ValueError("requirements must be a non-empty tuple")
        if any(not isinstance(item, KnowledgeGapRequirement) for item in requirements):
            raise TypeError("requirements must contain KnowledgeGapRequirement values")
        if not isinstance(query, KnowledgeRetrievalQuery):
            raise TypeError("query must be a KnowledgeRetrievalQuery")
        if query.scope is None or not isinstance(query.scope, KnowledgeScope) or not query.scope.dimensions:
            raise ValueError("Hive-first research lookup requires an explicit non-empty scope")

        records_by_id: dict[KnowledgeId, KnowledgeRecord] = {}
        ids = _candidate_ids(requirements)
        for knowledge_id in ids:
            point_query = replace(query, knowledge_id=knowledge_id)
            for record in self._retrieval.retrieve(point_query):
                records_by_id[record.knowledge_id] = record
        records = tuple(
            sorted(
                records_by_id.values(),
                key=lambda item: (item.created_at.isoformat(), item.knowledge_id.to_str()),
            )
        )
        gap = ResearchGapClassifier().classify(
            KnowledgeGapAssessmentRequest(requirements=requirements, evidence=records),
            stale_knowledge_ids=stale_knowledge_ids,
            contradictory_knowledge_ids=contradictory_knowledge_ids,
        )
        return HiveFirstResearchDecision(
            records=records,
            retrieved_knowledge_ids=tuple(record.knowledge_id for record in records),
            gap=gap,
            external_research_required=gap.research_required,
        )
