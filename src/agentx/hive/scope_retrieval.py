"""Fail-closed scope-isolated Hive retrieval (AX-124).

This module is the protected retrieval boundary for callers that operate inside
one applicability scope.  It composes over the existing canonical
``KnowledgeScope`` and ``KnowledgeStorePort`` contracts instead of creating a
second knowledge store or interpreting free-text scope claims.

A scope is DATA, never authority.  Supplying a scope can only narrow which
knowledge is visible; it never grants permission, broadens machine authority,
or changes risk/policy decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
)
from agentx.hive.semantic_memory import KnowledgeStorePort, SEMANTIC_KNOWLEDGE_TYPES

__all__ = [
    "GlobalKnowledgePolicy",
    "ScopedKnowledgeQuery",
    "ScopedKnowledgeRetrieval",
    "ScopedKnowledgeRetrievalError",
]


class ScopedKnowledgeRetrievalError(ValueError):
    """Raised when a protected retrieval request is malformed or unsafe."""


class GlobalKnowledgePolicy(StrEnum):
    """Explicit treatment of globally scoped knowledge.

    Global records are not silently mixed into a scoped query. A caller must
    select ``INCLUDE`` deliberately. This flag still grants no execution
    authority; it only affects data visibility.
    """

    EXCLUDE = "exclude"
    INCLUDE = "include"


@dataclass(frozen=True, slots=True, kw_only=True)
class ScopedKnowledgeQuery:
    """Exact scope boundary plus optional exact structured filters."""

    scope: KnowledgeScope
    global_policy: GlobalKnowledgePolicy = GlobalKnowledgePolicy.EXCLUDE
    knowledge_types: frozenset[KnowledgeType] | None = None
    statuses: frozenset[KnowledgeStatus] | None = None
    provenance_kinds: frozenset[ProvenanceKind] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, KnowledgeScope):
            raise ScopedKnowledgeRetrievalError("scope must be a KnowledgeScope")
        if not self.scope.dimensions:
            raise ScopedKnowledgeRetrievalError(
                "protected scoped retrieval requires a non-empty explicit scope"
            )
        if not isinstance(self.global_policy, GlobalKnowledgePolicy):
            raise ScopedKnowledgeRetrievalError(
                "global_policy must be a GlobalKnowledgePolicy"
            )
        _validate_members(self.knowledge_types, KnowledgeType, "knowledge_types")
        _validate_members(self.statuses, KnowledgeStatus, "statuses")
        _validate_members(self.provenance_kinds, ProvenanceKind, "provenance_kinds")

    def matches(self, record: KnowledgeRecord) -> bool:
        """Return whether ``record`` is visible inside this exact boundary."""
        if not isinstance(record, KnowledgeRecord):
            raise TypeError("record must be a KnowledgeRecord")

        if record.scope != self.scope:
            if not (
                self.global_policy is GlobalKnowledgePolicy.INCLUDE
                and not record.scope.dimensions
            ):
                return False
        if self.knowledge_types is not None and record.knowledge_type not in self.knowledge_types:
            return False
        if self.statuses is not None and record.status not in self.statuses:
            return False
        if self.provenance_kinds is not None:
            provenance = record.provenance
            if provenance is None or provenance.kind not in self.provenance_kinds:
                return False
        return True


@dataclass(frozen=True, slots=True)
class ScopedKnowledgeRetrieval:
    """Deterministic, read-only exact-scope retrieval over canonical storage."""

    store: KnowledgeStorePort

    def __post_init__(self) -> None:
        if not isinstance(self.store, KnowledgeStorePort):
            raise TypeError("store must satisfy KnowledgeStorePort")

    def retrieve(self, query: ScopedKnowledgeQuery) -> tuple[KnowledgeRecord, ...]:
        if not isinstance(query, ScopedKnowledgeQuery):
            raise TypeError("query must be a ScopedKnowledgeQuery")
        return tuple(
            record
            for record in self.store.list_records()
            if record.knowledge_type in SEMANTIC_KNOWLEDGE_TYPES and query.matches(record)
        )


def _validate_members(
    values: frozenset[object] | None,
    member_type: type[StrEnum],
    field_name: str,
) -> None:
    if values is None:
        return
    if not isinstance(values, frozenset):
        raise ScopedKnowledgeRetrievalError(f"{field_name} must be a frozenset or None")
    if not values:
        raise ScopedKnowledgeRetrievalError(f"{field_name} must not be empty")
    for value in values:
        if not isinstance(value, member_type):
            raise ScopedKnowledgeRetrievalError(
                f"{field_name} must contain only {member_type.__name__} values"
            )
