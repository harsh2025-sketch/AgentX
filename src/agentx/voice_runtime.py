"""Voice-to-AgentX bridge, telemetry and spoken confirmation protocol for M11.

Voice is an input modality only. Transcripts become inert Task objective data and
never select capabilities, grant authority, alter risk/budgets, or manufacture
verification. Governed actions remain owned by the existing AgentLoop/Executor,
ActionGate, HumanApproval, EmergencyStop and Verifier paths.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

from agentx.agent_loop import (
    AgentLoop,
    OrchestrationLimits,
    OrchestrationOutcome,
    OrchestrationRequest,
)
from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.executor import Executor, ExecutorRequest
from agentx.capabilities.human_approval import (
    HumanApprovalDecision,
    HumanApprovalOutcome,
    HumanApprovalRequest,
)
from agentx.capabilities.runtime import CapabilityExecutionLoop, ClosedLoopOutcome
from agentx.capabilities.verifier import VerificationRequirement
from agentx.cognition.router import RoutingEvidence
from agentx.cognition.task_manager import TaskManager
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.events import Event
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result
from agentx.core.runtime_ui_events import RuntimeUiEvent, RuntimeUiEventKind, project_event_for_ui
from agentx.core.tasks import Task

__all__ = [
    "PendingVoiceConfirmation",
    "SpokenConfirmationProtocol",
    "VoiceGovernedActionBridge",
    "VoiceRuntimeTelemetry",
    "VoiceTaskBridge",
    "VoiceTaskPlan",
]


def _error(code: str, message: str, category: ErrorCategory) -> AgentXError:
    return AgentXError(
        code=f"voice.{code}",
        message=message,
        category=category,
        retryability=Retryability.NON_RETRYABLE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class VoiceTaskPlan:
    """Trusted composition facts for one voice-originated task.

    No field is derived from transcript text inside this boundary.
    """

    routing_evidence: RoutingEvidence
    requirement: VerificationRequirement
    limits: OrchestrationLimits

    def __post_init__(self) -> None:
        if not isinstance(self.routing_evidence, RoutingEvidence):
            raise TypeError("routing_evidence must be RoutingEvidence")
        if not isinstance(self.requirement, VerificationRequirement):
            raise TypeError("requirement must be VerificationRequirement")
        if not isinstance(self.limits, OrchestrationLimits):
            raise TypeError("limits must be OrchestrationLimits")


class VoiceRuntimeTelemetry:
    """Sequenced, redacted runtime-to-HUD telemetry using the AX-452 envelope."""

    __slots__ = ("_dropped", "_runtime_instance_id", "_sequence", "_sink")

    def __init__(
        self,
        sink: Callable[[RuntimeUiEvent], None],
        *,
        runtime_instance_id: UUID | None = None,
    ) -> None:
        if not callable(sink):
            raise TypeError("sink must be callable")
        self._sink = sink
        self._runtime_instance_id = uuid4() if runtime_instance_id is None else runtime_instance_id
        if not isinstance(self._runtime_instance_id, UUID) or self._runtime_instance_id.int == 0:
            raise ValueError("runtime_instance_id must be a non-nil UUID")
        self._sequence = 0
        self._dropped = 0

    @property
    def runtime_instance_id(self) -> UUID:
        return self._runtime_instance_id

    @property
    def dropped_events(self) -> int:
        return self._dropped

    def state(
        self,
        state: str,
        *,
        context: ExecutionContext | None = None,
        correlation_id: UUID | None = None,
        task: Task | None = None,
        payload: dict[str, object] | None = None,
    ) -> RuntimeUiEvent:
        if (
            context is not None
            and correlation_id is not None
            and context.correlation_id != correlation_id
        ):
            raise ValueError("context and explicit correlation_id disagree")
        event_correlation_id = context.correlation_id if context is not None else correlation_id
        event = RuntimeUiEvent(
            runtime_instance_id=self._runtime_instance_id,
            sequence=self._sequence,
            timestamp=datetime.now(UTC),
            kind=RuntimeUiEventKind.EVENT,
            state=state,
            correlation_id=event_correlation_id,
            task_id=None if task is None else task.task_id.to_str(),
            payload={} if payload is None else payload,
        )
        self._sequence += 1
        try:
            self._sink(event)
        except Exception:
            self._dropped += 1
        return event

    def canonical(self, event: Event) -> RuntimeUiEvent:
        projected = project_event_for_ui(
            event,
            runtime_instance_id=self._runtime_instance_id,
            sequence=self._sequence,
        )
        self._sequence += 1
        try:
            self._sink(projected)
        except Exception:
            self._dropped += 1
        return projected


class VoiceTaskBridge:
    """Convert untrusted transcript text into canonical bounded orchestration."""

    __slots__ = ("_active", "_agent_loop", "_lock", "_task_manager", "_telemetry")

    def __init__(
        self,
        *,
        task_manager: TaskManager,
        agent_loop: AgentLoop,
        telemetry: VoiceRuntimeTelemetry,
    ) -> None:
        if not isinstance(task_manager, TaskManager):
            raise TypeError("task_manager must be TaskManager")
        if not isinstance(agent_loop, AgentLoop):
            raise TypeError("agent_loop must be AgentLoop")
        if not isinstance(telemetry, VoiceRuntimeTelemetry):
            raise TypeError("telemetry must be VoiceRuntimeTelemetry")
        self._task_manager = task_manager
        self._agent_loop = agent_loop
        self._telemetry = telemetry
        self._lock = Lock()
        self._active: CancellationSource | None = None

    @property
    def telemetry(self) -> VoiceRuntimeTelemetry:
        return self._telemetry

    def ingest(
        self,
        transcript: str,
        plan: VoiceTaskPlan,
        *,
        correlation_id: UUID | None = None,
    ) -> Result[OrchestrationOutcome, AgentXError]:
        if not isinstance(transcript, str) or not transcript.strip():
            return Result.failure(
                _error(
                    "empty_transcript",
                    "voice transcript is empty",
                    ErrorCategory.VALIDATION,
                )
            )
        if len(transcript) > 64_000:
            return Result.failure(
                _error(
                    "transcript_too_large",
                    "voice transcript exceeds bound",
                    ErrorCategory.RESOURCE,
                )
            )
        if not isinstance(plan, VoiceTaskPlan):
            raise TypeError("plan must be VoiceTaskPlan")

        task = self._task_manager.create(
            transcript,
            metadata={"input_modality": "voice"},
        )
        cancellation = CancellationSource()
        actual_correlation_id = uuid4() if correlation_id is None else correlation_id
        context = ExecutionContext(
            correlation_id=actual_correlation_id,
            cancellation_token=cancellation.token,
            task_id=task.task_id,
        )
        with self._lock:
            if self._active is not None:
                return Result.failure(
                    _error("session_busy", "another voice task is active", ErrorCategory.CONFLICT)
                )
            self._active = cancellation

        self._telemetry.state("voice.processing", context=context, task=task)
        routed = plan.routing_evidence
        reasoning = (
            not routed.verified_reusable_result
            and not routed.deterministic_direct_path
            and not routed.verified_reasoning_free_procedure
        )
        if reasoning:
            self._telemetry.state("voice.reasoning", context=context, task=task)
        self._telemetry.state(
            "voice.executing",
            context=context,
            task=task,
        )
        try:
            outcome = self._agent_loop.run(
                OrchestrationRequest(
                    task=task,
                    context=context,
                    routing_evidence=plan.routing_evidence,
                    requirement=plan.requirement,
                    limits=plan.limits,
                )
            )
            if outcome.is_failure:
                self._telemetry.state("voice.failed", context=context, task=task)
                return outcome
            value = outcome.unwrap()
            if value.attempt_count > 1:
                self._telemetry.state(
                    "voice.recovering",
                    context=context,
                    task=value.task,
                    payload={"attempt_count": value.attempt_count},
                )
            self._telemetry.state("voice.verifying", context=context, task=value.task)
            self._telemetry.state(
                "voice.verified" if value.verified else "voice.failed",
                context=context,
                task=value.task,
                payload={"verified": value.verified},
            )
            return outcome
        finally:
            with self._lock:
                self._active = None

    def cancel(self, reason: str = "voice runtime cancellation") -> bool:
        with self._lock:
            active = self._active
        if active is None:
            return False
        return active.request_cancellation(reason)

    def barge_in(self) -> bool:
        return self.cancel("user barge-in")


@dataclass(frozen=True, slots=True, kw_only=True)
class PendingVoiceConfirmation:
    """Ephemeral confirmation bound to one exact canonical approval request."""

    request: HumanApprovalRequest
    nonce: str
    issued_monotonic: float
    expires_monotonic: float

    def __post_init__(self) -> None:
        if not isinstance(self.request, HumanApprovalRequest):
            raise TypeError("request must be HumanApprovalRequest")
        if not isinstance(self.nonce, str) or not self.nonce or len(self.nonce) > 64:
            raise ValueError("nonce must be bounded non-empty text")
        if self.expires_monotonic <= self.issued_monotonic:
            raise ValueError("confirmation expiry must follow issuance")


class SpokenConfirmationProtocol:
    """Fresh, exact-request, non-replayable spoken confirmation evidence."""

    __slots__ = ("_active", "_consumed", "_lock")

    def __init__(self) -> None:
        self._active: dict[str, PendingVoiceConfirmation] = {}
        self._consumed: set[str] = set()
        self._lock = Lock()

    def issue(
        self,
        request: HumanApprovalRequest,
        *,
        ttl_seconds: float = 30.0,
        nonce: str | None = None,
        now: float | None = None,
    ) -> PendingVoiceConfirmation:
        if not isinstance(request, HumanApprovalRequest):
            raise TypeError("request must be HumanApprovalRequest")
        if not isinstance(ttl_seconds, int | float) or not 1 <= ttl_seconds <= 300:
            raise ValueError("ttl_seconds must be in [1, 300]")
        issued = time.monotonic() if now is None else now
        token = secrets.token_urlsafe(8) if nonce is None else nonce
        pending = PendingVoiceConfirmation(
            request=request,
            nonce=token,
            issued_monotonic=issued,
            expires_monotonic=issued + float(ttl_seconds),
        )
        with self._lock:
            self._active[token] = pending
        return pending

    def resolve(
        self,
        transcript: str,
        pending: PendingVoiceConfirmation,
        *,
        now: float | None = None,
    ) -> Result[HumanApprovalDecision, AgentXError]:
        if not isinstance(transcript, str):
            raise TypeError("transcript must be str")
        if not isinstance(pending, PendingVoiceConfirmation):
            raise TypeError("pending must be PendingVoiceConfirmation")
        current = time.monotonic() if now is None else now
        normalized = " ".join(transcript.strip().casefold().split())
        with self._lock:
            if pending.nonce in self._consumed:
                return Result.failure(
                    _error(
                        "confirmation_replay",
                        "spoken confirmation was already consumed",
                        ErrorCategory.CONFLICT,
                    )
                )
            active = self._active.get(pending.nonce)
            if active != pending:
                return Result.failure(
                    _error(
                        "confirmation_stale",
                        "spoken confirmation is no longer active",
                        ErrorCategory.CONFLICT,
                    )
                )
            if current > pending.expires_monotonic:
                self._active.pop(pending.nonce, None)
                self._consumed.add(pending.nonce)
                return Result.failure(
                    _error(
                        "confirmation_expired",
                        "spoken confirmation expired",
                        ErrorCategory.TIMEOUT,
                    )
                )
            approve_phrase = f"confirm {pending.nonce}".casefold()
            deny_phrase = f"reject {pending.nonce}".casefold()
            if normalized not in {approve_phrase, deny_phrase}:
                return Result.failure(
                    _error(
                        "confirmation_mismatch",
                        "spoken confirmation did not exactly match the pending request",
                        ErrorCategory.PERMISSION,
                    )
                )
            self._active.pop(pending.nonce, None)
            self._consumed.add(pending.nonce)
        outcome = (
            HumanApprovalOutcome.APPROVED
            if normalized == approve_phrase
            else HumanApprovalOutcome.DENIED
        )
        return Result.success(HumanApprovalDecision(request=pending.request, outcome=outcome))

    def invalidate(self, pending: PendingVoiceConfirmation) -> None:
        if not isinstance(pending, PendingVoiceConfirmation):
            raise TypeError("pending must be PendingVoiceConfirmation")
        with self._lock:
            self._active.pop(pending.nonce, None)
            self._consumed.add(pending.nonce)


class VoiceGovernedActionBridge:
    """Trusted composition helper for exact approval preflight and canonical execution."""

    __slots__ = ("_executor", "_loop")

    def __init__(
        self,
        *,
        execution_loop: CapabilityExecutionLoop,
        executor: Executor,
    ) -> None:
        if not isinstance(execution_loop, CapabilityExecutionLoop):
            raise TypeError("execution_loop must be CapabilityExecutionLoop")
        if not isinstance(executor, Executor):
            raise TypeError("executor must be Executor")
        if executor.execution_loop is not execution_loop:
            raise ValueError("executor and execution_loop must share the same canonical runtime")
        self._loop = execution_loop
        self._executor = executor

    def approval_requests(
        self,
        task: Task,
        request: CapabilityRequest[Any],
        context: ExecutionContext,
    ) -> Result[tuple[HumanApprovalRequest, ...], AgentXError]:
        return self._loop.approval_requests(task, request, context)

    def execute(
        self,
        request: ExecutorRequest,
        *,
        approvals: tuple[HumanApprovalDecision, ...] = (),
    ) -> Result[ClosedLoopOutcome, AgentXError]:
        return self._executor.execute(request, approvals=approvals)
