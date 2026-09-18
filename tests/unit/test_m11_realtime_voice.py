"""State-machine, VAD, turn-end and barge-in tests for M11 realtime voice."""

from __future__ import annotations

import struct
import threading
from datetime import UTC, datetime

from agentx.cognition.realtime_voice import (
    ComposedRealtimeVoiceProvider,
    EnergyVoiceActivityDetector,
    RealtimeSessionState,
    RealtimeVoiceSession,
    TurnEndDetector,
    VoiceActivity,
    VoiceSessionEvent,
)
from agentx.cognition.speech import (
    SpeechProviderId,
    SttRequest,
    SttTranscript,
    TtsRequest,
    TtsResponse,
)
from agentx.core.audio import (
    AudioAdmissionDecision,
    AudioBufferPolicy,
    AudioEndpoint,
    AudioEndpointId,
    AudioEndpointKind,
    AudioFailureKind,
    AudioFormat,
    AudioFormatSupport,
    AudioFrame,
    AudioProviderId,
    AudioStreamDescriptor,
    AudioStreamState,
    AudioStreamStatus,
    audio_failure,
)
from agentx.core.errors import AgentXError
from agentx.core.execution import CancellationSource, CancellationToken
from agentx.core.ids import AudioStreamId
from agentx.core.result import Result

_PROVIDER = AudioProviderId("test-m11")
_SUPPORT = AudioFormatSupport(
    formats=frozenset({AudioFormat.PCM_S16LE}),
    sample_rates_hz=frozenset({16000}),
    channel_counts=frozenset({1}),
    max_payload_bytes=64 * 1024,
)


def _descriptor(kind: AudioEndpointKind) -> AudioStreamDescriptor:
    endpoint = AudioEndpoint(
        endpoint_id=AudioEndpointId(_PROVIDER, kind.value),
        kind=kind,
        label=kind.value,
        support=_SUPPORT,
    )
    return AudioStreamDescriptor(
        stream_id=AudioStreamId.create(),
        endpoint=endpoint,
        format=AudioFormat.PCM_S16LE,
        sample_rate_hz=16000,
        channel_count=1,
        buffer=AudioBufferPolicy(max_frames=8, max_bytes=64 * 1024),
    )


def _frame(stream_id: AudioStreamId, amplitude: int, sequence: int = 0) -> AudioFrame:
    payload = b"".join(struct.pack("<h", amplitude) for _ in range(160))
    return AudioFrame(
        stream_id=stream_id,
        format=AudioFormat.PCM_S16LE,
        sample_rate_hz=16000,
        channel_count=1,
        sequence=sequence,
        timestamp=datetime.now(UTC),
        payload=payload,
    )


class _Capture:
    def __init__(self) -> None:
        self.descriptor = _descriptor(AudioEndpointKind.SOURCE)
        self._source = CancellationSource()
        self._status = AudioStreamStatus(state=AudioStreamState.OPEN)
        self.frames = [_frame(self.descriptor.stream_id, 6000)]

    @property
    def status(self) -> AudioStreamStatus:
        return self._status

    @property
    def cancellation_token(self) -> CancellationToken:
        return self._source.token

    def read(self) -> Result[AudioFrame, AgentXError]:
        return Result.success(self.frames.pop(0))

    def close(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        self._status = AudioStreamStatus(state=AudioStreamState.CLOSED, reason=reason)
        return Result.success(self._status)

    def cancel(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        self._source.request_cancellation(reason)
        self._status = AudioStreamStatus(state=AudioStreamState.CANCELLED, reason=reason)
        return Result.success(self._status)


class _Playback:
    def __init__(self, *, block: bool = False) -> None:
        self.descriptor = _descriptor(AudioEndpointKind.SINK)
        self._source = CancellationSource()
        self._status = AudioStreamStatus(state=AudioStreamState.OPEN)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = block

    @property
    def status(self) -> AudioStreamStatus:
        return self._status

    @property
    def cancellation_token(self) -> CancellationToken:
        return self._source.token

    def write(self, frame: AudioFrame) -> Result[AudioAdmissionDecision, AgentXError]:
        self.entered.set()
        if self.block:
            assert self.release.wait(2)
        if self._source.token.is_cancelled:
            return Result.failure(
                audio_failure(AudioFailureKind.CANCELLED, message="playback cancelled")
            )
        decision = self.descriptor.buffer.assess(
            buffered_frames=0,
            buffered_bytes=0,
            frame_bytes=frame.byte_count,
        )
        return Result.success(decision)

    def drain(self) -> Result[AudioStreamStatus, AgentXError]:
        return Result.success(self._status)

    def close(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        self._status = AudioStreamStatus(state=AudioStreamState.CLOSED, reason=reason)
        return Result.success(self._status)

    def cancel(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        self._source.request_cancellation(reason)
        self._status = AudioStreamStatus(state=AudioStreamState.CANCELLED, reason=reason)
        self.release.set()
        return Result.success(self._status)


class _Stt:
    provider_id = SpeechProviderId("deterministic-stt")

    def transcribe(self, request: SttRequest) -> Result[SttTranscript, AgentXError]:
        return Result.success(
            SttTranscript(
                session_id=request.session_id,
                text="open the note",
                is_final=True,
                provider_id=self.provider_id,
                confidence=0.9,
            )
        )


class _Tts:
    provider_id = SpeechProviderId("deterministic-tts")

    def synthesize(self, request: TtsRequest) -> Result[TtsResponse, AgentXError]:
        return Result.success(
            TtsResponse(
                session_id=request.session_id,
                provider_id=self.provider_id,
                frames=(_frame(AudioStreamId.create(), 1000),),
            )
        )


def test_vad_and_turn_end_boundaries() -> None:
    vad = EnergyVoiceActivityDetector(speech_threshold=0.05, noise_ceiling=0.005)
    stream = AudioStreamId.create()
    assert vad.classify(_frame(stream, 0))[0] is VoiceActivity.SILENCE
    assert vad.classify(_frame(stream, 200))[0] is VoiceActivity.NOISE
    assert vad.classify(_frame(stream, 6000))[0] is VoiceActivity.SPEECH

    detector = TurnEndDetector(silence_seconds=0.5, max_turn_seconds=5)
    assert not detector.observe(VoiceActivity.SPEECH, elapsed_silence=0, now=1.0)
    assert not detector.observe(VoiceActivity.SILENCE, elapsed_silence=0.49, now=1.49)
    assert detector.observe(VoiceActivity.SILENCE, elapsed_silence=0.5, now=1.5)


def test_session_transcribes_and_speaks_sequential_turn() -> None:
    session = RealtimeVoiceSession(
        capture=_Capture(),
        playback=_Playback(),
        stt=_Stt(),
        tts=_Tts(),
    )
    session.start_turn()
    assert session.capture_frame().unwrap().activity is VoiceActivity.SPEECH
    transcript = session.finish_turn().unwrap()
    assert transcript.text == "open the note"
    assert session.speak("done").is_success
    assert session.state is RealtimeSessionState.LISTENING
    assert session.close().kind.value == "closed"
    assert session.close().kind.value == "closed"


def test_barge_in_cancels_stale_playback_without_orphan() -> None:
    playback = _Playback(block=True)
    session = RealtimeVoiceSession(
        capture=_Capture(),
        playback=playback,
        stt=_Stt(),
        tts=_Tts(),
    )
    session.start_turn()
    session.capture_frame().unwrap()
    session.finish_turn().unwrap()

    result: list[Result[VoiceSessionEvent, AgentXError]] = []

    def speak() -> None:
        result.append(session.speak("long response"))

    worker = threading.Thread(target=speak)
    worker.start()
    assert playback.entered.wait(2)
    session.barge_in()
    worker.join(2)
    assert not worker.is_alive()
    assert result and result[0].is_failure
    assert session.state is RealtimeSessionState.INTERRUPTED


class _AudioProvider:
    provider_id = _PROVIDER

    def __init__(self) -> None:
        self.capture = _Capture()
        self.playback = _Playback()

    def supports(self, config: AudioFormatSupport) -> bool:
        return config.formats <= _SUPPORT.formats

    def endpoints(
        self,
        kind: AudioEndpointKind | None = None,
    ) -> Result[tuple[AudioEndpoint, ...], AgentXError]:
        endpoints = (self.capture.descriptor.endpoint, self.playback.descriptor.endpoint)
        if kind is None:
            return Result.success(endpoints)
        return Result.success(tuple(endpoint for endpoint in endpoints if endpoint.kind is kind))

    def open(
        self,
        descriptor: AudioStreamDescriptor,
        *,
        deadline: object | None = None,
    ) -> Result[object, AgentXError]:
        del deadline
        if descriptor.kind is AudioEndpointKind.SOURCE:
            return Result.success(self.capture)
        return Result.success(self.playback)


def test_composed_realtime_provider_opens_fresh_session_from_canonical_contracts() -> None:
    audio = _AudioProvider()
    provider = ComposedRealtimeVoiceProvider(
        audio=audio,
        capture_descriptor=audio.capture.descriptor,
        playback_descriptor=audio.playback.descriptor,
        stt=_Stt(),
        tts=_Tts(),
    )
    session = provider.open_session().unwrap()
    assert isinstance(session, RealtimeVoiceSession)
    assert session.state is RealtimeSessionState.IDLE


def test_realtime_session_reports_turn_end_after_speech_then_silence() -> None:
    capture = _Capture()
    capture.frames = [
        _frame(capture.descriptor.stream_id, 6000, sequence=0),
        *[
            _frame(capture.descriptor.stream_id, 0, sequence=index)
            for index in range(1, 62)
        ],
    ]
    session = RealtimeVoiceSession(
        capture=capture,
        playback=_Playback(),
        stt=_Stt(),
        tts=_Tts(),
        turn_end=TurnEndDetector(silence_seconds=0.6, max_turn_seconds=5),
    )
    session.start_turn()
    ended = False
    for _ in range(62):
        observation = session.capture_frame().unwrap()
        if observation.turn_ended:
            ended = True
            break
    assert ended
