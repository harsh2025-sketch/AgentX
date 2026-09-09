"""Top-level L3 guided-procedure strategy adapter for A2.10.

This module is composition wiring only. It binds one explicitly selected,
already-applicable canonical A3.01 Procedure to the ``L3_GUIDED`` strategy
level and walks it with the canonical M3.01
:class:`~agentx.procedures.interpreter.ProcedureInterpreter`, dispatching each
node family to the canonical contract that already owns it:

* ``ACTION`` -> the canonical A2.04 :class:`~agentx.capabilities.executor.Executor`
  over the A1.10 ``CapabilityExecutionLoop`` (registry resolution, permissions,
  ActionGate, EmergencyStop, ResourceBudget, execution, independent capability
  verification, Task transitions, events, and audit all stay inside it);
* ``REASON`` -> the canonical A2.03 :class:`~agentx.cognition.reasoner.Reasoner`
  boundary, invoked once per canonical REASON region and never anywhere else;
* ``END`` -> procedure control termination, which is emphatically *not* task
  success.

What "guided" means here
------------------------

    L3 = deterministic Procedure + bounded reasoning at declared uncertain
    regions.

The adapter is not an agent loop. It never plans, never chooses what to do
next, never retries, never escalates, never falls back to another level, never
researches, and never re-routes a failure. Control flow comes exclusively from
the canonical interpreter walking canonical graph data.

Reasoning happens for exactly one reason: the canonical Procedure declares a
``REASON`` node, the interpreter reports the canonical
:attr:`~agentx.procedures.interpreter.ProcedureInstructionKind.REASON_REQUIRED`
instruction for it, and the composition root explicitly bound that node with a
:class:`GuidedReasoningBinding` and an explicit non-zero reasoning bound.
Reasoning is never triggered because a string "looks hard", because a node
label, objective, param value, or Task objective asks for a model, because an
action failed, or because the adapter is unsure. Free text is never inspected
for control meaning: only the canonical typed ``ProcedureNodeKind.REASON`` /
``REASON_REQUIRED`` vocabulary can request reasoning, and a Procedure that
declares no REASON region provably performs zero model calls.

Reasoning output is inert data
------------------------------

A :class:`~agentx.cognition.reasoner.ReasonerResult` is untrusted cognitive
data. The adapter reads exactly one thing from it — the concatenated inert
:class:`~agentx.cognition.model_provider.TextContent` — and writes it into the
one typed reasoning-output slot the canonical A3.04
:class:`~agentx.procedures.reason_research.ReasonNodeSpec` declares
(``output_binding``). That slot is a string-keyed store of strings, readable
only by a later REASON node that explicitly names it in ``input_references``.

Model text therefore cannot grant a :class:`~agentx.kernel.permissions.Permission`,
lower a risk level, bypass the ``ActionGate``, widen a ``ResourceEnvelope``,
clear an ``EmergencyStop``, select or rewrite a capability request, choose a
branch, fabricate verification, activate a Procedure, or mark a Task
successful. It cannot even decide whether the procedure continues: continuation
follows the canonical interpreter's ``NEXT`` edges, and the capability executed
at an ``ACTION`` node is the exact pre-bound canonical
:class:`~agentx.capabilities.abi.CapabilityRequest` supplied at composition
time — never anything derived from node params, model output, or Task text.

Procedure END is not Task success
---------------------------------

Reaching a canonical END node means graph control flow terminated. Nothing
more. This adapter never constructs a
:class:`~agentx.capabilities.abi.VerificationResult`, never transitions the
orchestrated Task, and structurally cannot report task verification: the
:class:`GuidedProcedureRun` value it produces pins ``task_verification`` to
:attr:`~agentx.core.procedure_execution.ProcedureTaskVerification.NOT_ASSESSED`.
What it hands back to A2.10 is the canonical A1.10 governed result of the last
capability dispatch, exactly as the Executor produced it; A2.10 then re-checks
that evidence against the caller's explicit A2.05
:class:`~agentx.capabilities.verifier.VerificationRequirement`. A run that
reaches END without ever dispatching a governed action carries no canonical
evidence at all and fails closed, so a successful Reasoner call, a successful
ACTION node, and procedure control termination can none of them become task
success.

Deliberate non-scope
--------------------

No second Reasoner, interpreter, Executor, Verifier, router, escalation table,
anti-loop engine, planner, prompt framework, research engine, condition
evaluator, transform engine, subprocedure loader, recovery driver, or
persistence write. Node families this adapter has no canonical governed
collaborator for — ``OBSERVE``, ``VERIFY``, ``BRANCH``, ``TRANSFORM``,
``RESEARCH``, ``WAIT``, ``ROLLBACK``, ``SUBPROCEDURE`` — are rejected at
binding construction rather than improvised at runtime. In particular a
Procedure containing a canonical ``RESEARCH`` region is simply not bindable
here: this adapter never performs research and owns no research provider.

Bounds
------

Every run is finite by construction: the walk is a bounded ``for`` over the
canonical interpreter's own ``max_steps`` ceiling, and reasoning invocations
are bounded by an explicit ``max_reasoning_calls`` that defaults to zero.
That bound is orchestration control flow only — C1.08 remains the sole
resource-accounting authority, and this module never constructs, reads,
consumes, resets, or widens a ``ResourceEnvelope``. A1.07 cancellation and
deadline state is observed before every step and is never converted into
success.

Owner: N2.05. Top-level composition only; adds no subsystem edge.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

from agentx.agent_loop import StrategyResult
from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.executor import Executor, ExecutorRequest
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.cognition.model_provider import TextContent
from agentx.cognition.reasoner import Reasoner, ReasonerRequest, ReasonerResult
from agentx.cognition.router import ExecutionLevel
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext, ExecutionStopStatus, MonotonicClock
from agentx.core.procedure_execution import (
    ExecutedNodeKind,
    ProcedureRunDisposition,
    ProcedureStepDisposition,
    ProcedureTaskVerification,
)
from agentx.core.result import Result
from agentx.core.tasks import Task
from agentx.procedures.graph import ProcedureNode, ProcedureNodeId, ProcedureNodeKind
from agentx.procedures.interpreter import (
    ProcedureInstruction,
    ProcedureInstructionKind,
    ProcedureInterpreter,
    ProcedureInterpreterState,
    StepCompleted,
)
from agentx.procedures.nodes import ActionNodeSpec
from agentx.procedures.reason_research import ReasonNodeSpec, ReasonResearchContractError

__all__ = [
    "GUIDED_STRATEGY_LEVEL",
    "MAX_GUIDED_REASONING_CALLS",
    "MAX_REASONING_INSTRUCTION_CHARS",
    "MAX_REASONING_OUTPUT_CHARS",
    "GuidedActionBinding",
    "GuidedProcedureBinding",
    "GuidedProcedureBindingError",
    "GuidedProcedureRun",
    "GuidedProcedureStrategy",
    "GuidedReasoningBinding",
    "GuidedStepRecord",
]

#: The only execution level this adapter may serve. There is no fallback to
#: L2, no escalation to L4/L5, and no "try another level" path.
GUIDED_STRATEGY_LEVEL: Final[ExecutionLevel] = ExecutionLevel.L3_GUIDED

#: Hard ceiling on the configurable per-run reasoning bound. Bounded reasoning
#: is the whole point of L3; no configuration can make it unbounded.
MAX_GUIDED_REASONING_CALLS: Final[int] = 16

#: Maximum size of one constructed reasoning instruction. Oversized inert
#: procedure/reasoning text fails closed instead of being silently truncated.
MAX_REASONING_INSTRUCTION_CHARS: Final[int] = 8_192

#: Maximum size of one stored reasoning output slot value. Hostile or runaway
#: model output fails closed rather than growing the next instruction.
MAX_REASONING_OUTPUT_CHARS: Final[int] = 8_192

#: Error-code prefix for every structured error this adapter produces.
_ERROR_PREFIX: Final[str] = "guided_procedure_strategy"

#: Canonical node kinds this adapter has an explicit governed collaborator for.
#: Everything else is rejected at binding construction, never improvised.
_SUPPORTED_NODE_KINDS: Final[frozenset[ProcedureNodeKind]] = frozenset(
    {
        ProcedureNodeKind.ACTION,
        ProcedureNodeKind.REASON,
        ProcedureNodeKind.END,
    }
)

#: Typed map from the canonical A1.10 outcome verdict to the canonical
#: procedure-execution step vocabulary. Reading the ``LoopOutcome`` enum is the
#: only thing that decides whether procedure control may continue; no message,
#: summary, observation value, or error text is ever consulted.
_STEP_DISPOSITION_BY_OUTCOME: Final[Mapping[LoopOutcome, ProcedureStepDisposition]] = (
    MappingProxyType(
        {
            LoopOutcome.VERIFIED: ProcedureStepDisposition.VERIFIED,
            LoopOutcome.VERIFICATION_FAILED: ProcedureStepDisposition.VERIFICATION_FAILED,
            LoopOutcome.EXECUTION_FAILED: ProcedureStepDisposition.EXECUTION_FAILED,
            LoopOutcome.DENIED: ProcedureStepDisposition.DENIED,
        }
    )
)

#: Step dispositions that record a governed capability dispatch actually
#: happening, i.e. the only ones that can accompany canonical A1.10 evidence.
_GOVERNED_STEP_DISPOSITIONS: Final[frozenset[ProcedureStepDisposition]] = frozenset(
    {
        ProcedureStepDisposition.VERIFIED,
        ProcedureStepDisposition.VERIFICATION_FAILED,
        ProcedureStepDisposition.EXECUTION_FAILED,
        ProcedureStepDisposition.DENIED,
    }
)


class GuidedProcedureBindingError(ValueError):
    """Raised when a guided-procedure binding is internally inconsistent.

    This is a composition-shape error only. It never carries, encodes, or
    implies an authority decision, a verification verdict, or a Task
    transition.
    """


def _error(
    *,
    code: str,
    message: str,
    category: ErrorCategory,
    details: dict[str, object] | None = None,
    retryability: Retryability = Retryability.NON_RETRYABLE,
) -> AgentXError:
    """Build one structured guided-strategy error value."""
    return AgentXError(
        code=f"{_ERROR_PREFIX}.{code}",
        message=message,
        category=category,
        retryability=retryability,
        details=details,
    )


def _require_node_id(value: object, *, field_name: str) -> ProcedureNodeId:
    if not isinstance(value, ProcedureNodeId):
        raise TypeError(f"{field_name} must be a ProcedureNodeId, got {type(value).__name__}")
    return value


def _require_slot_name(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise GuidedProcedureBindingError(f"{field_name} must be non-empty and trimmed")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class GuidedActionBinding:
    """Immutable binding of one canonical ACTION node to one canonical request.

    The :class:`~agentx.capabilities.abi.CapabilityRequest` is supplied by the
    composition root, never derived from node params, Task text, or model
    output. A canonical A3.02 :class:`~agentx.procedures.nodes.ActionNodeSpec`
    describes an action; it is deliberately not a request and cannot become
    one, so hostile procedure data can never choose the capability, its typed
    parameters, its permissions, its risk, or its verification.
    """

    node_id: ProcedureNodeId
    request: CapabilityRequest[Any]

    def __post_init__(self) -> None:
        _require_node_id(self.node_id, field_name="node_id")
        if not isinstance(self.request, CapabilityRequest):
            raise TypeError(
                f"request must be a CapabilityRequest, got {type(self.request).__name__}"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class GuidedReasoningBinding:
    """Immutable binding of one canonical REASON node to its declared slot.

    ``output_binding`` must equal, exactly, the ``output_binding`` the node's
    canonical A3.04 :class:`~agentx.procedures.reason_research.ReasonNodeSpec`
    declares. The composition root therefore states in advance which reasoning
    regions exist and where their inert output may land; a mismatch fails
    closed at construction.

    There is deliberately no prompt, template, provider, model, temperature,
    tool, permission, risk, budget, or trust field: the A2.03 Reasoner owns the
    model binding, and its output is data.
    """

    node_id: ProcedureNodeId
    output_binding: str
    max_output_tokens: int | None = None

    def __post_init__(self) -> None:
        _require_node_id(self.node_id, field_name="node_id")
        object.__setattr__(
            self,
            "output_binding",
            _require_slot_name(self.output_binding, field_name="output_binding"),
        )
        if self.max_output_tokens is not None:
            if type(self.max_output_tokens) is not int:
                raise TypeError("max_output_tokens must be an int or None")
            if self.max_output_tokens < 1:
                raise GuidedProcedureBindingError(
                    "max_output_tokens must be at least 1 when provided"
                )


@dataclass(frozen=True, slots=True, kw_only=True)
class GuidedProcedureBinding:
    """One explicitly selected Procedure, fully bound for guided execution.

    ``interpreter`` is the canonical M3.01 interpreter the composition root
    already built around the explicitly selected, applicable Procedure graph
    (with its own step ceiling). This adapter never selects, matches,
    validates, activates, promotes, loads, or stores a Procedure: procedure
    selection and lifecycle stay with their canonical owners.

    Construction is strict and fails closed. Every ``ACTION`` node must carry
    exactly one :class:`GuidedActionBinding` whose canonical request identity
    matches the node's canonical A3.02 spec; every ``REASON`` node must carry
    exactly one :class:`GuidedReasoningBinding` matching the node's canonical
    A3.04 declared output slot; bindings for unknown nodes are rejected; and a
    graph containing any other node kind is rejected outright rather than
    improvised at runtime.

    ``max_reasoning_calls`` is an explicit finite bound on Reasoner
    invocations for one run and **defaults to zero**: reasoning is opt-in. It
    bounds orchestration control flow only and is not resource accounting.
    """

    interpreter: ProcedureInterpreter
    action_bindings: tuple[GuidedActionBinding, ...] = ()
    reasoning_bindings: tuple[GuidedReasoningBinding, ...] = ()
    max_reasoning_calls: int = 0
    level: ExecutionLevel = GUIDED_STRATEGY_LEVEL

    _actions_by_node: Mapping[str, GuidedActionBinding] = field(
        init=False, repr=False, compare=False
    )
    _reasoning_by_node: Mapping[str, GuidedReasoningBinding] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.interpreter, ProcedureInterpreter):
            raise TypeError(
                "interpreter must be a canonical ProcedureInterpreter, "
                f"got {type(self.interpreter).__name__}"
            )
        if not isinstance(self.level, ExecutionLevel):
            raise TypeError(f"level must be an ExecutionLevel, got {type(self.level).__name__}")
        if self.level is not GUIDED_STRATEGY_LEVEL:
            raise GuidedProcedureBindingError(
                "guided procedure strategy bindings must use L3_GUIDED"
            )
        if type(self.max_reasoning_calls) is not int:
            raise TypeError("max_reasoning_calls must be an int")
        if self.max_reasoning_calls < 0:
            raise GuidedProcedureBindingError("max_reasoning_calls must not be negative")
        if self.max_reasoning_calls > MAX_GUIDED_REASONING_CALLS:
            raise GuidedProcedureBindingError(
                f"max_reasoning_calls must not exceed {MAX_GUIDED_REASONING_CALLS}"
            )

        actions = _index_action_bindings(self.action_bindings)
        reasoning = _index_reasoning_bindings(self.reasoning_bindings)
        _validate_graph_bindings(self.interpreter, actions, reasoning)
        if reasoning and self.max_reasoning_calls < 1:
            raise GuidedProcedureBindingError(
                "the bound procedure declares REASON regions, so max_reasoning_calls "
                "must be at least 1; reasoning is never implicitly permitted"
            )

        object.__setattr__(self, "_actions_by_node", MappingProxyType(actions))
        object.__setattr__(self, "_reasoning_by_node", MappingProxyType(reasoning))

    @property
    def declares_reasoning_regions(self) -> bool:
        """Whether the bound Procedure declares any canonical REASON region."""
        return bool(self._reasoning_by_node)

    @property
    def reasoning_output_slots(self) -> tuple[str, ...]:
        """Return the declared reasoning-output slot names, sorted."""
        return tuple(sorted(binding.output_binding for binding in self.reasoning_bindings))

    def action_binding(self, node_id: ProcedureNodeId) -> GuidedActionBinding | None:
        """Return the explicit ACTION binding for ``node_id``, if any."""
        return self._actions_by_node.get(_require_node_id(node_id, field_name="node_id").to_str())

    def reasoning_binding(self, node_id: ProcedureNodeId) -> GuidedReasoningBinding | None:
        """Return the explicit REASON binding for ``node_id``, if any."""
        return self._reasoning_by_node.get(_require_node_id(node_id, field_name="node_id").to_str())


def _index_action_bindings(
    bindings: object,
) -> dict[str, GuidedActionBinding]:
    if not isinstance(bindings, tuple):
        raise TypeError("action_bindings must be a tuple of GuidedActionBinding")
    indexed: dict[str, GuidedActionBinding] = {}
    for binding in bindings:
        if not isinstance(binding, GuidedActionBinding):
            raise TypeError("action_bindings must contain only GuidedActionBinding values")
        key = binding.node_id.to_str()
        if key in indexed:
            raise GuidedProcedureBindingError(f"duplicate ACTION binding for node {key!r}")
        indexed[key] = binding
    return indexed


def _index_reasoning_bindings(
    bindings: object,
) -> dict[str, GuidedReasoningBinding]:
    if not isinstance(bindings, tuple):
        raise TypeError("reasoning_bindings must be a tuple of GuidedReasoningBinding")
    indexed: dict[str, GuidedReasoningBinding] = {}
    for binding in bindings:
        if not isinstance(binding, GuidedReasoningBinding):
            raise TypeError("reasoning_bindings must contain only GuidedReasoningBinding values")
        key = binding.node_id.to_str()
        if key in indexed:
            raise GuidedProcedureBindingError(f"duplicate REASON binding for node {key!r}")
        indexed[key] = binding
    return indexed


def _validate_action_node(node: ProcedureNode, binding: GuidedActionBinding | None) -> None:
    key = node.id.to_str()
    if binding is None:
        raise GuidedProcedureBindingError(
            f"ACTION node {key!r} has no explicit CapabilityRequest binding; the guided "
            "adapter never derives a capability from procedure data"
        )
    spec = ActionNodeSpec.from_node(node)
    identity = binding.request.identity
    if identity.name.value != spec.capability_name:
        raise GuidedProcedureBindingError(
            f"ACTION node {key!r} declares capability {spec.capability_name!r} but the bound "
            f"request targets {identity.name.value!r}"
        )
    if identity.version.to_str() != spec.capability_version:
        raise GuidedProcedureBindingError(
            f"ACTION node {key!r} declares capability version {spec.capability_version!r} "
            f"but the bound request targets {identity.version.to_str()!r}"
        )


def _validate_reason_node(node: ProcedureNode, binding: GuidedReasoningBinding | None) -> None:
    key = node.id.to_str()
    if binding is None:
        raise GuidedProcedureBindingError(
            f"REASON node {key!r} has no explicit reasoning binding; reasoning regions must "
            "be declared at composition time"
        )
    try:
        spec = ReasonNodeSpec.from_node(node)
    except ReasonResearchContractError as exc:
        raise GuidedProcedureBindingError(
            f"REASON node {key!r} does not carry a canonical A3.04 REASON payload: {exc}"
        ) from exc
    if spec.output_binding != binding.output_binding:
        raise GuidedProcedureBindingError(
            f"REASON node {key!r} declares output slot {spec.output_binding!r} but the "
            f"binding declares {binding.output_binding!r}"
        )


def _validate_graph_bindings(
    interpreter: ProcedureInterpreter,
    actions: Mapping[str, GuidedActionBinding],
    reasoning: Mapping[str, GuidedReasoningBinding],
) -> None:
    """Cross-check every graph node against the explicit bindings, fail closed."""
    nodes_by_id = {node.id.to_str(): node for node in interpreter.graph.nodes}

    unknown_actions = sorted(set(actions) - set(nodes_by_id))
    if unknown_actions:
        raise GuidedProcedureBindingError(
            f"ACTION bindings name nodes that are not in the bound graph: {unknown_actions}"
        )
    unknown_reasoning = sorted(set(reasoning) - set(nodes_by_id))
    if unknown_reasoning:
        raise GuidedProcedureBindingError(
            f"REASON bindings name nodes that are not in the bound graph: {unknown_reasoning}"
        )

    for key, node in sorted(nodes_by_id.items()):
        if node.kind not in _SUPPORTED_NODE_KINDS:
            raise GuidedProcedureBindingError(
                f"node {key!r} has kind {node.kind.value!r}, which the L3 guided adapter does "
                "not serve; it dispatches only ACTION (governed capability execution) and "
                "REASON (canonical Reasoner boundary) regions"
            )
        if node.kind is ProcedureNodeKind.ACTION:
            if key in reasoning:
                raise GuidedProcedureBindingError(
                    f"node {key!r} is an ACTION node and cannot carry a reasoning binding"
                )
            _validate_action_node(node, actions.get(key))
        elif node.kind is ProcedureNodeKind.REASON:
            if key in actions:
                raise GuidedProcedureBindingError(
                    f"node {key!r} is a REASON node and cannot carry an ACTION binding"
                )
            _validate_reason_node(node, reasoning.get(key))
        else:
            if key in actions or key in reasoning:
                raise GuidedProcedureBindingError(
                    f"END node {key!r} cannot carry an ACTION or reasoning binding"
                )


@dataclass(frozen=True, slots=True, kw_only=True)
class GuidedStepRecord:
    """One inert record of what happened at one procedure node.

    The vocabulary is the canonical core procedure-execution vocabulary
    (:class:`~agentx.core.procedure_execution.ExecutedNodeKind`,
    :class:`~agentx.core.procedure_execution.ProcedureStepDisposition`) rather
    than a competing one. This value is in-memory reporting evidence only: it
    is never persisted, never replayed, grants nothing, and cannot be read as
    verification of anything.
    """

    step_index: int
    node_id: str
    node_kind: ExecutedNodeKind
    disposition: ProcedureStepDisposition

    def __post_init__(self) -> None:
        if type(self.step_index) is not int or self.step_index < 0:
            raise TypeError("step_index must be a non-negative int")
        if not isinstance(self.node_id, str) or not self.node_id:
            raise TypeError("node_id must be a non-empty string")
        if not isinstance(self.node_kind, ExecutedNodeKind):
            raise TypeError("node_kind must be an ExecutedNodeKind")
        if not isinstance(self.disposition, ProcedureStepDisposition):
            raise TypeError("disposition must be a ProcedureStepDisposition")


@dataclass(frozen=True, slots=True, kw_only=True)
class GuidedProcedureRun:
    """Immutable report of one guided procedure walk.

    ``outcome`` is the canonical A1.10 ``Result`` of the last governed
    capability dispatch, propagated verbatim, or an explicit structured error
    when the guided walk could not produce canonical governed evidence.

    Two invariants are enforced structurally, so no code path — and no hostile
    input — can express the forbidden states:

    * ``task_verification`` is always
      :attr:`~agentx.core.procedure_execution.ProcedureTaskVerification.NOT_ASSESSED`.
      This value cannot claim the original Task succeeded. Task-level
      verification belongs to A2.05/A2.10 and happens after, and independently
      of, this run.
    * a run that never dispatched a governed capability cannot carry a
      canonical outcome value: reaching END, completing reasoning, or simply
      not failing produces an explicit failure instead of evidence.
    """

    disposition: ProcedureRunDisposition
    steps: tuple[GuidedStepRecord, ...]
    reasoning_calls: int
    reasoning_outputs: Mapping[str, str]
    outcome: Result[ClosedLoopOutcome, AgentXError]
    task_verification: ProcedureTaskVerification = ProcedureTaskVerification.NOT_ASSESSED

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, ProcedureRunDisposition):
            raise TypeError("disposition must be a ProcedureRunDisposition")
        if not isinstance(self.steps, tuple) or any(
            not isinstance(step, GuidedStepRecord) for step in self.steps
        ):
            raise TypeError("steps must be a tuple of GuidedStepRecord")
        if type(self.reasoning_calls) is not int or self.reasoning_calls < 0:
            raise TypeError("reasoning_calls must be a non-negative int")
        if not isinstance(self.reasoning_outputs, Mapping):
            raise TypeError("reasoning_outputs must be a mapping")
        object.__setattr__(
            self, "reasoning_outputs", MappingProxyType(dict(self.reasoning_outputs))
        )
        if not isinstance(self.outcome, Result):
            raise TypeError("outcome must be a Result[ClosedLoopOutcome, AgentXError]")
        if self.task_verification is not ProcedureTaskVerification.NOT_ASSESSED:
            raise ValueError(
                "a guided procedure run never assesses task verification; procedure END is "
                "not task success"
            )
        if self.outcome.is_success and not self.governed_dispatches:
            raise ValueError(
                "a guided run without a governed capability dispatch cannot carry a "
                "canonical execution outcome"
            )

    @property
    def governed_dispatches(self) -> int:
        """Number of steps that actually reached the governed A1.10 path."""
        return sum(1 for step in self.steps if step.disposition in _GOVERNED_STEP_DISPOSITIONS)

    @property
    def reached_end(self) -> bool:
        """Whether procedure CONTROL terminated at a canonical END node.

        This is control termination only: never task success, never
        verification, and never a claim about external state.
        """
        return self.disposition is ProcedureRunDisposition.REACHED_END


@dataclass(frozen=True, slots=True, kw_only=True)
class _StepOutcome:
    """Internal per-step result: continue the walk, or stop with this report."""

    record: GuidedStepRecord
    stop_disposition: ProcedureRunDisposition | None
    outcome: Result[ClosedLoopOutcome, AgentXError] | None


class GuidedProcedureStrategy:
    """A2.10 strategy adapter for one explicitly bound guided Procedure.

    The adapter is stateless between attempts: every run recomputes its walk
    from the immutable binding and the explicit arguments, so identical inputs
    and identical collaborator behaviour produce identical dispositions, step
    records, and reasoning-call counts.

    Collaborators are injected and canonical: the A2.04 Executor is the only
    way an action reaches the world, and the A2.03 Reasoner is the only way a
    model is ever called. The adapter owns no registry, no capability, no
    provider, no store, no kernel object, and no authority.
    """

    __slots__ = ("_binding", "_clock", "_executor", "_reasoner")

    def __init__(
        self,
        *,
        executor: Executor,
        binding: GuidedProcedureBinding,
        reasoner: Reasoner | None = None,
        clock: MonotonicClock | None = None,
    ) -> None:
        if not isinstance(executor, Executor):
            raise TypeError(f"executor must be an Executor, got {type(executor).__name__}")
        if not isinstance(binding, GuidedProcedureBinding):
            raise TypeError(
                f"binding must be a GuidedProcedureBinding, got {type(binding).__name__}"
            )
        if reasoner is not None and not isinstance(reasoner, Reasoner):
            raise TypeError(f"reasoner must be a Reasoner or None, got {type(reasoner).__name__}")
        if binding.declares_reasoning_regions and reasoner is None:
            raise GuidedProcedureBindingError(
                "the bound procedure declares REASON regions, so a canonical Reasoner must "
                "be injected; the adapter never substitutes another model path"
            )
        self._executor = executor
        self._binding = binding
        self._reasoner = reasoner
        self._clock = clock

    @property
    def binding(self) -> GuidedProcedureBinding:
        """Return the immutable explicit procedure binding."""
        return self._binding

    @property
    def executor(self) -> Executor:
        """Return the canonical A2.04 Executor used for governed dispatch."""
        return self._executor

    @property
    def reasoner(self) -> Reasoner | None:
        """Return the canonical A2.03 Reasoner boundary, when one is bound."""
        return self._reasoner

    # ------------------------------------------------------------------
    # A2.10 strategy port.
    # ------------------------------------------------------------------

    def attempt(
        self,
        task: Task,
        context: ExecutionContext,
        level: ExecutionLevel,
    ) -> StrategyResult:
        """Attempt the bound Procedure once at ``L3_GUIDED``.

        A level mismatch fails closed as canonical A2.10 strategy
        unavailability: this adapter serves exactly one level and never
        substitutes itself for a cache, compiled, planned, or exploratory
        strategy. The canonical governed result of the walk is returned
        unwrapped and unrewritten; A2.10 re-checks it against canonical
        verification evidence.
        """
        if not isinstance(task, Task):
            raise TypeError(f"task must be a Task, got {type(task).__name__}")
        if not isinstance(context, ExecutionContext):
            raise TypeError(f"context must be an ExecutionContext, got {type(context).__name__}")
        if not isinstance(level, ExecutionLevel):
            raise TypeError(f"level must be an ExecutionLevel, got {type(level).__name__}")

        if level is not self._binding.level:
            return StrategyResult.unavailable(
                "guided procedure strategy is available only for L3_GUIDED"
            )
        return StrategyResult.executed(self.run_guided(task, context).outcome)

    # ------------------------------------------------------------------
    # The bounded guided walk.
    # ------------------------------------------------------------------

    def run_guided(self, task: Task, context: ExecutionContext) -> GuidedProcedureRun:
        """Walk the bound Procedure once and report the run as inert data.

        The walk is a bounded ``for`` over the canonical interpreter's own step
        ceiling, so no graph shape, cycle, capability behaviour, or model
        output can produce an unbounded loop.
        """
        if not isinstance(task, Task):
            raise TypeError(f"task must be a Task, got {type(task).__name__}")
        if not isinstance(context, ExecutionContext):
            raise TypeError(f"context must be an ExecutionContext, got {type(context).__name__}")

        interpreter = self._binding.interpreter
        steps: list[GuidedStepRecord] = []
        outputs: dict[str, str] = {}
        reasoning_calls = 0
        governed: Result[ClosedLoopOutcome, AgentXError] | None = None
        state: ProcedureInterpreterState = interpreter.start()

        # The canonical interpreter owns the real ceiling and reports crossing
        # it as an explicit FAILED control state; the extra iterations exist
        # only so that state can be observed. This bound is a structural
        # backstop, so the walk is finite even for a forged interpreter.
        for _ in range(interpreter.max_steps + 2):
            stop = context.observe_stop(clock=self._clock)
            if stop.should_stop:
                return self._stopped(steps, outputs, reasoning_calls, state, stop)

            if state.is_failed:
                return self._halted(
                    steps,
                    outputs,
                    reasoning_calls,
                    _error(
                        code="procedure_control_failed",
                        message="canonical procedure control flow failed",
                        category=ErrorCategory.EXECUTION,
                        details={
                            "node_id": state.current_node.to_str(),
                            "failure_reason": (
                                None if state.failure_reason is None else state.failure_reason.value
                            ),
                        },
                    ),
                )
            if state.is_terminated:
                return self._terminated(steps, outputs, reasoning_calls, state, governed)

            instruction = interpreter.instruction(state)
            step = self._perform(task, context, instruction, outputs, reasoning_calls, len(steps))
            steps.append(step.record)
            if step.record.node_kind is ExecutedNodeKind.ACTION:
                governed = step.outcome if step.outcome is not None else governed
            elif step.record.disposition is ProcedureStepDisposition.EXECUTED:
                reasoning_calls += 1

            if step.stop_disposition is not None:
                stop_outcome = step.outcome
                if stop_outcome is None:  # pragma: no cover - _perform guarantees this
                    raise AssertionError("a stopping step must carry an explicit outcome")
                return GuidedProcedureRun(
                    disposition=step.stop_disposition,
                    steps=tuple(steps),
                    reasoning_calls=reasoning_calls,
                    reasoning_outputs=outputs,
                    outcome=stop_outcome,
                )

            state = interpreter.advance(
                state, StepCompleted(node_kind=_NODE_KIND_BY_INSTRUCTION[instruction.kind])
            )

        return self._halted(
            steps,
            outputs,
            reasoning_calls,
            _error(
                code="step_ceiling_exceeded",
                message="the bounded guided walk exhausted the canonical step ceiling",
                category=ErrorCategory.RESOURCE,
                details={"max_steps": interpreter.max_steps},
            ),
        )

    # ------------------------------------------------------------------
    # Node dispatch.
    # ------------------------------------------------------------------

    def _perform(
        self,
        task: Task,
        context: ExecutionContext,
        instruction: ProcedureInstruction,
        outputs: dict[str, str],
        reasoning_calls: int,
        step_index: int,
    ) -> _StepOutcome:
        """Dispatch exactly one canonical instruction to its canonical owner."""
        if instruction.kind is ProcedureInstructionKind.ACTION_REQUIRED:
            return self._dispatch_action(task, context, instruction, step_index)
        if instruction.kind is ProcedureInstructionKind.REASON_REQUIRED:
            return self._dispatch_reason(context, instruction, outputs, reasoning_calls, step_index)
        # Unreachable for a validated binding: unsupported node kinds are
        # rejected at construction. Kept as a fail-closed runtime guard.
        return _StepOutcome(
            record=GuidedStepRecord(
                step_index=step_index,
                node_id=instruction.node_id.to_str(),
                node_kind=_EXECUTED_KIND_BY_NODE_KIND[instruction.node_kind],
                disposition=ProcedureStepDisposition.DENIED,
            ),
            stop_disposition=ProcedureRunDisposition.HALTED_ON_STEP_FAILURE,
            outcome=Result[ClosedLoopOutcome, AgentXError].failure(
                _error(
                    code="unsupported_node_kind",
                    message="the guided adapter serves only ACTION and REASON regions",
                    category=ErrorCategory.PRECONDITION,
                    details={
                        "node_id": instruction.node_id.to_str(),
                        "node_kind": instruction.node_kind.value,
                    },
                )
            ),
        )

    def _dispatch_action(
        self,
        task: Task,
        context: ExecutionContext,
        instruction: ProcedureInstruction,
        step_index: int,
    ) -> _StepOutcome:
        """Dispatch one ACTION region through the canonical governed path."""
        node_id = instruction.node_id
        binding = self._binding.action_binding(node_id)
        if binding is None:  # pragma: no cover - binding validation guarantees this
            return _StepOutcome(
                record=GuidedStepRecord(
                    step_index=step_index,
                    node_id=node_id.to_str(),
                    node_kind=ExecutedNodeKind.ACTION,
                    disposition=ProcedureStepDisposition.DENIED,
                ),
                stop_disposition=ProcedureRunDisposition.DENIED,
                outcome=Result[ClosedLoopOutcome, AgentXError].failure(
                    _error(
                        code="action_binding_missing",
                        message="ACTION node has no explicit governed capability binding",
                        category=ErrorCategory.PRECONDITION,
                        details={"node_id": node_id.to_str()},
                    )
                ),
            )

        # A1.10 requires a PENDING Task at its own boundary while A2.10 owns the
        # orchestrated Task's lifecycle, so each governed dispatch gets a fresh
        # PENDING sibling carrying only the inert objective. The sibling context
        # reuses the caller's correlation identity, cancellation token, and
        # deadline.
        governed_task = Task.create(objective=task.objective)
        governed_context = ExecutionContext(
            correlation_id=context.correlation_id,
            cancellation_token=context.cancellation_token,
            task_id=governed_task.task_id,
            deadline=context.deadline,
        )
        result = self._executor.execute(
            ExecutorRequest(
                task=governed_task,
                capability_request=binding.request,
                context=governed_context,
            )
        )

        if result.is_failure:
            return _StepOutcome(
                record=GuidedStepRecord(
                    step_index=step_index,
                    node_id=node_id.to_str(),
                    node_kind=ExecutedNodeKind.ACTION,
                    disposition=ProcedureStepDisposition.DENIED,
                ),
                stop_disposition=ProcedureRunDisposition.DENIED,
                outcome=result,
            )

        outcome = result.unwrap()
        disposition = _STEP_DISPOSITION_BY_OUTCOME[outcome.kind]
        record = GuidedStepRecord(
            step_index=step_index,
            node_id=node_id.to_str(),
            node_kind=ExecutedNodeKind.ACTION,
            disposition=disposition,
        )
        if outcome.kind is LoopOutcome.VERIFIED:
            # Continuation is decided by the canonical typed A1.10 verdict
            # alone. The procedure never continues past an unverified effect.
            return _StepOutcome(record=record, stop_disposition=None, outcome=result)
        stop_disposition = (
            ProcedureRunDisposition.DENIED
            if outcome.kind is LoopOutcome.DENIED
            else ProcedureRunDisposition.HALTED_ON_STEP_FAILURE
        )
        return _StepOutcome(record=record, stop_disposition=stop_disposition, outcome=result)

    def _dispatch_reason(
        self,
        context: ExecutionContext,
        instruction: ProcedureInstruction,
        outputs: dict[str, str],
        reasoning_calls: int,
        step_index: int,
    ) -> _StepOutcome:
        """Invoke the canonical Reasoner for one explicitly declared region."""
        node_id = instruction.node_id
        failure = self._reason_precondition_failure(instruction, outputs, reasoning_calls)
        if failure is not None:
            return _reason_halt(step_index, node_id, failure)

        reasoner = self._reasoner
        binding = self._binding.reasoning_binding(node_id)
        if reasoner is None or binding is None:  # pragma: no cover - guarded above
            raise AssertionError("reasoning preconditions must be validated before dispatch")
        spec = ReasonNodeSpec.from_dict(instruction.params)

        text = _reasoning_instruction_text(spec, outputs)
        if len(text) > MAX_REASONING_INSTRUCTION_CHARS:
            return _reason_halt(
                step_index,
                node_id,
                _error(
                    code="reasoning_instruction_too_large",
                    message="the constructed reasoning instruction exceeds its explicit bound",
                    category=ErrorCategory.VALIDATION,
                    details={"node_id": node_id.to_str(), "characters": len(text)},
                ),
            )

        reason_result = reasoner.reason(
            ReasonerRequest(
                execution_context=context,
                instruction=TextContent(text),
                max_output_tokens=binding.max_output_tokens,
            )
        )
        if reason_result.is_failure:
            # A Reasoner failure is explicit and terminal for this run. There is
            # no retry, no second provider, no fallback model, and no synthesized
            # answer.
            return _reason_halt(step_index, node_id, reason_result.unwrap_error())

        output = _reasoning_output_text(reason_result.unwrap())
        if output is None:
            return _reason_halt(
                step_index,
                node_id,
                _error(
                    code="reasoning_output_unusable",
                    message="the reasoning result carried no usable inert text output",
                    category=ErrorCategory.DEPENDENCY,
                    details={"node_id": node_id.to_str()},
                ),
            )
        if len(output) > MAX_REASONING_OUTPUT_CHARS:
            return _reason_halt(
                step_index,
                node_id,
                _error(
                    code="reasoning_output_too_large",
                    message="the reasoning output exceeds its explicit bound",
                    category=ErrorCategory.VALIDATION,
                    details={"node_id": node_id.to_str(), "characters": len(output)},
                ),
            )

        # The single, exact, typed effect a Reasoner may have on a guided run:
        # the canonical declared output slot is filled with inert text. Nothing
        # else in the run reads model output.
        outputs[spec.output_binding] = output
        return _StepOutcome(
            record=GuidedStepRecord(
                step_index=step_index,
                node_id=node_id.to_str(),
                node_kind=ExecutedNodeKind.REASON,
                disposition=ProcedureStepDisposition.EXECUTED,
            ),
            stop_disposition=None,
            outcome=None,
        )

    def _reason_precondition_failure(
        self,
        instruction: ProcedureInstruction,
        outputs: Mapping[str, str],
        reasoning_calls: int,
    ) -> AgentXError | None:
        """Validate every reasoning precondition before any model is reached."""
        node_id = instruction.node_id
        binding = self._binding.reasoning_binding(node_id)
        if binding is None:  # pragma: no cover - binding validation guarantees this
            return _error(
                code="reasoning_binding_missing",
                message="REASON node has no explicit reasoning binding",
                category=ErrorCategory.PRECONDITION,
                details={"node_id": node_id.to_str()},
            )
        if self._reasoner is None:  # pragma: no cover - construction guarantees this
            return _error(
                code="reasoner_unavailable",
                message="no canonical Reasoner is bound to this guided strategy",
                category=ErrorCategory.PRECONDITION,
                details={"node_id": node_id.to_str()},
            )
        if reasoning_calls >= self._binding.max_reasoning_calls:
            return _error(
                code="reasoning_bound_exhausted",
                message="the explicit bound on reasoning invocations is exhausted",
                category=ErrorCategory.RESOURCE,
                details={
                    "node_id": node_id.to_str(),
                    "max_reasoning_calls": self._binding.max_reasoning_calls,
                },
            )
        try:
            spec = ReasonNodeSpec.from_dict(instruction.params)
        except ReasonResearchContractError as exc:
            return _error(
                code="reason_payload_malformed",
                message="REASON node params are not a canonical A3.04 REASON payload",
                category=ErrorCategory.VALIDATION,
                details={"node_id": node_id.to_str(), "reason": str(exc)},
            )
        if spec.output_binding != binding.output_binding:
            return _error(
                code="reasoning_binding_mismatch",
                message="the declared reasoning output slot does not match the binding",
                category=ErrorCategory.VALIDATION,
                details={
                    "node_id": node_id.to_str(),
                    "declared_output_binding": spec.output_binding,
                    "bound_output_binding": binding.output_binding,
                },
            )
        unresolved = [reference for reference in spec.input_references if reference not in outputs]
        if unresolved:
            return _error(
                code="unresolved_reasoning_reference",
                message=(
                    "a declared reasoning input reference names no reasoning-output slot "
                    "produced earlier in this run"
                ),
                category=ErrorCategory.PRECONDITION,
                details={"node_id": node_id.to_str(), "unresolved": sorted(unresolved)},
            )
        return None

    # ------------------------------------------------------------------
    # Terminal reports.
    # ------------------------------------------------------------------

    def _terminated(
        self,
        steps: list[GuidedStepRecord],
        outputs: Mapping[str, str],
        reasoning_calls: int,
        state: ProcedureInterpreterState,
        governed: Result[ClosedLoopOutcome, AgentXError] | None,
    ) -> GuidedProcedureRun:
        """Close a run whose control flow reached a canonical END node.

        Reaching END is control termination, not success. When the walk never
        dispatched a governed capability there is no canonical evidence to
        report, and the adapter fails closed instead of implying anything.
        """
        outcome = governed
        if outcome is None:
            outcome = Result[ClosedLoopOutcome, AgentXError].failure(
                _error(
                    code="no_governed_execution_evidence",
                    message=(
                        "procedure control reached END without any governed capability "
                        "execution; END is not task success and reasoning is not evidence"
                    ),
                    category=ErrorCategory.PRECONDITION,
                    details={"end_node_id": state.current_node.to_str()},
                )
            )
        return GuidedProcedureRun(
            disposition=ProcedureRunDisposition.REACHED_END,
            steps=tuple(steps),
            reasoning_calls=reasoning_calls,
            reasoning_outputs=outputs,
            outcome=outcome,
        )

    def _halted(
        self,
        steps: list[GuidedStepRecord],
        outputs: Mapping[str, str],
        reasoning_calls: int,
        error: AgentXError,
    ) -> GuidedProcedureRun:
        """Close a run that stopped on an explicit structured failure."""
        return GuidedProcedureRun(
            disposition=ProcedureRunDisposition.HALTED_ON_STEP_FAILURE,
            steps=tuple(steps),
            reasoning_calls=reasoning_calls,
            reasoning_outputs=outputs,
            outcome=Result[ClosedLoopOutcome, AgentXError].failure(error),
        )

    def _stopped(
        self,
        steps: list[GuidedStepRecord],
        outputs: Mapping[str, str],
        reasoning_calls: int,
        state: ProcedureInterpreterState,
        stop: ExecutionStopStatus,
    ) -> GuidedProcedureRun:
        """Close a run stopped by canonical A1.07 cancellation or deadline."""
        details: dict[str, object] = {
            "node_id": state.current_node.to_str(),
            "deadline_status": stop.deadline_status.value,
        }
        if stop.cancellation_reason is not None:
            details["cancellation_reason"] = stop.cancellation_reason
        if stop.cancellation_requested:
            disposition = ProcedureRunDisposition.CANCELLED
            error = _error(
                code="cancelled",
                message="the guided procedure walk observed canonical cancellation",
                category=ErrorCategory.CANCELLED,
                details=details,
            )
        else:
            disposition = ProcedureRunDisposition.TIMED_OUT
            error = _error(
                code="deadline_expired",
                message="the guided procedure walk observed canonical deadline expiry",
                category=ErrorCategory.TIMEOUT,
                details=details,
            )
        return GuidedProcedureRun(
            disposition=disposition,
            steps=tuple(steps),
            reasoning_calls=reasoning_calls,
            reasoning_outputs=outputs,
            outcome=Result[ClosedLoopOutcome, AgentXError].failure(error),
        )


def _reason_halt(
    step_index: int,
    node_id: ProcedureNodeId,
    error: AgentXError,
) -> _StepOutcome:
    """Build the fail-closed step outcome for a refused/failed reasoning step."""
    return _StepOutcome(
        record=GuidedStepRecord(
            step_index=step_index,
            node_id=node_id.to_str(),
            node_kind=ExecutedNodeKind.REASON,
            disposition=ProcedureStepDisposition.DENIED,
        ),
        stop_disposition=ProcedureRunDisposition.HALTED_ON_STEP_FAILURE,
        outcome=Result[ClosedLoopOutcome, AgentXError].failure(error),
    )


def _reasoning_instruction_text(spec: ReasonNodeSpec, outputs: Mapping[str, str]) -> str:
    """Build one deterministic instruction from canonical inert declared data.

    The text is the node's declared objective plus, in declared order, the
    reasoning-output slots the node explicitly references. Everything in it is
    inert: hostile strings are passed to the model as characters and change no
    decision here.
    """
    lines = [spec.objective]
    lines.extend(f"{reference}: {outputs[reference]}" for reference in spec.input_references)
    return "\n".join(lines)


def _reasoning_output_text(result: ReasonerResult) -> str | None:
    """Read the inert text of one reasoning result, or ``None`` when unusable."""
    raw: object = result
    if not isinstance(raw, ReasonerResult):  # pragma: no cover - Reasoner guarantees this
        return None
    parts = [item.text for item in result.content if isinstance(item, TextContent)]
    if not parts:
        return None
    text = "\n".join(parts)
    return text if text else None


#: Canonical instruction kind -> canonical node kind, for the explicit
#: ``StepCompleted`` the interpreter requires to continue.
_NODE_KIND_BY_INSTRUCTION: Final[Mapping[ProcedureInstructionKind, ProcedureNodeKind]] = (
    MappingProxyType(
        {
            ProcedureInstructionKind.ACTION_REQUIRED: ProcedureNodeKind.ACTION,
            ProcedureInstructionKind.REASON_REQUIRED: ProcedureNodeKind.REASON,
        }
    )
)

#: Canonical graph node kind -> canonical core execution-trace vocabulary.
_EXECUTED_KIND_BY_NODE_KIND: Final[Mapping[ProcedureNodeKind, ExecutedNodeKind]] = MappingProxyType(
    {
        ProcedureNodeKind.ACTION: ExecutedNodeKind.ACTION,
        ProcedureNodeKind.OBSERVE: ExecutedNodeKind.OBSERVE,
        ProcedureNodeKind.VERIFY: ExecutedNodeKind.VERIFY,
        ProcedureNodeKind.BRANCH: ExecutedNodeKind.BRANCH,
        ProcedureNodeKind.TRANSFORM: ExecutedNodeKind.TRANSFORM,
        ProcedureNodeKind.REASON: ExecutedNodeKind.REASON,
        ProcedureNodeKind.RESEARCH: ExecutedNodeKind.RESEARCH,
        ProcedureNodeKind.WAIT: ExecutedNodeKind.WAIT,
        ProcedureNodeKind.ROLLBACK: ExecutedNodeKind.ROLLBACK,
        ProcedureNodeKind.SUBPROCEDURE: ExecutedNodeKind.SUBPROCEDURE,
        ProcedureNodeKind.END: ExecutedNodeKind.END,
    }
)
