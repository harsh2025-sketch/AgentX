"""Deterministic in-memory relationship graph over canonical Hive knowledge (M6.02).

AgentX Hive is not a bag of independent records. Long-term memory needs
explicit, provenance-bearing relationships between canonical knowledge records:
*supports*, *contradicts*, *supersedes*, *derived from*, *related to*.

This module is the graph DATA boundary only. Everything it stores or returns is
inert data. It grants no authority and it never resolves truth.

Node identity
=============

Graph nodes are canonical :class:`~agentx.core.ids.KnowledgeId` values. There is
deliberately no ``GraphNodeId``: a record already has canonical identity and the
graph must never mint a duplicate one. The graph does not hold, load, or
dereference :class:`~agentx.core.knowledge.KnowledgeRecord` objects — it cannot
tell whether a ``KnowledgeId`` exists in any store, and it never tries to.
Dangling references are the caller's concern (no "dangling resolution magic").

Relationship vocabulary and directionality
==========================================

The vocabulary is a small closed :class:`RelationshipKind` enum. There is no
free-text edge type. Directionality is explicit per kind:

======================  ===========  ============================================
Kind                    Direction    Meaning of ``source -> target``
======================  ===========  ============================================
``SUPPORTS``            directional  ``source`` is evidence in favour of ``target``
``CONTRADICTS``         symmetric    ``source`` and ``target`` cannot both hold
``SUPERSEDES``          directional  ``source`` is the newer claim replacing ``target``
``DERIVED_FROM``        directional  ``source`` was derived from ``target``
``RELATED_TO``          symmetric    an explicit, unranked association
======================  ===========  ============================================

Symmetric kinds are canonicalized deterministically: the endpoint whose
``KnowledgeId.to_str()`` sorts first becomes ``source``. Consequently
``A CONTRADICTS B`` and ``B CONTRADICTS A`` are the *same* edge.

Contradiction and supersession are preserved, never resolved
=============================================================

Adding ``A CONTRADICTS B`` records the relationship and nothing else. It does
not delete either record, choose a winner, change ``KnowledgeStatus``, promote,
demote, rewrite, or merge anything. Adding ``A SUPERSEDES B`` is descriptive
graph evidence; it does not set ``B`` to ``SUPERSEDED`` and never calls a store.
Lifecycle coordination is a separate C2.08 policy concern.

Invariants
==========

* **No self-edges.** Every kind is semantically invalid from a record to itself.
* **Provenance is mandatory.** Every edge carries at least one canonical
  :class:`~agentx.core.provenance.EvidenceReference`. Relationship existence is
  never truth; the evidence says only *why someone asserted* the relationship.
* **Append-oriented, lossless.** An identical edge (same identity and identical
  evidence) is idempotent. The same relationship with *different* evidence is
  preserved as a distinct edge — evidence is never silently merged or
  overwritten. There is no remove operation.
* **Bounded.** Total edges, per-node degree, evidence per edge, and query result
  counts are all hard-capped by :class:`RelationshipGraphLimits`.
* **No inference.** No transitive closure, no symmetry beyond the declared
  kinds, no inverse-kind derivation. Only explicitly added edges exist.
* **Deterministic.** Every query result is sorted by a total order independent
  of insertion order.

Non-goals (owned elsewhere): embeddings, fuzzy search, ranking, confidence
scores, contradiction resolution, lifecycle transitions, persistence,
migrations, consolidation, ontology, GraphRAG.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import KnowledgeValidationError
from agentx.core.provenance import EvidenceReference

__all__ = [
    "DEFAULT_RELATIONSHIP_GRAPH_LIMITS",
    "RelationshipDirection",
    "RelationshipEdge",
    "RelationshipGraph",
    "RelationshipGraphLimitError",
    "RelationshipGraphLimits",
    "RelationshipKind",
    "RelationshipQuery",
    "RelationshipResult",
    "RelationshipValidationError",
]


class RelationshipValidationError(KnowledgeValidationError):
    """Raised when an edge, query, or limit violates the relationship contract."""


class RelationshipGraphLimitError(RelationshipValidationError):
    """Raised when a mutation would exceed a configured hard bound (fail closed)."""


class RelationshipKind(StrEnum):
    """Closed vocabulary of relationships between two knowledge records."""

    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"
    DERIVED_FROM = "derived_from"
    RELATED_TO = "related_to"

    @property
    def is_symmetric(self) -> bool:
        """``True`` when ``source``/``target`` order carries no meaning."""
        return self in _SYMMETRIC_KINDS


_SYMMETRIC_KINDS: Final[frozenset[RelationshipKind]] = frozenset(
    {RelationshipKind.CONTRADICTS, RelationshipKind.RELATED_TO}
)


class RelationshipDirection(StrEnum):
    """Which end of an edge a queried node must occupy.

    For symmetric kinds the node may be either endpoint regardless of
    direction, because canonical ordering is not semantic.
    """

    OUTGOING = "outgoing"
    INCOMING = "incoming"
    ANY = "any"


def _validate_knowledge_id(value: object, *, field_name: str) -> KnowledgeId:
    if not isinstance(value, KnowledgeId):
        raise RelationshipValidationError(f"{field_name} must be a KnowledgeId")
    return value


def _validate_positive_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RelationshipValidationError(f"{field_name} must be an int")
    if value < 1:
        raise RelationshipValidationError(f"{field_name} must be >= 1")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class RelationshipGraphLimits:
    """Hard bounds for one :class:`RelationshipGraph`. All values are ``>= 1``."""

    max_edges: int = 10_000
    max_degree: int = 256
    max_evidence_per_edge: int = 32
    max_query_results: int = 256

    def __post_init__(self) -> None:
        _validate_positive_int(self.max_edges, field_name="max_edges")
        _validate_positive_int(self.max_degree, field_name="max_degree")
        _validate_positive_int(self.max_evidence_per_edge, field_name="max_evidence_per_edge")
        _validate_positive_int(self.max_query_results, field_name="max_query_results")
        if self.max_degree > self.max_edges:
            raise RelationshipValidationError("max_degree must not exceed max_edges")


DEFAULT_RELATIONSHIP_GRAPH_LIMITS: Final = RelationshipGraphLimits()


@dataclass(frozen=True, slots=True, kw_only=True)
class RelationshipEdge:
    """One immutable, provenance-bearing relationship between two records.

    ``source``/``target`` are canonical ``KnowledgeId`` values. For symmetric
    kinds the pair is canonicalized on construction so equal relationships are
    equal values. ``evidence`` is a non-empty tuple of canonical
    :class:`EvidenceReference` values explaining why the relationship was
    asserted; it is inert data, never fetched, and never a trust signal.
    """

    source: KnowledgeId
    target: KnowledgeId
    kind: RelationshipKind
    evidence: tuple[EvidenceReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RelationshipKind):
            raise RelationshipValidationError("kind must be a RelationshipKind")
        source = _validate_knowledge_id(self.source, field_name="source")
        target = _validate_knowledge_id(self.target, field_name="target")
        if source == target:
            raise RelationshipValidationError(
                f"self-relationship is invalid: {self.kind.value} from a record to itself"
            )
        if not isinstance(self.evidence, tuple):
            raise RelationshipValidationError("evidence must be a tuple")
        if not self.evidence:
            raise RelationshipValidationError("evidence must not be empty")
        for item in self.evidence:
            if not isinstance(item, EvidenceReference):
                raise RelationshipValidationError(
                    "evidence must contain only EvidenceReference values"
                )
        if self.kind.is_symmetric and target.to_str() < source.to_str():
            object.__setattr__(self, "source", target)
            object.__setattr__(self, "target", source)

    @property
    def identity(self) -> tuple[RelationshipKind, KnowledgeId, KnowledgeId]:
        """Relationship identity, excluding evidence: ``(kind, source, target)``."""
        return (self.kind, self.source, self.target)

    def touches(self, node: KnowledgeId) -> bool:
        """``True`` when ``node`` is either endpoint."""
        return node == self.source or node == self.target

    def other_end(self, node: KnowledgeId) -> KnowledgeId:
        """Return the endpoint that is not ``node``.

        Raises :class:`RelationshipValidationError` if ``node`` is not an endpoint.
        """
        if node == self.source:
            return self.target
        if node == self.target:
            return self.source
        raise RelationshipValidationError("node is not an endpoint of this edge")

    def sort_key(self) -> tuple[str, str, str, tuple[str, ...]]:
        """Total, insertion-independent ordering key."""
        return (
            self.kind.value,
            self.source.to_str(),
            self.target.to_str(),
            tuple(_evidence_key(item) for item in self.evidence),
        )


def _evidence_key(reference: EvidenceReference) -> str:
    observed = reference.observed_at
    return "|".join(
        (
            reference.kind.value,
            reference.provenance.kind.value,
            reference.provenance.reference,
            reference.reference,
            "" if observed is None else observed.isoformat(),
        )
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class RelationshipQuery:
    """Exact, bounded direct-neighbour query for one node.

    ``limit`` is clamped by the graph's ``max_query_results`` at execution time.
    No fuzzy matching, ranking, or traversal beyond one hop exists.
    """

    node: KnowledgeId
    kind: RelationshipKind | None = None
    direction: RelationshipDirection = RelationshipDirection.ANY
    limit: int | None = None

    def __post_init__(self) -> None:
        _validate_knowledge_id(self.node, field_name="node")
        if self.kind is not None and not isinstance(self.kind, RelationshipKind):
            raise RelationshipValidationError("kind must be a RelationshipKind or None")
        if not isinstance(self.direction, RelationshipDirection):
            raise RelationshipValidationError("direction must be a RelationshipDirection")
        if self.limit is not None:
            _validate_positive_int(self.limit, field_name="limit")


@dataclass(frozen=True, slots=True, kw_only=True)
class RelationshipResult:
    """Deterministic result of a :class:`RelationshipQuery`.

    ``edges`` is sorted by :meth:`RelationshipEdge.sort_key` and never longer
    than the effective limit. ``total_matches`` is the untruncated count;
    ``truncated`` is ``True`` when ``len(edges) < total_matches``.
    """

    query: RelationshipQuery
    edges: tuple[RelationshipEdge, ...]
    total_matches: int

    @property
    def truncated(self) -> bool:
        return len(self.edges) < self.total_matches


def _matches(edge: RelationshipEdge, query: RelationshipQuery) -> bool:
    if query.kind is not None and edge.kind is not query.kind:
        return False
    if edge.kind.is_symmetric or query.direction is RelationshipDirection.ANY:
        return edge.touches(query.node)
    if query.direction is RelationshipDirection.OUTGOING:
        return edge.source == query.node
    return edge.target == query.node


@dataclass(slots=True)
class RelationshipGraph:
    """Bounded, append-only, deterministic in-memory relationship graph.

    The graph holds edges only. It holds no store, no records, no clock, no
    model, and performs no I/O. Its only mutation is :meth:`add`.
    """

    limits: RelationshipGraphLimits = DEFAULT_RELATIONSHIP_GRAPH_LIMITS
    _edges: list[RelationshipEdge] = field(default_factory=list, init=False, repr=False)
    _seen: set[RelationshipEdge] = field(default_factory=set, init=False, repr=False)
    _degree: dict[KnowledgeId, int] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.limits, RelationshipGraphLimits):
            raise RelationshipValidationError("limits must be a RelationshipGraphLimits")

    # -- mutation -----------------------------------------------------------

    def add(self, edge: RelationshipEdge) -> bool:
        """Append ``edge``; return ``True`` if it was new, ``False`` if identical existed.

        An edge equal to an existing one (same kind, canonical endpoints, and
        identical evidence tuple) is a no-op. The same relationship with
        different evidence is stored as an additional distinct edge. Bounds are
        checked before any state changes; on violation nothing is mutated.
        """
        if not isinstance(edge, RelationshipEdge):
            raise RelationshipValidationError("edge must be a RelationshipEdge")
        if edge in self._seen:
            return False
        if len(edge.evidence) > self.limits.max_evidence_per_edge:
            raise RelationshipGraphLimitError(
                f"edge carries {len(edge.evidence)} evidence references; "
                f"limit is {self.limits.max_evidence_per_edge}"
            )
        if len(self._edges) >= self.limits.max_edges:
            raise RelationshipGraphLimitError(
                f"graph already holds {len(self._edges)} edges; limit is {self.limits.max_edges}"
            )
        for node in (edge.source, edge.target):
            if self._degree.get(node, 0) >= self.limits.max_degree:
                raise RelationshipGraphLimitError(
                    f"node {node.to_str()} already has degree {self._degree[node]}; "
                    f"limit is {self.limits.max_degree}"
                )
        self._edges.append(edge)
        self._seen.add(edge)
        self._degree[edge.source] = self._degree.get(edge.source, 0) + 1
        self._degree[edge.target] = self._degree.get(edge.target, 0) + 1
        return True

    def add_all(self, edges: Iterable[RelationshipEdge]) -> int:
        """Add each edge in order; return how many were new. Stops at the first error."""
        added = 0
        for edge in edges:
            if self.add(edge):
                added += 1
        return added

    # -- inspection ---------------------------------------------------------

    def __len__(self) -> int:
        return len(self._edges)

    def __iter__(self) -> Iterator[RelationshipEdge]:
        return iter(self.edges())

    def __contains__(self, edge: object) -> bool:
        return isinstance(edge, RelationshipEdge) and edge in self._seen

    def edges(self) -> tuple[RelationshipEdge, ...]:
        """All edges in deterministic order (a snapshot; mutation-safe)."""
        return tuple(sorted(self._edges, key=RelationshipEdge.sort_key))

    def nodes(self) -> tuple[KnowledgeId, ...]:
        """All node ids that occur on at least one edge, deterministically ordered."""
        return tuple(sorted(self._degree, key=KnowledgeId.to_str))

    def degree(self, node: KnowledgeId) -> int:
        """Number of edges touching ``node`` (0 for unknown nodes)."""
        _validate_knowledge_id(node, field_name="node")
        return self._degree.get(node, 0)

    # -- exact queries ------------------------------------------------------

    def query(self, query: RelationshipQuery) -> RelationshipResult:
        """Execute a bounded direct-neighbour query deterministically."""
        if not isinstance(query, RelationshipQuery):
            raise RelationshipValidationError("query must be a RelationshipQuery")
        limit = self.limits.max_query_results
        if query.limit is not None:
            limit = min(limit, query.limit)
        matched = sorted(
            (edge for edge in self._edges if _matches(edge, query)),
            key=RelationshipEdge.sort_key,
        )
        return RelationshipResult(
            query=query,
            edges=tuple(matched[:limit]),
            total_matches=len(matched),
        )

    def edges_from(
        self,
        node: KnowledgeId,
        kind: RelationshipKind | None = None,
    ) -> tuple[RelationshipEdge, ...]:
        """Edges where ``node`` is the source (either endpoint for symmetric kinds).

        Bounded by ``max_query_results``; use :meth:`query` to observe truncation.
        """
        return self.query(
            RelationshipQuery(node=node, kind=kind, direction=RelationshipDirection.OUTGOING)
        ).edges

    def edges_to(
        self,
        node: KnowledgeId,
        kind: RelationshipKind | None = None,
    ) -> tuple[RelationshipEdge, ...]:
        """Edges where ``node`` is the target (either endpoint for symmetric kinds).

        Bounded by ``max_query_results``; use :meth:`query` to observe truncation.
        """
        return self.query(
            RelationshipQuery(node=node, kind=kind, direction=RelationshipDirection.INCOMING)
        ).edges

    def relationships_between(
        self,
        a: KnowledgeId,
        b: KnowledgeId,
        kind: RelationshipKind | None = None,
    ) -> tuple[RelationshipEdge, ...]:
        """All explicit edges whose endpoints are exactly ``{a, b}`` in either direction.

        Bounded by ``max_query_results``. Nothing is inferred: if no edge was
        added between ``a`` and ``b``, the result is empty regardless of any
        path that might connect them.
        """
        _validate_knowledge_id(a, field_name="a")
        _validate_knowledge_id(b, field_name="b")
        if kind is not None and not isinstance(kind, RelationshipKind):
            raise RelationshipValidationError("kind must be a RelationshipKind or None")
        if a == b:
            # Self-edges cannot exist, so there is nothing between a node and itself.
            return ()
        matched = sorted(
            (
                edge
                for edge in self._edges
                if (kind is None or edge.kind is kind) and edge.touches(a) and edge.touches(b)
            ),
            key=RelationshipEdge.sort_key,
        )
        return tuple(matched[: self.limits.max_query_results])

    def neighbors(
        self,
        node: KnowledgeId,
        kind: RelationshipKind | None = None,
    ) -> tuple[KnowledgeId, ...]:
        """Distinct ids directly connected to ``node`` (one hop), sorted, bounded.

        Bounded by ``max_query_results`` distinct ids. No transitive closure.
        """
        _validate_knowledge_id(node, field_name="node")
        if kind is not None and not isinstance(kind, RelationshipKind):
            raise RelationshipValidationError("kind must be a RelationshipKind or None")
        found: set[KnowledgeId] = set()
        for edge in self._edges:
            if (kind is None or edge.kind is kind) and edge.touches(node):
                found.add(edge.other_end(node))
        ordered = sorted(found, key=KnowledgeId.to_str)
        return tuple(ordered[: self.limits.max_query_results])
