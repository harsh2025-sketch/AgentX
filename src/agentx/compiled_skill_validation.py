"""Governed execution harness for varied validation of compiled CANDIDATE procedures.

This top-level composition boundary exists only to gather validation evidence.
It does not promote, persist, route, or activate procedures. A CANDIDATE may
be exercised here because validation must happen before promotion, but every
ACTION still runs through the canonical Executor and its ActionGate,
permission, risk, budget, stop, capability execution, and verification path.

The harness consumes the safe compiler/runtime binding from
compiled_skill_binding. Procedure text remains DATA: exact typed
CapabilityRequest factories are injected by the composition root, never
constructed from arbitrary code or authority-shaped strings.
"""

from __future__ import annotations

from collections.abc import Mapping

from agentx.capabilities.abi import CapabilityIdentity
from agentx.capabilities.executor import Executor, ExecutorRequest
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.compiled_skill_binding import (
    CapabilityRequestFactory,
    CompiledSkillBindingError,
    build_compiled_action_requests,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.procedures import ProcedurePayloadKind, ProcedureRecord, ProcedureStatus
from agentx.core.result import Result
from agentx.core.tasks import Task
from agentx.procedure_validation import ProcedureCandidateIdentity
from agentx.procedure_validation_runner import ProcedureValidationRunRequest
from agentx.procedures.graph import ProcedureGraph, ProcedureNodeKind
from agentx.procedures.interpreter import (
    InterpreterStatus,
    ProcedureInstructionKind,
    ProcedureInterpreter,
    StepCompleted,
)
from agentx.procedures.nodes import ActionNodeSpec

__all__ = [
    "GovernedCompiledSkillValidationHarness",
]


def _validation_error(code: str, message: str) -> AgentXError:
    return AgentXError(
        code=code,
        message=message,
        category=ErrorCategory.VALIDATION,
        retryability=Retryability.NON_RETRYABLE,
    )


def _validated_candidate_graph(candidate: ProcedureRecord) -> ProcedureGraph:
    if candidate.status is not ProcedureStatus.CANDIDATE:
        raise CompiledSkillBindingError(
            "validation execution requires ProcedureStatus.CANDIDATE"
        )
    if candidate.payload.kind is not ProcedurePayloadKind.CANONICAL_JSON:
        raise CompiledSkillBindingError(
            "validation execution requires an inline CANONICAL_JSON ProcedureGraph"
        )
    try:
        graph = ProcedureGraph.from_json(candidate.payload.content)
    except ValueError as exc:
        raise CompiledSkillBindingError(
            "candidate payload is not a valid canonical ProcedureGraph"
        ) from exc

    for node in graph.nodes:
        if node.kind is ProcedureNodeKind.ACTION:
            try:
                ActionNodeSpec.from_node(node)
            except ValueError as exc:
                raise CompiledSkillBindingError(
                    f"ACTION node {node.id.to_str()!r} is malformed"
                ) from exc
        elif node.kind is not ProcedureNodeKind.END:
            raise CompiledSkillBindingError(
                "compiled validation harness accepts reasoning-free ACTION/END graphs only"
            )
    return graph


class GovernedCompiledSkillValidationHarness:
    """Execute one varied validation case through canonical governed machinery.

    The candidate identity is pinned at construction. run rejects a request for
    any other revision, binds only the supplied case parameters, and executes
    every ACTION exactly once in canonical interpreter order. Control advances
    only after LoopOutcome.VERIFIED.

    A returned verified ClosedLoopOutcome is still not promotion. The existing
    ProcedureValidationRunner independently applies any explicit case
    verification requirement and the canonical varied-validation policy.
    """

    __slots__ = ("_candidate", "_executor", "_graph", "_request_factories")

    def __init__(
        self,
        *,
        candidate: ProcedureRecord,
        executor: Executor,
        request_factories: Mapping[CapabilityIdentity, CapabilityRequestFactory],
    ) -> None:
        if not isinstance(candidate, ProcedureRecord):
            raise TypeError(
                f"candidate must be a ProcedureRecord, got {type(candidate).__name__}"
            )
        if not isinstance(executor, Executor):
            raise TypeError(f"executor must be an Executor, got {type(executor).__name__}")
        if not isinstance(request_factories, Mapping):
            raise TypeError("request_factories must be a mapping")
        for identity, factory in request_factories.items():
            if not isinstance(identity, CapabilityIdentity):
                raise TypeError(
                    "request_factories keys must be CapabilityIdentity values"
                )
            if not callable(factory):
                raise TypeError("request_factories values must be callable")

        self._candidate = candidate
        self._executor = executor
        self._request_factories = dict(request_factories)
        self._graph = _validated_candidate_graph(candidate)

    @property
    def candidate(self) -> ProcedureRecord:
        return self._candidate

    def run(
        self,
        request: ProcedureValidationRunRequest,
    ) -> Result[ClosedLoopOutcome, AgentXError]:
        if not isinstance(request, ProcedureValidationRunRequest):
            raise TypeError(
                "request must be a ProcedureValidationRunRequest, "
                f"got {type(request).__name__}"
            )

        expected = ProcedureCandidateIdentity(
            self._candidate.procedure_id,
            self._candidate.revision,
        )
        if request.candidate != expected:
            return Result.failure(
                _validation_error(
                    "procedure.validation.identity_mismatch",
                    "validation request does not match the pinned candidate revision",
                )
            )

        try:
            action_requests = build_compiled_action_requests(
                self._graph,
                request.case.parameter_binding,
                self._request_factories,
            )
        except (CompiledSkillBindingError, TypeError, ValueError):
            return Result.failure(
                _validation_error(
                    "procedure.validation.parameter_binding_rejected",
                    "validation parameter binding could not form canonical typed requests",
                )
            )

        interpreter = ProcedureInterpreter(
            graph=self._graph,
            max_steps=max(1, len(self._graph.nodes)),
        )
        state = interpreter.start()
        last_outcome: ClosedLoopOutcome | None = None

        while not state.is_terminal:
            instruction = interpreter.instruction(state)
            if instruction.kind is not ProcedureInstructionKind.ACTION_REQUIRED:
                return Result.failure(
                    _validation_error(
                        "procedure.validation.unsupported_instruction",
                        "compiled validation emitted a non-ACTION instruction",
                    )
                )

            node_id = instruction.node_id.to_str()
            capability_request = action_requests.get(node_id)
            if capability_request is None:
                return Result.failure(
                    _validation_error(
                        "procedure.validation.unbound_action",
                        "compiled validation action has no canonical typed request",
                    )
                )

            task = Task.create(
                "validate one compiled procedure action",
                task_id=request.case.run_id,
            )
            context = ExecutionContext(
                correlation_id=request.case.run_id.value,
                cancellation_token=CancellationSource().token,
                task_id=task.task_id,
            )
            result = self._executor.execute(
                ExecutorRequest(
                    task=task,
                    capability_request=capability_request,
                    context=context,
                )
            )
            if result.is_failure:
                return Result.failure(result.unwrap_error())
            outcome = result.unwrap()
            last_outcome = outcome

            if outcome.kind is not LoopOutcome.VERIFIED:
                return Result.success(outcome)

            state = interpreter.advance(
                state,
                StepCompleted(node_kind=ProcedureNodeKind.ACTION),
            )

        if state.status is not InterpreterStatus.TERMINATED or last_outcome is None:
            return Result.failure(
                _validation_error(
                    "procedure.validation.no_verified_action",
                    "candidate did not terminate after at least one verified governed action",
                )
            )
        return Result.success(last_outcome)
