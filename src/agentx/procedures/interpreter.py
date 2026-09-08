"""Deterministic Procedure Graph control-flow interpreter (M3.01).

This module owns the smallest production-quality deterministic machine that
can WALK one canonical A3.01 :class:`~agentx.procedures.graph.ProcedureGraph`.
It answers exactly one family of questions::

    Given ONE canonical procedure graph, one explicit interpreter state, and
    one explicit caller-supplied step result, what is the next control state,
    and what inert instruction describes what must happen externally next?

It is CONTROL-FLOW interpretation only. It is not an executor, not a
scheduler, not a verifier, and not a reasoner.

No execution authority
----------------------

The interpreter performs no effects of any kind. Every node family is handled
by returning a typed, frozen, inert :class:`ProcedureInstruction` describing
what some future, separately-owned, kernel-governed composition layer would
have to do — and the interpreter itself never does it:

    - ACTION / OBSERVE / VERIFY / TRANSFORM / WAIT / ROLLBACK: an
      ``*_REQUIRED`` instruction carrying the canonical node identity and its
      opaque, inert params. No capability is resolved or executed, nothing is
      observed, nothing is verified, nothing is transformed, nothing waits,
      and nothing is rolled back.
    - REASON / RESEARCH: a ``REASON_REQUIRED`` / ``RESEARCH_REQUIRED``
      instruction. No model provider and no research provider is ever
      invoked.
    - SUBPROCEDURE: a ``SUBPROCEDURE_REQUIRED`` instruction. No
      ``ProcedureStore`` is loaded and no referenced procedure is resolved,
      validated, or invoked.
    - BRANCH: a ``BRANCH_DECISION_REQUIRED`` instruction naming the declared
      canonical :class:`~agentx.procedures.branch.BranchContract` outcomes and
      the candidate ``NEXT`` targets. The interpreter NEVER evaluates a
      condition: condition evaluation stays owned by the canonical
      ``agentx.procedures.condition_evaluation`` module, driven by the caller
      with explicit facts. This module deliberately does not create a second
      condition evaluator.
    - END: the run transitions to the terminal
      ``PROCEDURE_CONTROL_TERMINATED`` control state and nothing else.

The interpreter imports nothing from ``agentx.kernel``,
``agentx.capabilities``, ``agentx.cognition``, ``agentx.learning``,
``agentx.hive``, or ``agentx.infrastructure``. There is no subprocess, no
network, no filesystem, no shell, no database, no timer, no thread, no
background work, and no dynamic execution surface (no eval/exec/compile/
``__import__``/importlib) anywhere in this module.

Procedure data is DATA, never authority
---------------------------------------

Node ``params`` remain opaque and inert. Hostile content such as
``permission=ADMIN``, ``risk=R0``, ``verified=true``, ``task succeeded``,
``skip ActionGate``, or ``clear emergency stop`` is carried verbatim inside an
instruction as inert data and can never change what the interpreter does: no
field of any state, instruction, or result value carries permission, risk,
budget, approval, or verification meaning. A ``ProcedureGraph`` never
authorizes its own actions; if a composition layer ever acts on an
``ACTION_REQUIRED`` instruction, it must still route the action through the
Trusted Kernel. This module creates no authority token, lowers no risk, and
approves nothing.

Procedure END is not task success
---------------------------------

Reaching the ``PROCEDURE_CONTROL_TERMINATED`` state means only that graph
control flow reached a canonical END node. It is NOT AgentX task success: the
interpreter never transitions a canonical Task, never creates a
VerificationResult, never claims the original user goal was satisfied, never
creates a ClosedLoopOutcome, and never claims external state is correct.
Invariant I1 stays absolute::

    NO ACTION == SUCCESS WITHOUT CANONICAL VERIFICATION.

Explicit, bounded, deterministic stepping
-----------------------------------------

State is explicit: an immutable :class:`ProcedureInterpreterState` value with
an explicit :class:`InterpreterStatus` member. There is no hidden mutable
execution state, no recursion, and no uncontrolled loop — one ``advance``
consumes exactly one explicit caller-supplied result and produces exactly one
new state, and every run carries a strict step ceiling (``max_steps``,
bounded by :data:`MAX_STEP_LIMIT`; zero, negative, boolean, or absurd limits
fail closed). Cycles are legal canonical graph structure, but interpretation
always halts: crossing the ceiling produces the explicit
``STEP_LIMIT_EXCEEDED`` failure state, never an unbounded walk. This module
owns only its own local procedure-step ceiling; it does not reuse the
cognition LoopGuard.

Failure routing follows only explicit canonical failure evidence: a
:class:`StepFailed` result names one canonical
:class:`~agentx.core.failure_taxonomy.FailureCategory`, and the route is read
from the canonical A3.07 :class:`~agentx.procedures.recovery.\
ProcedureRecoveryEdges` document — never guessed from text, and never
silently rerouted onto a normal ``NEXT`` edge. A failure with no declared
route fails the run explicitly (``NO_RECOVERY_ROUTE``).

Given an identical graph, identical limits, an identical starting state, and
identical explicit results, the interpreter always chooses identical
transitions: there is no randomness, no wall-clock branching, no environment
inspection, no model call, and no global state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from agentx.core.failure_taxonomy import FailureCategory
from agentx.procedures.branch import BranchContract, BranchContractError
from agentx.procedures.graph import (
    ProcedureEdgeKind,
    ProcedureGraph,
    ProcedureGraphError,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.procedures.recovery import ProcedureRecoveryEdges, RecoveryContractError

__all__ = [
    "DEFAULT_MAX_STEPS",
    "MAX_STEP_LIMIT",
    "BranchSelected",
    "InterpreterFailureReason",
    "InterpreterStatus",
    "ProcedureInstruction",
    "ProcedureInstructionKind",
    "ProcedureInterpreter",
    "ProcedureInterpreterError",
    "ProcedureInterpreterState",
    "StepCompleted",
    "StepFailed",
]

#: Conservative default step ceiling for one procedure interpretation run.
DEFAULT_MAX_STEPS: Final[int] = 256

#: Hard upper bound for any configured step ceiling. Larger values fail
#: closed: no configuration can make a run effectively unbounded.
MAX_STEP_LIMIT: Final[int] = 10_000


class ProcedureInterpreterError(ProcedureGraphError):
    """Raised when interpreter inputs violate this contract's request shape.

    This covers malformed graphs and limits at construction, forged or
    inconsistent state values, illegal state transitions (advancing a
    terminal state), wrong result types, undeclared branch outcomes, and
    branch selections that name no declared ``NEXT`` successor. It is a
    request-shape error only: it never carries authority and never encodes a
    run outcome. Run-level failures discovered during interpretation (a
    missing edge, an ambiguous edge, a missing recovery route, an exhausted
    step ceiling) are NOT exceptions — they are explicit
    :attr:`InterpreterStatus.FAILED` states carrying a typed
    :class:`InterpreterFailureReason`, so no failure is ever swallowed into a
    false success and no expected failure escapes as control flow.
    """


class InterpreterStatus(StrEnum):
    """The explicit, finite control-state vocabulary of one interpretation run.

    Every non-terminal member names exactly which explicit external result the
    interpreter is waiting for; the member is always derived deterministically
    from the canonical kind of the current node. The two terminal members are
    deliberately verbose about what they are NOT:

        - ``TERMINATED`` (``procedure_control_terminated``) means graph
          control flow reached a canonical END node — and nothing more. It is
          not task success, not verification, and not a claim about external
          state.
        - ``FAILED`` (``procedure_control_failed``) means the run failed
          structurally or exhausted its explicit bound; the paired
          :class:`InterpreterFailureReason` says exactly why.
    """

    AWAITING_ACTION_RESULT = "awaiting_action_result"
    AWAITING_OBSERVATION_RESULT = "awaiting_observation_result"
    AWAITING_VERIFICATION_RESULT = "awaiting_verification_result"
    AWAITING_BRANCH_SELECTION = "awaiting_branch_selection"
    AWAITING_TRANSFORM_RESULT = "awaiting_transform_result"
    AWAITING_REASON_RESULT = "awaiting_reason_result"
    AWAITING_RESEARCH_RESULT = "awaiting_research_result"
    AWAITING_WAIT_RESULT = "awaiting_wait_result"
    AWAITING_ROLLBACK_RESULT = "awaiting_rollback_result"
    AWAITING_SUBPROCEDURE_RESULT = "awaiting_subprocedure_result"
    TERMINATED = "procedure_control_terminated"
    FAILED = "procedure_control_failed"


class InterpreterFailureReason(StrEnum):
    """Why a run reached the explicit ``FAILED`` terminal control state.

    Members:
        STEP_LIMIT_EXCEEDED: The configured step ceiling was crossed. Cycles
            are legal graph structure, but interpretation is always bounded.
        NO_RECOVERY_ROUTE: A step reported an explicit canonical failure and
            the recovery document declares no route for that exact
            ``(node, failure category)`` pair. There is no implicit fallback
            to a ``NEXT`` edge and no implicit retry.
        MISSING_NEXT_EDGE: A completed non-branch node has no outgoing
            ``NEXT`` edge to follow.
        AMBIGUOUS_NEXT_EDGE: A completed non-branch node has more than one
            outgoing ``NEXT`` edge; a deterministic interpreter never guesses.
    """

    STEP_LIMIT_EXCEEDED = "step_limit_exceeded"
    NO_RECOVERY_ROUTE = "no_recovery_route"
    MISSING_NEXT_EDGE = "missing_next_edge"
    AMBIGUOUS_NEXT_EDGE = "ambiguous_next_edge"


class ProcedureInstructionKind(StrEnum):
    """What kind of external requirement the current node describes.

    Every member is a *requirement*, never a performance: the interpreter
    reports that reasoning, research, an action, an observation, a
    verification, a transform, a wait, a rollback, a subprocedure invocation,
    or a branch decision is required, and performs none of them.
    """

    ACTION_REQUIRED = "action_required"
    OBSERVATION_REQUIRED = "observation_required"
    VERIFICATION_REQUIRED = "verification_required"
    BRANCH_DECISION_REQUIRED = "branch_decision_required"
    TRANSFORM_REQUIRED = "transform_required"
    REASON_REQUIRED = "reason_required"
    RESEARCH_REQUIRED = "research_required"
    WAIT_REQUIRED = "wait_required"
    ROLLBACK_REQUIRED = "rollback_required"
    SUBPROCEDURE_REQUIRED = "subprocedure_required"


_STATUS_BY_KIND: Final[Mapping[ProcedureNodeKind, InterpreterStatus]] = MappingProxyType(
    {
        ProcedureNodeKind.ACTION: InterpreterStatus.AWAITING_ACTION_RESULT,
        ProcedureNodeKind.OBSERVE: InterpreterStatus.AWAITING_OBSERVATION_RESULT,
        ProcedureNodeKind.VERIFY: InterpreterStatus.AWAITING_VERIFICATION_RESULT,
        ProcedureNodeKind.BRANCH: InterpreterStatus.AWAITING_BRANCH_SELECTION,
        ProcedureNodeKind.TRANSFORM: InterpreterStatus.AWAITING_TRANSFORM_RESULT,
        ProcedureNodeKind.REASON: InterpreterStatus.AWAITING_REASON_RESULT,
        ProcedureNodeKind.RESEARCH: InterpreterStatus.AWAITING_RESEARCH_RESULT,
        ProcedureNodeKind.WAIT: InterpreterStatus.AWAITING_WAIT_RESULT,
        ProcedureNodeKind.ROLLBACK: InterpreterStatus.AWAITING_ROLLBACK_RESULT,
        ProcedureNodeKind.SUBPROCEDURE: InterpreterStatus.AWAITING_SUBPROCEDURE_RESULT,
    }
)

_INSTRUCTION_BY_KIND: Final[Mapping[ProcedureNodeKind, ProcedureInstructionKind]] = (
    MappingProxyType(
        {
            ProcedureNodeKind.ACTION: ProcedureInstructionKind.ACTION_REQUIRED,
            ProcedureNodeKind.OBSERVE: ProcedureInstructionKind.OBSERVATION_REQUIRED,
            ProcedureNodeKind.VERIFY: ProcedureInstructionKind.VERIFICATION_REQUIRED,
            ProcedureNodeKind.BRANCH: ProcedureInstructionKind.BRANCH_DECISION_REQUIRED,
            ProcedureNodeKind.TRANSFORM: ProcedureInstructionKind.TRANSFORM_REQUIRED,
            ProcedureNodeKind.REASON: ProcedureInstructionKind.REASON_REQUIRED,
            ProcedureNodeKind.RESEARCH: ProcedureInstructionKind.RESEARCH_REQUIRED,
            ProcedureNodeKind.WAIT: ProcedureInstructionKind.WAIT_REQUIRED,
            ProcedureNodeKind.ROLLBACK: ProcedureInstructionKind.ROLLBACK_REQUIRED,
            ProcedureNodeKind.SUBPROCEDURE: ProcedureInstructionKind.SUBPROCEDURE_REQUIRED,
        }
    )
)

_TERMINAL_STATUSES: Final[frozenset[InterpreterStatus]] = frozenset(
    {InterpreterStatus.TERMINATED, InterpreterStatus.FAILED}
)


def _validate_node_kind(value: object, *, field_name: str) -> ProcedureNodeKind:
    if not isinstance(value, ProcedureNodeKind):
        raise ProcedureInterpreterError(f"{field_name} must be a ProcedureNodeKind")
    return value


def _validate_node_id(value: object, *, field_name: str) -> ProcedureNodeId:
    if isinstance(value, ProcedureNodeId):
        return value
    if not isinstance(value, str):
        raise ProcedureInterpreterError(f"{field_name} must be a ProcedureNodeId or a string")
    try:
        return ProcedureNodeId.parse(value)
    except ProcedureGraphError as exc:
        raise ProcedureInterpreterError(f"invalid {field_name}: {exc}") from exc


def _validate_failure_category(value: object) -> FailureCategory:
    """Require an explicit canonical failure category member (never coerced).

    A failure category is explicit evidence supplied by the caller; free text
    is never interpreted into a category and an unknown string fails closed.
    """
    if isinstance(value, FailureCategory):
        return value
    if not isinstance(value, str):
        raise ProcedureInterpreterError("failure must be a canonical FailureCategory")
    try:
        return FailureCategory(value)
    except ValueError as exc:
        raise ProcedureInterpreterError(f"unknown failure category: {value!r}") from exc


def _validate_outcome_name(value: object) -> str:
    if not isinstance(value, str):
        raise ProcedureInterpreterError("branch outcome must be a string")
    if value == "" or value != value.strip():
        raise ProcedureInterpreterError("branch outcome must be non-empty and trimmed")
    return value


def _validate_max_steps(value: object) -> int:
    """Require an explicit, bounded, positive step ceiling; fail closed otherwise."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProcedureInterpreterError("max_steps must be an integer")
    if value < 1:
        raise ProcedureInterpreterError("max_steps must be a positive integer")
    if value > MAX_STEP_LIMIT:
        raise ProcedureInterpreterError(
            f"max_steps must not exceed the hard step ceiling {MAX_STEP_LIMIT}"
        )
    return value


# --------------------------------------------------------------------------
# Explicit caller-supplied step results.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class StepCompleted:
    """The caller's explicit statement that one required external step is done.

    ``node_kind`` names the canonical node family the caller believes it
    completed; ``advance`` rejects the result unless it matches the current
    node exactly (the "wrong result type" failure mode). The value carries no
    output, no evidence, no verdict, and no success claim beyond bare control
    progression — whether the external effect was correct is owned by
    canonical verification, elsewhere. BRANCH and END can never be
    "completed": a branch requires an explicit :class:`BranchSelected`
    decision, and END is entered by the interpreter itself.
    """

    node_kind: ProcedureNodeKind

    def __post_init__(self) -> None:
        kind = _validate_node_kind(self.node_kind, field_name="node_kind")
        if kind is ProcedureNodeKind.END:
            raise ProcedureInterpreterError("END is entered by the interpreter, never completed")
        if kind is ProcedureNodeKind.BRANCH:
            raise ProcedureInterpreterError(
                "a BRANCH node requires an explicit BranchSelected decision"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class StepFailed:
    """The caller's explicit, typed statement that one required step failed.

    ``failure`` is exactly one canonical
    :class:`~agentx.core.failure_taxonomy.FailureCategory` member — explicit
    failure evidence, never text the interpreter guessed a failure from.
    Reporting a failure performs nothing: routing follows only the canonical
    A3.07 recovery document, and a failure with no declared route fails the
    run explicitly.
    """

    node_kind: ProcedureNodeKind
    failure: FailureCategory

    def __post_init__(self) -> None:
        kind = _validate_node_kind(self.node_kind, field_name="node_kind")
        if kind is ProcedureNodeKind.END:
            raise ProcedureInterpreterError("END is terminal and can never report a step failure")
        object.__setattr__(self, "failure", _validate_failure_category(self.failure))


@dataclass(frozen=True, slots=True, kw_only=True)
class BranchSelected:
    """The caller's explicit branch decision for one BRANCH node.

    ``outcome`` must name an outcome the node's canonical
    :class:`~agentx.procedures.branch.BranchContract` declares, and ``target``
    must be one of the node's declared ``NEXT`` successors. How the caller
    decided the outcome is its own concern — the canonical single-condition
    evaluator (``agentx.procedures.condition_evaluation``) exists for exactly
    that, driven by explicit facts. The interpreter only validates and follows
    the declared graph data; it never evaluates a condition itself.
    """

    outcome: str
    target: ProcedureNodeId

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", _validate_outcome_name(self.outcome))
        object.__setattr__(
            self, "target", _validate_node_id(self.target, field_name="branch target")
        )


# --------------------------------------------------------------------------
# Inert instruction and explicit state values.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureInstruction:
    """One typed, frozen, inert description of what must happen externally.

    The instruction echoes canonical node identity (``node_id``, ``node_kind``,
    ``label``) plus the node's opaque ``params`` exactly as the graph carries
    them: inert data, never interpreted, never resolved, never dispatched. For
    BRANCH nodes it additionally names the declared outcome vocabulary (in
    author priority order) and the candidate ``NEXT`` targets (in canonical
    graph order).

    An instruction is a requirement, not a performance, and it is not
    authority: acting on it remains the job of a future composition layer
    that must still pass through the Trusted Kernel.
    """

    kind: ProcedureInstructionKind
    node_id: ProcedureNodeId
    node_kind: ProcedureNodeKind
    label: str | None = None
    params: Mapping[str, object] = field(default_factory=dict)
    branch_outcomes: tuple[str, ...] = ()
    branch_targets: tuple[ProcedureNodeId, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProcedureInstructionKind):
            raise ProcedureInterpreterError("instruction kind must be a ProcedureInstructionKind")
        object.__setattr__(self, "node_id", _validate_node_id(self.node_id, field_name="node_id"))
        _validate_node_kind(self.node_kind, field_name="node_kind")
        if self.label is not None and not isinstance(self.label, str):
            raise ProcedureInterpreterError("instruction label must be a string or None")
        if not isinstance(self.params, Mapping):
            raise ProcedureInterpreterError("instruction params must be a mapping")
        if not isinstance(self.branch_outcomes, tuple):
            raise ProcedureInterpreterError("branch_outcomes must be a tuple")
        if not isinstance(self.branch_targets, tuple):
            raise ProcedureInterpreterError("branch_targets must be a tuple")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureInterpreterState:
    """The complete, explicit, immutable control state of one run.

    ``status`` is the explicit finite-state-machine member; ``current_node``
    names the canonical node the state is at (for terminal states, the END
    node reached or the node at which the run failed); ``steps_taken`` counts
    ``advance`` operations consumed so far; ``failure_reason`` is present
    exactly when ``status`` is ``FAILED``.

    There is deliberately nothing else: no task, no outcome, no verdict, no
    evidence, no success flag, no timestamp, and no generated identity. A
    ``TERMINATED`` state means procedure CONTROL completed — it is
    structurally incapable of claiming task success or verified external
    state.
    """

    status: InterpreterStatus
    current_node: ProcedureNodeId
    steps_taken: int
    failure_reason: InterpreterFailureReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, InterpreterStatus):
            raise ProcedureInterpreterError("status must be an InterpreterStatus")
        object.__setattr__(
            self, "current_node", _validate_node_id(self.current_node, field_name="current_node")
        )
        if not isinstance(self.steps_taken, int) or isinstance(self.steps_taken, bool):
            raise ProcedureInterpreterError("steps_taken must be an integer")
        if self.steps_taken < 0:
            raise ProcedureInterpreterError("steps_taken must not be negative")
        if self.status is InterpreterStatus.FAILED:
            if not isinstance(self.failure_reason, InterpreterFailureReason):
                raise ProcedureInterpreterError(
                    "a FAILED state must carry an InterpreterFailureReason"
                )
        elif self.failure_reason is not None:
            raise ProcedureInterpreterError("only a FAILED state may carry a failure_reason")

    @property
    def is_terminal(self) -> bool:
        """Whether this state is one of the two terminal control states."""
        return self.status in _TERMINAL_STATUSES

    @property
    def is_terminated(self) -> bool:
        """Whether graph control flow reached a canonical END node.

        This is procedure CONTROL termination only — never task success,
        never verification, and never a claim about external state.
        """
        return self.status is InterpreterStatus.TERMINATED

    @property
    def is_failed(self) -> bool:
        """Whether the run reached the explicit FAILED control state."""
        return self.status is InterpreterStatus.FAILED


# --------------------------------------------------------------------------
# The interpreter.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureInterpreter:
    """A deterministic, bounded control-flow interpreter for one graph.

    The interpreter value is the immutable *program*: one canonical graph, an
    optional canonical recovery document (cross-checked against the graph at
    construction), and one explicit bounded step ceiling. Runs are pure value
    transformations over :class:`ProcedureInterpreterState`:

        - :meth:`start` returns the initial state at the canonical entry node;
        - :meth:`instruction` inspects a waiting state and returns the inert
          instruction describing what must happen externally;
        - :meth:`advance` consumes exactly one explicit caller-supplied result
          and returns exactly one new state.

    Construction fails closed on anything that is not exactly canonical data:
    a non-graph, a malformed recovery document, a recovery document that does
    not bind to the graph, a BRANCH node whose params are not exactly a
    canonical :class:`~agentx.procedures.branch.BranchContract`, or an invalid
    step ceiling.
    """

    graph: ProcedureGraph
    recovery: ProcedureRecoveryEdges | None = None
    max_steps: int = DEFAULT_MAX_STEPS

    _nodes_by_id: Mapping[str, ProcedureNode] = field(init=False, repr=False, compare=False)
    _next_targets: Mapping[str, tuple[ProcedureNodeId, ...]] = field(
        init=False, repr=False, compare=False
    )
    _branch_contracts: Mapping[str, BranchContract] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.graph, ProcedureGraph):
            raise ProcedureInterpreterError("graph must be a canonical ProcedureGraph")
        object.__setattr__(self, "max_steps", _validate_max_steps(self.max_steps))

        if self.recovery is not None:
            if not isinstance(self.recovery, ProcedureRecoveryEdges):
                raise ProcedureInterpreterError(
                    "recovery must be a canonical ProcedureRecoveryEdges document or None"
                )
            try:
                self.recovery.bind_to_graph(self.graph)
            except RecoveryContractError as exc:
                raise ProcedureInterpreterError(
                    f"recovery document does not bind to the graph: {exc}"
                ) from exc

        nodes_by_id: dict[str, ProcedureNode] = {
            node.id.to_str(): node for node in self.graph.nodes
        }

        # Canonical successor order is inherited from the graph's own
        # normalized edge ordering, so traversal is order-independent data.
        next_targets: dict[str, list[ProcedureNodeId]] = {}
        for edge in self.graph.edges:
            if edge.kind is ProcedureEdgeKind.NEXT:
                next_targets.setdefault(edge.source.to_str(), []).append(edge.target)

        branch_contracts: dict[str, BranchContract] = {}
        for node in self.graph.nodes:
            if node.kind is ProcedureNodeKind.BRANCH:
                try:
                    branch_contracts[node.id.to_str()] = BranchContract.bind(node)
                except BranchContractError as exc:
                    raise ProcedureInterpreterError(
                        f"BRANCH node {node.id.to_str()!r} does not carry a canonical "
                        f"branch contract: {exc}"
                    ) from exc

        object.__setattr__(self, "_nodes_by_id", MappingProxyType(nodes_by_id))
        object.__setattr__(
            self,
            "_next_targets",
            MappingProxyType({source: tuple(targets) for source, targets in next_targets.items()}),
        )
        object.__setattr__(self, "_branch_contracts", MappingProxyType(branch_contracts))

    # -- state construction helpers (pure) --------------------------------

    def _node(self, node_id: ProcedureNodeId) -> ProcedureNode:
        node = self._nodes_by_id.get(node_id.to_str())
        if node is None:
            raise ProcedureInterpreterError(
                f"state references a node that does not exist in the graph: {node_id.to_str()!r}"
            )
        return node

    def _enter(self, node_id: ProcedureNodeId, *, steps_taken: int) -> ProcedureInterpreterState:
        node = self._node(node_id)
        if node.kind is ProcedureNodeKind.END:
            return ProcedureInterpreterState(
                status=InterpreterStatus.TERMINATED,
                current_node=node.id,
                steps_taken=steps_taken,
            )
        return ProcedureInterpreterState(
            status=_STATUS_BY_KIND[node.kind],
            current_node=node.id,
            steps_taken=steps_taken,
        )

    def _fail(
        self,
        node_id: ProcedureNodeId,
        *,
        steps_taken: int,
        reason: InterpreterFailureReason,
    ) -> ProcedureInterpreterState:
        return ProcedureInterpreterState(
            status=InterpreterStatus.FAILED,
            current_node=node_id,
            steps_taken=steps_taken,
            failure_reason=reason,
        )

    def _validated_waiting_node(self, state: ProcedureInterpreterState) -> ProcedureNode:
        """Re-derive the expected status from graph data; reject forged states."""
        if not isinstance(state, ProcedureInterpreterState):
            raise ProcedureInterpreterError("state must be a ProcedureInterpreterState")
        if state.is_terminal:
            raise ProcedureInterpreterError(
                f"illegal transition: state {state.status.value!r} is terminal"
            )
        if state.steps_taken > self.max_steps:
            raise ProcedureInterpreterError("state step count exceeds the configured ceiling")
        node = self._node(state.current_node)
        if node.kind is ProcedureNodeKind.END:
            raise ProcedureInterpreterError(
                "state is inconsistent: a non-terminal state cannot sit at an END node"
            )
        if _STATUS_BY_KIND[node.kind] is not state.status:
            raise ProcedureInterpreterError(
                f"state is inconsistent: node {node.id.to_str()!r} of kind "
                f"{node.kind.value!r} cannot be awaited as {state.status.value!r}"
            )
        return node

    # -- public API --------------------------------------------------------

    def start(self) -> ProcedureInterpreterState:
        """Return the initial control state at the canonical entry node.

        Starting performs nothing: it reads only graph data. If the entry
        node is a canonical END node, the run is immediately
        ``PROCEDURE_CONTROL_TERMINATED`` — which is still not task success.
        """
        return self._enter(self.graph.entry, steps_taken=0)

    def instruction(self, state: ProcedureInterpreterState) -> ProcedureInstruction:
        """Describe, as inert data, what must happen externally next.

        The instruction is a typed reading of canonical node data. Nothing is
        executed, evaluated, resolved, invoked, or loaded — hostile strings in
        node params are carried verbatim as inert data. Terminal states have
        no instruction and fail closed.
        """
        node = self._validated_waiting_node(state)
        if node.kind is ProcedureNodeKind.BRANCH:
            contract = self._branch_contracts[node.id.to_str()]
            return ProcedureInstruction(
                kind=ProcedureInstructionKind.BRANCH_DECISION_REQUIRED,
                node_id=node.id,
                node_kind=node.kind,
                label=node.label,
                params=node.params,
                branch_outcomes=tuple(outcome.name for outcome in contract.outcomes),
                branch_targets=self._next_targets.get(node.id.to_str(), ()),
            )
        return ProcedureInstruction(
            kind=_INSTRUCTION_BY_KIND[node.kind],
            node_id=node.id,
            node_kind=node.kind,
            label=node.label,
            params=node.params,
        )

    def advance(
        self,
        state: ProcedureInterpreterState,
        step_result: StepCompleted | StepFailed | BranchSelected,
    ) -> ProcedureInterpreterState:
        """Consume one explicit result and return exactly one new state.

        The operation is a pure function of ``(graph, recovery, max_steps,
        state, step_result)``: identical inputs always produce an equal state.
        It validates the state against graph data, validates the explicit
        result type against the current node, applies the one deterministic
        canonical transition, and enforces the step ceiling — returning the
        explicit ``STEP_LIMIT_EXCEEDED`` failure state instead of walking
        past the bound.
        """
        node = self._validated_waiting_node(state)

        steps_taken = state.steps_taken + 1
        if steps_taken > self.max_steps:
            return self._fail(
                node.id,
                steps_taken=state.steps_taken,
                reason=InterpreterFailureReason.STEP_LIMIT_EXCEEDED,
            )

        if isinstance(step_result, BranchSelected):
            return self._advance_branch(node, step_result, steps_taken=steps_taken)
        if isinstance(step_result, StepCompleted):
            return self._advance_completed(node, step_result, steps_taken=steps_taken)
        if isinstance(step_result, StepFailed):
            return self._advance_failed(node, step_result, steps_taken=steps_taken)
        raise ProcedureInterpreterError(
            "step_result must be a StepCompleted, StepFailed, or BranchSelected"
        )

    # -- transition rules (pure) -------------------------------------------

    def _advance_branch(
        self, node: ProcedureNode, step_result: BranchSelected, *, steps_taken: int
    ) -> ProcedureInterpreterState:
        if node.kind is not ProcedureNodeKind.BRANCH:
            raise ProcedureInterpreterError(
                f"wrong result type: node {node.id.to_str()!r} of kind {node.kind.value!r} "
                "does not take a BranchSelected decision"
            )
        contract = self._branch_contracts[node.id.to_str()]
        declared = {outcome.name for outcome in contract.outcomes}
        if step_result.outcome not in declared:
            raise ProcedureInterpreterError(
                f"branch outcome {step_result.outcome!r} is not declared by node "
                f"{node.id.to_str()!r}"
            )
        candidates = self._next_targets.get(node.id.to_str(), ())
        if not candidates:
            return self._fail(
                node.id,
                steps_taken=steps_taken,
                reason=InterpreterFailureReason.MISSING_NEXT_EDGE,
            )
        if step_result.target not in candidates:
            raise ProcedureInterpreterError(
                f"branch target {step_result.target.to_str()!r} is not a declared NEXT "
                f"successor of node {node.id.to_str()!r}"
            )
        return self._enter(step_result.target, steps_taken=steps_taken)

    def _advance_completed(
        self, node: ProcedureNode, step_result: StepCompleted, *, steps_taken: int
    ) -> ProcedureInterpreterState:
        if step_result.node_kind is not node.kind:
            raise ProcedureInterpreterError(
                f"wrong result type: node {node.id.to_str()!r} is {node.kind.value!r}, "
                f"result claims {step_result.node_kind.value!r}"
            )
        candidates = self._next_targets.get(node.id.to_str(), ())
        if not candidates:
            return self._fail(
                node.id,
                steps_taken=steps_taken,
                reason=InterpreterFailureReason.MISSING_NEXT_EDGE,
            )
        if len(candidates) > 1:
            return self._fail(
                node.id,
                steps_taken=steps_taken,
                reason=InterpreterFailureReason.AMBIGUOUS_NEXT_EDGE,
            )
        return self._enter(candidates[0], steps_taken=steps_taken)

    def _advance_failed(
        self, node: ProcedureNode, step_result: StepFailed, *, steps_taken: int
    ) -> ProcedureInterpreterState:
        if step_result.node_kind is not node.kind:
            raise ProcedureInterpreterError(
                f"wrong result type: node {node.id.to_str()!r} is {node.kind.value!r}, "
                f"result claims {step_result.node_kind.value!r}"
            )
        if self.recovery is None:
            return self._fail(
                node.id,
                steps_taken=steps_taken,
                reason=InterpreterFailureReason.NO_RECOVERY_ROUTE,
            )
        target = self.recovery.declared_target(node.id, step_result.failure)
        if target is None:
            return self._fail(
                node.id,
                steps_taken=steps_taken,
                reason=InterpreterFailureReason.NO_RECOVERY_ROUTE,
            )
        return self._enter(target, steps_taken=steps_taken)
