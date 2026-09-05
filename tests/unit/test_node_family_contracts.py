"""Cross-family tests for the A3.03 node-family contracts (BRANCH, TRANSFORM,
WAIT) on the canonical A3.01 Procedure Graph IR.

These tests prove that the three typed contracts embed in the A3.01 graph
through the opaque ``ProcedureNode.params`` without redesigning it: the graph
stays finite, directed, and cyclic where the IR allows, serialization
round-trips deterministically at the graph level, the C2.03 durable store
persists the graph opaquely and verbatim, and A3.03 does not claim the
A3.02-owned ACTION/OBSERVE/VERIFY node families.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentx.core.procedures import (
    ProcedurePayload,
    ProcedurePayloadKind,
    ProcedureRecord,
)
from agentx.core.provenance import EvidenceKind
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import ProcedureStore
from agentx.procedures.branch import BranchContract, BranchOutcome
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureGraph,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.procedures.nodes import ActionNodeSpec, ObserveNodeSpec, VerifyNodeSpec
from agentx.procedures.transform import TransformContract
from agentx.procedures.wait import WaitContract


def _family_nodes() -> tuple[BranchContract, TransformContract, WaitContract]:
    branch = BranchContract(
        outcomes=(
            BranchOutcome("proceed", condition="checks passed"),
            BranchOutcome("retry", condition="checks failed; retry once"),
        )
    )
    transform = TransformContract(
        operation="normalize_record",
        arguments={"strip": True, "max_length": 256},
    )
    wait = WaitContract(requirement="external job completion", timeout_seconds=120)
    return branch, transform, wait


def _family_graph() -> tuple[
    ProcedureGraph, tuple[BranchContract, TransformContract, WaitContract]
]:
    branch, transform, wait = _family_nodes()
    nodes = (
        branch.to_node("b"),
        transform.to_node("t", label="shape"),
        wait.to_node("w"),
        ProcedureNode(ProcedureNodeId("end"), ProcedureNodeKind.END),
    )
    edges = (
        # The BRANCH node's two outgoing NEXT edges are its explicit options.
        ProcedureEdge(ProcedureNodeId("b"), ProcedureNodeId("t")),
        ProcedureEdge(ProcedureNodeId("b"), ProcedureNodeId("w")),
        ProcedureEdge(ProcedureNodeId("t"), ProcedureNodeId("end")),
        ProcedureEdge(ProcedureNodeId("w"), ProcedureNodeId("end")),
    )
    return ProcedureGraph(ProcedureNodeId("b"), nodes, edges), (branch, transform, wait)


def test_all_three_families_embed_in_one_graph_and_round_trip() -> None:
    graph, (branch, transform, wait) = _family_graph()
    restored = ProcedureGraph.from_json(graph.to_json())
    assert restored == graph
    assert restored.to_json() == graph.to_json()

    by_id = {node.id.to_str(): node for node in restored.nodes}
    assert BranchContract.bind(by_id["b"]) == branch
    assert TransformContract.bind(by_id["t"]) == transform
    assert WaitContract.bind(by_id["w"]) == wait


def test_graph_cycles_with_node_family_contracts_remain_valid() -> None:
    """Cycles are legal in the A3.01 IR (recovery/retry flows need them);
    typed contracts must not change that."""
    branch, transform, wait = _family_nodes()
    graph = ProcedureGraph(
        ProcedureNodeId("b"),
        (
            branch.to_node("b"),
            transform.to_node("t"),
            wait.to_node("w"),
        ),
        (
            ProcedureEdge(ProcedureNodeId("b"), ProcedureNodeId("t")),
            ProcedureEdge(ProcedureNodeId("t"), ProcedureNodeId("w")),
            ProcedureEdge(ProcedureNodeId("w"), ProcedureNodeId("b")),  # cycle
        ),
    )
    graph.validate()
    restored = ProcedureGraph.from_json(graph.to_json())
    assert restored == graph
    by_id = {node.id.to_str(): node for node in restored.nodes}
    assert BranchContract.bind(by_id["b"]) == branch
    assert TransformContract.bind(by_id["t"]) == transform
    assert WaitContract.bind(by_id["w"]) == wait


def test_procedure_store_persists_node_family_graph_opaquely(tmp_path: Path) -> None:
    """The C2.03 durable store persists the graph as verbatim opaque payload
    text: it never parses, rewrites, or interprets node-family contracts."""
    graph, (branch, transform, wait) = _family_graph()
    canonical_text = graph.to_json()
    payload = ProcedurePayload(kind=ProcedurePayloadKind.CANONICAL_JSON, content=canonical_text)
    record = ProcedureRecord.create(payload=payload)

    store = ProcedureStore(SQLiteDatabase(tmp_path / "agentx.sqlite3"))
    store.insert(record)
    stored = store.get(record.procedure_id, 1)

    assert stored is not None
    assert stored.payload.kind is ProcedurePayloadKind.CANONICAL_JSON
    # Verbatim: the store did not rewrite, interpret, or strip the graph text.
    assert stored.payload.content == canonical_text

    restored = ProcedureGraph.from_json(stored.payload.content)
    assert restored == graph
    by_id = {node.id.to_str(): node for node in restored.nodes}
    assert BranchContract.bind(by_id["b"]) == branch
    assert TransformContract.bind(by_id["t"]) == transform
    assert WaitContract.bind(by_id["w"]) == wait


def test_a3_02_node_families_are_not_claimed_by_a3_03() -> None:
    """A3.03 owns BRANCH/TRANSFORM/WAIT only. ACTION/OBSERVE/VERIFY belong to
    A3.02: no A3.03 contract class, and binding fails closed on their nodes."""
    import agentx.procedures.branch as branch_module
    import agentx.procedures.transform as transform_module
    import agentx.procedures.wait as wait_module

    for module in (branch_module, transform_module, wait_module):
        for name in dir(module):
            assert not name.startswith(("Action", "Observe", "Verify")), (
                module.__name__,
                name,
            )

    contract = BranchContract(outcomes=(BranchOutcome("x"),))
    params = contract.to_dict()
    for kind in (ProcedureNodeKind.ACTION, ProcedureNodeKind.OBSERVE, ProcedureNodeKind.VERIFY):
        node = ProcedureNode(ProcedureNodeId("n"), kind, params=params)
        for bind in (BranchContract.bind, TransformContract.bind, WaitContract.bind):
            with pytest.raises(ValueError, match=r"expected a (branch|transform|wait) node"):
                bind(node)


def test_a3_02_and_a3_03_families_coexist_in_one_graph() -> None:
    """Mechanical integration: the A3.02 ACTION/OBSERVE/VERIFY specs and the
    A3.03 BRANCH/TRANSFORM/WAIT contracts coexist on one A3.01 graph without
    interfering with each other, and both survive a deterministic graph
    round-trip as opaque, inert data."""
    action = ActionNodeSpec(capability_name="mail.compose_draft", capability_version="1.0.0")
    observe = ObserveNodeSpec(
        expectation="A draft message should exist.",
        evidence_kind=EvidenceKind.OBSERVATION,
        required_fields=("draft_id",),
    )
    verify = VerifyNodeSpec(
        requirement="The draft must exist.",
        criterion="draft_id is present",
        evidence_kind=EvidenceKind.ARTIFACT,
    )
    branch, transform, wait = _family_nodes()

    nodes = (
        action.to_node("a"),
        observe.to_node("o"),
        verify.to_node("v"),
        branch.to_node("b"),
        transform.to_node("t"),
        wait.to_node("w"),
        ProcedureNode(ProcedureNodeId("end"), ProcedureNodeKind.END),
    )
    edges = (
        ProcedureEdge(ProcedureNodeId("a"), ProcedureNodeId("o")),
        ProcedureEdge(ProcedureNodeId("o"), ProcedureNodeId("v")),
        ProcedureEdge(ProcedureNodeId("v"), ProcedureNodeId("b")),
        ProcedureEdge(ProcedureNodeId("b"), ProcedureNodeId("t")),
        ProcedureEdge(ProcedureNodeId("b"), ProcedureNodeId("w")),
        ProcedureEdge(ProcedureNodeId("t"), ProcedureNodeId("end")),
        ProcedureEdge(ProcedureNodeId("w"), ProcedureNodeId("end")),
    )
    graph = ProcedureGraph(ProcedureNodeId("a"), nodes, edges)
    restored = ProcedureGraph.from_json(graph.to_json())
    assert restored == graph
    assert restored.to_json() == graph.to_json()

    by_id = {node.id.to_str(): node for node in restored.nodes}
    # A3.02 specs re-bind from the restored nodes (A3.02 API, unchanged).
    assert ActionNodeSpec.from_node(by_id["a"]) == action
    assert ObserveNodeSpec.from_node(by_id["o"]) == observe
    assert VerifyNodeSpec.from_node(by_id["v"]) == verify
    # A3.03 contracts re-bind from the restored nodes (A3.03 API).
    assert BranchContract.bind(by_id["b"]) == branch
    assert TransformContract.bind(by_id["t"]) == transform
    assert WaitContract.bind(by_id["w"]) == wait
