"""First closed-loop deterministic execution path for AgentX (A1.10).

This module is the smallest production-quality orchestration path that proves
the canonical Day-1 pieces form one governed, verified execution loop::

    Task -> authority -> registry resolution -> ActionGate -> EmergencyStop
         -> ResourceBudget -> Capability.execute -> observation
         -> Capability.verify -> Task state -> canonical events + audit

It wires existing canonical contracts together and adds nothing that could
become a second authority:

    - resolution uses the canonical A1.09 :class:`CapabilityRegistry`
      (exact identity only);
    - authority reuses the canonical C1.07 :class:`PermissionEngine` and
      :class:`ActionGate`, including the C1.06 effective-risk floor;
    - safety reuses the canonical C1.09 :class:`EmergencyStop`;
    - resources reuse the canonical C1.08 :class:`ResourceBudget`
      (atomic check-and-consume semantics; no second counter);
    - Task lifecycle reuses the canonical A1.06 transition functions only;
    - evidence reuses the canonical C1.02 :class:`Event` envelope through an
      injected publisher (the composition root wires the C1.03 EventBus) and
      the canonical C1.09 :class:`SecurityAuditRecord` through an injected
      audit sink. No parallel telemetry format exists here.

The governing invariant
-----------------------

    NO ACTION == SUCCESS WITHOUT VERIFICATION.

``Capability.execute()`` returning normally is not success. An observation is
not success. A model claim is not success (this loop makes zero model calls).
A Task reaches ``SUCCEEDED`` only after the capability's canonical
``Capability.verify()`` returns a :class:`VerificationResult` with
``passed=True``. Every denied or failed path ends in a canonical non-success
Task state (``FAILED`` for denials and failures, ``CANCELLED`` for
emergency-stop, cancellation, and deadline expiry) and never fabricates a
:class:`VerificationResult`.

DISCOVERY != AUTHORITY
----------------------

Registry resolution only finds a capability. Execution happens strictly after
permission, gate, emergency-stop, context, and budget checks pass, in that
order. The injected ``authority`` is the one external grant; nothing inside
this loop — and nothing a capability returns — can widen it. Capabilities
receive only the canonical ``(request, context)`` arguments: no authority
object, no gate, no budget, and no event publisher is ever handed to
capability code.

Determinism and scope
---------------------

The path is fully deterministic: no model calls, no reasoning provider, no
network, no clock reads of its own (deadline observation uses the canonical
A1.07 monotonic contract), and no dynamic loading. Events carry the context
``correlation_id`` and are causally chained (each event's ``causation_id`` is
the previous event's id), so the evidence reconstructs the exact chain
Task -> policy/gate decision -> Capability -> Action -> Observation ->
Verification -> Outcome.

Budget semantics
----------------

The preflight delta is the descriptor's own :class:`ResourceEstimate`
(machine actions, wall clock, external cost) and the effective risk level;
model/token/research/repair consumption is always zero because this loop
performs no model work. Consumption happens through the canonical atomic
``check_and_consume`` immediately before execution, so a rejected run consumes
nothing and an attempted machine action is always represented in usage.

Deliberate non-scope
--------------------

This is not the A2.04 Executor subsystem, not a Verifier subsystem, not a Task
Manager, router, or agent loop. It runs exactly one already-known capability
per invocation. It performs no retry, no escalation, no rollback orchestration,
no persistence, no plugin discovery, and no measured wall-clock accounting
(C1.08 supplies elapsed consumption explicitly; the descriptor estimate is the
deterministic Day-1 preflight delta).

Owner: A1.10. Belongs to ``agentx.capabilities`` (the only canonical
subsystem allowed to depend on both ``agentx.core`` and ``agentx.kernel``);
imports only the standard library and canonical ``agentx.core``,
``agentx.kernel``, and sibling ``agentx.capabilities`` contracts. The C1.03
EventBus is injected at composition time rather than imported, because the
boundary manifest defines no ``capabilities -> infrastructure`` edge.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from agentx.capabilities.abi import (
    CapabilityDescriptor,
    CapabilityObservation,
    CapabilityRequest,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.registry import CapabilityNotFoundError, CapabilityRegistry
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.events import (
    ActionPayload,
    DecisionPayload,
    EmptyPayload,
    Event,
    EventPayload,
    EventType,
    ObservationPayload,
    SelectionPayload,
    VerificationPayload,
)
from agentx.core.execution import ExecutionContext
from agentx.core.ids import TaskId
from agentx.core.result import Result
from agentx.core.task_state import try_transition_task
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest, GateResult
from agentx.kernel.audit import AuditContext, AuditOutcome, SecurityAuditRecord
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission, PermissionEngine
from agentx.kernel.resource_budget import (
    BudgetDecision,
    ResourceBudget,
    ResourceDelta,
    ResourceRequest,
    ResourceUsage,
)
from agentx.kernel.risk import RiskLevel

__all__ = [
    "RUNTIME_SOURCE",
    "AuditSink",
    "CapabilityExecutionLoop",
    "ClosedLoopOutcome",
    "EventSink",
    "LoopOutcome",
]

#: Default ``source`` recorded on events published by this runtime path.
RUNTIME_SOURCE: Final[str] = "agentx.capabilities.runtime"

#: Sink that receives canonical :class:`Event` records. The composition root
#: binds the canonical C1.03 ``EventBus.publish`` here; the loop itself never
#: imports infrastructure.
type EventSink = Callable[[Event], object]

#: Sink that receives canonical C1.09 :class:`SecurityAuditRecord` values.
#: Audit storage (C2.04) is injected the same way.
type AuditSink = Callable[[SecurityAuditRecord], object]


class LoopOutcome(StrEnum):
    """Controlled terminal outcome of one deterministic closed-loop run.

    The four values distinguish exactly what A1.10 must distinguish:

    - ``DENIED`` — a pre-action check (resolution, authority, gate,
      emergency stop, context, or budget) refused the run; nothing executed.
    - ``EXECUTION_FAILED`` — the capability invocation failed (explicit
      failed result or raised exception).
    - ``VERIFICATION_FAILED`` — execution produced a result, but canonical
      verification did not confirm the expected postcondition (explicit
      failed verdict or raised exception).
    - ``VERIFIED`` — canonical verification explicitly confirmed the
      expected postcondition; the only path to Task success.
    """

    DENIED = "denied"
    EXECUTION_FAILED = "execution_failed"
    VERIFICATION_FAILED = "verification_failed"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True)
class ClosedLoopOutcome:
    """Immutable terminal state of one closed-loop run.

    ``task`` is the final canonical Task (derived through A1.06 transitions).
    ``verification`` carries the capability's own :class:`VerificationResult`
    exactly when the capability's ``verify`` method returned one; it is
    ``None`` on every path that never reached a returned verdict. A run is a
    verified success if and only if ``kind`` is :attr:`LoopOutcome.VERIFIED`,
    which the loop guarantees is equivalent to
    ``task.status is TaskStatus.SUCCEEDED`` and to
    ``verification.passed is True``.
    """

    task: Task
    kind: LoopOutcome
    error: AgentXError | None
    execution: ExecutionResult | None
    observation: CapabilityObservation | None
    verification: VerificationResult | None
    budget_usage: ResourceUsage

    @property
    def verified(self) -> bool:
        """True only when canonical verification confirmed success."""
        return self.kind is LoopOutcome.VERIFIED


class _CausalEventChain:
    """Builds causally chained canonical events and hands them to the sink.

    Every event uses the run's canonical ``correlation_id`` and task identity,
    and each event's ``causation_id`` is the previous event's id, so the
    published sequence is a deterministic causal chain under the C1.02
    contract.
    """

    __slots__ = ("_correlation_id", "_last_event_id", "_publish", "_source", "_task_id")

    def __init__(
        self,
        publish: EventSink,
        *,
        source: str,
        correlation_id: UUID,
        task_id: str,
    ) -> None:
        self._publish = publish
        self._source = source
        self._correlation_id = correlation_id
        self._task_id = task_id
        self._last_event_id: UUID | None = None

    def emit(self, event_type: EventType, payload: EventPayload) -> Event:
        """Create, publish, and chain one canonical event."""
        event = Event.create(
            event_type=event_type,
            source=self._source,
            payload=payload,
            correlation_id=self._correlation_id,
            causation_id=self._last_event_id,
            task_id=self._task_id,
        )
        self._last_event_id = event.event_id
        self._publish(event)
        return event


@dataclass(frozen=True, slots=True)
class _RunScope:
    """Per-run identity facts threaded through evidence helpers."""

    task_id: TaskId
    correlation_id: UUID


class CapabilityExecutionLoop:
    """The first closed-loop deterministic execution path (A1.10).

    The loop is a plain object with only canonical collaborators injected at
    construction: the A1.09 registry, the C1.07 ActionGate plus the one
    externally granted :class:`AuthorityContext`, the C1.09 EmergencyStop, the
    C1.08 ResourceBudget, and two evidence sinks (events and audit records).
    It keeps no state between runs, so runs are deterministic, and the loop
    itself adds no authority: it can only deny, never grant.
    """

    __slots__ = (
        "_action_gate",
        "_authority",
        "_budget",
        "_emergency_stop",
        "_publish_audit",
        "_publish_event",
        "_registry",
        "_source",
    )

    def __init__(
        self,
        *,
        registry: CapabilityRegistry,
        action_gate: ActionGate,
        authority: AuthorityContext | None,
        emergency_stop: EmergencyStop,
        budget: ResourceBudget,
        publish_event: EventSink,
        publish_audit: AuditSink,
        source: str = RUNTIME_SOURCE,
    ) -> None:
        """Inject canonical collaborators and evidence sinks.

        ``authority`` is the single externally granted permission set for the
        runs of this loop (``None`` means nothing is granted). It is never
        derived from the Task, the request, the registry, or a capability.
        """
        if not isinstance(registry, CapabilityRegistry):
            raise TypeError(f"registry must be a CapabilityRegistry, got {type(registry).__name__}")
        if not isinstance(action_gate, ActionGate):
            raise TypeError(f"action_gate must be an ActionGate, got {type(action_gate).__name__}")
        if authority is not None and not isinstance(authority, AuthorityContext):
            raise TypeError(
                f"authority must be an AuthorityContext or None, got {type(authority).__name__}"
            )
        if not isinstance(emergency_stop, EmergencyStop):
            raise TypeError(
                f"emergency_stop must be an EmergencyStop, got {type(emergency_stop).__name__}"
            )
        if not isinstance(budget, ResourceBudget):
            raise TypeError(f"budget must be a ResourceBudget, got {type(budget).__name__}")
        if not callable(publish_event):
            raise TypeError("publish_event must be callable")
        if not callable(publish_audit):
            raise TypeError("publish_audit must be callable")
        if not isinstance(source, str) or not source or source != source.strip():
            raise ValueError("source must be a non-empty trimmed string")

        self._registry = registry
        self._action_gate = action_gate
        self._authority = authority
        self._emergency_stop = emergency_stop
        self._budget = budget
        self._publish_event = publish_event
        self._publish_audit = publish_audit
        self._source = source

    # ------------------------------------------------------------------
    # Public entry point.
    # ------------------------------------------------------------------

    def run(
        self,
        task: Task,
        request: CapabilityRequest[Any],
        context: ExecutionContext,
    ) -> Result[ClosedLoopOutcome, AgentXError]:
        """Run one governed capability through the full canonical loop.

        Returns ``Result.success(ClosedLoopOutcome)`` for every deterministic
        terminal state of the loop (denied, execution failed, verification
        failed, verified). The outcome's ``kind`` and the final Task status
        carry the truth; :attr:`LoopOutcome.VERIFIED` is the only kind that
        ever accompanies ``TaskStatus.SUCCEEDED``.

        Returns ``Result.failure(AgentXError)`` — without touching Task state
        and without publishing any evidence — when the request itself is
        invalid (context/task identity mismatch, or the Task is not
        ``PENDING``). Wrong argument types are programming errors and raise
        ``TypeError``.
        """
        if not isinstance(task, Task):
            raise TypeError(f"task must be a Task, got {type(task).__name__}")
        if not isinstance(request, CapabilityRequest):
            raise TypeError(f"request must be a CapabilityRequest, got {type(request).__name__}")
        if not isinstance(context, ExecutionContext):
            raise TypeError(f"context must be an ExecutionContext, got {type(context).__name__}")

        # Value-level refusal: no state change, no events, no audit.
        if context.task_id is not None and context.task_id != task.task_id:
            return Result[ClosedLoopOutcome, AgentXError].failure(
                _refusal_error(
                    code="runtime.context_task_mismatch",
                    message="execution context task identity does not match the requested Task",
                )
            )
        if task.status is not TaskStatus.PENDING:
            return Result[ClosedLoopOutcome, AgentXError].failure(
                _refusal_error(
                    code="runtime.task_not_pending",
                    message=(
                        "closed-loop execution requires a Task in PENDING status; "
                        f"got {task.status.value}"
                    ),
                )
            )

        # Canonical A1.06 transition: PENDING -> RUNNING. Every later state
        # change also goes through the canonical transition functions.
        started = try_transition_task(task, TaskStatus.RUNNING)
        if started.is_failure:  # pragma: no cover - PENDING -> RUNNING is legal
            return Result[ClosedLoopOutcome, AgentXError].failure(started.unwrap_error())
        current = started.unwrap()

        scope = _RunScope(task_id=task.task_id, correlation_id=context.correlation_id)
        chain = _CausalEventChain(
            self._publish_event,
            source=self._source,
            correlation_id=context.correlation_id,
            task_id=task.task_id_str,
        )
        chain.emit(EventType.TASK_STARTED, EmptyPayload())
        usage = self._budget.snapshot()

        # 2. Resolve the exact capability identity through the A1.09 registry.
        #    Discovery only finds; it never executes.
        try:
            capability = self._registry.require(request.identity)
        except CapabilityNotFoundError:
            error = _failure_error(
                code="runtime.capability_not_registered",
                message=f"capability {request.identity} is not registered",
                category=ErrorCategory.NOT_FOUND,
                details={"capability": str(request.identity)},
            )
            return self._failed(
                chain,
                current,
                usage,
                scope=scope,
                kind=LoopOutcome.DENIED,
                error=error,
                observation=None,
            )

        descriptor = self._registry.describe(request.identity)
        if descriptor is None:  # pragma: no cover - registry invariant right after require
            error = _failure_error(
                code="runtime.descriptor_missing",
                message=f"registry returned no descriptor for {request.identity}",
                category=ErrorCategory.INTERNAL,
                details={"capability": str(request.identity)},
            )
            return self._failed(
                chain,
                current,
                usage,
                scope=scope,
                kind=LoopOutcome.DENIED,
                error=error,
                observation=None,
            )

        chain.emit(
            EventType.CAPABILITY_SELECTED,
            SelectionPayload(
                selection=str(request.identity),
                reason="exact-identity resolution from the canonical capability registry",
            ),
        )
        effective_level = descriptor.risk_assessment.effective_level

        # 4+5. Canonical permission/authority checks, then canonical effective
        # risk through the ActionGate. Any non-ALLOW decision denies the run.
        denial = self._evaluate_authority(chain, scope, descriptor)
        if denial is not None:
            return self._denied(chain, current, usage, scope, denial, policy_reason=None)

        # 6. Canonical C1.09 emergency stop.
        if self._emergency_stop.stop_requested:
            self._record_audit(
                scope,
                operation="runtime.emergency_stop",
                outcome=AuditOutcome.STOP_REQUESTED,
                reason="emergency stop is active; governed execution is halted",
                risk_level=effective_level,
                target=str(request.identity),
            )
            error = _failure_error(
                code="runtime.emergency_stop_active",
                message="emergency stop is active; no governed execution may start",
                category=ErrorCategory.PRECONDITION,
                details={"capability": str(request.identity)},
            )
            return self._cancelled(
                chain,
                current,
                usage,
                error,
                policy_reason="DENY: emergency stop is active.",
            )

        # Canonical A1.07 cooperative stop conditions (cancellation/deadline).
        stop = context.observe_stop()
        if stop.should_stop:
            reasons = ", ".join(reason.value for reason in stop.reasons)
            policy_reason = f"DENY: execution context stop condition active ({reasons})."
            self._record_audit(
                scope,
                operation="runtime.context_stop",
                outcome=AuditOutcome.DENY,
                reason=policy_reason,
                risk_level=effective_level,
                target=str(request.identity),
            )
            error = _failure_error(
                code="runtime.context_stopped",
                message=f"execution context stop condition active: {reasons}",
                category=ErrorCategory.CANCELLED,
                retryability=Retryability.UNKNOWN,
                details={"capability": str(request.identity), "stop_reasons": reasons},
            )
            return self._cancelled(chain, current, usage, error, policy_reason)

        # 7. Canonical C1.08 budget: atomic preflight check-and-consume of the
        # descriptor-declared estimate. A DENY consumes nothing.
        budget_result = self._budget.check_and_consume(
            ResourceRequest(
                delta=ResourceDelta(
                    wall_clock=descriptor.estimate.wall_clock,
                    model_calls=0,
                    model_tokens=0,
                    research_queries=0,
                    machine_actions=descriptor.estimate.machine_actions,
                    repair_attempts=0,
                    external_cost=descriptor.estimate.external_cost,
                ),
                risk_level=effective_level,
            )
        )
        self._record_audit(
            scope,
            operation="runtime.budget",
            outcome=(
                AuditOutcome.ALLOW
                if budget_result.decision is BudgetDecision.ALLOW
                else AuditOutcome.DENY
            ),
            reason=budget_result.reason,
            risk_level=effective_level,
            target=str(request.identity),
        )
        if budget_result.decision is BudgetDecision.DENY:
            error = _failure_error(
                code="runtime.budget_denied",
                message=budget_result.reason,
                category=ErrorCategory.RESOURCE,
                details={"capability": str(request.identity)},
            )
            return self._denied(
                chain,
                current,
                usage,
                scope,
                error,
                policy_reason=budget_result.reason,
            )
        chain.emit(
            EventType.POLICY_DECISION,
            DecisionPayload(decision="ALLOW", reason=budget_result.reason),
        )
        usage = budget_result.usage_after

        # 8. Execute — only after every pre-action gate passed.
        chain.emit(
            EventType.ACTION_REQUESTED,
            ActionPayload(
                name=str(request.identity),
                data={
                    "operation": str(request.identity),
                    "risk_level": effective_level.value,
                    "required_permissions": sorted(
                        permission.value for permission in descriptor.required_permissions
                    ),
                },
            ),
        )
        try:
            execution = capability.execute(request, context)
        except Exception as exc:
            chain.emit(
                EventType.ACTION_FAILED,
                ActionPayload(
                    name=str(request.identity),
                    data={"failure": "exception", "exception_type": type(exc).__name__},
                ),
            )
            error = _failure_error(
                code="runtime.capability_execution_raised",
                message=f"capability execute raised {type(exc).__name__}",
                category=ErrorCategory.EXECUTION,
                details={"capability": str(request.identity)},
                cause=exc,
            )
            return self._failed(
                chain,
                current,
                usage,
                scope=scope,
                kind=LoopOutcome.EXECUTION_FAILED,
                error=error,
                observation=None,
            )

        # 9. Canonical observation evidence, then the action's terminal event.
        if execution.succeeded:
            chain.emit(
                EventType.ACTION_COMPLETED,
                ActionPayload(name=str(request.identity), data={"succeeded": True}),
            )
        else:
            chain.emit(
                EventType.ACTION_FAILED,
                ActionPayload(name=str(request.identity), data={"failure": "execution_result"}),
            )
        chain.emit(
            EventType.OBSERVATION_RECORDED,
            ObservationPayload(value=execution.observation.to_dict()),
        )

        if not execution.succeeded:
            # Execution returning normally is NOT success; a failed invocation
            # result is an execution failure and never reaches verification.
            error = _failure_error(
                code="runtime.capability_execution_failed",
                message=execution.message,
                category=ErrorCategory.EXECUTION,
                details={"capability": str(request.identity)},
            )
            return self._failed(
                chain,
                current,
                usage,
                scope=scope,
                kind=LoopOutcome.EXECUTION_FAILED,
                error=error,
                observation=execution.observation,
                execution=execution,
            )

        # 10. Canonical verification — the only source of success.
        try:
            verification = capability.verify(request, execution.observation, context)
        except Exception as exc:
            chain.emit(
                EventType.VERIFICATION_COMPLETED,
                VerificationPayload(
                    passed=False,
                    detail=f"capability verification raised {type(exc).__name__}",
                ),
            )
            error = _failure_error(
                code="runtime.verification_raised",
                message=f"capability verify raised {type(exc).__name__}",
                category=ErrorCategory.VERIFICATION,
                details={"capability": str(request.identity)},
                cause=exc,
            )
            return self._failed(
                chain,
                current,
                usage,
                scope=scope,
                kind=LoopOutcome.VERIFICATION_FAILED,
                error=error,
                observation=execution.observation,
                execution=execution,
            )

        chain.emit(
            EventType.VERIFICATION_COMPLETED,
            VerificationPayload(passed=verification.passed, detail=verification.detail),
        )
        if not verification.passed:
            error = _failure_error(
                code="runtime.verification_failed",
                message=verification.detail,
                category=ErrorCategory.VERIFICATION,
                details={"capability": str(request.identity)},
            )
            return self._failed(
                chain,
                current,
                usage,
                scope=scope,
                kind=LoopOutcome.VERIFICATION_FAILED,
                error=error,
                observation=execution.observation,
                verification=verification,
                execution=execution,
            )

        # 12. Verified success: the only path to canonical Task success.
        completed = try_transition_task(current, TaskStatus.SUCCEEDED)
        if completed.is_failure:  # pragma: no cover - RUNNING -> SUCCEEDED is legal
            return Result[ClosedLoopOutcome, AgentXError].failure(completed.unwrap_error())
        final_task = completed.unwrap()
        chain.emit(EventType.TASK_COMPLETED, EmptyPayload())
        self._record_audit(
            scope,
            operation="runtime.run",
            outcome=AuditOutcome.SUCCEEDED,
            reason="canonical verification confirmed the expected postcondition",
            risk_level=effective_level,
            target=str(request.identity),
        )
        return Result[ClosedLoopOutcome, AgentXError].success(
            ClosedLoopOutcome(
                task=final_task,
                kind=LoopOutcome.VERIFIED,
                error=None,
                execution=execution,
                observation=execution.observation,
                verification=verification,
                budget_usage=usage,
            )
        )

    # ------------------------------------------------------------------
    # Authority evaluation.
    # ------------------------------------------------------------------

    def _evaluate_authority(
        self,
        chain: _CausalEventChain,
        scope: _RunScope,
        descriptor: CapabilityDescriptor,
    ) -> AgentXError | None:
        """Run canonical permission checks and the canonical ActionGate.

        Returns ``None`` when every required permission is granted and every
        gate evaluation returns ALLOW; otherwise returns the structured
        denial. Both allow and deny outcomes are published as canonical
        POLICY_DECISION events and canonical audit records.
        """
        required = tuple(sorted(descriptor.required_permissions, key=lambda item: item.value))
        effective_level = descriptor.risk_assessment.effective_level
        identity = str(descriptor.identity)

        if not required:
            # A risk-bearing operation that declares no required permission
            # can never be checked against authority: fail closed instead.
            if effective_level is not RiskLevel.R0:
                reason = (
                    f"DENY: capability declares no required permissions while effective "
                    f"risk is {effective_level.value}; authority cannot be checked."
                )
                self._record_audit(
                    scope,
                    operation="runtime.permission",
                    outcome=AuditOutcome.DENY,
                    reason=reason,
                    risk_level=effective_level,
                    target=identity,
                )
                self._emit_decision(chain, "DENY", reason)
                return _failure_error(
                    code="runtime.permission_denied",
                    message=reason,
                    category=ErrorCategory.PERMISSION,
                    details={"capability": identity},
                )
            reason = "ALLOW: capability is read-only and declares no required permissions."
            self._record_audit(
                scope,
                operation="runtime.permission",
                outcome=AuditOutcome.ALLOW,
                reason=reason,
                risk_level=effective_level,
                target=identity,
            )
            self._emit_decision(chain, "ALLOW", reason)
            return None

        for permission in required:
            permission_check = PermissionEngine().check(permission, self._authority)
            if not permission_check.present:
                self._record_audit(
                    scope,
                    operation="runtime.permission",
                    outcome=AuditOutcome.DENY,
                    reason=permission_check.reason,
                    risk_level=effective_level,
                    target=identity,
                    permission=permission,
                )
                self._emit_decision(chain, "DENY", permission_check.reason)
                return _failure_error(
                    code="runtime.permission_denied",
                    message=permission_check.reason,
                    category=ErrorCategory.PERMISSION,
                    details={
                        "capability": identity,
                        "required_permission": permission.value,
                    },
                )
            gate_result = self._action_gate.evaluate(
                GateRequest(
                    operation=identity,
                    required_permission=permission,
                    risk_assessment=descriptor.risk_assessment,
                ),
                self._authority,
            )
            self._record_audit(
                scope,
                operation="runtime.action_gate",
                outcome=_gate_outcome(gate_result),
                reason=gate_result.reason,
                risk_level=effective_level,
                target=identity,
                permission=permission,
            )
            if gate_result.decision is not GateDecision.ALLOW:
                self._emit_decision(chain, gate_result.decision.value, gate_result.reason)
                return _failure_error(
                    code="runtime.gate_denied",
                    message=gate_result.reason,
                    category=ErrorCategory.PERMISSION,
                    details={
                        "capability": identity,
                        "required_permission": permission.value,
                        "decision": gate_result.decision.value,
                    },
                )

        allowed_reason = (
            f"ALLOW: Action Gate allowed all {len(required)} required permission(s) at "
            f"effective risk {effective_level.value}."
        )
        self._emit_decision(chain, "ALLOW", allowed_reason)
        return None

    # ------------------------------------------------------------------
    # Terminal-state helpers. Every helper closes the run through canonical
    # A1.06 transitions and canonical evidence only.
    # ------------------------------------------------------------------

    def _denied(
        self,
        chain: _CausalEventChain,
        current: Task,
        usage: ResourceUsage,
        scope: _RunScope,
        error: AgentXError,
        *,
        policy_reason: str | None,
    ) -> Result[ClosedLoopOutcome, AgentXError]:
        """Close a run denied by a pre-action check: Task FAILED, zero execution."""
        if policy_reason is not None:
            self._emit_decision(chain, "DENY", policy_reason)
        return self._failed(
            chain,
            current,
            usage,
            scope=scope,
            kind=LoopOutcome.DENIED,
            error=error,
            observation=None,
        )

    def _cancelled(
        self,
        chain: _CausalEventChain,
        current: Task,
        usage: ResourceUsage,
        error: AgentXError,
        policy_reason: str,
    ) -> Result[ClosedLoopOutcome, AgentXError]:
        """Close a run stopped for safety (emergency stop / context stop).

        The C1.02 taxonomy has no cancelled-task lifecycle event, so the DENY
        policy decision plus the audit record carry the evidence; the Task
        still reaches its canonical terminal CANCELLED state.
        """
        self._emit_decision(chain, "DENY", policy_reason)
        cancelled = try_transition_task(current, TaskStatus.CANCELLED)
        if cancelled.is_failure:  # pragma: no cover - RUNNING -> CANCELLED is legal
            return Result[ClosedLoopOutcome, AgentXError].failure(cancelled.unwrap_error())
        final_task = cancelled.unwrap()
        return Result[ClosedLoopOutcome, AgentXError].success(
            ClosedLoopOutcome(
                task=final_task,
                kind=LoopOutcome.DENIED,
                error=error,
                execution=None,
                observation=None,
                verification=None,
                budget_usage=usage,
            )
        )

    def _failed(
        self,
        chain: _CausalEventChain,
        current: Task,
        usage: ResourceUsage,
        *,
        scope: _RunScope,
        kind: LoopOutcome,
        error: AgentXError,
        observation: CapabilityObservation | None,
        verification: VerificationResult | None = None,
        execution: ExecutionResult | None = None,
    ) -> Result[ClosedLoopOutcome, AgentXError]:
        """Close a failed run: canonical RUNNING -> FAILED plus TASK_FAILED."""
        failed = try_transition_task(current, TaskStatus.FAILED)
        if failed.is_failure:  # pragma: no cover - RUNNING -> FAILED is legal
            return Result[ClosedLoopOutcome, AgentXError].failure(failed.unwrap_error())
        final_task = failed.unwrap()
        chain.emit(EventType.TASK_FAILED, EmptyPayload())
        self._record_audit(
            scope,
            operation="runtime.run",
            outcome=AuditOutcome.FAILED,
            reason=error.message,
            risk_level=None,
            target=str(error.details.get("capability", "unknown")),
        )
        return Result[ClosedLoopOutcome, AgentXError].success(
            ClosedLoopOutcome(
                task=final_task,
                kind=kind,
                error=error,
                execution=execution,
                observation=observation,
                verification=verification,
                budget_usage=usage,
            )
        )

    # ------------------------------------------------------------------
    # Evidence helpers.
    # ------------------------------------------------------------------

    def _emit_decision(
        self,
        chain: _CausalEventChain,
        decision: str,
        reason: str,
    ) -> None:
        """Publish one canonical POLICY_DECISION event on the run's chain."""
        chain.emit(
            EventType.POLICY_DECISION,
            DecisionPayload(decision=decision, reason=reason),
        )

    def _record_audit(
        self,
        scope: _RunScope,
        *,
        operation: str,
        outcome: AuditOutcome,
        reason: str,
        risk_level: RiskLevel | None,
        target: str,
        permission: Permission | None = None,
    ) -> None:
        """Emit one canonical C1.09 audit record through the injected sink."""
        self._publish_audit(
            SecurityAuditRecord.create(
                operation=operation,
                outcome=outcome,
                reason=reason,
                task_id=scope.task_id,
                correlation_id=scope.correlation_id,
                risk_level=risk_level,
                context=AuditContext(target=target, permission=permission),
            )
        )


def _gate_outcome(gate_result: GateResult) -> AuditOutcome:
    """Map a canonical gate decision onto the canonical audit vocabulary."""
    if gate_result.decision is GateDecision.ALLOW:
        return AuditOutcome.ALLOW
    if gate_result.decision is GateDecision.DENY:
        return AuditOutcome.DENY
    return AuditOutcome.REQUIRE_CONFIRMATION


def _failure_error(
    *,
    code: str,
    message: str,
    category: ErrorCategory,
    details: dict[str, Any] | None = None,
    cause: BaseException | None = None,
    retryability: Retryability = Retryability.NON_RETRYABLE,
) -> AgentXError:
    """Build the structured error for a failed governed run."""
    return AgentXError(
        code=code,
        message=message,
        category=category,
        retryability=retryability,
        details=details,
        cause=cause,
    )


def _refusal_error(*, code: str, message: str) -> AgentXError:
    """Build the structured error for refusing an invalid run request."""
    return AgentXError(
        code=code,
        message=message,
        category=ErrorCategory.VALIDATION,
        retryability=Retryability.NON_RETRYABLE,
    )
