"""Integration tests: the M3.01 interpreter over real canonical graphs.

Every graph here is constructed from the real canonical contracts — A3.01
graph structure, A3.02 ACTION specs, A3.03 BRANCH contracts, A3.04
REASON/RESEARCH payloads, A3.05 SUBPROCEDURE/END payloads, and A3.07 recovery
documents — and driven end to end through the interpreter with explicit
results only. No capability is ever executed, no model or research provider
is invoked, no store is opened, and reaching END is never task success.
"""

from __future__ import annotations

from uuid import UUID

from agentx.core.failure_taxonomy import FailureCategory
from agentx.core.ids import ProcedureId
from agentx.procedures.branch import BranchContract, BranchOutcome
from agentx.procedures.end import EndNodeSpec
from agentx.procedures.graph import (
    ProcedureEdge,
    ProcedureEdgeKind,
    ProcedureGraph,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.procedures.interpreter import (
    BranchSelected,
    InterpreterFailureReason,
    InterpreterStatus,
    ProcedureInstructionKind,
    ProcedureInterpreter,
    ProcedureInterpreterState,
    StepCompleted,
    StepFailed,
)
from agentx.procedures.nodes import ActionNodeSpec
from agentx.procedures.reason_research import ReasonNodeSpec, ResearchNodeSpec
from agentx.procedures.recovery import ProcedureRecoveryEdges, RecoveryEdge
from agentx.procedures.subprocedure import SubprocedureNodeSpec


def _nid(value: str) -> ProcedureNodeId:
    return ProcedureNodeId(value)


def _next(source: str, target: str) -> ProcedureEdge:
    return ProcedureEdge(source=_nid(source), target=_nid(target), kind=ProcedureEdgeKind.NEXT)


def _action(node_id: str, capability: str) -> ProcedureNode:
    spec = ActionNodeSpec(
        capability_name=capability,
        capability_version="1.0.0",
        description="described, never executed",
        params={"target": "inert"},
    )
    return spec.to_node(node_id)


def _end(node_id: str) -> ProcedureNode:
    return EndNodeSpec().to_node(node_id)


# --------------------------------------------------------------------------
# 1. Straight-line graph.
# --------------------------------------------------------------------------


def test_straight_line_graph_walks_to_control_termination() -> None:
    graph = ProcedureGraph(
        entry=_nid("open"),
        nodes=(
            _action("open", "app.open"),
            _action("copy", "files.copy"),
            _end("done"),
        ),
        edges=(_next("open", "copy"), _next("copy", "done")),
    )
    interpreter = ProcedureInterpreter(graph=graph)

    state = interpreter.start()
    seen: list[str] = []
    while not state.is_terminal:
        instruction = interpreter.instruction(state)
        assert instruction.kind is ProcedureInstructionKind.ACTION_REQUIRED
        # The instruction exposes the canonical ACTION payload verbatim; it is
        # parseable by the canonical spec and remains a description only.
        spec = ActionNodeSpec.from_params(instruction.params)
        seen.append(spec.capability_name)
        state = interpreter.advance(state, StepCompleted(node_kind=ProcedureNodeKind.ACTION))

    assert seen == ["app.open", "files.copy"]
    assert state.status is InterpreterStatus.TERMINATED
    assert state.current_node == _nid("done")
    assert state.steps_taken == 2


# --------------------------------------------------------------------------
# 2. Conditional graph.
# --------------------------------------------------------------------------


def test_conditional_graph_takes_the_explicitly_selected_arm() -> None:
    branch = BranchContract(
        outcomes=(
            BranchOutcome("file_exists", condition="the target file already exists"),
            BranchOutcome("file_missing", condition="the target file does not exist"),
        )
    )
    graph = ProcedureGraph(
        entry=_nid("check"),
        nodes=(
            branch.to_node("check"),
            _action("skip", "noop.skip"),
            _action("create", "files.create"),
            _end("done"),
        ),
        edges=(
            _next("check", "skip"),
            _next("check", "create"),
            _next("skip", "done"),
            _next("create", "done"),
        ),
    )
    interpreter = ProcedureInterpreter(graph=graph)

    state = interpreter.start()
    instruction = interpreter.instruction(state)
    assert instruction.kind is ProcedureInstructionKind.BRANCH_DECISION_REQUIRED
    assert instruction.branch_outcomes == ("file_exists", "file_missing")
    assert set(instruction.branch_targets) == {_nid("skip"), _nid("create")}

    # The caller decides (using the canonical condition evaluator elsewhere);
    # the interpreter follows only the explicit decision.
    state = interpreter.advance(
        state, BranchSelected(outcome="file_missing", target=_nid("create"))
    )
    assert state.current_node == _nid("create")
    state = interpreter.advance(state, StepCompleted(node_kind=ProcedureNodeKind.ACTION))
    assert state.is_terminated


# --------------------------------------------------------------------------
# 3. Failure/recovery edge.
# --------------------------------------------------------------------------


def test_recovery_graph_routes_declared_failure_and_fails_undeclared_failure() -> None:
    recovery = ProcedureRecoveryEdges(
        recovery_edges=(
            RecoveryEdge(
                source=_nid("install"),
                target=_nid("cleanup"),
                on_failure=FailureCategory.TRANSIENT,
                label="retry after cleaning the partial install",
            ),
        )
    )
    graph = ProcedureGraph(
        entry=_nid("install"),
        nodes=(
            _action("install", "app.install"),
            _action("cleanup", "app.cleanup"),
            _end("done"),
        ),
        edges=(
            _next("install", "done"),
            _next("cleanup", "done"),
            *recovery.to_graph_edges(),
        ),
    )
    interpreter = ProcedureInterpreter(graph=graph, recovery=recovery)

    # Declared failure category: follow the canonical recovery route.
    state = interpreter.advance(
        interpreter.start(),
        StepFailed(node_kind=ProcedureNodeKind.ACTION, failure=FailureCategory.TRANSIENT),
    )
    assert state.current_node == _nid("cleanup")
    assert state.status is InterpreterStatus.AWAITING_ACTION_RESULT
    state = interpreter.advance(state, StepCompleted(node_kind=ProcedureNodeKind.ACTION))
    assert state.is_terminated

    # Undeclared failure category: the run fails explicitly; UNKNOWN never
    # matches TRANSIENT and nothing falls back to the NEXT edge.
    state = interpreter.advance(
        interpreter.start(),
        StepFailed(node_kind=ProcedureNodeKind.ACTION, failure=FailureCategory.UNKNOWN),
    )
    assert state.is_failed
    assert state.failure_reason is InterpreterFailureReason.NO_RECOVERY_ROUTE


# --------------------------------------------------------------------------
# 4. Reason / research pause points (plus a subprocedure reference).
# --------------------------------------------------------------------------


def test_reason_research_and_subprocedure_pause_points_are_inert() -> None:
    reason = ReasonNodeSpec(
        objective="decide which installer flavour applies",
        output_binding="installer_choice",
    )
    research = ResearchNodeSpec(
        objective="find the current silent-install flag",
        output_binding="install_flag",
        expected_evidence_bindings=("vendor_docs",),
    )
    subprocedure = SubprocedureNodeSpec(
        procedure_id=ProcedureId(UUID("00000000-0000-0000-0000-000000000042")),
        revision=3,
        arguments={"flag": "inert"},
    )
    graph = ProcedureGraph(
        entry=_nid("think"),
        nodes=(
            reason.to_node(node_id=_nid("think")),
            research.to_node(node_id=_nid("lookup")),
            subprocedure.to_node("delegate"),
            _end("done"),
        ),
        edges=(_next("think", "lookup"), _next("lookup", "delegate"), _next("delegate", "done")),
    )
    interpreter = ProcedureInterpreter(graph=graph)

    state = interpreter.start()
    assert state.status is InterpreterStatus.AWAITING_REASON_RESULT
    instruction = interpreter.instruction(state)
    assert instruction.kind is ProcedureInstructionKind.REASON_REQUIRED
    assert ReasonNodeSpec.from_dict(dict(instruction.params)) == reason

    state = interpreter.advance(state, StepCompleted(node_kind=ProcedureNodeKind.REASON))
    assert state.status is InterpreterStatus.AWAITING_RESEARCH_RESULT
    instruction = interpreter.instruction(state)
    assert instruction.kind is ProcedureInstructionKind.RESEARCH_REQUIRED
    assert ResearchNodeSpec.from_dict(dict(instruction.params)) == research

    state = interpreter.advance(state, StepCompleted(node_kind=ProcedureNodeKind.RESEARCH))
    assert state.status is InterpreterStatus.AWAITING_SUBPROCEDURE_RESULT
    instruction = interpreter.instruction(state)
    assert instruction.kind is ProcedureInstructionKind.SUBPROCEDURE_REQUIRED
    assert SubprocedureNodeSpec.from_dict(dict(instruction.params)) == subprocedure

    state = interpreter.advance(state, StepCompleted(node_kind=ProcedureNodeKind.SUBPROCEDURE))
    assert state.is_terminated
    # Control termination is the whole claim; nothing was reasoned, fetched,
    # or invoked, and no task became successful.
    assert state.status.value == "procedure_control_terminated"


# --------------------------------------------------------------------------
# 5. Bounded cyclic graph.
# --------------------------------------------------------------------------


def test_bounded_cyclic_graph_can_exit_or_exhausts_explicitly() -> None:
    retry_branch = BranchContract(outcomes=(BranchOutcome("retry"), BranchOutcome("give_up")))
    graph = ProcedureGraph(
        entry=_nid("try"),
        nodes=(
            _action("try", "net.fetch"),
            retry_branch.to_node("decide"),
            _end("done"),
        ),
        edges=(
            _next("try", "decide"),
            _next("decide", "try"),  # legal canonical cycle
            _next("decide", "done"),
        ),
    )
    interpreter = ProcedureInterpreter(graph=graph, max_steps=10)

    # Path A: loop twice, then exit through the declared END arm.
    state = interpreter.start()
    for _ in range(2):
        state = interpreter.advance(state, StepCompleted(node_kind=ProcedureNodeKind.ACTION))
        assert state.status is InterpreterStatus.AWAITING_BRANCH_SELECTION
        state = interpreter.advance(state, BranchSelected(outcome="retry", target=_nid("try")))
    state = interpreter.advance(state, StepCompleted(node_kind=ProcedureNodeKind.ACTION))
    state = interpreter.advance(state, BranchSelected(outcome="give_up", target=_nid("done")))
    assert state.is_terminated
    assert state.steps_taken == 6

    # Path B: loop forever; the ceiling stops the run explicitly.
    state = interpreter.start()
    advances = 0
    while not state.is_terminal:
        if state.status is InterpreterStatus.AWAITING_BRANCH_SELECTION:
            state = interpreter.advance(state, BranchSelected(outcome="retry", target=_nid("try")))
        else:
            state = interpreter.advance(state, StepCompleted(node_kind=ProcedureNodeKind.ACTION))
        advances += 1
        assert advances <= interpreter.max_steps + 1
    assert state.is_failed
    assert state.failure_reason is InterpreterFailureReason.STEP_LIMIT_EXCEEDED


# --------------------------------------------------------------------------
# Determinism across identical replays of a real graph.
# --------------------------------------------------------------------------


def test_identical_replays_produce_identical_state_sequences() -> None:
    branch = BranchContract(outcomes=(BranchOutcome("a"), BranchOutcome("b")))
    graph = ProcedureGraph(
        entry=_nid("act"),
        nodes=(
            _action("act", "app.open"),
            branch.to_node("pick"),
            _action("left", "files.copy"),
            _end("done"),
        ),
        edges=(
            _next("act", "pick"),
            _next("pick", "left"),
            _next("pick", "done"),
            _next("left", "done"),
        ),
    )

    def replay() -> tuple[ProcedureInterpreterState, ...]:
        interpreter = ProcedureInterpreter(graph=graph, max_steps=50)
        states = [interpreter.start()]
        states.append(
            interpreter.advance(states[-1], StepCompleted(node_kind=ProcedureNodeKind.ACTION))
        )
        states.append(
            interpreter.advance(states[-1], BranchSelected(outcome="a", target=_nid("left")))
        )
        states.append(
            interpreter.advance(states[-1], StepCompleted(node_kind=ProcedureNodeKind.ACTION))
        )
        return tuple(states)

    first = replay()
    second = replay()
    assert first == second
    assert first[-1].is_terminated
