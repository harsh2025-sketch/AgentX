"""Production composition root for one M11 voice/HUD interaction.

This module composes, rather than reimplements, the realtime voice state machine,
canonical VoiceTaskBridge and AX-452 HUD telemetry. A spoken turn is transcribed
as untrusted data, ingested through the canonical AgentLoop, independently
verified there, and only then rendered as a short spoken outcome. Barge-in
propagates to both the realtime audio owner and the governed task owner.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentx.agent_loop import OrchestrationOutcome
from agentx.cognition.realtime_voice import RealtimeVoiceSession, VoiceActivity
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result
from agentx.voice_runtime import VoiceTaskBridge, VoiceTaskPlan

__all__ = ["VoiceHudRuntime", "VoiceTurnResult"]


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
