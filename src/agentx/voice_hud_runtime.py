"""Production composition root for one M11 voice/HUD interaction.

This module composes, rather than reimplements, the realtime voice state machine,
canonical VoiceTaskBridge and AX-452 HUD telemetry. A spoken turn is transcribed
as untrusted data, ingested through the canonical AgentLoop, independently
verified there, and only then rendered as a short spoken outcome. Barge-in
propagates to both the realtime audio owner and the governed task owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agentx.agent_loop import OrchestrationOutcome
from agentx.capabilities.human_approval import HumanApprovalDecision
from agentx.cognition.realtime_voice import RealtimeVoiceSession, VoiceActivity
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result
from agentx.voice_runtime import (
    PendingVoiceConfirmation,
    SpokenConfirmationProtocol,
    VoiceTaskBridge,
    VoiceTaskPlan,
)

__all__ = [
    "ApprovalDecisionSink",
    "RetryCommandTarget",
    "VoiceControlTarget",
    "VoiceHudController",
    "VoiceHudRuntime",
    "VoiceTurnResult",
]


def _error(code: str, message: str, category: ErrorCategory) -> AgentXError:
    return AgentXError(
        code=f"voice_hud.{code}",
        message=message,
        category=category,
        retryability=Retryability.NON_RETRYABLE,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class VoiceTurnResult:
    transcript_received: bool
    outcome: OrchestrationOutcome
    spoken: bool


class VoiceHudRuntime:
    """Bounded synchronous voice/HUD composition with cross-owner cancellation."""

    __slots__ = ("_session", "_task_bridge")

    def __init__(
        self,
        *,
        session: RealtimeVoiceSession,
        task_bridge: VoiceTaskBridge,
    ) -> None:
        if not isinstance(session, RealtimeVoiceSession):
            raise TypeError("session must be RealtimeVoiceSession")
        if not isinstance(task_bridge, VoiceTaskBridge):
            raise TypeError("task_bridge must be VoiceTaskBridge")
        self._session = session
        self._task_bridge = task_bridge

    def run_turn(
        self,
        plan: VoiceTaskPlan,
        *,
        max_capture_frames: int = 32,
    ) -> Result[VoiceTurnResult, AgentXError]:
        if not isinstance(plan, VoiceTaskPlan):
            raise TypeError("plan must be VoiceTaskPlan")
        if type(max_capture_frames) is not int or not 1 <= max_capture_frames <= 512:
            raise ValueError("max_capture_frames must be in [1, 512]")

        self._task_bridge.telemetry.state("voice.listening")
        self._session.start_turn()
        speech_seen = False
        for _ in range(max_capture_frames):
            observed = self._session.capture_frame()
            if observed.is_failure:
                return Result.failure(observed.unwrap_error())
            observation = observed.unwrap()
            if observation.activity is VoiceActivity.SPEECH:
                speech_seen = True
            if observation.turn_ended:
                break
        if not speech_seen:
            self._session.barge_in(reason="bounded capture ended without speech")
            return Result.failure(
                _error(
                    "no_speech",
                    "bounded voice capture completed without speech activity",
                    ErrorCategory.TIMEOUT,
                )
            )

        transcript_result = self._session.finish_turn()
        if transcript_result.is_failure:
            return Result.failure(transcript_result.unwrap_error())
        transcript = transcript_result.unwrap()

        governed = self._task_bridge.ingest(transcript.text, plan)
        if governed.is_failure:
            return Result.failure(governed.unwrap_error())
        outcome = governed.unwrap()

        self._task_bridge.telemetry.state(
            "voice.speaking",
            task=outcome.task,
            payload={"verified": outcome.verified},
        )
        spoken = self._session.speak(
            "Task verified." if outcome.verified else "Task could not be verified."
        )
        if spoken.is_failure:
            return Result.failure(spoken.unwrap_error())
        self._task_bridge.telemetry.state(
            "voice.idle",
            task=outcome.task,
            payload={"verified": outcome.verified},
        )
        return Result.success(
            VoiceTurnResult(
                transcript_received=True,
                outcome=outcome,
                spoken=True,
            )
        )

    def barge_in(self) -> bool:
        """Cancel both realtime audio and the active canonical task, idempotently."""

        self._session.barge_in(reason="user barge-in")
        cancelled = self._task_bridge.barge_in()
        self._task_bridge.telemetry.state("voice.cancelled")
        return cancelled

    def close(self) -> None:
        self._task_bridge.cancel("voice/HUD runtime closed")
        self._session.close()
        self._task_bridge.telemetry.state("voice.idle")


@runtime_checkable
class VoiceControlTarget(Protocol):
    """Narrow runtime-owned cancellation surface exposed to the HUD."""

    def barge_in(self) -> bool: ...


@runtime_checkable
class ApprovalDecisionSink(Protocol):
    """Trusted sink for exact canonical human-approval decisions."""

    def submit(self, decision: HumanApprovalDecision) -> bool: ...


@runtime_checkable
class RetryCommandTarget(Protocol):
    """Owner of retry semantics; the HUD never implements retry itself."""

    def retry(self, task_id: str | None) -> bool: ...


class VoiceHudController:
    """Bind typed HUD controls to existing runtime and approval owners.

    The controller never mutates Task, ActionGate, risk, permission, budget, or
    verification state. Confirmation remains bound to an exact pending
    HumanApprovalRequest and is converted to the same canonical decision type
    consumed by governed execution.
    """

    __slots__ = ("_decision_sink", "_pending", "_protocol", "_retry", "_runtime")

    def __init__(
        self,
        *,
        runtime: VoiceControlTarget,
        confirmation_protocol: SpokenConfirmationProtocol,
        decision_sink: ApprovalDecisionSink,
        retry_target: RetryCommandTarget | None = None,
    ) -> None:
        if not isinstance(runtime, VoiceControlTarget):
            raise TypeError("runtime must satisfy VoiceControlTarget")
        if not isinstance(confirmation_protocol, SpokenConfirmationProtocol):
            raise TypeError("confirmation_protocol must be SpokenConfirmationProtocol")
        if not isinstance(decision_sink, ApprovalDecisionSink):
            raise TypeError("decision_sink must satisfy ApprovalDecisionSink")
        if retry_target is not None and not isinstance(retry_target, RetryCommandTarget):
            raise TypeError("retry_target must satisfy RetryCommandTarget or be None")
        self._runtime = runtime
        self._protocol = confirmation_protocol
        self._decision_sink = decision_sink
        self._retry = retry_target
        self._pending: dict[str, PendingVoiceConfirmation] = {}

    def bind_confirmation(
        self,
        task_id: str,
        pending: PendingVoiceConfirmation,
    ) -> None:
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 512:
            raise ValueError("task_id must be bounded non-empty text")
        if not isinstance(pending, PendingVoiceConfirmation):
            raise TypeError("pending must be PendingVoiceConfirmation")
        previous = self._pending.get(task_id)
        if previous is not None:
            self._protocol.invalidate(previous)
        self._pending[task_id] = pending

    def cancel(self, task_id: str | None) -> bool:
        pending = None if task_id is None else self._pending.pop(task_id, None)
        if pending is not None:
            self._protocol.invalidate(pending)
        runtime_cancelled = self._runtime.barge_in()
        return runtime_cancelled or pending is not None

    def confirm(self, task_id: str | None, nonce: str | None) -> bool:
        return self._resolve(task_id, nonce, approve=True)

    def reject(self, task_id: str | None, nonce: str | None) -> bool:
        return self._resolve(task_id, nonce, approve=False)

    def retry(self, task_id: str | None) -> bool:
        if self._retry is None:
            return False
        return self._retry.retry(task_id)

    def dismiss(self, task_id: str | None) -> bool:
        if task_id is None:
            return False
        pending = self._pending.pop(task_id, None)
        if pending is None:
            return False
        self._protocol.invalidate(pending)
        return True

    def _resolve(
        self,
        task_id: str | None,
        nonce: str | None,
        *,
        approve: bool,
    ) -> bool:
        if task_id is None or nonce is None:
            return False
        pending = self._pending.get(task_id)
        if pending is None or pending.nonce != nonce:
            return False
        phrase = f"{'confirm' if approve else 'reject'} {nonce}"
        resolved = self._protocol.resolve(phrase, pending)
        if resolved.is_failure:
            return False
        self._pending.pop(task_id, None)
        return self._decision_sink.submit(resolved.unwrap())
