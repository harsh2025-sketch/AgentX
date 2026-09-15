"""Relationship-aware Hive queries for contradiction and supersession (AX-120/121).

The canonical durable relationship writer remains ``KnowledgeStore``. This
module adds deterministic read/traversal views only; it never selects a truth
winner, rewrites historical records, promotes status, or grants authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import KnowledgeRecord
from agentx.core.knowledge_integrity import KnowledgeContradiction, KnowledgeSupersession
from agentx.infrastructure.knowledge_store import (
    CorruptKnowledgeRelationshipError,
    KnowledgeNotFoundError,
    KnowledgeStore,
)

__all__ = [
    "ContradictionView",
    "KnowledgeRelationshipQuery",
    "SupersessionDirection",
    "SupersessionView",
]


class SupersessionDirection(StrEnum):
    """Explicit traversal direction; never inferred from query text."""

    REPLACEMENT_TO_HISTORY = "replacement_to_history"
    HISTORY_TO_REPLACEMENT = "history_to_replacement"


@dataclass(frozen=True, slots=True)
class ContradictionView:
    relation: KnowledgeContradiction
    first: KnowledgeRecord
    second: KnowledgeRecord


@dataclass(frozen=True, slots=True)
class SupersessionView:
    relation: KnowledgeSupersession
    replacement: KnowledgeRecord
    historical: KnowledgeRecord


@dataclass(frozen=True, slots=True)
class KnowledgeRelationshipQuery:
    """Deterministic relationship traversal over one canonical KnowledgeStore."""

    store: KnowledgeStore

    def __post_init__(self) -> None:
        if not isinstance(self.store, KnowledgeStore):
            raise TypeError("store must be a KnowledgeStore")

    def _require_record(self, knowledge_id: KnowledgeId) -> KnowledgeRecord:
        if not isinstance(knowledge_id, KnowledgeId):
            raise TypeError("knowledge_id must be a KnowledgeId")
        record = self.store.get(knowledge_id)
        if record is None:
            raise KnowledgeNotFoundError(f"No stored knowledge record {knowledge_id}")
        return record

    def contradiction_views(self, knowledge_id: KnowledgeId) -> tuple[ContradictionView, ...]:
        """Return all contradictions touching ``knowledge_id`` in canonical edge order."""
        self._require_record(knowledge_id)
        result: list[ContradictionView] = []
        for relation in self.store.list_contradictions():
            if knowledge_id not in (relation.first_knowledge_id, relation.second_knowledge_id):
                continue
            first = self.store.get(relation.first_knowledge_id)
            second = self.store.get(relation.second_knowledge_id)
            if first is None or second is None:
                raise CorruptKnowledgeRelationshipError(relationship="contradiction")
            result.append(ContradictionView(relation=relation, first=first, second=second))
        return tuple(result)

    def supersession_views(self, knowledge_id: KnowledgeId) -> tuple[SupersessionView, ...]:
        """Return all incoming/outgoing supersession edges touching one record."""
        self._require_record(knowledge_id)
        result: list[SupersessionView] = []
        for relation in self.store.list_supersessions():
            if knowledge_id not in (
                relation.replacement_knowledge_id,
                relation.superseded_knowledge_id,
            ):
                continue
            replacement = self.store.get(relation.replacement_knowledge_id)
            historical = self.store.get(relation.superseded_knowledge_id)
            if replacement is None or historical is None:
                raise CorruptKnowledgeRelationshipError(relationship="supersession")
            result.append(
                SupersessionView(
                    relation=relation,
                    replacement=replacement,
                    historical=historical,
                )
            )
        return tuple(result)

    def traverse_supersession(
        self,
        knowledge_id: KnowledgeId,
        *,
        direction: SupersessionDirection,
        max_nodes: int = 256,
    ) -> tuple[KnowledgeRecord, ...]:
        """Breadth-first deterministic traversal with an explicit finite bound.

        The start record is included first. C2.08 rejects cycles on write, but
        this read path still tracks visited identities and fails closed on a
        corrupted cyclic graph rather than looping forever.
        """
        start = self._require_record(knowledge_id)
        if not isinstance(direction, SupersessionDirection):
            raise TypeError("direction must be SupersessionDirection")
        if not isinstance(max_nodes, int) or isinstance(max_nodes, bool) or max_nodes <= 0:
            raise ValueError("max_nodes must be a positive integer")

        edges = self.store.list_supersessions()
        queued: list[KnowledgeId] = [knowledge_id]
        visited: set[KnowledgeId] = set()
        ordered: list[KnowledgeRecord] = []

        while queued:
            current = queued.pop(0)
            if current in visited:
                continue
            if len(visited) >= max_nodes:
                raise CorruptKnowledgeRelationshipError(
                    relationship="supersession traversal exceeded bound"
                )
            visited.add(current)
            record = start if current == knowledge_id else self.store.get(current)
            if record is None:
                raise CorruptKnowledgeRelationshipError(relationship="supersession")
            ordered.append(record)

            adjacent: list[KnowledgeId] = []
            for edge in edges:
                if direction is SupersessionDirection.HISTORY_TO_REPLACEMENT:
                    if edge.superseded_knowledge_id == current:
                        adjacent.append(edge.replacement_knowledge_id)
                elif edge.replacement_knowledge_id == current:
                    adjacent.append(edge.superseded_knowledge_id)
            queued.extend(sorted(adjacent, key=lambda item: item.to_str()))

        return tuple(ordered)
