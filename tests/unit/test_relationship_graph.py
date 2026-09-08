"""Unit tests for the M6.02 Hive relationship graph contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import UUID

import pytest

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeStatus,
    KnowledgeType,
    KnowledgeValidationError,
    ProvenanceKind,
    ProvenanceReference,
)
from agentx.core.provenance import EvidenceKind, EvidenceReference
from agentx.hive.relationship_graph import (
    DEFAULT_RELATIONSHIP_GRAPH_LIMITS,
    RelationshipDirection,
    RelationshipEdge,
    RelationshipGraph,
    RelationshipGraphLimitError,
    RelationshipGraphLimits,
    RelationshipKind,
    RelationshipQuery,
    RelationshipResult,
    RelationshipValidationError,
)


def _kid(n: int) -> KnowledgeId:
    return KnowledgeId(UUID(int=n))


A = _kid(1)
B = _kid(2)
C = _kid(3)
D = _kid(4)


def _ev(ref: str = "obs-1", kind: ProvenanceKind = ProvenanceKind.SYSTEM) -> EvidenceReference:
    return EvidenceReference(
        kind=EvidenceKind.OBSERVATION,
        reference=ref,
        provenance=ProvenanceReference(kind=kind, reference="src"),
    )


def _edge(
    source: KnowledgeId,
    target: KnowledgeId,
    kind: RelationshipKind,
    *evidence: EvidenceReference,
) -> RelationshipEdge:
    return RelationshipEdge(source=source, target=target, kind=kind, evidence=evidence or (_ev(),))


# --- vocabulary / directionality -------------------------------------------


def test_vocabulary_is_closed_and_exact() -> None:
    assert {k.value for k in RelationshipKind} == {
        "supports",
        "contradicts",
        "supersedes",
        "derived_from",
        "related_to",
    }


@pytest.mark.parametrize(
    ("kind", "symmetric"),
    [
        (RelationshipKind.SUPPORTS, False),
        (RelationshipKind.CONTRADICTS, True),
        (RelationshipKind.SUPERSEDES, False),
        (RelationshipKind.DERIVED_FROM, False),
        (RelationshipKind.RELATED_TO, True),
    ],
)
def test_directionality_matrix(kind: RelationshipKind, symmetric: bool) -> None:
    assert kind.is_symmetric is symmetric


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ValueError):
        RelationshipKind("implies")
    with pytest.raises(RelationshipValidationError):
        RelationshipEdge(source=A, target=B, kind="supports", evidence=(_ev(),))  # type: ignore[arg-type]


# --- edge construction -----------------------------------------------------


@pytest.mark.parametrize("kind", list(RelationshipKind))
def test_self_edge_rejected_for_every_kind(kind: RelationshipKind) -> None:
    with pytest.raises(RelationshipValidationError, match="self-relationship"):
        _edge(A, A, kind)


def test_directional_edge_preserves_order() -> None:
    edge = _edge(B, A, RelationshipKind.SUPPORTS)
    assert (edge.source, edge.target) == (B, A)
    assert _edge(B, A, RelationshipKind.SUPPORTS) != _edge(A, B, RelationshipKind.SUPPORTS)


@pytest.mark.parametrize("kind", [RelationshipKind.CONTRADICTS, RelationshipKind.RELATED_TO])
def test_symmetric_edge_canonicalizes_deterministically(kind: RelationshipKind) -> None:
    forward = _edge(A, B, kind)
    reverse = _edge(B, A, kind)
    assert forward == reverse
    assert hash(forward) == hash(reverse)
    assert (reverse.source, reverse.target) == (A, B)
    assert A.to_str() < B.to_str()


def test_evidence_required_and_typed() -> None:
    with pytest.raises(RelationshipValidationError, match="not be empty"):
        RelationshipEdge(source=A, target=B, kind=RelationshipKind.SUPPORTS, evidence=())
    with pytest.raises(RelationshipValidationError, match="tuple"):
        RelationshipEdge(source=A, target=B, kind=RelationshipKind.SUPPORTS, evidence=[_ev()])  # type: ignore[arg-type]
    with pytest.raises(RelationshipValidationError):
        RelationshipEdge(source=A, target=B, kind=RelationshipKind.SUPPORTS, evidence=("x",))  # type: ignore[arg-type]


def test_exact_node_identity_required() -> None:
    with pytest.raises(RelationshipValidationError, match="KnowledgeId"):
        RelationshipEdge(
            source=A.to_str(),  # type: ignore[arg-type]
            target=B,
            kind=RelationshipKind.SUPPORTS,
            evidence=(_ev(),),
        )
    with pytest.raises(RelationshipValidationError, match="KnowledgeId"):
        RelationshipEdge(
            source=A,
            target=UUID(int=2),  # type: ignore[arg-type]
            kind=RelationshipKind.SUPPORTS,
            evidence=(_ev(),),
        )


def test_edge_records_are_immutable() -> None:
    edge = _edge(A, B, RelationshipKind.SUPPORTS)
    with pytest.raises(FrozenInstanceError):
        edge.kind = RelationshipKind.CONTRADICTS  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        edge.evidence = ()  # type: ignore[misc]


def test_edge_helpers() -> None:
    edge = _edge(A, B, RelationshipKind.SUPPORTS)
    assert edge.identity == (RelationshipKind.SUPPORTS, A, B)
    assert edge.touches(A) and edge.touches(B) and not edge.touches(C)
    assert edge.other_end(A) == B and edge.other_end(B) == A
    with pytest.raises(RelationshipValidationError):
        edge.other_end(C)


def test_validation_errors_are_knowledge_validation_errors() -> None:
    assert issubclass(RelationshipValidationError, KnowledgeValidationError)
    assert issubclass(RelationshipGraphLimitError, RelationshipValidationError)


# --- add semantics ---------------------------------------------------------


@pytest.mark.parametrize("kind", list(RelationshipKind))
def test_add_each_kind(kind: RelationshipKind) -> None:
    graph = RelationshipGraph()
    assert graph.add(_edge(A, B, kind)) is True
    assert len(graph) == 1
    assert graph.edges()[0].kind is kind


def test_duplicate_identical_edge_is_idempotent() -> None:
    graph = RelationshipGraph()
    assert graph.add(_edge(A, B, RelationshipKind.SUPPORTS)) is True
    assert graph.add(_edge(A, B, RelationshipKind.SUPPORTS)) is False
    assert len(graph) == 1
    assert graph.degree(A) == 1 and graph.degree(B) == 1


def test_reverse_duplicate_of_symmetric_edge_is_same_edge() -> None:
    graph = RelationshipGraph()
    assert graph.add(_edge(A, B, RelationshipKind.CONTRADICTS)) is True
    assert graph.add(_edge(B, A, RelationshipKind.CONTRADICTS)) is False
    assert len(graph) == 1


def test_reverse_of_directional_edge_is_distinct() -> None:
    graph = RelationshipGraph()
    assert graph.add(_edge(A, B, RelationshipKind.SUPPORTS)) is True
    assert graph.add(_edge(B, A, RelationshipKind.SUPPORTS)) is True
    assert len(graph) == 2


def test_same_relationship_with_different_evidence_is_preserved_distinctly() -> None:
    graph = RelationshipGraph()
    first = _edge(A, B, RelationshipKind.SUPPORTS, _ev("obs-1"))
    second = _edge(A, B, RelationshipKind.SUPPORTS, _ev("obs-2"))
    assert graph.add(first) and graph.add(second)
    between = graph.relationships_between(A, B)
    assert between == (first, second)
    # Evidence is never merged or overwritten.
    assert between[0].evidence == (_ev("obs-1"),)
    assert between[1].evidence == (_ev("obs-2"),)


def test_add_rejects_non_edge() -> None:
    graph = RelationshipGraph()
    with pytest.raises(RelationshipValidationError):
        graph.add("A supports B")  # type: ignore[arg-type]


def test_add_all_counts_new_edges() -> None:
    graph = RelationshipGraph()
    e = _edge(A, B, RelationshipKind.SUPPORTS)
    assert graph.add_all([e, e, _edge(B, C, RelationshipKind.SUPPORTS)]) == 2


def test_graph_has_no_remove_or_clear() -> None:
    for name in ("remove", "delete", "clear", "pop", "discard", "merge", "resolve"):
        assert not hasattr(RelationshipGraph, name)


def test_contains_and_iter() -> None:
    graph = RelationshipGraph()
    e = _edge(A, B, RelationshipKind.SUPPORTS)
    graph.add(e)
    assert e in graph
    assert _edge(A, C, RelationshipKind.SUPPORTS) not in graph
    assert "nope" not in graph
    assert list(graph) == [e]


# --- queries ---------------------------------------------------------------


def _populated() -> RelationshipGraph:
    graph = RelationshipGraph()
    graph.add(_edge(A, B, RelationshipKind.SUPPORTS))
    graph.add(_edge(C, A, RelationshipKind.SUPPORTS))
    graph.add(_edge(A, D, RelationshipKind.CONTRADICTS))
    graph.add(_edge(D, B, RelationshipKind.SUPERSEDES))
    return graph


def test_edges_from_and_edges_to_respect_direction() -> None:
    graph = _populated()
    out = graph.edges_from(A)
    assert {e.identity for e in out} == {
        (RelationshipKind.SUPPORTS, A, B),
        (RelationshipKind.CONTRADICTS, A, D),  # symmetric: visible from both ends
    }
    inc = graph.edges_to(A)
    assert {e.identity for e in inc} == {
        (RelationshipKind.SUPPORTS, C, A),
        (RelationshipKind.CONTRADICTS, A, D),
    }
    assert graph.edges_from(D, RelationshipKind.CONTRADICTS) == graph.edges_to(
        D, RelationshipKind.CONTRADICTS
    )
    assert graph.edges_from(A, RelationshipKind.SUPPORTS)[0].target == B
    assert graph.edges_to(A, RelationshipKind.SUPPORTS)[0].source == C
    assert graph.edges_to(A, RelationshipKind.SUPERSEDES) == ()


def test_relationships_between_is_exact_and_bidirectional() -> None:
    graph = _populated()
    assert [e.identity for e in graph.relationships_between(B, A)] == [
        (RelationshipKind.SUPPORTS, A, B)
    ]
    assert graph.relationships_between(A, D, RelationshipKind.CONTRADICTS)[0].kind is (
        RelationshipKind.CONTRADICTS
    )
    assert graph.relationships_between(A, D, RelationshipKind.SUPPORTS) == ()
    assert graph.relationships_between(A, A) == ()
    assert graph.relationships_between(B, C) == ()


def test_neighbors_one_hop_only() -> None:
    graph = _populated()
    assert graph.neighbors(A) == (B, C, D)
    assert graph.neighbors(A, RelationshipKind.SUPPORTS) == (B, C)
    assert graph.neighbors(A, RelationshipKind.CONTRADICTS) == (D,)
    assert graph.neighbors(_kid(99)) == ()


def test_no_transitive_inference() -> None:
    graph = RelationshipGraph()
    graph.add(_edge(A, B, RelationshipKind.SUPPORTS))
    graph.add(_edge(B, C, RelationshipKind.SUPPORTS))
    assert graph.relationships_between(A, C) == ()
    assert C not in graph.neighbors(A)
    assert graph.edges_from(A, RelationshipKind.SUPPORTS)[0].target == B
    assert len(graph) == 2


def test_no_inverse_kind_inference() -> None:
    graph = RelationshipGraph()
    graph.add(_edge(A, B, RelationshipKind.SUPERSEDES))
    assert graph.edges_from(B) == ()
    assert graph.edges_to(A) == ()
    graph.add(_edge(A, B, RelationshipKind.DERIVED_FROM))
    assert {e.kind for e in graph.edges_from(A)} == {
        RelationshipKind.SUPERSEDES,
        RelationshipKind.DERIVED_FROM,
    }
    assert graph.edges_from(B) == ()


def test_query_object_and_result() -> None:
    graph = _populated()
    result = graph.query(RelationshipQuery(node=A, direction=RelationshipDirection.OUTGOING))
    assert isinstance(result, RelationshipResult)
    assert result.total_matches == 2
    assert result.truncated is False
    limited = graph.query(RelationshipQuery(node=A, limit=1))
    assert limited.total_matches == 3
    assert len(limited.edges) == 1
    assert limited.truncated is True


def test_query_validation() -> None:
    with pytest.raises(RelationshipValidationError):
        RelationshipQuery(node="A")  # type: ignore[arg-type]
    with pytest.raises(RelationshipValidationError):
        RelationshipQuery(node=A, kind="supports")  # type: ignore[arg-type]
    with pytest.raises(RelationshipValidationError):
        RelationshipQuery(node=A, direction="out")  # type: ignore[arg-type]
    with pytest.raises(RelationshipValidationError):
        RelationshipQuery(node=A, limit=0)
    with pytest.raises(RelationshipValidationError):
        RelationshipQuery(node=A, limit=True)
    graph = RelationshipGraph()
    with pytest.raises(RelationshipValidationError):
        graph.query("A")  # type: ignore[arg-type]
    with pytest.raises(RelationshipValidationError):
        graph.neighbors(A, "supports")  # type: ignore[arg-type]
    with pytest.raises(RelationshipValidationError):
        graph.degree("A")  # type: ignore[arg-type]


def test_deterministic_ordering_independent_of_insertion() -> None:
    edges = [
        _edge(C, A, RelationshipKind.SUPPORTS),
        _edge(A, B, RelationshipKind.RELATED_TO),
        _edge(A, B, RelationshipKind.SUPPORTS, _ev("z")),
        _edge(A, B, RelationshipKind.SUPPORTS, _ev("a")),
        _edge(D, A, RelationshipKind.CONTRADICTS),
    ]
    forward = RelationshipGraph()
    forward.add_all(edges)
    backward = RelationshipGraph()
    backward.add_all(reversed(edges))
    assert forward.edges() == backward.edges()
    assert forward.nodes() == backward.nodes() == (A, B, C, D)
    assert forward.edges_from(A) == backward.edges_from(A)
    assert forward.neighbors(A) == backward.neighbors(A)
    assert forward.edges() == tuple(sorted(edges, key=RelationshipEdge.sort_key))


def test_edges_returns_snapshot() -> None:
    graph = RelationshipGraph()
    graph.add(_edge(A, B, RelationshipKind.SUPPORTS))
    snapshot = graph.edges()
    graph.add(_edge(A, C, RelationshipKind.SUPPORTS))
    assert len(snapshot) == 1


# --- bounds ----------------------------------------------------------------


def test_default_limits() -> None:
    assert RelationshipGraphLimits() == DEFAULT_RELATIONSHIP_GRAPH_LIMITS
    assert DEFAULT_RELATIONSHIP_GRAPH_LIMITS.max_edges >= 1


def test_limits_validation() -> None:
    for name in ("max_edges", "max_degree", "max_evidence_per_edge", "max_query_results"):
        with pytest.raises(RelationshipValidationError):
            RelationshipGraphLimits(**{name: 0})
        with pytest.raises(RelationshipValidationError):
            RelationshipGraphLimits(**{name: True})
    with pytest.raises(RelationshipValidationError, match="max_degree"):
        RelationshipGraphLimits(max_edges=1, max_degree=2)
    with pytest.raises(RelationshipValidationError):
        RelationshipGraph(limits="big")  # type: ignore[arg-type]


def test_bounded_edge_count_fails_closed() -> None:
    graph = RelationshipGraph(limits=RelationshipGraphLimits(max_edges=2, max_degree=2))
    graph.add(_edge(A, B, RelationshipKind.SUPPORTS))
    graph.add(_edge(A, C, RelationshipKind.SUPPORTS))
    with pytest.raises(RelationshipGraphLimitError, match="edges"):
        graph.add(_edge(B, C, RelationshipKind.SUPPORTS))
    assert len(graph) == 2
    # Identical duplicate is still a harmless no-op at the limit.
    assert graph.add(_edge(A, B, RelationshipKind.SUPPORTS)) is False


def test_bounded_degree_fails_closed_without_partial_mutation() -> None:
    graph = RelationshipGraph(limits=RelationshipGraphLimits(max_degree=1))
    graph.add(_edge(A, B, RelationshipKind.SUPPORTS))
    with pytest.raises(RelationshipGraphLimitError, match="degree"):
        graph.add(_edge(A, C, RelationshipKind.SUPPORTS))
    assert graph.degree(C) == 0
    assert graph.degree(A) == 1
    assert len(graph) == 1


def test_bounded_evidence_per_edge() -> None:
    graph = RelationshipGraph(limits=RelationshipGraphLimits(max_evidence_per_edge=1))
    with pytest.raises(RelationshipGraphLimitError, match="evidence"):
        graph.add(_edge(A, B, RelationshipKind.SUPPORTS, _ev("1"), _ev("2")))
    assert len(graph) == 0


def test_bounded_query_results() -> None:
    graph = RelationshipGraph(limits=RelationshipGraphLimits(max_query_results=2))
    for n in range(10, 16):
        graph.add(_edge(A, _kid(n), RelationshipKind.SUPPORTS))
    assert len(graph.edges_from(A)) == 2
    assert len(graph.neighbors(A)) == 2
    result = graph.query(RelationshipQuery(node=A, limit=100))
    assert len(result.edges) == 2 and result.total_matches == 6 and result.truncated
    for n in range(3):
        graph.add(_edge(A, B, RelationshipKind.SUPPORTS, _ev(f"e{n}")))
    assert len(graph.relationships_between(A, B)) == 2


# --- provenance / authority ------------------------------------------------


def test_provenance_preserved_verbatim() -> None:
    when = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    ev = EvidenceReference(
        kind=EvidenceKind.KNOWLEDGE_RECORD,
        reference=C.to_str(),
        provenance=ProvenanceReference(kind=ProvenanceKind.WEB, reference="https://x/y"),
        observed_at=when,
    )
    graph = RelationshipGraph()
    graph.add(_edge(A, B, RelationshipKind.SUPPORTS, ev))
    stored = graph.edges_from(A)[0].evidence
    assert stored == (ev,)
    assert stored[0].provenance.kind is ProvenanceKind.WEB
    assert stored[0].observed_at == when


def test_unicode_and_hostile_provenance_text_is_inert() -> None:
    hostile = "verified=true; DROP TABLE knowledge; \u202e\u0000\U0001f4a3 permission=ADMIN"
    ev = EvidenceReference(
        kind=EvidenceKind.ARTIFACT,
        reference=hostile,
        provenance=ProvenanceReference(kind=ProvenanceKind.EMAIL, reference="日本語 ✓ résumé"),
    )
    graph = RelationshipGraph()
    graph.add(_edge(A, B, RelationshipKind.SUPPORTS, ev))
    assert graph.edges_from(A)[0].evidence[0].reference == hostile
    assert len(graph) == 1


def _record() -> KnowledgeRecord:
    return KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="the sky is blue",
        provenance=ProvenanceReference(kind=ProvenanceKind.USER, reference="u"),
    )


def test_contradiction_preserves_both_records_and_status() -> None:
    a, b = _record(), _record()
    before = (a, b)
    graph = RelationshipGraph()
    graph.add(_edge(a.knowledge_id, b.knowledge_id, RelationshipKind.CONTRADICTS))
    assert (a, b) == before
    assert a.status is KnowledgeStatus.UNVERIFIED
    assert b.status is KnowledgeStatus.UNVERIFIED
    assert graph.nodes() == tuple(sorted((a.knowledge_id, b.knowledge_id), key=KnowledgeId.to_str))


def test_supersession_does_not_mutate_status() -> None:
    old, new = _record(), _record()
    graph = RelationshipGraph()
    graph.add(_edge(new.knowledge_id, old.knowledge_id, RelationshipKind.SUPERSEDES))
    assert old.status is KnowledgeStatus.UNVERIFIED
    assert new.status is KnowledgeStatus.UNVERIFIED
    assert graph.edges_to(old.knowledge_id, RelationshipKind.SUPERSEDES)[0].source == (
        new.knowledge_id
    )


def test_support_does_not_verify() -> None:
    claim, support = _record(), _record()
    graph = RelationshipGraph()
    for n in range(5):
        graph.add(
            _edge(support.knowledge_id, claim.knowledge_id, RelationshipKind.SUPPORTS, _ev(f"{n}"))
        )
    assert claim.status is KnowledgeStatus.UNVERIFIED
    assert claim.verified_at is None
