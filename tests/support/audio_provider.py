"""In-process audio provider fixtures for A7.01 (test support only).

These fakes exist so the audio contracts can be exercised the way a future real
adapter will be exercised: reached through :class:`agentx.core.audio.AudioProvider`,
read and written through the stream protocols, and audited by the reusable
conformance check in ``tests.support.audio_conformance``. They are not shipped
runtime code, not a reference implementation, and not a vendor stub: no device,
socket, thread, asyncio loop, model call, transcription, or synthesis is involved.

Deliberate defects
------------------

:class:`DefectSpec` switches on the misbehaviours a real adapter could plausibly
ship — skipped or replayed sequence numbers, regressed timestamps, a frame whose
layout contradicts the stream descriptor, a sink that silently overruns its own
bound, a provider that claims cancellation it never performed, and a stream that
keeps reporting ``OPEN`` after it terminated. ``audio_conformance_violations`` must
report each one; a conformance harness that passes a lying provider is worthless.

A provider cannot forge every defect. An oversized frame payload is rejected by
:class:`~agentx.core.audio.AudioFrame` itself, which is why the scaffold checks the
endpoint's advertised byte bound instead of trusting a "too large" flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from agentx.core.audio import (
    AudioAdmissionDecision,
    AudioAdmissionOutcome,
    AudioBufferPolicy,
    AudioCaptureStream,
    AudioEndpoint,
    AudioEndpointId,
    AudioEndpointKind,
    AudioFailureKind,
    AudioFormat,
    AudioFormatSupport,
    AudioFrame,
    AudioOverflowPolicy,
    AudioPlaybackStream,
    AudioProviderId,
    AudioStreamDescriptor,
    AudioStreamState,
    AudioStreamStatus,
    audio_failure,
    can_transition_audio_state,
)
from agentx.core.errors import AgentXError
from agentx.core.execution import CancellationSource, CancellationToken, Deadline
from agentx.core.ids import AudioStreamId
from agentx.core.result import Result

FAKE_PROVIDER_ID: Final[AudioProviderId] = AudioProviderId("test.audio.fake")
FAKE_SOURCE_ENDPOINT: Final[str] = "source-default"
FAKE_SINK_ENDPOINT: Final[str] = "sink-default"

#: Samples per channel in one fixture frame (64 bytes at 48 kHz mono s16le).
SAMPLE_FRAMES_PER_CHUNK: Final[int] = 16

#: Frame interval the fixtures space their timestamps by.
FRAME_INTERVAL: Final[timedelta] = timedelta(milliseconds=20)


@dataclass(frozen=True, slots=True)
class DefectSpec:
    """Named misbehaviours a conformance harness must be able to detect."""

    skip_frames: bool = False
    replay_frames: bool = False
    regress_timestamps: bool = False
    exceeds_endpoint_bound: bool = False
    wrong_channel_count: bool = False
    wrong_sample_rate: bool = False
    foreign_stream: bool = False
    ignores_cancellation: bool = False
    unbounded_sink: bool = False
    frozen_status: bool = False
    starts_closed: bool = False

    def enabled(self) -> tuple[str, ...]:
        """Return the enabled defect names, so a failure can be read as ``skip_frames``."""

        names: list[str] = []
        for name in self.__dataclass_fields__:
            if getattr(self, name) is True:
                names.append(name)
        return tuple(names)


def supported_formats() -> AudioFormatSupport:
    """What the fake provider honestly advertises."""

    return AudioFormatSupport(
        formats=frozenset({AudioFormat.PCM_S16LE, AudioFormat.PCM_F32LE, AudioFormat.OPUS}),
        sample_rates_hz=frozenset({16_000, 48_000}),
        channel_counts=frozenset({1, 2}),
        max_payload_bytes=64 * 1024,
    )


def narrow_formats() -> AudioFormatSupport:
    """An advertisement small enough that most requests must be refused."""

    return AudioFormatSupport(
        formats=frozenset({AudioFormat.PCM_S16LE}),
        sample_rates_hz=frozenset({16_000}),
        channel_counts=frozenset({1}),
        max_payload_bytes=4 * 1024,
    )


def make_endpoint(
    *,
    value: str = FAKE_SOURCE_ENDPOINT,
    kind: AudioEndpointKind = AudioEndpointKind.SOURCE,
    provider_id: AudioProviderId = FAKE_PROVIDER_ID,
    support: AudioFormatSupport | None = None,
    label: str = "Fake audio device",
    is_default: bool = False,
) -> AudioEndpoint:
    """Build one endpoint description."""

    return AudioEndpoint(
        endpoint_id=AudioEndpointId(provider_id=provider_id, value=value),
        kind=kind,
        label=label,
        support=supported_formats() if support is None else support,
        is_default=is_default,
    )


def make_sink_endpoint(
    *,
    provider_id: AudioProviderId = FAKE_PROVIDER_ID,
    support: AudioFormatSupport | None = None,
    label: str = "Fake audio output",
    is_default: bool = False,
) -> AudioEndpoint:
    """Build a sink endpoint, defaulting to the fake provider."""

    return make_endpoint(
        value=FAKE_SINK_ENDPOINT,
        kind=AudioEndpointKind.SINK,
        provider_id=provider_id,
        support=support,
        label=label,
        is_default=is_default,
    )


def make_buffer_policy(
    *,
    max_frames: int = 4,
    max_bytes: int = 4 * 1024,
    overflow: AudioOverflowPolicy = AudioOverflowPolicy.REJECT,
) -> AudioBufferPolicy:
    """A small bounded policy the fixtures can overrun quickly."""

    return AudioBufferPolicy(max_frames=max_frames, max_bytes=max_bytes, overflow=overflow)


def make_descriptor(
    *,
    stream_id: AudioStreamId | None = None,
    endpoint: AudioEndpoint | None = None,
    fmt: AudioFormat = AudioFormat.PCM_S16LE,
    sample_rate_hz: int = 48_000,
    channel_count: int = 1,
    buffer: AudioBufferPolicy | None = None,
    sequence_origin: int = 0,
    latency_target_ms: float | None = 20.0,
) -> AudioStreamDescriptor:
    """Build a descriptor that is consistent with its endpoint by construction."""

    return AudioStreamDescriptor(
        stream_id=AudioStreamId.create() if stream_id is None else stream_id,
        endpoint=make_endpoint() if endpoint is None else endpoint,
        format=fmt,
        sample_rate_hz=sample_rate_hz,
        channel_count=channel_count,
        buffer=make_buffer_policy() if buffer is None else buffer,
        sequence_origin=sequence_origin,
        latency_target_ms=latency_target_ms,
    )


def make_sink_descriptor(
    *,
    stream_id: AudioStreamId | None = None,
    fmt: AudioFormat = AudioFormat.PCM_S16LE,
    sample_rate_hz: int = 48_000,
    channel_count: int = 1,
    buffer: AudioBufferPolicy | None = None,
    latency_target_ms: float | None = 20.0,
) -> AudioStreamDescriptor:
    """Build a playback descriptor bound to the fake sink endpoint."""

    return make_descriptor(
        stream_id=stream_id,
        endpoint=make_sink_endpoint(),
        fmt=fmt,
        sample_rate_hz=sample_rate_hz,
        channel_count=channel_count,
        buffer=buffer,
        latency_target_ms=latency_target_ms,
    )


def payload_for(
    sequence: int,
    *,
    fmt: AudioFormat = AudioFormat.PCM_S16LE,
    channel_count: int = 1,
    size: int | None = None,
) -> bytes:
    """Deterministic filler bytes shaped for one frame of ``fmt``.

    The bytes are arbitrary: A7.01 never interprets audio and neither may the
    fixtures. Only the length matters to the contract checks.
    """

    if size is None:
        width = 4 if fmt is AudioFormat.PCM_F32LE else 2
        size = SAMPLE_FRAMES_PER_CHUNK * width * channel_count
    return bytes((sequence * 7 + index) % 253 for index in range(size))


def make_frame(
    *,
    sequence: int,
    stream_id: AudioStreamId,
    descriptor: AudioStreamDescriptor | None = None,
    timestamp: datetime | None = None,
    size: int | None = None,
    fmt: AudioFormat | None = None,
    sample_rate_hz: int | None = None,
    channel_count: int | None = None,
) -> AudioFrame:
    """Build one valid frame, defaulting to the shape of ``descriptor``."""

    resolved = make_descriptor() if descriptor is None else descriptor
    frame_format = resolved.format if fmt is None else fmt
    channels = resolved.channel_count if channel_count is None else channel_count
    rate = resolved.sample_rate_hz if sample_rate_hz is None else sample_rate_hz
    return AudioFrame(
        stream_id=stream_id,
        format=frame_format,
        sample_rate_hz=rate,
        channel_count=channels,
        sequence=sequence,
        timestamp=timestamp
        if timestamp is not None
        else datetime(2026, 1, 1, tzinfo=UTC) + FRAME_INTERVAL * sequence,
        payload=payload_for(sequence, fmt=frame_format, channel_count=channels, size=size),
    )


def unavailable_failure(state: AudioStreamState) -> AgentXError:
    """The canonical failure for a state that transfers no frames."""

    kinds: dict[AudioStreamState, AudioFailureKind] = {
        AudioStreamState.NEW: AudioFailureKind.NOT_OPEN,
        AudioStreamState.DRAINING: AudioFailureKind.NOT_OPEN,
        AudioStreamState.CLOSED: AudioFailureKind.NOT_OPEN,
        AudioStreamState.CANCELLED: AudioFailureKind.CANCELLED,
        AudioStreamState.FAILED: AudioFailureKind.INTERNAL,
    }
    return audio_failure(
        kinds[state],
        message=f"stream state {state.value} transfers no frames",
    )


class _StreamBase:
    """Lifecycle and cancellation mechanics shared by the two fake streams."""

    def __init__(
        self,
        descriptor: AudioStreamDescriptor,
        *,
        initial_state: AudioStreamState = AudioStreamState.OPEN,
        defects: DefectSpec | None = None,
    ) -> None:
        self._descriptor = descriptor
        self._state = initial_state
        self._reason: str | None = None
        self._source = CancellationSource()
        self._defects = DefectSpec() if defects is None else defects
        self._transitions: list[tuple[AudioStreamState, AudioStreamState]] = []

    @property
    def descriptor(self) -> AudioStreamDescriptor:
        return self._descriptor

    @property
    def status(self) -> AudioStreamStatus:
        if self._defects.frozen_status:
            # The lie: keep claiming OPEN after the stream terminated.
            return AudioStreamStatus(state=AudioStreamState.OPEN)
        return AudioStreamStatus(state=self._state, reason=self._reason)

    @property
    def cancellation_token(self) -> CancellationToken:
        return self._source.token

    @property
    def transitions(self) -> tuple[tuple[AudioStreamState, AudioStreamState], ...]:
        """Every lifecycle move this fake actually performed, in order."""

        return tuple(self._transitions)

    def _force_state(self, state: AudioStreamState, reason: str | None = None) -> None:
        """Test-only defect injection: move without obeying the transition matrix."""

        self._state = state
        self._reason = reason

    def _move_to(
        self, target: AudioStreamState, reason: str | None
    ) -> Result[AudioStreamStatus, AgentXError]:
        if not can_transition_audio_state(self._state, target):
            return Result[AudioStreamStatus, AgentXError].failure(
                audio_failure(
                    AudioFailureKind.INVALID_STATE_TRANSITION,
                    message=f"cannot move from {self._state.value} to {target.value}",
                    stream_id=self._descriptor.stream_id,
                )
            )
        self._transitions.append((self._state, target))
        self._state = target
        self._reason = reason
        if target in (AudioStreamState.CANCELLED, AudioStreamState.FAILED):
            self._source.request_cancellation(reason)
        return Result[AudioStreamStatus, AgentXError].success(
            AudioStreamStatus(state=target, reason=reason)
        )

    def transfers_frames(self) -> bool:
        """Whether this fake currently moves audio (defect-aware)."""

        if self._state is AudioStreamState.OPEN:
            return True
        return self._defects.ignores_cancellation and self._state in (
            AudioStreamState.CLOSED,
            AudioStreamState.CANCELLED,
        )

    def close(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        if self._state is AudioStreamState.OPEN:
            # A real sink drains before closing; the fake records the legal path.
            self._move_to(AudioStreamState.DRAINING, None)
        return self._move_to(AudioStreamState.CLOSED, reason)

    def cancel(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        if self._defects.ignores_cancellation:
            # The lie: report success, change nothing, keep transferring frames.
            return Result[AudioStreamStatus, AgentXError].success(
                AudioStreamStatus(state=AudioStreamState.CANCELLED, reason=reason)
            )
        return self._move_to(AudioStreamState.CANCELLED, reason)


class FakeCaptureStream(_StreamBase):
    """A capture stream that emits a fixed, well-formed frame sequence."""

    def __init__(
        self,
        descriptor: AudioStreamDescriptor,
        *,
        frame_count: int = 5,
        defects: DefectSpec | None = None,
    ) -> None:
        super().__init__(descriptor, defects=defects)
        self._frame_count = frame_count
        self._produced = 0
        self._clock = datetime(2026, 1, 1, tzinfo=UTC)

    def read(self) -> Result[AudioFrame, AgentXError]:
        if not self.transfers_frames():
            return Result[AudioFrame, AgentXError].failure(unavailable_failure(self._state))
        if self._produced >= self._frame_count:
            return Result[AudioFrame, AgentXError].failure(
                audio_failure(
                    AudioFailureKind.NOT_OPEN,
                    message="no further frames are available",
                    stream_id=self._descriptor.stream_id,
                )
            )
        return Result[AudioFrame, AgentXError].success(self._next_frame())

    def _next_frame(self) -> AudioFrame:
        defects = self._defects
        index = self._produced
        self._produced += 1
        sequence = self._descriptor.sequence_origin + index
        if defects.skip_frames:
            sequence += 1
        elif defects.replay_frames and index > 0:
            # Replay an earlier number without falling off the bottom of the range.
            sequence = self._descriptor.sequence_origin + max(0, index - 2)
        if defects.regress_timestamps and index > 0:
            self._clock -= FRAME_INTERVAL
        else:
            self._clock += FRAME_INTERVAL
        size: int | None = None
        if defects.exceeds_endpoint_bound:
            size = self._descriptor.endpoint.support.max_payload_bytes + 32
        channel_count = self._descriptor.channel_count
        if defects.wrong_channel_count:
            channel_count = 2 if channel_count == 1 else 1
        sample_rate = self._descriptor.sample_rate_hz
        if defects.wrong_sample_rate:
            sample_rate = 16_000 if sample_rate == 48_000 else 48_000
        stream_id = self._descriptor.stream_id
        if defects.foreign_stream:
            stream_id = AudioStreamId.create()
        return make_frame(
            sequence=sequence,
            stream_id=stream_id,
            descriptor=self._descriptor,
            timestamp=self._clock,
            size=size,
            channel_count=channel_count,
            sample_rate_hz=sample_rate,
        )


class FakePlaybackStream(_StreamBase):
    """A sink whose buffering obeys its :class:`agentx.core.audio.AudioBufferPolicy`."""

    def __init__(
        self,
        descriptor: AudioStreamDescriptor,
        *,
        defects: DefectSpec | None = None,
    ) -> None:
        super().__init__(descriptor, defects=defects)
        self.accepted: list[AudioFrame] = []
        self.refused: list[AudioFrame] = []

    @property
    def buffered_frames(self) -> int:
        return len(self.accepted)

    @property
    def buffered_bytes(self) -> int:
        return sum(frame.byte_count for frame in self.accepted)

    def write(self, frame: AudioFrame) -> Result[AudioAdmissionDecision, AgentXError]:
        if not self.transfers_frames():
            return Result[AudioAdmissionDecision, AgentXError].failure(
                unavailable_failure(self._state)
            )
        if frame.stream_id != self._descriptor.stream_id:
            return Result[AudioAdmissionDecision, AgentXError].failure(
                audio_failure(
                    AudioFailureKind.UNSUPPORTED_CONFIGURATION,
                    message="frame belongs to a different stream",
                    stream_id=self._descriptor.stream_id,
                )
            )
        policy = self._descriptor.buffer
        buffered_frames = self.buffered_frames
        buffered_bytes = self.buffered_bytes
        if self._defects.unbounded_sink:
            # The defect a real-time system must never tolerate: silent overflow.
            self.accepted.append(frame)
            outcome = AudioAdmissionOutcome.ACCEPTED
        else:
            outcome = policy.assess(
                buffered_frames=buffered_frames,
                buffered_bytes=buffered_bytes,
                frame_bytes=frame.byte_count,
            ).outcome
        if outcome in (AudioAdmissionOutcome.ACCEPTED, AudioAdmissionOutcome.EVICTED):
            self.accepted.append(frame)
        else:
            self.refused.append(frame)
        decision = AudioAdmissionDecision(
            outcome=outcome,
            policy=policy,
            buffered_frames=buffered_frames,
            buffered_bytes=buffered_bytes,
            frame_bytes=frame.byte_count,
        )
        return Result[AudioAdmissionDecision, AgentXError].success(decision)

    def drain(self) -> Result[AudioStreamStatus, AgentXError]:
        return self._move_to(AudioStreamState.DRAINING, None)


class FakeAudioProvider:
    """A conforming, in-memory ``agentx.core.audio.AudioProvider``."""

    def __init__(
        self,
        *,
        provider_id: AudioProviderId = FAKE_PROVIDER_ID,
        defects: DefectSpec | None = None,
        frame_count: int = 5,
        support: AudioFormatSupport | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._defects = DefectSpec() if defects is None else defects
        self._frame_count = frame_count
        advertised = supported_formats() if support is None else support
        self._endpoints = (
            make_endpoint(
                value=FAKE_SOURCE_ENDPOINT,
                provider_id=provider_id,
                support=advertised,
                is_default=True,
            ),
            make_sink_endpoint(provider_id=provider_id, support=advertised, is_default=True),
        )
        self.opened: list[AudioStreamDescriptor] = []

    @property
    def provider_id(self) -> AudioProviderId:
        return self._provider_id

    def supports(self, config: AudioFormatSupport) -> bool:
        advertised = self._endpoints[0].support
        if not config.formats <= advertised.formats:
            return False
        if config.sample_rates_hz and not config.sample_rates_hz <= advertised.sample_rates_hz:
            return False
        if config.channel_counts and not config.channel_counts <= advertised.channel_counts:
            return False
        return config.max_payload_bytes <= advertised.max_payload_bytes

    def endpoints(
        self,
        kind: AudioEndpointKind | None = None,
    ) -> Result[tuple[AudioEndpoint, ...], AgentXError]:
        if kind is None:
            return Result[tuple[AudioEndpoint, ...], AgentXError].success(self._endpoints)
        return Result[tuple[AudioEndpoint, ...], AgentXError].success(
            tuple(endpoint for endpoint in self._endpoints if endpoint.kind is kind)
        )

    def open(
        self,
        descriptor: AudioStreamDescriptor,
        *,
        deadline: Deadline | None = None,
    ) -> Result[AudioCaptureStream | AudioPlaybackStream, AgentXError]:
        del deadline
        self.opened.append(descriptor)
        if descriptor.provider_id != self._provider_id:
            return Result[AudioCaptureStream | AudioPlaybackStream, AgentXError].failure(
                audio_failure(
                    AudioFailureKind.UNSUPPORTED_ENDPOINT,
                    message="endpoint belongs to another provider",
                    stream_id=descriptor.stream_id,
                )
            )
        if descriptor.kind is AudioEndpointKind.SINK:
            sink = FakePlaybackStream(descriptor, defects=self._defects)
            if self._defects.starts_closed:
                sink._force_state(AudioStreamState.CLOSED)
            return Result[AudioCaptureStream | AudioPlaybackStream, AgentXError].success(sink)
        source = FakeCaptureStream(
            descriptor,
            frame_count=self._frame_count,
            defects=self._defects,
        )
        if self._defects.starts_closed:
            source._force_state(AudioStreamState.CLOSED)
        return Result[AudioCaptureStream | AudioPlaybackStream, AgentXError].success(source)


class HostileAudioProvider:
    """A provider that fails conformance on purpose.

    It advertises endpoints it cannot honour, promises every configuration, hands
    out an already-terminal capture stream that skips sequence numbers, contradicts
    its own descriptor, keeps transferring frames after "cancellation", and reports
    ``OPEN`` forever — including after close. Every one of these is a finding the
    scaffold must name.
    """

    def __init__(self) -> None:
        self._provider_id = AudioProviderId("test.audio.hostile")
        self._endpoints = (
            make_endpoint(
                value=FAKE_SOURCE_ENDPOINT,
                provider_id=self._provider_id,
                is_default=True,
                label="Hostile device (claims everything)",
            ),
            make_sink_endpoint(provider_id=self._provider_id, is_default=True),
        )

    @property
    def provider_id(self) -> AudioProviderId:
        return self._provider_id

    def supports(self, config: AudioFormatSupport) -> bool:
        del config
        return True

    def endpoints(
        self,
        kind: AudioEndpointKind | None = None,
    ) -> Result[tuple[AudioEndpoint, ...], AgentXError]:
        if kind is None:
            return Result[tuple[AudioEndpoint, ...], AgentXError].success(self._endpoints)
        return Result[tuple[AudioEndpoint, ...], AgentXError].success(
            tuple(endpoint for endpoint in self._endpoints if endpoint.kind is kind)
        )

    def open(
        self,
        descriptor: AudioStreamDescriptor,
        *,
        deadline: Deadline | None = None,
    ) -> Result[AudioCaptureStream | AudioPlaybackStream, AgentXError]:
        """Hand out a stream that is already closed but never admits it."""

        del deadline
        stream = FakeCaptureStream(
            descriptor,
            frame_count=3,
            defects=DefectSpec(
                skip_frames=True,
                wrong_channel_count=True,
                frozen_status=True,
                ignores_cancellation=True,
            ),
        )
        stream._force_state(AudioStreamState.CLOSED)
        return Result[AudioCaptureStream | AudioPlaybackStream, AgentXError].success(stream)
