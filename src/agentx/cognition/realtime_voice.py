"""Realtime voice session, VAD, turn detection, interruption and cancellation for M11."""

from __future__ import annotations

import math
import struct
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Protocol, runtime_checkable

from agentx.cognition.speech import (
    SpeechSessionId,
    SpeechToTextProvider,
    SttRequest,
    SttTranscript,
    TextToSpeechProvider,
    TtsRequest,
)
from agentx.core.audio import (
    AudioCaptureStream,
    AudioFormat,
    AudioFrame,
    AudioPlaybackStream,
    AudioProvider,
    AudioStreamDescriptor,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import CancellationSource
from agentx.core.result import Result

__all__ = [
    "ComposedRealtimeVoiceProvider",
    "EnergyVoiceActivityDetector",
    "RealtimeSessionState",
    "RealtimeVoiceProvider",
    "RealtimeVoiceSession",
    "TurnEndDetector",
    "TurnObservation",
    "VoiceActivity",
    "VoiceSessionEvent",
    "VoiceSessionEventKind",
    "VoiceSessionProvider",
]

_MAX_TURN_FRAMES: Final[int] = 512
_MAX_TURN_BYTES: Final[int] = 8 * 1024 * 1024


class RealtimeSessionState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"
    CLOSED = "closed"


_ALLOWED: Final[Mapping[RealtimeSessionState, frozenset[RealtimeSessionState]]] = MappingProxyType(
    {
        RealtimeSessionState.IDLE: frozenset(
            {RealtimeSessionState.LISTENING, RealtimeSessionState.CLOSED}
        ),
        RealtimeSessionState.LISTENING: frozenset(
            {
                RealtimeSessionState.PROCESSING,
                RealtimeSessionState.CANCELLING,
                RealtimeSessionState.FAILED,
                RealtimeSessionState.CLOSED,
            }
        ),
        RealtimeSessionState.PROCESSING: frozenset(
            {
                RealtimeSessionState.SPEAKING,
                RealtimeSessionState.LISTENING,
                RealtimeSessionState.CANCELLING,
                RealtimeSessionState.FAILED,
                RealtimeSessionState.CLOSED,
            }
        ),
        RealtimeSessionState.SPEAKING: frozenset(
            {
                RealtimeSessionState.INTERRUPTED,
                RealtimeSessionState.LISTENING,
                RealtimeSessionState.CANCELLING,
                RealtimeSessionState.FAILED,
                RealtimeSessionState.CLOSED,
            }
        ),
        RealtimeSessionState.INTERRUPTED: frozenset(
            {RealtimeSessionState.LISTENING, RealtimeSessionState.CLOSED}
        ),
        RealtimeSessionState.CANCELLING: frozenset(
            {RealtimeSessionState.CANCELLED, RealtimeSessionState.FAILED}
        ),
        RealtimeSessionState.CANCELLED: frozenset(
            {RealtimeSessionState.LISTENING, RealtimeSessionState.CLOSED}
        ),
        RealtimeSessionState.FAILED: frozenset(
            {RealtimeSessionState.LISTENING, RealtimeSessionState.CLOSED}
        ),
        RealtimeSessionState.CLOSED: frozenset(),
    }
)


def _is_cancelled(source: CancellationSource) -> bool:
    """Observe current cancellation without stale flow narrowing."""
    return source.token.is_cancelled


def _voice_error(code: str, message: str, category: ErrorCategory) -> AgentXError:
    return AgentXError(
        code=f"voice.{code}",
        message=message,
        category=category,
        retryability=(
            Retryability.RETRYABLE
            if category in {ErrorCategory.DEPENDENCY, ErrorCategory.TIMEOUT}
            else Retryability.NON_RETRYABLE
        ),
    )


class VoiceActivity(StrEnum):
    SILENCE = "silence"
    SPEECH = "speech"
    NOISE = "noise"


@dataclass(frozen=True, slots=True)
class TurnObservation:
    activity: VoiceActivity
    rms: float
    turn_ended: bool


class EnergyVoiceActivityDetector:
    """Deterministic PCM-S16LE energy detector; never an authorization signal."""

    __slots__ = ("_noise_ceiling", "_speech_threshold")

    def __init__(self, *, speech_threshold: float = 0.025, noise_ceiling: float = 0.005) -> None:
        if not math.isfinite(speech_threshold) or not 0 < speech_threshold <= 1:
            raise ValueError("speech_threshold must be finite in (0, 1]")
        if not math.isfinite(noise_ceiling) or not 0 <= noise_ceiling < speech_threshold:
            raise ValueError("noise_ceiling must be finite and below speech_threshold")
        self._speech_threshold = speech_threshold
        self._noise_ceiling = noise_ceiling

    def classify(self, frame: AudioFrame) -> tuple[VoiceActivity, float]:
        if not isinstance(frame, AudioFrame):
            raise TypeError("frame must be AudioFrame")
        if frame.format is not AudioFormat.PCM_S16LE:
            raise ValueError("energy VAD requires PCM_S16LE")
        samples = tuple(value[0] for value in struct.iter_unpack("<h", frame.payload))
        if not samples:
            return VoiceActivity.SILENCE, 0.0
        mean_square = sum(float(sample) * sample for sample in samples) / len(samples)
        rms = math.sqrt(mean_square) / 32768.0
        if rms >= self._speech_threshold:
            return VoiceActivity.SPEECH, rms
        if rms <= self._noise_ceiling:
            return VoiceActivity.SILENCE, rms
        return VoiceActivity.NOISE, rms


class TurnEndDetector:
    """Bounded deterministic silence-after-speech turn detector."""

    __slots__ = ("_max_turn_seconds", "_silence_seconds", "_speech_seen", "_turn_started")

    def __init__(self, *, silence_seconds: float = 0.6, max_turn_seconds: float = 30.0) -> None:
        if not math.isfinite(silence_seconds) or not 0 < silence_seconds <= 5:
            raise ValueError("silence_seconds must be finite in (0, 5]")
        if not math.isfinite(max_turn_seconds) or not silence_seconds < max_turn_seconds <= 120:
            raise ValueError("max_turn_seconds must be finite and larger than silence_seconds")
        self._silence_seconds = silence_seconds
        self._max_turn_seconds = max_turn_seconds
        self._speech_seen = False
        self._turn_started: float | None = None

    def reset(self) -> None:
        self._speech_seen = False
        self._turn_started = None

    def observe(
        self,
        activity: VoiceActivity,
        *,
        elapsed_silence: float,
        now: float | None = None,
    ) -> bool:
        if not isinstance(activity, VoiceActivity):
            raise TypeError("activity must be VoiceActivity")
        if not math.isfinite(elapsed_silence) or elapsed_silence < 0:
            raise ValueError("elapsed_silence must be finite and non-negative")
        current = time.monotonic() if now is None else now
        if not math.isfinite(current):
            raise ValueError("now must be finite")
        if self._turn_started is None:
            self._turn_started = current
        if current - self._turn_started >= self._max_turn_seconds:
            return True
        if activity is VoiceActivity.SPEECH:
            self._speech_seen = True
            return False
        return self._speech_seen and elapsed_silence >= self._silence_seconds


class VoiceSessionEventKind(StrEnum):
    LISTENING = "listening"
    PARTIAL_TRANSCRIPT = "partial_transcript"
    FINAL_TRANSCRIPT = "final_transcript"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True, kw_only=True)
class VoiceSessionEvent:
    session_id: SpeechSessionId
    sequence: int
    kind: VoiceSessionEventKind
    transcript: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, SpeechSessionId):
            raise TypeError("session_id must be SpeechSessionId")
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("sequence must be a non-negative int")
        if not isinstance(self.kind, VoiceSessionEventKind):
            raise TypeError("kind must be VoiceSessionEventKind")
        if self.transcript is not None and (
            not isinstance(self.transcript, str) or not self.transcript.strip()
        ):
            raise ValueError("transcript must be non-empty text or None")


@runtime_checkable
class RealtimeVoiceProvider(Protocol):
    """Provider-neutral factory for one bounded realtime voice session."""

    def open_session(self) -> Result[RealtimeVoiceSession, AgentXError]: ...


@runtime_checkable
class VoiceSessionProvider(Protocol):
    def transcribe(self, request: SttRequest) -> Result[SttTranscript, AgentXError]: ...

    def synthesize(self, request: TtsRequest) -> Result[object, AgentXError]: ...


class RealtimeVoiceSession:
    """Synchronous bounded realtime state machine over injected production contracts.

    Every turn receives a fresh cancellation source. Barge-in cancels the active
    source and playback, discards buffered turn audio, and permits a fresh turn;
    late results are rejected by the turn generation check.
    """

    __slots__ = (
        "_capture",
        "_event_sequence",
        "_frames",
        "_generation",
        "_playback",
        "_session_id",
        "_source",
        "_state",
        "_stt",
        "_tts",
        "_turn_bytes",
        "_vad",
    )

    def __init__(
        self,
        *,
        capture: AudioCaptureStream,
        playback: AudioPlaybackStream,
        stt: SpeechToTextProvider,
        tts: TextToSpeechProvider,
        vad: EnergyVoiceActivityDetector | None = None,
    ) -> None:
        if not isinstance(capture, AudioCaptureStream):
            raise TypeError("capture must satisfy AudioCaptureStream")
        if not isinstance(playback, AudioPlaybackStream):
            raise TypeError("playback must satisfy AudioPlaybackStream")
        if not isinstance(stt, SpeechToTextProvider):
            raise TypeError("stt must satisfy SpeechToTextProvider")
        if not isinstance(tts, TextToSpeechProvider):
            raise TypeError("tts must satisfy TextToSpeechProvider")
        self._capture = capture
        self._playback = playback
        self._stt = stt
        self._tts = tts
        self._vad = EnergyVoiceActivityDetector() if vad is None else vad
        self._session_id = SpeechSessionId.create()
        self._state = RealtimeSessionState.IDLE
        self._source = CancellationSource()
        self._frames: list[AudioFrame] = []
        self._turn_bytes = 0
        self._generation = 0
        self._event_sequence = 0

    @property
    def session_id(self) -> SpeechSessionId:
        return self._session_id

    @property
    def state(self) -> RealtimeSessionState:
        return self._state

    @property
    def cancellation_source(self) -> CancellationSource:
        return self._source

    def _transition(self, target: RealtimeSessionState) -> None:
        if target not in _ALLOWED[self._state]:
            raise RuntimeError(f"illegal voice transition {self._state.value} -> {target.value}")
        self._state = target

    def start_turn(self) -> VoiceSessionEvent:
        if self._state in {
            RealtimeSessionState.INTERRUPTED,
            RealtimeSessionState.CANCELLED,
            RealtimeSessionState.FAILED,
        }:
            self._transition(RealtimeSessionState.LISTENING)
        else:
            self._transition(RealtimeSessionState.LISTENING)
        self._source = CancellationSource()
        self._frames.clear()
        self._turn_bytes = 0
        self._generation += 1
        return self._event(VoiceSessionEventKind.LISTENING)

    def capture_frame(self) -> Result[TurnObservation, AgentXError]:
        if self._state is not RealtimeSessionState.LISTENING:
            return Result.failure(
                _voice_error(
                    "not_listening",
                    "voice session is not listening",
                    ErrorCategory.PRECONDITION,
                )
            )
        if _is_cancelled(self._source):
            return Result.failure(
                _voice_error("cancelled", "voice turn cancelled", ErrorCategory.CANCELLED)
            )
        result = self._capture.read()
        if result.is_failure:
            return Result.failure(result.unwrap_error())
        frame = result.unwrap()
        if (
            len(self._frames) >= _MAX_TURN_FRAMES
            or self._turn_bytes + frame.byte_count > _MAX_TURN_BYTES
        ):
            return Result.failure(
                _voice_error(
                    "turn_resource_limit",
                    "voice turn audio bound reached",
                    ErrorCategory.RESOURCE,
                )
            )
        self._frames.append(frame)
        self._turn_bytes += frame.byte_count
        activity, rms = self._vad.classify(frame)
        return Result.success(TurnObservation(activity=activity, rms=rms, turn_ended=False))

    def finish_turn(self) -> Result[SttTranscript, AgentXError]:
        if self._state is not RealtimeSessionState.LISTENING or not self._frames:
            return Result.failure(
                _voice_error(
                    "empty_turn",
                    "voice turn has no captured audio",
                    ErrorCategory.PRECONDITION,
                )
            )
        generation = self._generation
        self._transition(RealtimeSessionState.PROCESSING)
        result = self._stt.transcribe(
            SttRequest(
                session_id=self._session_id,
                frames=tuple(self._frames),
                cancellation_token=self._source.token,
            )
        )
        self._frames.clear()
        self._turn_bytes = 0
        if generation != self._generation or _is_cancelled(self._source):
            return Result.failure(
                _voice_error(
                    "stale_result",
                    "late STT result rejected after cancellation",
                    ErrorCategory.CANCELLED,
                )
            )
        if result.is_failure:
            self._transition(RealtimeSessionState.FAILED)
            return Result.failure(result.unwrap_error())
        transcript = result.unwrap()
        if not transcript.is_final:
            return Result.failure(
                _voice_error(
                    "partial_only",
                    "turn ended without a final transcript",
                    ErrorCategory.DEPENDENCY,
                )
            )
        return Result.success(transcript)

    def speak(self, text: str) -> Result[VoiceSessionEvent, AgentXError]:
        if self._state is not RealtimeSessionState.PROCESSING:
            return Result.failure(
                _voice_error(
                    "not_processing",
                    "voice session is not ready to speak",
                    ErrorCategory.PRECONDITION,
                )
            )
        generation = self._generation
        response = self._tts.synthesize(
            TtsRequest(
                session_id=self._session_id,
                text=text,
                cancellation_token=self._source.token,
            )
        )
        if generation != self._generation or _is_cancelled(self._source):
            return Result.failure(
                _voice_error(
                    "stale_result",
                    "late TTS result rejected after cancellation",
                    ErrorCategory.CANCELLED,
                )
            )
        if response.is_failure:
            self._transition(RealtimeSessionState.FAILED)
            return Result.failure(response.unwrap_error())
        self._transition(RealtimeSessionState.SPEAKING)
        for frame in response.unwrap().frames:
            written = self._playback.write(frame)
            if written.is_failure:
                if not _is_cancelled(self._source):
                    self._transition(RealtimeSessionState.FAILED)
                return Result.failure(written.unwrap_error())
        self._transition(RealtimeSessionState.LISTENING)
        return Result.success(self._event(VoiceSessionEventKind.SPEAKING))

    def barge_in(self, *, reason: str = "user barge-in") -> VoiceSessionEvent:
        if self._state is RealtimeSessionState.CLOSED:
            return self._event(VoiceSessionEventKind.CLOSED)
        if self._state is RealtimeSessionState.SPEAKING:
            self._source.request_cancellation(reason)
            self._playback.cancel(reason=reason)
            self._transition(RealtimeSessionState.INTERRUPTED)
        elif self._state in {RealtimeSessionState.LISTENING, RealtimeSessionState.PROCESSING}:
            self._transition(RealtimeSessionState.CANCELLING)
            self._source.request_cancellation(reason)
            self._capture.cancel(reason=reason)
            self._playback.cancel(reason=reason)
            self._transition(RealtimeSessionState.CANCELLED)
        self._generation += 1
        self._frames.clear()
        self._turn_bytes = 0
        return self._event(VoiceSessionEventKind.INTERRUPTED)

    def close(self) -> VoiceSessionEvent:
        if self._state is RealtimeSessionState.CLOSED:
            return self._event(VoiceSessionEventKind.CLOSED)
        self._source.request_cancellation("voice session closed")
        self._capture.cancel(reason="voice session closed")
        self._playback.cancel(reason="voice session closed")
        if (
            self._state not in {RealtimeSessionState.CANCELLED, RealtimeSessionState.CLOSED}
            and RealtimeSessionState.CANCELLING in _ALLOWED[self._state]
        ):
            self._transition(RealtimeSessionState.CANCELLING)
            self._transition(RealtimeSessionState.CANCELLED)
        self._transition(RealtimeSessionState.CLOSED)
        self._generation += 1
        self._frames.clear()
        self._turn_bytes = 0
        return self._event(VoiceSessionEventKind.CLOSED)

    def _event(
        self,
        kind: VoiceSessionEventKind,
        transcript: str | None = None,
    ) -> VoiceSessionEvent:
        event = VoiceSessionEvent(
            session_id=self._session_id,
            sequence=self._event_sequence,
            kind=kind,
            transcript=transcript,
        )
        self._event_sequence += 1
        return event


class ComposedRealtimeVoiceProvider:
    """Concrete realtime provider composed from canonical audio/STT/TTS providers.

    It performs no action execution. Provider callbacks and transcripts remain data;
    reconnect is caller-driven and bounded by `max_open_attempts`.
    """

    __slots__ = (
        "_audio",
        "_capture_descriptor",
        "_max_open_attempts",
        "_playback_descriptor",
        "_stt",
        "_tts",
    )

    def __init__(
        self,
        *,
        audio: AudioProvider,
        capture_descriptor: AudioStreamDescriptor,
        playback_descriptor: AudioStreamDescriptor,
        stt: SpeechToTextProvider,
        tts: TextToSpeechProvider,
        max_open_attempts: int = 2,
    ) -> None:
        if not isinstance(audio, AudioProvider):
            raise TypeError("audio must satisfy AudioProvider")
        if not isinstance(capture_descriptor, AudioStreamDescriptor):
            raise TypeError("capture_descriptor must be AudioStreamDescriptor")
        if not isinstance(playback_descriptor, AudioStreamDescriptor):
            raise TypeError("playback_descriptor must be AudioStreamDescriptor")
        if not isinstance(stt, SpeechToTextProvider):
            raise TypeError("stt must satisfy SpeechToTextProvider")
        if not isinstance(tts, TextToSpeechProvider):
            raise TypeError("tts must satisfy TextToSpeechProvider")
        if type(max_open_attempts) is not int or not 1 <= max_open_attempts <= 3:
            raise ValueError("max_open_attempts must be in [1, 3]")
        self._audio = audio
        self._capture_descriptor = capture_descriptor
        self._playback_descriptor = playback_descriptor
        self._stt = stt
        self._tts = tts
        self._max_open_attempts = max_open_attempts

    def open_session(self) -> Result[RealtimeVoiceSession, AgentXError]:
        last_error: AgentXError | None = None
        for _attempt in range(self._max_open_attempts):
            capture_result = self._audio.open(self._capture_descriptor)
            if capture_result.is_failure:
                last_error = capture_result.unwrap_error()
                continue
            capture = capture_result.unwrap()
            if not isinstance(capture, AudioCaptureStream):
                return Result.failure(
                    _voice_error(
                        "capture_contract",
                        "audio provider returned a non-capture stream",
                        ErrorCategory.DEPENDENCY,
                    )
                )
            playback_result = self._audio.open(self._playback_descriptor)
            if playback_result.is_failure:
                capture.cancel(reason="playback open failed")
                last_error = playback_result.unwrap_error()
                continue
            playback = playback_result.unwrap()
            if not isinstance(playback, AudioPlaybackStream):
                capture.cancel(reason="invalid playback stream")
                return Result.failure(
                    _voice_error(
                        "playback_contract",
                        "audio provider returned a non-playback stream",
                        ErrorCategory.DEPENDENCY,
                    )
                )
            return Result.success(
                RealtimeVoiceSession(
                    capture=capture,
                    playback=playback,
                    stt=self._stt,
                    tts=self._tts,
                )
            )
        assert last_error is not None
        return Result.failure(last_error)
