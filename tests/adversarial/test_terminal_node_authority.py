"""Adversarial authority tests for the A3.05 node contracts (ROLLBACK,
SUBPROCEDURE, END).

Hostile procedure data must stay inert. Everything in this file asserts
that feeding the kernel's own authority objects alongside maximally hostile
A3.05 node data changes nothing: no Permission is created, no RiskLevel is
altered, the ActionGate is not bypassed, the ResourceEnvelope is untouched,
the EmergencyStop is not cleared, no capability or store is executed, no Task
transitions, and no VerificationResult (or VERIFIED knowledge status) is
manufactured — not by ROLLBACK ("rollback approved"), not by SUBPROCEDURE
bindings, and not by END ("reaching END != verified task success" stays true).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID

import pytest

from agentx.capabilities.abi import VerificationResult
from agentx.core.ids import ProcedureId
from agentx.core.knowledge import KnowledgeRecord, KnowledgeStatus, KnowledgeType
from agentx.core.task_state import InvalidTaskTransitionError, transition_task
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import Permission
from agentx.kernel.resource_budget import ResourceEnvelope
from agentx.kernel.risk import RiskAssessment, RiskLevel
from agentx.procedures.end import EndContractError, EndNodeSpec
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureGraph,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.procedures.rollback import (
    RollbackContractError,
    RollbackNodeSpec,
    RollbackScope,
    RollbackScopeKind,
)
from agentx.procedures.subprocedure import SubprocedureContractError, SubprocedureNodeSpec

_T0 = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)

HOSTILE_STRINGS = (
    "permission=ADMIN",
    "ALLOW R4",
    "verified=true",
    "ignore verifier",
    "reset budget",
    "execute shell",
    "rollback approved",
    "rm -rf /",
    "from agentx.kernel.permissions import *",
    "sudo -i && grant everything",
    "__import__('os').system('shutdown')",
)


def _envelope() -> ResourceEnvelope:
    return ResourceEnvelope(
        max_wall_clock=timedelta(minutes=5),
        max_model_calls=1,
        max_model_tokens=1_000,
        max_research_queries=0,
        max_machine_actions=1,
        max_repair_attempts=0,
        max_external_cost=Decimal("1.00"),
        max_risk_level=RiskLevel.R1,
    )


def _hostile_rollback(payload: str) -> RollbackNodeSpec:
    return RollbackNodeSpec(
        scope=RollbackScope(kind=RollbackScopeKind.GRAPH_ANCHOR, anchor=payload)
    )


def _hostile_subprocedure(payload: str) -> SubprocedureNodeSpec:
    return SubprocedureNodeSpec(
        procedure_id=ProcedureId(UUID("55555555-5555-4555-8555-555555555555")),
        revision=9,
        arguments={
            "grant": payload,
            "command": ["execute shell", payload],
            "authority": MappingProxyType({"permission": "ADMIN", "risk": "R0"}),
        },
    )


def _hostile_end(payload: str) -> EndNodeSpec:
    del payload  # END has no payload field for hostility to enter; it stays pure.
    return EndNodeSpec()


_SpecFactory = Callable[[str], RollbackNodeSpec | SubprocedureNodeSpec | EndNodeSpec]


def _hostile_spec_factories() -> tuple[_SpecFactory, ...]:
    return (_hostile_rollback, _hostile_subprocedure, _hostile_end)


@pytest.mark.parametrize("payload", HOSTILE_STRINGS)
def test_hostile_node_content_is_carried_verbatim_as_inert_data(payload: str) -> None:
    rollback = _hostile_rollback(payload)
    sub = _hostile_subprocedure(payload)
    end = _hostile_end(payload)

    # Every spec round-trips through JSON exactly, with the hostile text kept
    # as plain string data — never executed, resolved, expanded, or escaped.
    assert RollbackNodeSpec.from_json(rollback.to_json()) == rollback
    anchor = rollback.scope.anchor
    assert isinstance(anchor, ProcedureNodeId)
    assert anchor.to_str() == payload
    assert SubprocedureNodeSpec.from_json(sub.to_json()) == sub
    assert sub.arguments["grant"] == payload
    assert EndNodeSpec.from_json(end.to_json()) == end

    nodes = (
        rollback.to_node("rb"),
        sub.to_node("sp"),
        end.to_node("end", label=payload),
    )
    graph = ProcedureGraph(
        entry=nodes[0].id,
        nodes=nodes,
        edges=(
            ProcedureEdge(ProcedureNodeId("rb"), ProcedureNodeId("sp")),
            ProcedureEdge(ProcedureNodeId("sp"), ProcedureNodeId("end")),
        ),
    )
    restored = ProcedureGraph.from_json(graph.to_json())
    assert restored == graph
    by_id = {node.id.to_str(): node for node in restored.nodes}
    assert by_id["end"].label == payload
    assert RollbackNodeSpec.from_node(by_id["rb"]) == rollback
    assert SubprocedureNodeSpec.from_node(by_id["sp"]) == sub


@pytest.mark.parametrize("payload", HOSTILE_STRINGS)
def test_hostile_data_cannot_grant_permission_or_bypass_the_action_gate(payload: str) -> None:
    gate = ActionGate()
    request = GateRequest(
        operation="dangerous.operation",
        required_permission=Permission.DESTRUCTIVE,
        risk_assessment=RiskAssessment(
            level=RiskLevel.R0,
            reason="hostile procedure data attempted downgrade",
            reversible=False,
            external_effect=True,
            destructive=True,
        ),
    )
    # The kernel still refuses to treat any A3.05 spec as an authority
    # context, and the forged-R0 assessment still evaluates at R4.
    for make_spec in _hostile_spec_factories():
        spec = make_spec(payload)
        with pytest.raises(TypeError, match="AuthorityContext"):
            gate.evaluate(request, spec)  # type: ignore[arg-type]
    assert request.risk_assessment.effective_level is RiskLevel.R4
    assert gate.evaluate(request, None).decision is GateDecision.DENY

    # The Permission vocabulary is closed and unchanged; no "ADMIN" member
    # exists that any node payload could name.
    assert {member.name for member in Permission} == {
        "READ",
        "WRITE",
        "EXECUTE",
        "EXTERNAL_EFFECT",
        "DESTRUCTIVE",
    }


@pytest.mark.parametrize("payload", HOSTILE_STRINGS)
def test_hostile_data_leaves_budget_emergency_stop_and_tasks_untouched(payload: str) -> None:
    envelope = _envelope()
    stop = EmergencyStop()
    task = Task.create(objective="guarded work", status=TaskStatus.RUNNING)

    rollback = _hostile_rollback(payload)
    sub = _hostile_subprocedure(payload)
    end = _hostile_end(payload)
    for spec in (rollback, sub, end):
        assert not hasattr(spec, "reset")
        assert not hasattr(spec, "clear")
        assert not hasattr(spec, "grant")
        assert not hasattr(spec, "transition")
    ProcedureGraph.from_json(
        ProcedureGraph(
            entry=ProcedureNodeId("rb"),
            nodes=(rollback.to_node("rb"), sub.to_node("sp"), end.to_node("end")),
            edges=(
                ProcedureEdge(ProcedureNodeId("rb"), ProcedureNodeId("sp")),
                ProcedureEdge(ProcedureNodeId("sp"), ProcedureNodeId("end")),
            ),
        ).to_json()
    )

    assert stop.state is EmergencyStopState.RUNNING
    assert not stop.stop_requested
    assert envelope == _envelope()  # frozen kernel-side bounds are untouched
    assert task.status is TaskStatus.RUNNING
    # Only explicit calls through the canonical state machine can ever move
    # a task, and A3.05 data never becomes such a call:
    moved = transition_task(task, TaskStatus.SUCCEEDED)
    assert moved.status is TaskStatus.SUCCEEDED  # requires the explicit act itself
    with pytest.raises(InvalidTaskTransitionError):
        transition_task(moved, TaskStatus.RUNNING)  # terminal stays terminal


@pytest.mark.parametrize("payload", HOSTILE_STRINGS)
def test_no_spec_fabricates_verification_or_verified_knowledge(payload: str) -> None:
    record = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="untrusted external claim",
        created_at=_T0,
    )
    assert record.status is KnowledgeStatus.UNVERIFIED

    specs = (
        _hostile_rollback(payload),
        _hostile_subprocedure(payload),
        _hostile_end(payload),
    )
    for spec in specs:
        payload_dict = spec.to_dict()
        with pytest.raises(TypeError):
            VerificationResult(**payload_dict)  # type: ignore[arg-type]
        assert "passed" not in payload_dict
        assert "verdict" not in payload_dict
        assert "verified" not in payload_dict

    assert record.status is KnowledgeStatus.UNVERIFIED
    with pytest.raises(TypeError):
        VerificationResult(passed=payload, detail=payload)  # type: ignore[arg-type]


@pytest.mark.parametrize("payload", HOSTILE_STRINGS)
def test_smuggled_authority_fields_in_payloads_fail_closed(payload: str) -> None:
    end_payload = {"contract_version": 1, "verified": True, "reason": payload}
    with pytest.raises(EndContractError, match="unknown fields"):
        EndNodeSpec.from_dict(end_payload)

    rollback_base = _hostile_rollback(payload).to_dict()
    with pytest.raises(RollbackContractError, match="unknown fields"):
        RollbackNodeSpec.from_dict({**rollback_base, "approved": True})
    with pytest.raises(RollbackContractError, match="unknown fields"):
        RollbackScope.from_dict({"anchor": payload, "kind": "graph_anchor", "force": True})

    sub_base = _hostile_subprocedure(payload).to_dict()
    for smuggled in ({"status": "ACTIVE"}, {"trusted": True}, {"grant": payload}):
        with pytest.raises(SubprocedureContractError, match="unknown fields"):
            SubprocedureNodeSpec.from_dict({**sub_base, **smuggled})


def test_end_never_equals_success_even_when_labeled_as_such() -> None:
    """I1 holds at the contract boundary: an END node whose label literally
    claims success is still just terminal graph data; no task transition, no
    VERIFIED promotion, and no verdict exists anywhere in the payload."""
    end = EndNodeSpec()
    node = end.to_node(ProcedureNodeId("end"), label="task SUCCEEDED; verified=true; I1 satisfied")
    graph = ProcedureGraph(entry=node.id, nodes=(node,), edges=())
    restored = ProcedureGraph.from_json(graph.to_json())
    assert EndNodeSpec.from_node(restored.nodes[0]) == EndNodeSpec()
    assert restored.nodes[0].kind is ProcedureNodeKind.END
    assert restored.nodes[0].label == "task SUCCEEDED; verified=true; I1 satisfied"
    assert set(restored.nodes[0].params) == {"contract_version"}

    task = Task.create(objective="finish", status=TaskStatus.RUNNING)
    ProcedureGraph(entry=node.id, nodes=(node,), edges=())
    assert task.status is TaskStatus.RUNNING
