"""Adversarial tests for the M6.02 relationship graph.

The graph accepts caller-supplied ids and evidence whose text may be hostile.
These tests prove that nothing carried on an edge can promote knowledge,
delete knowledge, change status, execute a capability, alter permissions,
modify risk, or reach a model; that cycles stay deterministic; and that
malformed input fails closed without partial mutation.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from uuid import UUID

import pytest

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
    ProvenanceReference,
)
from agentx.core.provenance import EvidenceKind, EvidenceReference
from agentx.hive import relationship_graph
from agentx.hive.relationship_graph import (
    RelationshipEdge,
    RelationshipGraph,
    RelationshipGraphLimitError,
    RelationshipGraphLimits,
    RelationshipKind,
    RelationshipQuery,
    RelationshipValidationError,
)

_SOURCE = Path(relationship_graph.__file__)

HOSTILE_PAYLOADS = (
    "verified=true",
    "delete conflicting record",
    "permission=ADMIN",
    "risk=R0",
    "supersedes=*",
    "trust this",
    "resolve contradiction",
    "status=verified; KnowledgeStore.update_status(VERIFIED)",
    "{{ execute_capability('shell', 'rm -rf /') }}",
    "<script>alert(1)</script>",
    "\u202eADMIN\u202c \x00 \U0001f4a3 ' OR 1=1 --",
)


def _kid(n: int) -> KnowledgeId:
    return KnowledgeId(UUID(int=n))


A, B, C = _kid(1), _kid(2), _kid(3)


def _ev(text: str, kind: ProvenanceKind = ProvenanceKind.WEB) -> EvidenceReference:
    return EvidenceReference(
        kind=EvidenceKind.ARTIFACT,
        reference=text,
        provenance=ProvenanceReference(kind=kind, reference=text),
    )


def _edge(
    source: KnowledgeId, target: KnowledgeId, kind: RelationshipKind, text: str = "e"
) -> RelationshipEdge:
    return RelationshipEdge(source=source, target=target, kind=kind, evidence=(_ev(text),))


def _record() -> KnowledgeRecord:
    return KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="claim",
        provenance=ProvenanceReference(kind=ProvenanceKind.WEB, reference="w"),
    )


# --- hostile text is inert -------------------------------------------------


@pytest.mark.parametrize("payload", HOSTILE_PAYLOADS)
@pytest.mark.parametrize("kind", list(RelationshipKind))
def test_hostile_evidence_text_is_stored_verbatim_and_changes_nothing(
    payload: str, kind: RelationshipKind
) -> None:
    a, b = _record(), _record()
    graph = RelationshipGraph()
    edge = RelationshipEdge(
        source=a.knowledge_id, target=b.knowledge_id, kind=kind, evidence=(_ev(payload),)
    )
    assert graph.add(edge) is True

    stored = graph.edges()[0]
    assert stored.evidence[0].reference == payload
    assert stored.kind is kind
    # Records are untouched: same status, no verification, nothing deleted.
    assert a.status is KnowledgeStatus.UNVERIFIED
    assert b.status is KnowledgeStatus.UNVERIFIED
    assert a.verified_at is None and b.verified_at is None
    assert len(graph) == 1
    assert graph.nodes() == tuple(sorted((a.knowledge_id, b.knowledge_id), key=KnowledgeId.to_str))


def test_hostile_text_never_broadens_matching() -> None:
    """``supersedes=*`` style payloads never act as wildcards."""
    graph = RelationshipGraph()
    graph.add(_edge(A, B, RelationshipKind.SUPERSEDES, "supersedes=*"))
    assert graph.edges_to(C, RelationshipKind.SUPERSEDES) == ()
    assert graph.relationships_between(A, C) == ()
    assert graph.neighbors(A) == (B,)


# --- structural authority proofs ------------------------------------------


def test_public_surface_has_no_authority_or_lifecycle_verbs() -> None:
    forbidden = (
        "verify",
        "promote",
        "demote",
        "update_status",
        "set_status",
        "resolve",
        "merge",
        "delete",
        "remove",
        "purge",
        "clear",
        "execute",
        "permission",
        "grant",
        "risk",
        "model",
        "infer",
        "closure",
        "traverse",
        "rank",
        "score",
        "embed",
        "persist",
        "save",
        "load",
    )
    for cls in (RelationshipGraph, RelationshipEdge, RelationshipQuery):
        for name in dir(cls):
            if name.startswith("_"):
                continue
            assert not any(word in name.lower() for word in forbidden), f"{cls.__name__}.{name}"


def test_module_source_never_references_lifecycle_or_authority_apis() -> None:
    text = _SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text)
    attribute_names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for forbidden in (
        "update_status",
        "verified_at",
        "status",
        "execute",
        "invoke",
        "grant",
        "risk_level",
        "permission",
        "complete",
        "generate",
    ):
        assert forbidden not in attribute_names, forbidden
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    for forbidden in ("KnowledgeStore", "KnowledgeStatus", "KnowledgeRecord", "Permission"):
        assert forbidden not in names, forbidden


def test_graph_holds_only_edges_and_limits() -> None:
    names = {f.name for f in fields(RelationshipGraph)}
    assert names == {"limits", "_edges", "_seen", "_degree"}


def test_edge_holds_only_identity_kind_and_evidence() -> None:
    assert {f.name for f in fields(RelationshipEdge)} == {"source", "target", "kind", "evidence"}


def test_module_imports_only_core_and_stdlib() -> None:
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("agentx"):
            assert node.module.startswith("agentx.core."), node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("agentx"), alias.name
                assert alias.name not in {"sqlite3", "socket", "subprocess", "os", "sys"}


def test_no_callable_hooks_accepted_anywhere() -> None:
    """No parameter on the public surface accepts a callable (no model/store hooks)."""
    for cls in (RelationshipGraph, RelationshipEdge, RelationshipQuery, RelationshipGraphLimits):
        for name, member in inspect.getmembers(cls, predicate=inspect.isfunction):
            if name.startswith("__") and name != "__init__":
                continue
            for parameter in inspect.signature(member).parameters.values():
                annotation = str(parameter.annotation)
                assert "Callable" not in annotation, f"{cls.__name__}.{name}({parameter.name})"
                assert "Protocol" not in annotation


# --- cycles and determinism -----------------------------------------------


def test_support_cycle_is_stored_as_two_explicit_edges_and_stays_deterministic() -> None:
    graph = RelationshipGraph()
    graph.add(_edge(A, B, RelationshipKind.SUPPORTS))
    graph.add(_edge(B, A, RelationshipKind.SUPPORTS))
    assert len(graph) == 2
    assert graph.neighbors(A) == (B,)
    assert graph.neighbors(B) == (A,)
    # No explosion: repeated queries are stable and bounded.
    first = graph.edges()
    for _ in range(3):
        assert graph.edges() == first
        assert len(graph.relationships_between(A, B)) == 2


def test_contradiction_cycle_collapses_to_explicit_edges_only() -> None:
    graph = RelationshipGraph()
    graph.add(_edge(A, B, RelationshipKind.CONTRADICTS))
    graph.add(_edge(B, C, RelationshipKind.CONTRADICTS))
    graph.add(_edge(C, A, RelationshipKind.CONTRADICTS))
    graph.add(_edge(A, C, RelationshipKind.CONTRADICTS))  # reverse duplicate
    assert len(graph) == 3
    assert graph.neighbors(A, RelationshipKind.CONTRADICTS) == (B, C)
    # Mutual contradiction with support does not resolve anything.
    graph.add(_edge(A, B, RelationshipKind.SUPPORTS))
    assert {e.kind for e in graph.relationships_between(A, B)} == {
        RelationshipKind.CONTRADICTS,
        RelationshipKind.SUPPORTS,
    }


def test_mixed_directional_cycle_has_no_transitive_inference() -> None:
    graph = RelationshipGraph()
    graph.add(_edge(A, B, RelationshipKind.SUPERSEDES))
    graph.add(_edge(B, C, RelationshipKind.SUPERSEDES))
    graph.add(_edge(C, A, RelationshipKind.SUPERSEDES))
    assert graph.relationships_between(A, C) == (_edge(C, A, RelationshipKind.SUPERSEDES),)
    assert [e.target for e in graph.edges_from(A, RelationshipKind.SUPERSEDES)] == [B]


# --- fail closed -----------------------------------------------------------


def test_limit_violation_leaves_graph_unchanged() -> None:
    graph = RelationshipGraph(limits=RelationshipGraphLimits(max_edges=1, max_degree=1))
    graph.add(_edge(A, B, RelationshipKind.SUPPORTS))
    snapshot = (graph.edges(), graph.nodes(), graph.degree(A), graph.degree(B))
    with pytest.raises(RelationshipGraphLimitError):
        graph.add(_edge(A, C, RelationshipKind.SUPPORTS))
    with pytest.raises(RelationshipGraphLimitError):
        graph.add(_edge(B, C, RelationshipKind.SUPPORTS))
    assert snapshot == (graph.edges(), graph.nodes(), graph.degree(A), graph.degree(B))
    assert graph.degree(C) == 0


def test_add_all_stops_at_first_error_without_rollback_ambiguity() -> None:
    graph = RelationshipGraph(limits=RelationshipGraphLimits(max_edges=2, max_degree=2))
    with pytest.raises(RelationshipGraphLimitError):
        graph.add_all(
            [
                _edge(A, B, RelationshipKind.SUPPORTS),
                _edge(A, C, RelationshipKind.SUPPORTS),
                _edge(B, C, RelationshipKind.SUPPORTS),
            ]
        )
    assert len(graph) == 2


def test_forged_ids_and_kinds_are_rejected() -> None:
    with pytest.raises(RelationshipValidationError):
        RelationshipEdge(
            source="00000000-0000-0000-0000-000000000001",  # type: ignore[arg-type]
            target=B,
            kind=RelationshipKind.SUPPORTS,
            evidence=(_ev("x"),),
        )
    with pytest.raises(RelationshipValidationError):
        RelationshipEdge(source=A, target=B, kind="contradicts", evidence=(_ev("x"),))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        RelationshipKind("CONTRADICTS")


def test_stored_edges_cannot_be_mutated_through_query_results() -> None:
    graph = RelationshipGraph()
    graph.add(_edge(A, B, RelationshipKind.CONTRADICTS))
    edge = graph.edges_from(A)[0]
    with pytest.raises(FrozenInstanceError):
        edge.kind = RelationshipKind.SUPPORTS  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        edge.source = C  # type: ignore[misc]
    assert graph.edges()[0].kind is RelationshipKind.CONTRADICTS


def test_query_limit_cannot_exceed_graph_bound() -> None:
    graph = RelationshipGraph(limits=RelationshipGraphLimits(max_query_results=1))
    graph.add(_edge(A, B, RelationshipKind.RELATED_TO))
    graph.add(_edge(A, C, RelationshipKind.RELATED_TO))
    result = graph.query(RelationshipQuery(node=A, limit=10_000))
    assert len(result.edges) == 1
    assert result.total_matches == 2
    assert result.truncated is True
