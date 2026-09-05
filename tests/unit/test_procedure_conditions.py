"""Tests for the A3.06 procedure preconditions/postconditions DATA contract.

These tests prove that ``agentx.procedures.conditions`` is deterministic,
strictly validated, immutable representation only: canonical
serialization/round-trips, order-independent identity, fail-closed rejection
of malformed data at every level, reuse of canonical IDs and vocabularies
(``ProcedureNodeId``, ``EvidenceKind``), graph compatibility with every
A3.01-A3.05 node family, and a persistence story that requires no migration.
Nothing here executes, evaluates, verifies, or interprets anything; the
authority/adversarial properties are covered in
``tests/adversarial/test_procedure_conditions_authority.py`` and
``tests/architecture/test_procedure_conditions_boundaries.py``.
"""

from __future__ import annotations

import copy
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from agentx.core.ids import ProcedureId
from agentx.core.procedures import ProcedurePayload, ProcedurePayloadKind
from agentx.core.provenance import EvidenceKind
from agentx.procedures.branch import BranchContract, BranchOutcome
from agentx.procedures.conditions import (
    CURRENT_CONDITIONS_CONTRACT_VERSION,
    ConditionId,
    NodeConditions,
    ProcedureCondition,
    ProcedureConditions,
    ProcedureConditionsError,
)
from agentx.procedures.end import EndNodeSpec
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureEdgeKind,
    ProcedureGraph,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.procedures.reason_research import ReasonNodeSpec, ResearchNodeSpec
from agentx.procedures.rollback import RollbackNodeSpec, RollbackScope, RollbackScopeKind
from agentx.procedures.subprocedure import SubprocedureNodeSpec
from agentx.procedures.transform import TransformContract
from agentx.procedures.wait import WaitContract


def _condition(
    name: str = "cfg-present",
    *,
    statement: str = "the agentx configuration file exists",
    evidence_kind: EvidenceKind = EvidenceKind.ARTIFACT,
    reference: str | None = "artifact:agentx.toml",
) -> ProcedureCondition:
    return ProcedureCondition(
        id=ConditionId(name),
        statement=statement,
        evidence_kind=evidence_kind,
        evidence_reference=reference,
    )


def _sample_document() -> ProcedureConditions:
    return ProcedureConditions(
        preconditions=(
            _condition("cfg-present"),
            ProcedureCondition(
                id=ConditionId("hive-reachable"),
                statement="the semantic memory store answers a trivial read",
                evidence_kind=EvidenceKind.KNOWLEDGE_RECORD,
            ),
        ),
        postconditions=(
            ProcedureCondition(
                id=ConditionId("report-artifact"),
                statement="a report artifact exists under the run directory",
                evidence_kind=EvidenceKind.ARTIFACT,
            ),
        ),
        node_conditions=(
            NodeConditions(
                node_id=ProcedureNodeId("probe"),
                preconditions=(_condition("probe-cfg-present"),),
            ),
            NodeConditions(
                node_id=ProcedureNodeId("finish"),
                postconditions=(
                    ProcedureCondition(
                        id=ConditionId("summary-recorded"),
                        statement="a summary knowledge record exists for the run",
                        evidence_kind=EvidenceKind.KNOWLEDGE_RECORD,
                    ),
                ),
            ),
        ),
    )


def _all_families_graph() -> ProcedureGraph:
    """Build a graph containing every canonical A3.01-A3.05 node family."""
    nodes = (
        ProcedureNode(ProcedureNodeId("start"), ProcedureNodeKind.ACTION),
        ProcedureNode(ProcedureNodeId("probe"), ProcedureNodeKind.OBSERVE),
        ProcedureNode(ProcedureNodeId("check"), ProcedureNodeKind.VERIFY),
        BranchContract(
            outcomes=(
                BranchOutcome(name="ok", condition=None),
                BranchOutcome(name="retry", condition="probe evidence inconclusive"),
            )
        ).to_node("route"),
        TransformContract(operation="normalize", arguments={"scale": 2}).to_node("shape"),
        WaitContract(requirement="operator approval", timeout_seconds=30.0).to_node("hold"),
        ReasonNodeSpec(objective="summarize the observation", output_binding="summary").to_node(
            node_id=ProcedureNodeId("think")
        ),
        ResearchNodeSpec(
            objective="locate the configuration reference", output_binding="reference"
        ).to_node(node_id=ProcedureNodeId("find")),
        RollbackNodeSpec(scope=RollbackScope(kind=RollbackScopeKind.PROCEDURE_EXECUTION)).to_node(
            "undo"
        ),
        SubprocedureNodeSpec(
            procedure_id=ProcedureId(UUID("55555555-5555-4555-8555-555555555555")),
            revision=3,
        ).to_node("sub"),
        EndNodeSpec().to_node("finish"),
    )
    edges = (
        ProcedureEdge(ProcedureNodeId("start"), ProcedureNodeId("probe")),
        ProcedureEdge(ProcedureNodeId("probe"), ProcedureNodeId("check")),
        ProcedureEdge(ProcedureNodeId("check"), ProcedureNodeId("route")),
        ProcedureEdge(ProcedureNodeId("route"), ProcedureNodeId("shape")),
        ProcedureEdge(ProcedureNodeId("shape"), ProcedureNodeId("hold")),
        ProcedureEdge(ProcedureNodeId("hold"), ProcedureNodeId("think")),
        ProcedureEdge(ProcedureNodeId("think"), ProcedureNodeId("find")),
        ProcedureEdge(ProcedureNodeId("find"), ProcedureNodeId("undo")),
        ProcedureEdge(ProcedureNodeId("undo"), ProcedureNodeId("sub")),
        ProcedureEdge(ProcedureNodeId("sub"), ProcedureNodeId("finish")),
        ProcedureEdge(
            ProcedureNodeId("check"),
            ProcedureNodeId("undo"),
            ProcedureEdgeKind.RECOVERY,
        ),
    )
    return ProcedureGraph(entry=ProcedureNodeId("start"), nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# Deterministic representation / round-trips
# ---------------------------------------------------------------------------


def test_condition_dict_round_trip_is_exact() -> None:
    condition = _condition()
    restored = ProcedureCondition.from_dict(condition.to_dict())
    assert restored == condition
    assert restored.to_dict() == condition.to_dict()


def test_document_dict_round_trip_is_exact() -> None:
    document = _sample_document()
    restored = ProcedureConditions.from_dict(document.to_dict())
    assert restored == document
    assert restored.to_dict() == document.to_dict()


def test_document_json_round_trip_is_deterministic_and_canonical() -> None:
    document = _sample_document()
    first = document.to_json()
    for _ in range(5):
        assert document.to_json() == first
    canonical = json.loads(first)
    assert (
        json.dumps(
            canonical,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        == first
    )
    restored = ProcedureConditions.from_json(first)
    assert restored == document
    assert restored.to_json() == first


def test_condition_and_node_scoping_json_round_trips() -> None:
    condition = _condition()
    scoped = NodeConditions(
        node_id=ProcedureNodeId("n1"),
        preconditions=(condition,),
        postconditions=(
            ProcedureCondition(
                id=ConditionId("after"),
                statement="the probe evidence exists",
                evidence_kind=EvidenceKind.OBSERVATION,
            ),
        ),
    )
    assert ProcedureCondition.from_json(condition.to_json()) == condition
    assert NodeConditions.from_json(scoped.to_json()) == scoped


def test_unreferenced_evidence_round_trips_as_none() -> None:
    condition = _condition(reference=None)
    assert condition.evidence_reference is None
    restored = ProcedureCondition.from_json(condition.to_json())
    assert restored == condition
    assert restored.to_dict()["evidence_reference"] is None


def test_empty_document_is_valid_and_round_trips() -> None:
    document = ProcedureConditions()
    assert document.preconditions == ()
    assert document.postconditions == ()
    assert document.node_conditions == ()
    assert document.contract_version == CURRENT_CONDITIONS_CONTRACT_VERSION == 1
    assert ProcedureConditions.from_json(document.to_json()) == document


def test_author_ordering_is_normalized_to_canonical_identity_order() -> None:
    alpha = _condition("alpha")
    beta = _condition("beta")
    gamma = _condition("gamma")
    one = ProcedureConditions(preconditions=(gamma, alpha, beta))
    two = ProcedureConditions(preconditions=(alpha, beta, gamma))
    assert one == two
    assert one.to_dict() == two.to_dict()
    assert one.to_json() == two.to_json()
    assert one.preconditions == (alpha, beta, gamma)


def test_node_scoping_order_is_normalized() -> None:
    pre = NodeConditions(node_id=ProcedureNodeId("b"), preconditions=(_condition("p-b"),))
    post = NodeConditions(node_id=ProcedureNodeId("a"), postconditions=(_condition("p-a"),))
    document = ProcedureConditions(node_conditions=(pre, post))
    assert document.node_conditions[0].node_id == ProcedureNodeId("a")
    assert document.node_conditions[1].node_id == ProcedureNodeId("b")
    reordered = ProcedureConditions(node_conditions=(post, pre))
    assert document == reordered
    assert document.to_json() == reordered.to_json()


def test_node_scoped_conditions_are_normalized_within_each_node() -> None:
    alpha = _condition("alpha")
    beta = _condition("beta")
    scoped = NodeConditions(node_id=ProcedureNodeId("n"), preconditions=(beta, alpha))
    assert scoped.preconditions == (alpha, beta)
    assert NodeConditions.from_json(scoped.to_json()) == scoped


def test_to_dict_shape_is_explicit_and_complete() -> None:
    document = _sample_document()
    raw = document.to_dict()
    assert set(raw) == {"contract_version", "preconditions", "postconditions", "node_conditions"}
    preconditions = raw["preconditions"]
    assert isinstance(preconditions, list)
    first = preconditions[0]
    assert isinstance(first, dict)
    assert set(first) == {"id", "statement", "evidence_kind", "evidence_reference"}
    node_conditions = raw["node_conditions"]
    assert isinstance(node_conditions, list)
    scoped = node_conditions[0]
    assert isinstance(scoped, dict)
    assert set(scoped) == {"node_id", "preconditions", "postconditions"}


# ---------------------------------------------------------------------------
# Canonical ID and vocabulary reuse
# ---------------------------------------------------------------------------


def test_node_scoping_reuses_the_canonical_procedure_node_id() -> None:
    scoped = NodeConditions(node_id=ProcedureNodeId("probe"), preconditions=(_condition(),))
    assert isinstance(scoped.node_id, ProcedureNodeId)
    assert scoped.to_dict()["node_id"] == "probe"
    restored = NodeConditions.from_dict(scoped.to_dict())
    assert isinstance(restored.node_id, ProcedureNodeId)


def test_condition_id_is_its_own_canonical_type() -> None:
    identifier = ConditionId("cfg-present")
    assert identifier.to_str() == "cfg-present"
    assert ConditionId.parse("cfg-present") == identifier
    with pytest.raises(ProcedureConditionsError):
        ConditionId.parse(42)


def test_evidence_kind_composes_the_canonical_core_vocabulary() -> None:
    for kind in EvidenceKind:
        condition = _condition(evidence_kind=kind)
        assert condition.evidence_kind is kind
        assert ProcedureCondition.from_json(condition.to_json()).evidence_kind is kind


def test_constructor_coerces_the_canonical_string_form() -> None:
    condition = ProcedureCondition(
        id=ConditionId("x"),
        statement="s",
        evidence_kind=cast(EvidenceKind, "knowledge_record"),
    )
    assert condition.evidence_kind is EvidenceKind.KNOWLEDGE_RECORD


def test_evidence_kind_accepts_only_canonical_string_forms() -> None:
    condition = ProcedureCondition.from_dict(
        {
            "id": "x",
            "statement": "s",
            "evidence_kind": "artifact",
            "evidence_reference": None,
        }
    )
    assert condition.evidence_kind is EvidenceKind.ARTIFACT
    with pytest.raises(ProcedureConditionsError, match="unknown evidence kind"):
        ProcedureCondition.from_dict(
            {
                "id": "x",
                "statement": "s",
                "evidence_kind": "opinion",
                "evidence_reference": None,
            }
        )


# ---------------------------------------------------------------------------
# Strict malformed-data rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        {},  # nothing
        {"preconditions": [], "postconditions": [], "node_conditions": []},  # missing version
        {"contract_version": 1, "postconditions": [], "node_conditions": []},
        {
            "contract_version": 1,
            "preconditions": [],
            "postconditions": [],
            "node_conditions": [],
            "extra": True,
        },  # unknown field
        {
            "contract_version": 2,
            "preconditions": [],
            "postconditions": [],
            "node_conditions": [],
        },  # unsupported version
        {
            "contract_version": True,
            "preconditions": [],
            "postconditions": [],
            "node_conditions": [],
        },  # bool is not an int
        {
            "contract_version": "1",
            "preconditions": [],
            "postconditions": [],
            "node_conditions": [],
        },  # string version
    ],
)
def test_malformed_documents_fail_closed(raw: object) -> None:
    with pytest.raises(ProcedureConditionsError):
        ProcedureConditions.from_dict(cast(dict[str, object], raw))


@pytest.mark.parametrize(
    "raw",
    [
        {"statement": "s", "evidence_kind": "artifact", "evidence_reference": None},
        {"id": "x", "evidence_kind": "artifact", "evidence_reference": None},
        {"id": "x", "statement": "s", "evidence_reference": None},
        {"id": "x", "statement": "s", "evidence_kind": "artifact"},
        {"id": "x", "statement": "s", "evidence_kind": "artifact", "reference": "r"},
        {"id": 7, "statement": "s", "evidence_kind": "artifact", "evidence_reference": None},
        {"id": "x", "statement": 7, "evidence_kind": "artifact", "evidence_reference": None},
        {"id": "x", "statement": "s", "evidence_kind": 7, "evidence_reference": None},
        {"id": "x", "statement": "s", "evidence_kind": "artifact", "evidence_reference": 7},
    ],
)
def test_malformed_conditions_fail_closed(raw: object) -> None:
    with pytest.raises(ProcedureConditionsError):
        ProcedureCondition.from_dict(cast(dict[str, object], raw))


@pytest.mark.parametrize(
    "raw",
    [
        {"preconditions": [], "postconditions": []},
        {"node_id": "n", "preconditions": []},
        {"node_id": "n", "postconditions": []},
        {"node_id": "n", "preconditions": [], "postconditions": [], "extra": 1},
        {"node_id": 7, "preconditions": [], "postconditions": []},
        {"node_id": "n", "preconditions": {}, "postconditions": []},
        {"node_id": "n", "preconditions": [], "postconditions": ["not-a-mapping"]},
    ],
)
def test_malformed_node_conditions_fail_closed(raw: object) -> None:
    with pytest.raises(ProcedureConditionsError):
        NodeConditions.from_dict(cast(dict[str, object], raw))


@pytest.mark.parametrize("raw", ["", "{not json", "[1,2]", "null", "42", '"text"'])
def test_malformed_json_fails_closed(raw: str) -> None:
    with pytest.raises(ProcedureConditionsError):
        ProcedureConditions.from_json(raw)
    with pytest.raises(ProcedureConditionsError):
        ProcedureCondition.from_json(raw)
    with pytest.raises(ProcedureConditionsError):
        NodeConditions.from_json(raw)


def test_from_json_rejects_non_string_input() -> None:
    with pytest.raises(ProcedureConditionsError, match="must be a string"):
        ProcedureConditions.from_json(cast(str, {"contract_version": 1}))


def test_document_root_must_be_an_object() -> None:
    with pytest.raises(ProcedureConditionsError, match="must be an object"):
        ProcedureConditions.from_dict(cast(dict[str, object], ["not", "an", "object"]))


def test_constructors_reject_non_tuple_collections() -> None:
    with pytest.raises(ProcedureConditionsError, match="preconditions must be a tuple"):
        ProcedureConditions(preconditions=[_condition()])  # type: ignore[arg-type]
    with pytest.raises(ProcedureConditionsError, match="node_conditions must be a tuple"):
        ProcedureConditions(node_conditions=[NodeConditions(node_id=ProcedureNodeId("n"))])  # type: ignore[arg-type]
    with pytest.raises(ProcedureConditionsError, match="preconditions must be a tuple"):
        NodeConditions(node_id=ProcedureNodeId("n"), preconditions=[_condition()])  # type: ignore[arg-type]


def test_constructors_reject_wrong_item_types() -> None:
    with pytest.raises(ProcedureConditionsError, match="ProcedureCondition"):
        ProcedureConditions(preconditions=("not a condition",))  # type: ignore[arg-type]
    with pytest.raises(ProcedureConditionsError, match="NodeConditions"):
        ProcedureConditions(node_conditions=(_condition(),))  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["", "   ", " padded ", 42, None, "line\nbreak", "tab\there"])
def test_invalid_statement_text_fails_closed(text: object) -> None:
    with pytest.raises(ProcedureConditionsError):
        ProcedureCondition(
            id=ConditionId("x"),
            statement=cast(str, text),
            evidence_kind=EvidenceKind.OBSERVATION,
        )


def test_oversized_text_fails_closed() -> None:
    with pytest.raises(ProcedureConditionsError, match="condition id"):
        ConditionId("x" * 129)
    with pytest.raises(ProcedureConditionsError, match="statement"):
        _condition(statement="y" * 1025)
    with pytest.raises(ProcedureConditionsError, match="evidence_reference"):
        _condition(reference="z" * 513)


def test_duplicate_condition_ids_fail_closed_across_scopes() -> None:
    shared = _condition("dup")
    with pytest.raises(ProcedureConditionsError, match="duplicate condition id"):
        ProcedureConditions(preconditions=(shared,), postconditions=(shared,))
    with pytest.raises(ProcedureConditionsError, match="duplicate condition id"):
        ProcedureConditions(
            preconditions=(shared,),
            node_conditions=(
                NodeConditions(node_id=ProcedureNodeId("n"), preconditions=(shared,)),
            ),
        )
    # Distinct ids with identical content remain distinct declarations.
    distinct = ProcedureConditions(
        preconditions=(_condition("a"),),
        postconditions=(_condition("b"),),
    )
    assert len(distinct.postconditions) == 1


def test_duplicate_node_scoping_fails_closed() -> None:
    scoped = NodeConditions(node_id=ProcedureNodeId("n"), preconditions=(_condition("p1"),))
    again = NodeConditions(node_id=ProcedureNodeId("n"), postconditions=(_condition("p2"),))
    with pytest.raises(ProcedureConditionsError, match="duplicate node conditions"):
        ProcedureConditions(node_conditions=(scoped, again))


def test_node_scoping_requires_canonical_node_id_instance() -> None:
    with pytest.raises(ProcedureConditionsError, match="ProcedureNodeId"):
        NodeConditions(node_id="n1", preconditions=(_condition(),))  # type: ignore[arg-type]


def test_condition_requires_canonical_condition_id_instance() -> None:
    with pytest.raises(ProcedureConditionsError, match="ConditionId"):
        ProcedureCondition(
            id="cfg-present",  # type: ignore[arg-type]
            statement="s",
            evidence_kind=EvidenceKind.ARTIFACT,
        )


def test_unsupported_contract_version_fails_closed_in_constructor() -> None:
    with pytest.raises(ProcedureConditionsError, match="unsupported conditions contract version"):
        ProcedureConditions(contract_version=99)


# ---------------------------------------------------------------------------
# Immutable contracts
# ---------------------------------------------------------------------------


def test_contracts_are_frozen() -> None:
    condition = _condition()
    document = _sample_document()
    with pytest.raises(FrozenInstanceError):
        condition.statement = "mutated"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        document.preconditions = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        ConditionId("x").value = "y"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        NodeConditions(node_id=ProcedureNodeId("n")).node_id = ProcedureNodeId("m")  # type: ignore[misc]


def test_fields_are_immutable_types_and_normalization_copies() -> None:
    gamma = _condition("gamma")
    conditions = (gamma, _condition("alpha"))
    document = ProcedureConditions(preconditions=conditions)
    assert document.preconditions is not conditions
    assert document.preconditions == (conditions[1], gamma)
    with pytest.raises(AttributeError):
        document.preconditions.append(gamma)  # type: ignore[attr-defined]


def test_deep_equality_is_value_based() -> None:
    one = _sample_document()
    two = _sample_document()
    assert one == two
    assert hash(one) == hash(two)
    three: dict[str, object] = copy.deepcopy(one.to_dict())
    assert ProcedureConditions.from_dict(three) == one


# ---------------------------------------------------------------------------
# Graph integration (A3.01 untouched; all node families compatible)
# ---------------------------------------------------------------------------


def test_conditions_bind_to_a_graph_of_every_node_family() -> None:
    graph = _all_families_graph()
    document = ProcedureConditions(
        preconditions=(_condition("cfg-present"),),
        postconditions=(
            ProcedureCondition(
                id=ConditionId("finish-state"),
                statement="the finish state is established by real evidence",
                evidence_kind=EvidenceKind.OBSERVATION,
            ),
        ),
        node_conditions=(
            NodeConditions(
                node_id=ProcedureNodeId("probe"), preconditions=(_condition("probe-cfg-present"),)
            ),
            NodeConditions(node_id=ProcedureNodeId("start"), postconditions=()),
        ),
    )
    bound = document.bind_to_graph(graph)
    assert bound == document  # binding validates references; it changes nothing


def test_binding_rejects_dangling_node_references() -> None:
    graph = _all_families_graph()
    document = ProcedureConditions(
        node_conditions=(NodeConditions(node_id=ProcedureNodeId("missing"), preconditions=()),),
    )
    with pytest.raises(ProcedureConditionsError, match="does not exist in the graph"):
        document.bind_to_graph(graph)


def test_bind_to_graph_requires_a_graph() -> None:
    with pytest.raises(ProcedureConditionsError, match="expects a ProcedureGraph"):
        _sample_document().bind_to_graph(cast(ProcedureGraph, {"nodes": []}))


def test_graph_and_conditions_serialize_independently_and_reunite() -> None:
    graph = _all_families_graph()
    document = ProcedureConditions(
        preconditions=(_condition("cfg-present"),),
        node_conditions=(NodeConditions(node_id=ProcedureNodeId("hold"), preconditions=()),),
    )
    graph_json = graph.to_json()
    document_json = document.to_json()

    restored_graph = ProcedureGraph.from_json(graph_json)
    restored_document = ProcedureConditions.from_json(document_json)
    assert restored_graph == graph
    assert restored_document == document
    assert restored_document.bind_to_graph(restored_graph) == document
    # The graph is untouched by the conditions document.
    assert restored_graph.to_json() == graph_json


def test_family_contracts_still_parse_after_condition_round_trips() -> None:
    """Binding conditions to a graph must not disturb any node-family view."""
    graph = ProcedureGraph.from_json(_all_families_graph().to_json())
    by_id = {node.id.to_str(): node for node in graph.nodes}
    document = ProcedureConditions(
        node_conditions=(
            NodeConditions(node_id=ProcedureNodeId("hold"), preconditions=(_condition(),)),
            NodeConditions(node_id=ProcedureNodeId("undo"), postconditions=()),
        )
    )
    document.bind_to_graph(graph)

    assert BranchContract.bind(by_id["route"]).outcomes[0].name == "ok"
    assert TransformContract.bind(by_id["shape"]).operation == "normalize"
    assert WaitContract.bind(by_id["hold"]).requirement == "operator approval"
    assert ReasonNodeSpec.from_node(by_id["think"]).objective == "summarize the observation"
    assert ResearchNodeSpec.from_node(by_id["find"]).objective == (
        "locate the configuration reference"
    )
    assert RollbackNodeSpec.from_node(by_id["undo"]).scope.kind is (
        RollbackScopeKind.PROCEDURE_EXECUTION
    )
    assert SubprocedureNodeSpec.from_node(by_id["sub"]).revision == 3
    assert EndNodeSpec.from_node(by_id["finish"]) == EndNodeSpec()


def test_conditions_document_is_storable_in_the_existing_opaque_payload() -> None:
    """The existing C2.03 payload channel carries the document verbatim:
    no persistence schema change and no migration is needed."""
    document = _sample_document()
    payload = ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=document.to_json())
    restored = ProcedurePayload.from_dict(payload.to_dict())
    assert restored == payload
    assert ProcedureConditions.from_json(restored.content) == document


def test_no_second_graph_schema_is_introduced() -> None:
    """The conditions module adds no node kind, no edge kind, and no graph
    type: the canonical graph vocabulary is unchanged by A3.06."""
    graph = _all_families_graph()
    assert {node.kind for node in graph.nodes} == set(ProcedureNodeKind)
    # Conditions never become graph structure: nodes and edges stay exactly
    # the A3.01 types, and the conditions document carries none of them.
    document = _sample_document()
    raw = document.to_dict()
    assert "nodes" not in raw
    assert "edges" not in raw
    assert "entry" not in raw


# ---------------------------------------------------------------------------
# Inert descriptive metadata; no executable payload
# ---------------------------------------------------------------------------


def test_module_surface_is_data_only() -> None:
    import agentx.procedures.conditions as conditions_module

    assert set(conditions_module.__all__) == {
        "CURRENT_CONDITIONS_CONTRACT_VERSION",
        "ConditionId",
        "NodeConditions",
        "ProcedureCondition",
        "ProcedureConditions",
        "ProcedureConditionsError",
    }
    for name in (
        "eval",
        "exec",
        "compile",
        "evaluate",
        "satisfy",
        "verify",
        "check",
        "run",
        "grant",
        "transition",
    ):
        assert not hasattr(conditions_module, name), name


def test_conditions_have_no_outcome_or_evaluation_surface() -> None:
    """No field, attribute, or callable can express that a condition holds."""
    document = _sample_document()
    for obj in (*document.preconditions, *document.postconditions, document):
        for forbidden in (
            "satisfied",
            "holds",
            "passed",
            "verified",
            "verdict",
            "result",
            "success",
            "succeeded",
            "status",
            "outcome",
            "evaluate",
            "check",
        ):
            assert not hasattr(obj, forbidden), forbidden
    methods = {
        name
        for cls in (ConditionId, ProcedureCondition, NodeConditions, ProcedureConditions)
        for name in dir(cls)
        if not name.startswith("_") and callable(getattr(cls, name))
    }
    assert methods <= {
        "to_dict",
        "from_dict",
        "to_json",
        "from_json",
        "to_str",
        "parse",
        "bind_to_graph",
    }


def test_module_location_and_boundary_anchor() -> None:
    module_path = (
        Path(__file__).resolve().parents[2] / "src" / "agentx" / "procedures" / "conditions.py"
    )
    assert module_path.is_file()
    source = module_path.read_text(encoding="utf-8")
    # Conditions never read node kinds: they are attachable to every family.
    for family_token in ("ProcedureNodeKind.", "ActionNodeSpec", "VerifyNodeSpec"):
        assert family_token not in source, family_token
