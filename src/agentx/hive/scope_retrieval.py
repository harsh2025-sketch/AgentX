"""Fail-closed scope-isolated Hive retrieval (AX-124).

This module is the protected retrieval boundary for callers that operate inside
one applicability scope. It composes over the existing canonical
``KnowledgeScope`` and a bounded read port on canonical storage instead of creating a
second knowledge store or interpreting free-text scope claims.

A scope is DATA, never authority. Supplying a scope can only narrow which
knowledge is visible; it never grants permission, broadens machine authority,
or changes risk/policy decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
)
from agentx.hive.semantic_memory import SEMANTIC_KNOWLEDGE_TYPES

__all__ = [
    "MAX_SCOPED_KNOWLEDGE_RESULTS",
    "MAX_SCOPED_KNOWLEDGE_SCAN",
    "BoundedKnowledgeScanPort",
    "GlobalKnowledgePolicy",
    "ScopedKnowledgeQuery",
    "ScopedKnowledgeRetrieval",
    "ScopedKnowledgeRetrievalError",
]

MAX_SCOPED_KNOWLEDGE_RESULTS: Final[int] = 256
MAX_SCOPED_KNOWLEDGE_SCAN: Final[int] = 4096


@runtime_checkable
class BoundedKnowledgeScanPort(Protocol):
    """Read-only bounded seam implemented by the canonical KnowledgeStore."""

    def scan_records(self, *, limit: int) -> tuple[KnowledgeRecord, ...]:
        """Return at most limit rows, ordered by creation time and identity."""
        ...


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
    limit: int = MAX_SCOPED_KNOWLEDGE_RESULTS

    def __post_init__(self) -> None:
        if not isinstance(self.scope, KnowledgeScope):
            raise ScopedKnowledgeRetrievalError("scope must be a KnowledgeScope")
        if not self.scope.dimensions:
            raise ScopedKnowledgeRetrievalError(
                "protected scoped retrieval requires a non-empty explicit scope"
            )
        if not isinstance(self.global_policy, GlobalKnowledgePolicy):
            raise ScopedKnowledgeRetrievalError("global_policy must be a GlobalKnowledgePolicy")
        _validate_members(self.knowledge_types, KnowledgeType, "knowledge_types")
        _validate_members(self.statuses, KnowledgeStatus, "statuses")
        _validate_members(self.provenance_kinds, ProvenanceKind, "provenance_kinds")
        if (
            not isinstance(self.limit, int)
            or isinstance(self.limit, bool)
            or not 1 <= self.limit <= MAX_SCOPED_KNOWLEDGE_RESULTS
        ):
            raise ScopedKnowledgeRetrievalError(
                f"limit must be an integer from 1 to {MAX_SCOPED_KNOWLEDGE_RESULTS}"
            )

    def matches(self, record: KnowledgeRecord) -> bool:
        """Return whether ``record`` is visible inside this exact boundary."""
        if not isinstance(record, KnowledgeRecord):
            raise TypeError("record must be a KnowledgeRecord")

        if record.scope != self.scope and not (
            self.global_policy is GlobalKnowledgePolicy.INCLUDE and not record.scope.dimensions
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

    store: BoundedKnowledgeScanPort
    max_scan: int = MAX_SCOPED_KNOWLEDGE_SCAN

    def __post_init__(self) -> None:
        if not isinstance(self.store, BoundedKnowledgeScanPort):
            raise TypeError("store must satisfy BoundedKnowledgeScanPort")
        if type(self.max_scan) is not int or not 1 <= self.max_scan <= MAX_SCOPED_KNOWLEDGE_SCAN:
            raise ScopedKnowledgeRetrievalError("max_scan is outside the bounded range")

    def retrieve(self, query: ScopedKnowledgeQuery) -> tuple[KnowledgeRecord, ...]:
        """Return a bounded deterministic view, preferring exact-scope records.

        Global records can be visible only through the typed opt-in policy and
        are ordered after exact-scope evidence. This prevents storage insertion
        order or UUID generation from changing scope precedence.
        """
        if not isinstance(query, ScopedKnowledgeQuery):
            raise TypeError("query must be a ScopedKnowledgeQuery")
        records = self.store.scan_records(limit=self.max_scan + 1)
        if len(records) > self.max_scan:
            raise ScopedKnowledgeRetrievalError("knowledge scan exceeds the complete bounded view")
        matches = [
            record
            for record in records
            if record.knowledge_type in SEMANTIC_KNOWLEDGE_TYPES and query.matches(record)
        ]
        matches.sort(
            key=lambda record: (
                record.scope != query.scope,
                record.created_at,
                record.knowledge_id.to_str(),
            )
        )
        return tuple(matches[: query.limit])


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
