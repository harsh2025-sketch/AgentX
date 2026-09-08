"""Top-level governed L2 compiled-procedure strategy adapter (N2.04).

This module is composition wiring. Procedure code remains inert: the canonical
M3.01 interpreter emits ``ACTION_REQUIRED`` instructions, while this top-level
adapter selects an explicitly pre-bound canonical ``CapabilityRequest`` and
hands it to the canonical A2.04 ``Executor``. The Executor remains the only
route to A1.10 governed capability execution.

Reaching Procedure END is control termination only. Every produced M3.02
``ProcedureRunRecord`` therefore leaves original-task verification
``NOT_ASSESSED``. A2.10 performs its independent A2.05 verification after the
strategy returns; this adapter never imports or constructs a Verifier.

Procedure payload, node parameters, labels, descriptions, and match results are
data, never authority. In particular no procedure text can grant permission,
lower risk, widen a budget, bypass the ActionGate, clear an EmergencyStop, or
assert Task success.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID

from agentx.agent_loop import StrategyResult
from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.executor import Executor, ExecutorRequest
from agentx.capabilities.runtime import LoopOutcome
from agentx.cognition.router import ExecutionLevel
from agentx.core.execution import ExecutionContext
from agentx.core.procedure_execution import (
    ExecutedEdgeKind,
    ExecutedNodeKind,
    ProcedureEvidenceKind,
    ProcedureExecutionEvidence,
    ProcedureRunDisposition,
    ProcedureRunRecord,
    ProcedureStepDisposition,
    ProcedureStepRecord,
    ProcedureStepTransition,
)
from agentx.core.procedure_matching import (
    ProcedureApplicabilityMatcher,
    ProcedureCandidate,
    ProcedureMatchResult,
    ProcedureRequirement,
)
from agentx.core.procedures import ProcedurePayloadKind, ProcedureStatus
from agentx.core.tasks import Task
from agentx.procedures.end import EndNodeSpec
from agentx.procedures.graph import ProcedureGraph, ProcedureNodeKind
from agentx.procedures.interpreter import (
    InterpreterStatus,
    ProcedureInstructionKind,
    ProcedureInterpreter,
    StepCompleted,
)
from agentx.procedures.nodes import ActionNodeSpec

__all__ = [
    "COMPILED_PROCEDURE_STRATEGY_LEVEL",
    "CompiledProcedureAttempt",
    "CompiledProcedureStrategyBinding",
    "CompiledProcedureStrategyBindingError",
    "GovernedCompiledProcedureStrategy",
    "ProcedureRunSink",
]

COMPILED_PROCEDURE_STRATEGY_LEVEL: Final[ExecutionLevel] = ExecutionLevel.L2_COMPILED

type ProcedureRunSink = Callable[[ProcedureRunRecord], object]


class CompiledProcedureStrategyBindingError(ValueError):
    """Raised when an explicitly selected procedure cannot be used as L2."""


def _require_recorded_at(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"recorded_at must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise CompiledProcedureStrategyBindingError("recorded_at must be timezone-aware")
    return value.astimezone(UTC)


def _require_run_id(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"run_id must be a UUID, got {type(value).__name__}")
    if value.int == 0:
        raise CompiledProcedureStrategyBindingError("run_id must be a non-nil UUID")
    return value


def _parse_active_graph(candidate: ProcedureCandidate) -> ProcedureGraph:
    record = candidate.record
    if record.status is not ProcedureStatus.ACTIVE:
        raise CompiledProcedureStrategyBindingError(
            "normal L2 execution requires ProcedureStatus.ACTIVE"
        )
    if record.payload.kind is not ProcedurePayloadKind.CANONICAL_JSON:
        raise CompiledProcedureStrategyBindingError(
            "L2 adapter requires an inline CANONICAL_JSON ProcedureGraph payload"
        )
    try:
        graph = ProcedureGraph.from_json(record.payload.content)
    except ValueError as exc:
        raise CompiledProcedureStrategyBindingError(
            "procedure payload is not a valid canonical ProcedureGraph"
        ) from exc

    for node in graph.nodes:
        if node.kind is ProcedureNodeKind.ACTION:
            try:
                ActionNodeSpec.from_node(node)
            except ValueError as exc:
                raise CompiledProcedureStrategyBindingError(
                    f"ACTION node {node.id.to_str()!r} is malformed"
                ) from exc
        elif node.kind is ProcedureNodeKind.END:
            try:
                EndNodeSpec.from_node(node)
            except ValueError as exc:
                raise CompiledProcedureStrategyBindingError(
                    f"END node {node.id.to_str()!r} is malformed"
                ) from exc
        else:
            raise CompiledProcedureStrategyBindingError(
                "L2_COMPILED accepts reasoning-free ACTION/END graphs only; "
                f"found {node.kind.value!r}"
            )
    return graph


def _successful_action_sequence(graph: ProcedureGraph) -> tuple[str, ...]:
    """Use the canonical interpreter to prove the successful path reaches END."""
    interpreter = ProcedureInterpreter(graph=graph, max_steps=max(1, len(graph.nodes)))
    state = interpreter.start()
    sequence: list[str] = []
    while not state.is_terminal:
        instruction = interpreter.instruction(state)
        if instruction.kind is not ProcedureInstructionKind.ACTION_REQUIRED:
            raise CompiledProcedureStrategyBindingError(
                "L2_COMPILED successful path requires only ACTION_REQUIRED instructions"
            )
        sequence.append(instruction.node_id.to_str())
        state = interpreter.advance(
            state,
            StepCompleted(node_kind=ProcedureNodeKind.ACTION),
        )
    if state.status is not InterpreterStatus.TERMINATED:
        raise CompiledProcedureStrategyBindingError(
            "procedure successful control path does not reach canonical END"
        )
    return tuple(sequence)


def _freeze_action_requests(
    graph: ProcedureGraph,
    action_requests: Mapping[str, CapabilityRequest[Any]],
) -> Mapping[str, CapabilityRequest[Any]]:
    if not isinstance(action_requests, Mapping):
        raise TypeError(
            "action_requests must be a mapping of node id to CapabilityRequest, "
            f"got {type(action_requests).__name__}"
        )

    expected: dict[str, ActionNodeSpec] = {}
    for node in graph.nodes:
        if node.kind is ProcedureNodeKind.ACTION:
            expected[node.id.to_str()] = ActionNodeSpec.from_node(node)

    copied: dict[str, CapabilityRequest[Any]] = {}
    for node_id, request in action_requests.items():
        if not isinstance(node_id, str) or not node_id or node_id != node_id.strip():
            raise CompiledProcedureStrategyBindingError(
                "action request keys must be non-empty trimmed node-id strings"
            )
        if not isinstance(request, CapabilityRequest):
            raise TypeError(
                f"action request for {node_id!r} must be a CapabilityRequest, "
                f"got {type(request).__name__}"
            )
        copied[node_id] = request

    if set(copied) != set(expected):
        raise CompiledProcedureStrategyBindingError(
            "action_requests must bind exactly every canonical ACTION node"
        )

    for node_id, spec in expected.items():
        request = copied[node_id]
        if request.identity.name.value != spec.capability_name:
            raise CompiledProcedureStrategyBindingError(
                f"bound request identity does not match ACTION node {node_id!r}"
            )
        if request.identity.version.to_str() != spec.capability_version:
            raise CompiledProcedureStrategyBindingError(
                f"bound request version does not match ACTION node {node_id!r}"
            )

    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True, kw_only=True)
class CompiledProcedureStrategyBinding:
    """Explicit immutable selection used by one L2 compiled strategy.

    ``candidate`` and ``requirement`` are the canonical M4.04 inputs. The
    caller also supplies exact canonical capability requests for every ACTION
    node; procedure params never become executable requests inside this module.
    ``run_id`` and ``recorded_at`` are explicit so evidence generation performs
    no clock read or random-ID generation.
    """

    candidate: ProcedureCandidate
    requirement: ProcedureRequirement
    action_requests: Mapping[str, CapabilityRequest[Any]]
    run_id: UUID
    recorded_at: datetime
    level: ExecutionLevel = COMPILED_PROCEDURE_STRATEGY_LEVEL
    _graph: ProcedureGraph = field(init=False, repr=False, compare=False)
    _match_result: ProcedureMatchResult = field(init=False, repr=False, compare=False)
    _action_sequence: tuple[str, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, ProcedureCandidate):
            raise TypeError(
                f"candidate must be a ProcedureCandidate, got {type(self.candidate).__name__}"
            )
        if not isinstance(self.requirement, ProcedureRequirement):
            raise TypeError(
                f"requirement must be a ProcedureRequirement, got {type(self.requirement).__name__}"
            )
        if not isinstance(self.level, ExecutionLevel):
            raise TypeError(f"level must be an ExecutionLevel, got {type(self.level).__name__}")
        if self.level is not COMPILED_PROCEDURE_STRATEGY_LEVEL:
            raise CompiledProcedureStrategyBindingError(
                "compiled procedure binding must declare exactly L2_COMPILED"
            )

        graph = _parse_active_graph(self.candidate)
        match_result = ProcedureApplicabilityMatcher().assess(
            self.candidate,
            self.requirement,
        )
        if not match_result.structurally_applicable:
            raise CompiledProcedureStrategyBindingError(
                "selected procedure is not structurally applicable under canonical M4.04: "
                f"{match_result.outcome.value}"
            )

        sequence = _successful_action_sequence(graph)
        frozen_requests = _freeze_action_requests(graph, self.action_requests)
        if any(node_id not in frozen_requests for node_id in sequence):
            raise CompiledProcedureStrategyBindingError(
                "successful procedure path contains an unbound ACTION node"
            )

        object.__setattr__(self, "recorded_at", _require_recorded_at(self.recorded_at))
        object.__setattr__(self, "run_id", _require_run_id(self.run_id))
        object.__setattr__(self, "action_requests", frozen_requests)
        object.__setattr__(self, "_graph", graph)
        object.__setattr__(self, "_match_result", match_result)
        object.__setattr__(self, "_action_sequence", sequence)

    @property
    def graph(self) -> ProcedureGraph:
        """The validated canonical graph decoded from the selected record."""
        return self._graph

    @property
    def match_result(self) -> ProcedureMatchResult:
        """Canonical M4.04 structural applicability evidence."""
        return self._match_result

    @property
    def action_sequence(self) -> tuple[str, ...]:
        """Canonical-interpreter successful-path ACTION node identities."""
        return self._action_sequence


@dataclass(frozen=True, slots=True, kw_only=True)
class CompiledProcedureAttempt:
    """Strategy result plus the canonical M3.02 run evidence produced beside it."""

    strategy_result: StrategyResult
    run_record: ProcedureRunRecord | None

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_result, StrategyResult):
            raise TypeError(
                "strategy_result must be a StrategyResult, "
                f"got {type(self.strategy_result).__name__}"
            )
        if self.run_record is not None and not isinstance(self.run_record, ProcedureRunRecord):
            raise TypeError(
                "run_record must be a ProcedureRunRecord or None, "
                f"got {type(self.run_record).__name__}"
            )


def _step_evidence(correlation_id: UUID) -> tuple[ProcedureExecutionEvidence, ...]:
    return (
        ProcedureExecutionEvidence(
            kind=ProcedureEvidenceKind.CHAIN_CORRELATION,
            correlation_id=correlation_id,
        ),
    )


def _step_disposition(kind: LoopOutcome) -> ProcedureStepDisposition:
    if kind is LoopOutcome.DENIED:
        return ProcedureStepDisposition.DENIED
    if kind is LoopOutcome.EXECUTION_FAILED:
        return ProcedureStepDisposition.EXECUTION_FAILED
    if kind is LoopOutcome.VERIFICATION_FAILED:
        return ProcedureStepDisposition.VERIFICATION_FAILED
    return ProcedureStepDisposition.VERIFIED


def _run_disposition(kind: LoopOutcome) -> ProcedureRunDisposition:
    if kind is LoopOutcome.DENIED:
        return ProcedureRunDisposition.DENIED
    return ProcedureRunDisposition.HALTED_ON_STEP_FAILURE


class GovernedCompiledProcedureStrategy:
    """Canonical top-level A2.10 adapter for exactly ``L2_COMPILED``."""

    __slots__ = ("_binding", "_executor", "_run_sink")

    def __init__(
        self,
        *,
        executor: Executor,
        binding: CompiledProcedureStrategyBinding,
        run_sink: ProcedureRunSink | None = None,
    ) -> None:
        if not isinstance(executor, Executor):
            raise TypeError(f"executor must be an Executor, got {type(executor).__name__}")
        if not isinstance(binding, CompiledProcedureStrategyBinding):
            raise TypeError(
                "binding must be a CompiledProcedureStrategyBinding, "
                f"got {type(binding).__name__}"
            )
        if run_sink is not None and not callable(run_sink):
            raise TypeError("run_sink must be callable or None")
        self._executor = executor
        self._binding = binding
        self._run_sink = run_sink

    @property
    def executor(self) -> Executor:
        return self._executor

    @property
    def binding(self) -> CompiledProcedureStrategyBinding:
        return self._binding

    def attempt(
        self,
        task: Task,
        context: ExecutionContext,
        level: ExecutionLevel,
    ) -> StrategyResult:
        """Attempt one explicitly selected L2 procedure, without fallback."""
        attempt = self.attempt_with_evidence(task, context, level)
        if attempt.run_record is not None and self._run_sink is not None:
            self._run_sink(attempt.run_record)
        return attempt.strategy_result

    def attempt_with_evidence(
        self,
        task: Task,
        context: ExecutionContext,
        level: ExecutionLevel,
    ) -> CompiledProcedureAttempt:
        """Run the canonical interpreter and return canonical M3.02 evidence."""
        if not isinstance(task, Task):
            raise TypeError(f"task must be a Task, got {type(task).__name__}")
        if not isinstance(context, ExecutionContext):
            raise TypeError(
                f"context must be an ExecutionContext, got {type(context).__name__}"
            )
        if not isinstance(level, ExecutionLevel):
            raise TypeError(f"level must be an ExecutionLevel, got {type(level).__name__}")
        if level is not COMPILED_PROCEDURE_STRATEGY_LEVEL:
            return CompiledProcedureAttempt(
                strategy_result=StrategyResult.unavailable(
                    "governed compiled procedure strategy is available only for L2_COMPILED"
                ),
                run_record=None,
            )

        interpreter = ProcedureInterpreter(
            graph=self._binding.graph,
            max_steps=max(1, len(self._binding.graph.nodes)),
        )
        state = interpreter.start()
        steps: list[ProcedureStepRecord] = []
        last_strategy_result: StrategyResult | None = None
        correlation_evidence = _step_evidence(context.correlation_id)

        if state.status is InterpreterStatus.TERMINATED:
            steps.append(
                ProcedureStepRecord(
                    step_index=0,
                    node_id=state.current_node.to_str(),
                    node_kind=ExecutedNodeKind.END,
                    disposition=ProcedureStepDisposition.EXECUTED,
                    started_at=self._binding.recorded_at,
                    ended_at=self._binding.recorded_at,
                )
            )
            run_record = self._run_record(
                task=task,
                context=context,
                steps=tuple(steps),
                disposition=ProcedureRunDisposition.REACHED_END,
                control_evidence=correlation_evidence,
            )
            return CompiledProcedureAttempt(
                strategy_result=StrategyResult.unavailable(
                    "compiled procedure reached END without a governed capability outcome"
                ),
                run_record=run_record,
            )

        while not state.is_terminal:
            instruction = interpreter.instruction(state)
            if instruction.kind is not ProcedureInstructionKind.ACTION_REQUIRED:
                raise RuntimeError("validated L2 procedure emitted a non-ACTION instruction")
            node_id = instruction.node_id.to_str()
            request = self._binding.action_requests[node_id]

            governed_task = Task.create(objective=task.objective)
            governed_context = ExecutionContext(
                correlation_id=context.correlation_id,
                cancellation_token=context.cancellation_token,
                task_id=governed_task.task_id,
                deadline=context.deadline,
            )
            governed_result = self._executor.execute(
                ExecutorRequest(
                    task=governed_task,
                    capability_request=request,
                    context=governed_context,
                )
            )
            last_strategy_result = StrategyResult.executed(governed_result)

            if not governed_result.is_success:
                error = governed_result.unwrap_error()
                steps.append(
                    ProcedureStepRecord(
                        step_index=len(steps),
                        node_id=node_id,
                        node_kind=ExecutedNodeKind.ACTION,
                        disposition=ProcedureStepDisposition.EXECUTION_FAILED,
                        started_at=self._binding.recorded_at,
                        ended_at=self._binding.recorded_at,
                        error_code=error.code,
                    )
                )
                return CompiledProcedureAttempt(
                    strategy_result=last_strategy_result,
                    run_record=self._run_record(
                        task=task,
                        context=context,
                        steps=tuple(steps),
                        disposition=ProcedureRunDisposition.HALTED_ON_STEP_FAILURE,
                        control_evidence=(),
                    ),
                )

            outcome = governed_result.unwrap()
            disposition = _step_disposition(outcome.kind)
            error_code = None if outcome.error is None else outcome.error.code
            verification_evidence = (
                correlation_evidence
                if outcome.kind in (LoopOutcome.VERIFIED, LoopOutcome.VERIFICATION_FAILED)
                else ()
            )
            observation_evidence = correlation_evidence if outcome.observation is not None else ()

            if outcome.kind is not LoopOutcome.VERIFIED:
                steps.append(
                    ProcedureStepRecord(
                        step_index=len(steps),
                        node_id=node_id,
                        node_kind=ExecutedNodeKind.ACTION,
                        disposition=disposition,
                        started_at=self._binding.recorded_at,
                        ended_at=self._binding.recorded_at,
                        observation_evidence=observation_evidence,
                        verification_evidence=verification_evidence,
                        error_code=error_code,
                    )
                )
                return CompiledProcedureAttempt(
                    strategy_result=last_strategy_result,
                    run_record=self._run_record(
                        task=task,
                        context=context,
                        steps=tuple(steps),
                        disposition=_run_disposition(outcome.kind),
                        control_evidence=(),
                    ),
                )

            next_state = interpreter.advance(
                state,
                StepCompleted(node_kind=ProcedureNodeKind.ACTION),
            )
            transition = (
                None
                if next_state.status is InterpreterStatus.FAILED
                else ProcedureStepTransition(
                    edge_kind=ExecutedEdgeKind.NEXT,
                    target_node_id=next_state.current_node.to_str(),
                )
            )
            steps.append(
                ProcedureStepRecord(
                    step_index=len(steps),
                    node_id=node_id,
                    node_kind=ExecutedNodeKind.ACTION,
                    disposition=ProcedureStepDisposition.VERIFIED,
                    started_at=self._binding.recorded_at,
                    ended_at=self._binding.recorded_at,
                    observation_evidence=observation_evidence,
                    verification_evidence=correlation_evidence,
                    transition=transition,
                )
            )
            state = next_state

        if state.status is not InterpreterStatus.TERMINATED:
            raise RuntimeError("validated L2 procedure failed during canonical interpretation")
        if last_strategy_result is None:
            raise RuntimeError("terminated L2 procedure produced no governed capability outcome")

        steps.append(
            ProcedureStepRecord(
                step_index=len(steps),
                node_id=state.current_node.to_str(),
                node_kind=ExecutedNodeKind.END,
                disposition=ProcedureStepDisposition.EXECUTED,
                started_at=self._binding.recorded_at,
                ended_at=self._binding.recorded_at,
            )
        )
        return CompiledProcedureAttempt(
            strategy_result=last_strategy_result,
            run_record=self._run_record(
                task=task,
                context=context,
                steps=tuple(steps),
                disposition=ProcedureRunDisposition.REACHED_END,
                control_evidence=correlation_evidence,
            ),
        )

    def _run_record(
        self,
        *,
        task: Task,
        context: ExecutionContext,
        steps: tuple[ProcedureStepRecord, ...],
        disposition: ProcedureRunDisposition,
        control_evidence: tuple[ProcedureExecutionEvidence, ...],
    ) -> ProcedureRunRecord:
        return ProcedureRunRecord(
            run_id=self._binding.run_id,
            procedure_id=self._binding.candidate.record.procedure_id,
            procedure_revision=self._binding.candidate.record.revision,
            task_id=task.task_id,
            correlation_id=context.correlation_id,
            started_at=self._binding.recorded_at,
            ended_at=self._binding.recorded_at,
            steps=steps,
            disposition=disposition,
            control_evidence=control_evidence,
        )
