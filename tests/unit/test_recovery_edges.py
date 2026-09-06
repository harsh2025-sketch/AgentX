"""Tests for the canonical A3.07 error/recovery control-flow edge contract.

These tests prove that explicit recovery/error control flow is representable
in the canonical A3.01 Procedure Graph as inert DATA and nothing else:

    - a normal graph without recovery stays exactly as valid as before;
    - one or many typed recovery routes validate, serialize, and round-trip
      deterministically, independent of author-supplied ordering;
    - dangling recovery targets, nonexistent sources, invalid node
      references, duplicates, ambiguity, and malformed serialized forms all
      fail closed;
    - recovery routes compose with ROLLBACK, SUBPROCEDURE, END, and the A3.06
      preconditions/postconditions contract without invoking or satisfying
      any of them;
    - cycles follow the existing A3.01 graph rules while a self-loop is
      rejected as the unbounded retry it is;
    - a recovery route can never imply success or permission.

No A3.08 interpreter semantics are exercised anywhere here.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from agentx.core.failure_taxonomy import FailureCategory
from agentx.core.ids import ProcedureId
from agentx.core.provenance import EvidenceKind
from agentx.procedures.condition_evaluation import (
    ConditionEvaluationStatus,
    EvidenceFacts,
    evaluate_condition,
)
from agentx.procedures.conditions import (
    ConditionId,
    NodeConditions,
    ProcedureCondition,
    ProcedureConditions,
)
from agentx.procedures.end import EndNodeSpec
from agentx.procedures.graph import (
    CURRENT_GRAPH_SCHEMA_VERSION,
    ProcedureEdge,
    ProcedureEdgeKind,
    ProcedureGraph,
    ProcedureGraphValidationError,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.procedures.recovery import (
    CURRENT_RECOVERY_CONTRACT_VERSION,
    ProcedureRecoveryEdges,
    RecoveryContractError,
    RecoveryEdge,
)
from agentx.procedures.rollback import RollbackNodeSpec, RollbackScope, RollbackScopeKind
from agentx.procedures.subprocedure import SubprocedureNodeSpec

_ACTION = ProcedureNodeId("n_action")
_OBSERVE = ProcedureNodeId("n_observe")
_VERIFY = ProcedureNodeId("n_verify")
_ROLLBACK = ProcedureNodeId("n_rollback")
_SUBPROCEDURE = ProcedureNodeId("n_subprocedure")
_END = ProcedureNodeId("n_end")
_GONE = ProcedureNodeId("n_missing")

_PROCEDURE_ID = ProcedureId(UUID("33333333-3333-4333-8333-333333333333"))

#: The canonical closed failure vocabulary A3.07 composes (never duplicates).
_ALL_CATEGORIES = tuple(FailureCategory)


# ---------------------------------------------------------------------------
# Fixtures: graphs and documents.
# ---------------------------------------------------------------------------


def _rollback_spec() -> RollbackNodeSpec:
    return RollbackNodeSpec(scope=RollbackScope(kind=RollbackScopeKind.PROCEDURE_EXECUTION))


def _subprocedure_spec() -> SubprocedureNodeSpec:
    return SubprocedureNodeSpec(procedure_id=_PROCEDURE_ID, revision=3)


def _canonical_nodes() -> tuple[ProcedureNode, ...]:
    """One node per canonical family the recovery contract must compose with."""
    return (
        ProcedureNode(id=_ACTION, kind=ProcedureNodeKind.ACTION),
        ProcedureNode(id=_OBSERVE, kind=ProcedureNodeKind.OBSERVE),
        ProcedureNode(id=_VERIFY, kind=ProcedureNodeKind.VERIFY),
        _rollback_spec().to_node(_ROLLBACK),
        _subprocedure_spec().to_node(_SUBPROCEDURE),
        EndNodeSpec().to_node(_END),
    )


def _normal_graph() -> ProcedureGraph:
    """A valid A3.01 graph with normal progression only and no recovery."""
    return ProcedureGraph(
        entry=_ACTION,
        nodes=_canonical_nodes(),
        edges=(
            ProcedureEdge(source=_ACTION, target=_OBSERVE, kind=ProcedureEdgeKind.NEXT),
            ProcedureEdge(source=_OBSERVE, target=_VERIFY, kind=ProcedureEdgeKind.NEXT),
            ProcedureEdge(source=_VERIFY, target=_END, kind=ProcedureEdgeKind.NEXT),
        ),
    )


def _edge(
    source: ProcedureNodeId | str,
    target: ProcedureNodeId | str,
    on_failure: FailureCategory = FailureCategory.TRANSIENT,
    label: str | None = None,
) -> RecoveryEdge:
    # The contract coerces canonical strings to ProcedureNodeId at validation
    # time, so the helper deliberately accepts either form.
    return RecoveryEdge(
        source=source,  # type: ignore[arg-type]
        target=target,  # type: ignore[arg-type]
        on_failure=on_failure,
        label=label,
    )


def _document(*edges: RecoveryEdge) -> ProcedureRecoveryEdges:
    return ProcedureRecoveryEdges(recovery_edges=edges)


def _graph_with(
    document: ProcedureRecoveryEdges, *, extra: tuple[ProcedureEdge, ...] = ()
) -> ProcedureGraph:
    """A graph whose RECOVERY edges are exactly the document's declared routes."""
    return ProcedureGraph(
        entry=_ACTION,
        nodes=_canonical_nodes(),
        edges=(
            ProcedureEdge(source=_ACTION, target=_OBSERVE, kind=ProcedureEdgeKind.NEXT),
            ProcedureEdge(source=_OBSERVE, target=_VERIFY, kind=ProcedureEdgeKind.NEXT),
            ProcedureEdge(source=_VERIFY, target=_END, kind=ProcedureEdgeKind.NEXT),
            *extra,
            *document.to_graph_edges(),
        ),
    )


def _edge_dict(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "source": "n_action",
        "target": "n_rollback",
        "on_failure": FailureCategory.TRANSIENT.value,
        "label": None,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# A normal graph without recovery is unchanged.
# ---------------------------------------------------------------------------


def test_valid_normal_graph_without_recovery_needs_no_recovery_document() -> None:
    graph = _normal_graph()
    document = ProcedureRecoveryEdges()

    assert document.recovery_edges == ()
    assert document.to_graph_edges() == ()
    assert document.bind_to_graph(graph) is document
    # No declared route means no route at all: never an implicit fallback.
    for category in _ALL_CATEGORIES:
        assert document.declared_target(_ACTION, category) is None
    assert all(edge.kind is ProcedureEdgeKind.NEXT for edge in graph.edges)


def test_empty_document_is_valid_explicit_data() -> None:
    document = ProcedureRecoveryEdges()
    assert document.to_dict() == {"contract_version": 1, "recovery_edges": []}
    assert ProcedureRecoveryEdges.from_json(document.to_json()) == document


# ---------------------------------------------------------------------------
# One typed recovery edge.
# ---------------------------------------------------------------------------


def test_single_recovery_edge_is_typed_immutable_data() -> None:
    edge = _edge(_ACTION, _ROLLBACK, FailureCategory.PERMISSION, label="ask for authority")

    assert isinstance(edge.source, ProcedureNodeId)
    assert isinstance(edge.target, ProcedureNodeId)
    assert edge.on_failure is FailureCategory.PERMISSION
    assert edge.to_dict() == {
        "source": "n_action",
        "target": "n_rollback",
        "on_failure": "permission",
        "label": "ask for authority",
    }
    with pytest.raises(FrozenInstanceError):
        edge.target = _END  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        edge.on_failure = FailureCategory.UNKNOWN  # type: ignore[misc]


def test_recovery_edges_are_canonical_next_kind_edges_marked_recovery() -> None:
    """Recovery connectivity is ordinary A3.01 connectivity under the distinct
    canonical RECOVERY edge kind — no second edge schema is introduced."""
    document = _document(_edge(_ACTION, _ROLLBACK))
    rendered = document.to_graph_edges()

    assert len({ProcedureEdgeKind.RECOVERY, ProcedureEdgeKind.NEXT}) == 2
    assert rendered == (
        ProcedureEdge(source=_ACTION, target=_ROLLBACK, kind=ProcedureEdgeKind.RECOVERY),
    )
    graph = _graph_with(document)
    assert [(edge.source, edge.target, edge.kind) for edge in graph.edges] == [
        (_ACTION, _OBSERVE, ProcedureEdgeKind.NEXT),
        (_ACTION, _ROLLBACK, ProcedureEdgeKind.RECOVERY),
        (_OBSERVE, _VERIFY, ProcedureEdgeKind.NEXT),
        (_VERIFY, _END, ProcedureEdgeKind.NEXT),
    ]


def test_node_references_may_be_supplied_as_canonical_strings() -> None:
    assert _edge("n_action", "n_rollback") == _edge(_ACTION, _ROLLBACK)


@pytest.mark.parametrize("category", _ALL_CATEGORIES)
def test_every_canonical_failure_category_is_accepted_verbatim(
    category: FailureCategory,
) -> None:
    edge = _edge(_ACTION, _ROLLBACK, category)
    assert edge.on_failure is category
    assert RecoveryEdge.from_dict(edge.to_dict()) == edge
    assert RecoveryEdge.from_dict(_edge_dict(on_failure=category.value)) == _edge(
        _ACTION, _ROLLBACK, category
    )


def test_failure_category_is_required() -> None:
    with pytest.raises(TypeError):
        RecoveryEdge(source=_ACTION, target=_ROLLBACK)  # type: ignore[call-arg]


@pytest.mark.parametrize("intruder", ["", "  ", "recovery", "ANY", "*", "any_failure", "SUCCESS"])
def test_unknown_failure_categories_fail_closed_and_are_never_coerced(intruder: str) -> None:
    with pytest.raises(RecoveryContractError, match="unknown failure category"):
        _edge(_ACTION, _ROLLBACK, intruder)  # type: ignore[arg-type]


@pytest.mark.parametrize("intruder", [0, 1, None, True, ["transient"], {"kind": "transient"}])
def test_non_string_failure_categories_are_rejected(intruder: object) -> None:
    with pytest.raises(RecoveryContractError, match="canonical failure category"):
        _edge(_ACTION, _ROLLBACK, intruder)  # type: ignore[arg-type]


def test_no_catch_all_category_exists_in_the_composed_vocabulary() -> None:
    """The vocabulary is the canonical closed one: no wildcard member, and
    ``UNKNOWN`` is a category of its own rather than an ``ANY`` alias."""
    assert {member.value for member in FailureCategory} == {
        "transient",
        "precondition",
        "environment",
        "permission",
        "dependency",
        "ui_change",
        "api_change",
        "capability",
        "procedure",
        "knowledge",
        "plan",
        "verification",
        "unknown",
    }
    document = _document(_edge(_ACTION, _ROLLBACK, FailureCategory.UNKNOWN))
    assert document.declared_target(_ACTION, FailureCategory.UNKNOWN) == _ROLLBACK
    for category in _ALL_CATEGORIES:
        if category is not FailureCategory.UNKNOWN:
            assert document.declared_target(_ACTION, category) is None


# ---------------------------------------------------------------------------
# Invalid source/target references.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "intruder",
    [0, 1, None, True, 3.5, b"n_action", ["n_action"], {"value": "n_action"}, _normal_graph()],
)
def test_invalid_target_type_is_rejected(intruder: object) -> None:
    with pytest.raises(RecoveryContractError, match="recovery edge target"):
        _edge(_ACTION, intruder)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "intruder",
    [0, None, True, b"n_action", ["n_action"], {"value": "n_action"}, _normal_graph()],
)
def test_invalid_source_type_is_rejected(intruder: object) -> None:
    with pytest.raises(RecoveryContractError, match="recovery edge source"):
        _edge(intruder, _ROLLBACK)  # type: ignore[arg-type]


@pytest.mark.parametrize("intruder", ["", "   ", " n_action", "n_action ", "\t", "\n"])
def test_malformed_node_reference_text_is_rejected(intruder: str) -> None:
    with pytest.raises(RecoveryContractError, match=r"invalid recovery edge (source|target)"):
        _edge(intruder, _ROLLBACK)
    with pytest.raises(RecoveryContractError, match=r"invalid recovery edge (source|target)"):
        _edge(_ACTION, intruder)


@pytest.mark.parametrize("identifier", ["n\naction", "n\x00action", "n_action; DROP TABLE x; --"])
def test_canonical_node_id_syntax_is_composed_unchanged(identifier: str) -> None:
    """Whatever the canonical A3.01 node-id rule accepts, this contract accepts
    verbatim: it invents no stricter syntax, and a hostile id stays a reference
    key that names a node rather than an instruction to anything."""
    assert ProcedureNodeId(identifier).to_str() == identifier

    edge = _edge(identifier, _ROLLBACK)
    assert edge.source.to_str() == identifier
    assert RecoveryEdge.from_dict(edge.to_dict()) == edge
    assert edge.to_dict()["source"] == identifier
    assert _document(edge).to_json()  # serializes without escaping the identity away


@pytest.mark.parametrize("label", ["", "  ", " pad ", "two\nlines", "tab\there", "null\x00"])
def test_label_text_discipline_is_enforced(label: str) -> None:
    with pytest.raises(RecoveryContractError, match="recovery edge label"):
        _edge(_ACTION, _ROLLBACK, label=label)


def test_label_length_is_bounded() -> None:
    with pytest.raises(RecoveryContractError, match="256 characters"):
        _edge(_ACTION, _ROLLBACK, label="x" * 257)
    assert _edge(_ACTION, _ROLLBACK, label="x" * 256).label == "x" * 256


def test_self_loop_recovery_edge_is_rejected_as_an_unbounded_retry() -> None:
    with pytest.raises(RecoveryContractError, match="unbounded retry"):
        _edge(_ACTION, _ACTION)
    with pytest.raises(RecoveryContractError, match="unbounded retry"):
        _edge("n_action", "n_action")


# ---------------------------------------------------------------------------
# Multiple independent recovery edges.
# ---------------------------------------------------------------------------


def test_multiple_independent_recovery_edges_are_declared_together() -> None:
    document = _document(
        _edge(_ACTION, _ROLLBACK, FailureCategory.PERMISSION),
        _edge(_OBSERVE, _VERIFY, FailureCategory.UI_CHANGE),
        _edge(_ACTION, _SUBPROCEDURE, FailureCategory.DEPENDENCY),
    )

    assert len(document.recovery_edges) == 3
    assert document.bind_to_graph(_graph_with(document)) is document
    assert document.declared_target(_ACTION, FailureCategory.PERMISSION) == _ROLLBACK
    assert document.declared_target(_ACTION, FailureCategory.DEPENDENCY) == _SUBPROCEDURE
    assert document.declared_target(_OBSERVE, FailureCategory.UI_CHANGE) == _VERIFY
    # Independence is exact: no cross-talk between sources or categories.
    assert document.declared_target(_OBSERVE, FailureCategory.PERMISSION) is None
    assert document.declared_target(_VERIFY, FailureCategory.UI_CHANGE) is None


def test_one_source_may_declare_a_route_per_failure_category() -> None:
    """One route per failure category is unambiguous: the selection key is the
    ``(source, failure category)`` pair, never the source alone."""
    document = _document(
        *(_edge(_ACTION, _ROLLBACK, category) for category in _ALL_CATEGORIES),
    )

    assert len(document.recovery_edges) == len(_ALL_CATEGORIES)
    for category in _ALL_CATEGORIES:
        assert document.declared_target(_ACTION, category) == _ROLLBACK
    assert ProcedureRecoveryEdges.from_json(document.to_json()) == document


# ---------------------------------------------------------------------------
# Duplicate and ambiguous routes fail closed.
# ---------------------------------------------------------------------------


def test_duplicate_recovery_edge_is_rejected() -> None:
    with pytest.raises(RecoveryContractError, match="duplicate recovery edge"):
        _document(
            _edge(_ACTION, _ROLLBACK, FailureCategory.TRANSIENT),
            _edge(_ACTION, _ROLLBACK, FailureCategory.TRANSIENT),
        )


def test_duplicate_recovery_edge_with_a_different_label_is_still_a_duplicate() -> None:
    with pytest.raises(RecoveryContractError, match="duplicate recovery edge"):
        _document(
            _edge(_ACTION, _ROLLBACK, FailureCategory.TRANSIENT, label="first"),
            _edge(_ACTION, _ROLLBACK, FailureCategory.TRANSIENT, label="second"),
        )


def test_ambiguous_recovery_targets_for_one_failure_are_rejected() -> None:
    with pytest.raises(RecoveryContractError, match="ambiguous recovery edges"):
        _document(
            _edge(_ACTION, _ROLLBACK, FailureCategory.TRANSIENT),
            _edge(_ACTION, _SUBPROCEDURE, FailureCategory.TRANSIENT),
        )


def test_ambiguity_is_rejected_regardless_of_author_order() -> None:
    first = _edge(_ACTION, _ROLLBACK, FailureCategory.TRANSIENT)
    second = _edge(_ACTION, _SUBPROCEDURE, FailureCategory.TRANSIENT)
    for pair in ((first, second), (second, first)):
        with pytest.raises(RecoveryContractError, match="ambiguous recovery edges"):
            _document(*pair)


def test_document_container_shape_is_enforced() -> None:
    with pytest.raises(RecoveryContractError, match="recovery_edges must be a tuple"):
        ProcedureRecoveryEdges(recovery_edges=[_edge(_ACTION, _ROLLBACK)])  # type: ignore[arg-type]
    with pytest.raises(RecoveryContractError, match="must be a RecoveryEdge"):
        ProcedureRecoveryEdges(
            recovery_edges=({"source": "n_action"},)  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("version", [0, 2, -1, 99, "1", 1.0, None, True])
def test_contract_version_is_pinned_and_fails_closed(version: object) -> None:
    with pytest.raises(RecoveryContractError, match=r"contract_version|recovery contract version"):
        ProcedureRecoveryEdges(contract_version=version)  # type: ignore[arg-type]
    assert CURRENT_RECOVERY_CONTRACT_VERSION == 1


def test_document_is_immutable() -> None:
    document = _document(_edge(_ACTION, _ROLLBACK))
    with pytest.raises(FrozenInstanceError):
        document.recovery_edges = ()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Deterministic serialization.
# ---------------------------------------------------------------------------


def test_deterministic_round_trip_is_exact() -> None:
    document = _document(
        _edge(_ACTION, _ROLLBACK, FailureCategory.PERMISSION, label="escalate"),
        _edge(_OBSERVE, _END, FailureCategory.ENVIRONMENT),
        _edge(_VERIFY, _SUBPROCEDURE, FailureCategory.VERIFICATION, label="re-verify elsewhere"),
    )
    text = document.to_json()

    restored = ProcedureRecoveryEdges.from_json(text)
    assert restored == document
    assert restored.to_json() == text
    assert restored.to_dict() == document.to_dict()
    # Canonical JSON: compact separators, sorted keys, no ASCII escaping.
    assert text == json.dumps(
        document.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert ", " not in text and '": ' not in text


def test_serialization_order_is_author_order_independent() -> None:
    a = _edge(_VERIFY, _END, FailureCategory.PLAN)
    b = _edge(_ACTION, _ROLLBACK, FailureCategory.TRANSIENT)
    c = _edge(_ACTION, _SUBPROCEDURE, FailureCategory.API_CHANGE)

    assert _document(a, b, c).to_json() == _document(c, b, a).to_json()
    assert _document(a, b, c) == _document(c, b, a)
    # Canonical order: source, then failure category, then target, then label.
    assert [(edge.source, edge.on_failure) for edge in _document(c, b, a).recovery_edges] == [
        (_ACTION, FailureCategory.API_CHANGE),
        (_ACTION, FailureCategory.TRANSIENT),
        (_VERIFY, FailureCategory.PLAN),
    ]


def test_serialized_shape_is_the_exact_canonical_field_set() -> None:
    assert _edge(_ACTION, _ROLLBACK).to_dict() == {
        "source": "n_action",
        "target": "n_rollback",
        "on_failure": "transient",
        "label": None,
    }
    assert _document(_edge(_ACTION, _ROLLBACK)).to_dict() == {
        "contract_version": 1,
        "recovery_edges": [
            {
                "source": "n_action",
                "target": "n_rollback",
                "on_failure": "transient",
                "label": None,
            }
        ],
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"source": "n_action"},
        {"source": "n_action", "target": "n_rollback"},
        {"source": "n_action", "target": "n_rollback", "on_failure": "transient"},
        {**_edge_dict(), "retry": 3},
        {**_edge_dict(), "verified": True},
        {**_edge_dict(), "permission": "ADMIN"},
        {**_edge_dict(), "risk": "R0"},
        {**_edge_dict(), "outcome": "success"},
        {**_edge_dict(), "source": 7},
        {**_edge_dict(), "target": None},
        {**_edge_dict(), "on_failure": "not_a_category"},
        {**_edge_dict(), "label": 5},
    ],
)
def test_malformed_serialized_recovery_edges_fail_closed(payload: Mapping[str, object]) -> None:
    with pytest.raises(RecoveryContractError):
        RecoveryEdge.from_dict(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"contract_version": 1},
        {"recovery_edges": []},
        {"contract_version": 1, "recovery_edges": [], "entry": "n_action"},
        {"contract_version": 2, "recovery_edges": []},
        {"contract_version": "1", "recovery_edges": []},
        {"contract_version": 1, "recovery_edges": {}},
        {"contract_version": 1, "recovery_edges": ["n_action"]},
        {"contract_version": 1, "recovery_edges": [{"source": "n_action"}]},
    ],
)
def test_malformed_serialized_documents_fail_closed(payload: Mapping[str, object]) -> None:
    with pytest.raises(RecoveryContractError):
        ProcedureRecoveryEdges.from_dict(payload)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        '"recovery"',
        "null",
        "{",
        '{"contract_version": 1, "recovery_edges": [}',
        '{"contract_version": 1, "recovery_edges": [{"source": "n_action"},]}',
    ],
)
def test_malformed_json_text_fails_closed(text: str) -> None:
    with pytest.raises(RecoveryContractError):
        ProcedureRecoveryEdges.from_json(text)


@pytest.mark.parametrize("intruder", [None, 7, b"{}", ["{}"], _normal_graph()])
def test_non_string_json_input_is_rejected(intruder: object) -> None:
    with pytest.raises(RecoveryContractError, match="JSON must be a string"):
        ProcedureRecoveryEdges.from_json(intruder)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Graph-level validation: dangling targets, bad sources, END, graph presence.
# ---------------------------------------------------------------------------


def test_dangling_recovery_target_is_rejected() -> None:
    document = _document(_edge(_ACTION, _GONE))
    with pytest.raises(RecoveryContractError, match="dangling recovery target"):
        document.bind_to_graph(_normal_graph())
    # Even a graph that carries the RECOVERY edge cannot exist with a dangling
    # endpoint: A3.01 itself rejects it.
    with pytest.raises(ProcedureGraphValidationError, match="edge target node does not exist"):
        ProcedureGraph(
            entry=_ACTION,
            nodes=_canonical_nodes(),
            edges=(ProcedureEdge(source=_ACTION, target=_GONE, kind=ProcedureEdgeKind.RECOVERY),),
        )


def test_nonexistent_recovery_source_is_rejected() -> None:
    document = _document(_edge(_GONE, _ROLLBACK))
    with pytest.raises(RecoveryContractError, match="source node does not exist"):
        document.bind_to_graph(_normal_graph())


def test_recovery_route_absent_from_the_graph_is_rejected() -> None:
    """A declared route must exist in the graph as a RECOVERY edge: the graph
    stays the single authority for connectivity."""
    document = _document(_edge(_ACTION, _ROLLBACK))
    with pytest.raises(RecoveryContractError, match="not present in the graph as a RECOVERY"):
        document.bind_to_graph(_normal_graph())


def test_recovery_route_present_only_as_a_next_edge_is_rejected() -> None:
    """Normal progression is not recovery: the edge kinds stay distinct."""
    document = _document(_edge(_ACTION, _ROLLBACK))
    graph = _graph_with(
        ProcedureRecoveryEdges(),
        extra=(ProcedureEdge(source=_ACTION, target=_ROLLBACK, kind=ProcedureEdgeKind.NEXT),),
    )
    with pytest.raises(RecoveryContractError, match="not present in the graph as a RECOVERY"):
        document.bind_to_graph(graph)


def test_end_node_can_never_be_a_recovery_source() -> None:
    document = _document(_edge(_END, _ROLLBACK))
    with pytest.raises(RecoveryContractError, match="END node is terminal"):
        document.bind_to_graph(_normal_graph())
    # A3.01 already forbids any outgoing END edge, so the route cannot even be
    # materialized as a RECOVERY edge.
    with pytest.raises(ProcedureGraphValidationError, match="END node must not have outgoing"):
        ProcedureGraph(
            entry=_ACTION,
            nodes=_canonical_nodes(),
            edges=(ProcedureEdge(source=_END, target=_ROLLBACK, kind=ProcedureEdgeKind.RECOVERY),),
        )


def test_bind_to_graph_requires_an_actual_procedure_graph() -> None:
    document = _document(_edge(_ACTION, _ROLLBACK))
    for intruder in (None, "graph", 7, _edge(_ACTION, _ROLLBACK), _normal_graph().to_dict()):
        with pytest.raises(RecoveryContractError, match="expects a ProcedureGraph"):
            document.bind_to_graph(intruder)  # type: ignore[arg-type]


def test_bind_to_graph_never_mutates_the_graph_or_the_document() -> None:
    document = _document(_edge(_ACTION, _ROLLBACK, FailureCategory.PERMISSION))
    graph = _graph_with(document)
    graph_before = graph.to_json()
    document_before = document.to_json()

    assert document.bind_to_graph(graph) is document

    assert graph.to_json() == graph_before
    assert document.to_json() == document_before
    assert graph.schema_version == CURRENT_GRAPH_SCHEMA_VERSION


def test_undeclared_recovery_edge_is_inert_rather_than_an_error() -> None:
    """A RECOVERY edge with no declared failure condition is never applicable:
    fail-safe, not fail-open, and not a reason to reject the graph."""
    document = ProcedureRecoveryEdges()
    graph = _graph_with(
        document,
        extra=(ProcedureEdge(source=_ACTION, target=_ROLLBACK, kind=ProcedureEdgeKind.RECOVERY),),
    )

    assert document.bind_to_graph(graph) is document
    for category in _ALL_CATEGORIES:
        assert document.declared_target(_ACTION, category) is None


@pytest.mark.parametrize("intruder", ["", "  ", 7, None, ["n_action"]])
def test_declared_target_lookups_fail_closed_on_malformed_arguments(intruder: object) -> None:
    document = _document(_edge(_ACTION, _ROLLBACK))

    with pytest.raises(RecoveryContractError):
        document.declared_target(intruder, FailureCategory.TRANSIENT)  # type: ignore[arg-type]
    with pytest.raises(RecoveryContractError):
        document.declared_target(_ACTION, intruder)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Compatibility: ROLLBACK, SUBPROCEDURE, END, preconditions/postconditions.
# ---------------------------------------------------------------------------


def test_recovery_edge_into_a_rollback_node_invokes_no_rollback() -> None:
    spec = _rollback_spec()
    document = _document(_edge(_ACTION, _ROLLBACK, FailureCategory.CAPABILITY))
    graph = _graph_with(document)

    assert document.bind_to_graph(graph) is document
    assert document.declared_target(_ACTION, FailureCategory.CAPABILITY) == _ROLLBACK
    # The ROLLBACK payload is untouched, still inert, and still unresolved.
    rollback_node = next(node for node in graph.nodes if node.id == _ROLLBACK)
    assert rollback_node.kind is ProcedureNodeKind.ROLLBACK
    assert RollbackNodeSpec.from_node(rollback_node) == spec
    assert spec.scope.kind is RollbackScopeKind.PROCEDURE_EXECUTION


def test_recovery_edge_out_of_a_rollback_node_is_declared_data_only() -> None:
    document = _document(_edge(_ROLLBACK, _END, FailureCategory.ENVIRONMENT))
    graph = _graph_with(document)
    assert document.bind_to_graph(graph) is document


def test_recovery_edge_into_a_subprocedure_node_invokes_no_procedure() -> None:
    spec = _subprocedure_spec()
    document = _document(_edge(_ACTION, _SUBPROCEDURE, FailureCategory.DEPENDENCY))
    graph = _graph_with(document)

    assert document.bind_to_graph(graph) is document
    sub_node = next(node for node in graph.nodes if node.id == _SUBPROCEDURE)
    assert sub_node.kind is ProcedureNodeKind.SUBPROCEDURE
    assert SubprocedureNodeSpec.from_node(sub_node) == spec
    assert spec.revision == 3


def test_recovery_edge_out_of_a_subprocedure_node_is_declared_data_only() -> None:
    document = _document(_edge(_SUBPROCEDURE, _ROLLBACK, FailureCategory.PROCEDURE))
    graph = _graph_with(document)

    assert document.bind_to_graph(graph) is document
    assert document.declared_target(_SUBPROCEDURE, FailureCategory.PROCEDURE) == _ROLLBACK


def test_recovery_edge_near_end_can_target_end_but_never_leave_it() -> None:
    document = _document(
        _edge(_ACTION, _END, FailureCategory.PRECONDITION, label="terminate on bad preconditions"),
        _edge(_VERIFY, _END, FailureCategory.VERIFICATION),
    )
    graph = _graph_with(document)

    assert document.bind_to_graph(graph) is document
    assert document.declared_target(_VERIFY, FailureCategory.VERIFICATION) == _END
    end_node = next(node for node in graph.nodes if node.id == _END)
    assert EndNodeSpec.from_node(end_node) == EndNodeSpec()
    # END keeps the sole A3.01 terminal rule: no outgoing edge of either kind.
    assert all(edge.source != _END for edge in graph.edges)
    with pytest.raises(RecoveryContractError, match="END node is terminal"):
        _document(_edge(_END, _ACTION)).bind_to_graph(graph)


def test_recovery_routes_coexist_with_preconditions_and_postconditions() -> None:
    """A3.06 and A3.07 are independent companion documents over one graph."""
    precondition = ProcedureCondition(
        id=ConditionId("pre-browser-open"),
        statement="a browser session is open",
        evidence_kind=EvidenceKind.OBSERVATION,
    )
    postcondition = ProcedureCondition(
        id=ConditionId("post-form-filled"),
        statement="the form holds the submitted values",
        evidence_kind=EvidenceKind.ARTIFACT,
        evidence_reference="form://checkout",
    )
    node_precondition = ProcedureCondition(
        id=ConditionId("pre-rollback-approved-scope"),
        statement="the rollback scope is recorded before any rollback",
        evidence_kind=EvidenceKind.ARTIFACT,
    )
    conditions = ProcedureConditions(
        preconditions=(precondition,),
        postconditions=(postcondition,),
        node_conditions=(NodeConditions(node_id=_ROLLBACK, preconditions=(node_precondition,)),),
    )
    recovery = _document(_edge(_ACTION, _ROLLBACK, FailureCategory.PERMISSION))
    graph = _graph_with(recovery)

    assert conditions.bind_to_graph(graph) is conditions
    assert recovery.bind_to_graph(graph) is recovery
    # Declaring a recovery route satisfies nothing and verifies nothing.
    assert evaluate_condition(precondition, EvidenceFacts()).status is (
        ConditionEvaluationStatus.UNKNOWN
    )
    assert evaluate_condition(postcondition, EvidenceFacts()).status is (
        ConditionEvaluationStatus.UNKNOWN
    )
    assert evaluate_condition(node_precondition, EvidenceFacts()).status is (
        ConditionEvaluationStatus.UNKNOWN
    )
    assert recovery.declared_target(_ACTION, FailureCategory.PERMISSION) == _ROLLBACK


def test_recovery_routes_may_target_nodes_that_carry_declared_conditions() -> None:
    conditions = ProcedureConditions(
        node_conditions=(
            NodeConditions(
                node_id=_ROLLBACK,
                postconditions=(
                    ProcedureCondition(
                        id=ConditionId("post-rollback-scope"),
                        statement="the rollback scope is recorded",
                        evidence_kind=EvidenceKind.ARTIFACT,
                    ),
                ),
            ),
        )
    )
    recovery = _document(_edge(_ACTION, _ROLLBACK, FailureCategory.TRANSIENT))
    graph = _graph_with(recovery)

    assert conditions.bind_to_graph(graph) is conditions
    assert recovery.bind_to_graph(graph) is recovery
    assert recovery.declared_target(_ACTION, FailureCategory.TRANSIENT) == _ROLLBACK
    # Binding recovery routes leaves the conditions document byte-identical.
    assert (
        conditions.to_json()
        == ProcedureConditions(
            node_conditions=(
                NodeConditions(
                    node_id=_ROLLBACK,
                    postconditions=(
                        ProcedureCondition(
                            id=ConditionId("post-rollback-scope"),
                            statement="the rollback scope is recorded",
                            evidence_kind=EvidenceKind.ARTIFACT,
                        ),
                    ),
                ),
            )
        ).to_json()
    )


# ---------------------------------------------------------------------------
# Boundedness and cycles follow the existing graph rules.
# ---------------------------------------------------------------------------


def test_wide_recovery_cycles_are_legal_exactly_as_a3_01_allows() -> None:
    """The A3.01 graph is finite, directed, and possibly cyclic; recovery
    cycles are structural data, and no bound is invented here."""
    recovery = _document(
        _edge(_ACTION, _OBSERVE, FailureCategory.TRANSIENT),
        _edge(_OBSERVE, _ACTION, FailureCategory.TRANSIENT),
    )
    graph = _graph_with(recovery)

    assert recovery.bind_to_graph(graph) is recovery
    assert recovery.declared_target(_ACTION, FailureCategory.TRANSIENT) == _OBSERVE
    assert recovery.declared_target(_OBSERVE, FailureCategory.TRANSIENT) == _ACTION


def test_recovery_representation_declares_no_retry_or_limit_vocabulary() -> None:
    """No field could carry a retry count, a budget, a delay, or a schedule:
    boundedness stays a kernel/runtime concern, never graph data."""
    edge_fields = {field.name for field in dataclasses.fields(RecoveryEdge)}
    document_fields = {field.name for field in dataclasses.fields(ProcedureRecoveryEdges)}

    assert edge_fields == {"source", "target", "on_failure", "label"}
    assert document_fields == {"contract_version", "recovery_edges"}
    forbidden = {
        "retry",
        "retries",
        "attempts",
        "max_attempts",
        "limit",
        "budget",
        "delay",
        "timeout",
        "backoff",
        "risk",
        "permission",
        "verified",
        "outcome",
        "status",
        "success",
    }
    assert (edge_fields | document_fields).isdisjoint(forbidden)


def test_a3_01_structural_rules_are_unchanged_by_the_recovery_contract() -> None:
    """A3.01 keeps owning duplicate edges, dangling endpoints, entry validity,
    and END terminality, independent of any recovery document."""
    nodes = _canonical_nodes()
    with pytest.raises(ProcedureGraphValidationError, match="duplicate edge"):
        ProcedureGraph(
            entry=_ACTION,
            nodes=nodes,
            edges=(
                ProcedureEdge(source=_ACTION, target=_ROLLBACK, kind=ProcedureEdgeKind.RECOVERY),
                ProcedureEdge(source=_ACTION, target=_ROLLBACK, kind=ProcedureEdgeKind.RECOVERY),
            ),
        )
    with pytest.raises(ProcedureGraphValidationError, match="edge source node does not exist"):
        ProcedureGraph(
            entry=_ACTION,
            nodes=nodes,
            edges=(ProcedureEdge(source=_GONE, target=_ROLLBACK, kind=ProcedureEdgeKind.RECOVERY),),
        )
    with pytest.raises(ProcedureGraphValidationError, match="entry node id is not present"):
        ProcedureGraph(
            entry=_GONE,
            nodes=nodes,
            edges=(),
        )
    # NEXT and RECOVERY between the same pair are two distinct edges, exactly
    # as A3.01 has always classified them.
    graph = ProcedureGraph(
        entry=_ACTION,
        nodes=nodes,
        edges=(
            ProcedureEdge(source=_ACTION, target=_ROLLBACK, kind=ProcedureEdgeKind.NEXT),
            ProcedureEdge(source=_ACTION, target=_ROLLBACK, kind=ProcedureEdgeKind.RECOVERY),
        ),
    )
    assert len(graph.edges) == 2


def test_recovery_document_survives_a_full_graph_round_trip() -> None:
    """The graph and the recovery document are separately canonical data and
    stay consistent through serialization."""
    recovery = _document(
        _edge(_ACTION, _ROLLBACK, FailureCategory.PERMISSION),
        _edge(_OBSERVE, _END, FailureCategory.ENVIRONMENT),
    )
    graph = _graph_with(recovery)

    restored_graph = ProcedureGraph.from_json(graph.to_json())
    restored_document = ProcedureRecoveryEdges.from_json(recovery.to_json())

    assert restored_graph == graph
    assert restored_document == recovery
    assert restored_document.bind_to_graph(restored_graph) is restored_document
    assert restored_document.to_graph_edges() == tuple(
        edge for edge in restored_graph.edges if edge.kind is ProcedureEdgeKind.RECOVERY
    )
