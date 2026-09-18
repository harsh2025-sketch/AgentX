"""Concrete Windows WinMM microphone/speaker provider for M11.

The provider implements the existing :mod:`agentx.core.audio` contracts. Import
and construction are side-effect free; WinMM is loaded lazily only when a real
capture/read or playback/write is requested. Device availability is data, never
authority, and raw audio is held only in bounded in-memory buffers.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Protocol, runtime_checkable

from agentx.core.audio import (
    AudioAdmissionDecision,
    AudioCaptureStream,
    AudioEndpoint,
    AudioEndpointId,
    AudioEndpointKind,
    AudioFailureKind,
    AudioFormat,
    AudioFormatSupport,
    AudioFrame,
    AudioPlaybackStream,
    AudioProviderId,
    AudioStreamDescriptor,
    AudioStreamState,
    AudioStreamStatus,
    audio_failure,
)
from agentx.core.errors import AgentXError
from agentx.core.execution import CancellationSource, CancellationToken, Deadline
from agentx.core.result import Result
from agentx.capabilities.windows._audio_native import WinMmAudioSurface

__all__ = ["WinMmAudioProvider"]

_PROVIDER_ID = AudioProviderId("windows-winmm")
_SOURCE_ID = AudioEndpointId(_PROVIDER_ID, "default-microphone")
_SINK_ID = AudioEndpointId(_PROVIDER_ID, "default-speaker")
_SUPPORT = AudioFormatSupport(
    formats=frozenset({AudioFormat.PCM_S16LE}),
    sample_rates_hz=frozenset({8000, 16000, 22050, 32000, 44100, 48000}),
    channel_counts=frozenset({1, 2}),
    max_payload_bytes=256 * 1024,
)


def _failure(kind: AudioFailureKind, message: str) -> AgentXError:
    return audio_failure(kind, message=message)



@runtime_checkable
class _NativeAudioSurface(Protocol):
    def capture(
        self,
        *,
        sample_rate_hz: int,
        channel_count: int,
        milliseconds: int,
        cancellation_token: CancellationToken,
    ) -> Result[bytes, AgentXError]: ...

    def play(
        self,
        payload: bytes,
        *,
        sample_rate_hz: int,
        channel_count: int,
        cancellation_token: CancellationToken,
    ) -> Result[None, AgentXError]: ...



@dataclass(slots=True)
class _StreamBase:
    descriptor: AudioStreamDescriptor
    surface: _NativeAudioSurface
    source: CancellationSource
    status_value: AudioStreamStatus
    lock: Lock

    @property
    def status(self) -> AudioStreamStatus:
        return self.status_value

    @property
    def cancellation_token(self) -> CancellationToken:
        return self.source.token

    def _cancel(self, reason: str | None) -> Result[AudioStreamStatus, AgentXError]:
        with self.lock:
            if self.status_value.state is AudioStreamState.CANCELLED:
                return Result.success(self.status_value)
            if self.status_value.is_terminal:
                return Result.success(self.status_value)
            self.source.request_cancellation(reason)
            self.status_value = AudioStreamStatus(
                state=AudioStreamState.CANCELLED,
                reason=reason,
            )
            return Result.success(self.status_value)

    def _close(self, reason: str | None) -> Result[AudioStreamStatus, AgentXError]:
        with self.lock:
            if self.status_value.is_terminal:
                return Result.success(self.status_value)
            self.status_value = AudioStreamStatus(state=AudioStreamState.CLOSED, reason=reason)
            return Result.success(self.status_value)


class _WinMmCaptureStream(_StreamBase):
    __slots__ = ("_sequence",)

    def __init__(
        self,
        descriptor: AudioStreamDescriptor,
        surface: _NativeAudioSurface,
    ) -> None:
        super().__init__(
            descriptor=descriptor,
            surface=surface,
            source=CancellationSource(),
            status_value=AudioStreamStatus(state=AudioStreamState.OPEN),
            lock=Lock(),
        )
        self._sequence = descriptor.sequence_origin

    def read(self) -> Result[AudioFrame, AgentXError]:
        with self.lock:
            if self.status_value.state is not AudioStreamState.OPEN:
                return Result.failure(
                    _failure(AudioFailureKind.NOT_OPEN, "microphone stream is not open")
                )
            interval = 100
            result = self.surface.capture(
                sample_rate_hz=self.descriptor.sample_rate_hz,
                channel_count=self.descriptor.channel_count,
                milliseconds=interval,
                cancellation_token=self.source.token,
            )
            if result.is_failure:
                if result.unwrap_error().category.value == "cancelled":
                    self.status_value = AudioStreamStatus(state=AudioStreamState.CANCELLED)
                return Result.failure(result.unwrap_error())
            frame = AudioFrame(
                stream_id=self.descriptor.stream_id,
                format=self.descriptor.format,
                sample_rate_hz=self.descriptor.sample_rate_hz,
                channel_count=self.descriptor.channel_count,
                sequence=self._sequence,
                timestamp=datetime.now(UTC),
                payload=result.unwrap(),
            )
            self._sequence += 1
            return Result.success(frame)

    def close(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        return self._close(reason)

    def cancel(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        return self._cancel(reason)


class _WinMmPlaybackStream(_StreamBase):
    __slots__ = ("_buffered_bytes", "_buffered_frames")

    def __init__(
        self,
        descriptor: AudioStreamDescriptor,
        surface: _NativeAudioSurface,
    ) -> None:
        super().__init__(
            descriptor=descriptor,
            surface=surface,
            source=CancellationSource(),
            status_value=AudioStreamStatus(state=AudioStreamState.OPEN),
            lock=Lock(),
        )
        self._buffered_bytes = 0
        self._buffered_frames = 0

    def write(self, frame: AudioFrame) -> Result[AudioAdmissionDecision, AgentXError]:
        if not isinstance(frame, AudioFrame):
            raise TypeError("frame must be AudioFrame")
        with self.lock:
            if self.status_value.state is not AudioStreamState.OPEN:
                return Result.failure(
                    _failure(AudioFailureKind.NOT_OPEN, "speaker stream is not open")
                )
            if (
                frame.format is not self.descriptor.format
                or frame.sample_rate_hz != self.descriptor.sample_rate_hz
                or frame.channel_count != self.descriptor.channel_count
            ):
                return Result.failure(
                    _failure(
                        AudioFailureKind.UNSUPPORTED_CONFIGURATION,
                        "speaker frame layout differs from opened stream",
                    )
                )
            decision = self.descriptor.buffer.assess(
                buffered_frames=self._buffered_frames,
                buffered_bytes=self._buffered_bytes,
                frame_bytes=frame.byte_count,
            )
            if not decision.is_accepted:
                return Result.failure(
                    _failure(AudioFailureKind.BUFFER_FULL, "speaker buffer rejected frame")
                )
            self._buffered_frames = decision.projected_frames
            self._buffered_bytes = decision.projected_bytes
        result = self.surface.play(
            frame.payload,
            sample_rate_hz=frame.sample_rate_hz,
            channel_count=frame.channel_count,
            cancellation_token=self.source.token,
        )
        with self.lock:
            self._buffered_frames = max(0, self._buffered_frames - 1)
            self._buffered_bytes = max(0, self._buffered_bytes - frame.byte_count)
            if result.is_failure:
                if result.unwrap_error().category.value == "cancelled":
                    self.status_value = AudioStreamStatus(state=AudioStreamState.CANCELLED)
                return Result.failure(result.unwrap_error())
        return Result.success(decision)

    def drain(self) -> Result[AudioStreamStatus, AgentXError]:
        with self.lock:
            if self.status_value.state is not AudioStreamState.OPEN:
                return Result.failure(
                    _failure(AudioFailureKind.NOT_OPEN, "speaker stream is not open")
                )
            self.status_value = AudioStreamStatus(state=AudioStreamState.DRAINING)
            if self._buffered_frames == 0:
                self.status_value = AudioStreamStatus(state=AudioStreamState.CLOSED)
            return Result.success(self.status_value)

    def close(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        return self._close(reason)

    def cancel(self, *, reason: str | None = None) -> Result[AudioStreamStatus, AgentXError]:
        return self._cancel(reason)


class WinMmAudioProvider:
    """Production Windows microphone/speaker adapter behind the A7.01 contract."""

    __slots__ = ("_surface",)

    def __init__(self, surface: _NativeAudioSurface | None = None) -> None:
        if surface is not None and not isinstance(surface, _NativeAudioSurface):
            raise TypeError("surface must satisfy the native audio surface protocol")
        self._surface = WinMmAudioSurface() if surface is None else surface

    @property
    def provider_id(self) -> AudioProviderId:
        return _PROVIDER_ID

    def supports(self, config: AudioFormatSupport) -> bool:
        if not isinstance(config, AudioFormatSupport):
            raise TypeError("config must be AudioFormatSupport")
        return (
            all(fmt in _SUPPORT.formats for fmt in config.formats)
            and all(rate in _SUPPORT.sample_rates_hz for rate in config.sample_rates_hz)
            and all(count in _SUPPORT.channel_counts for count in config.channel_counts)
        )

    def endpoints(
        self,
        kind: AudioEndpointKind | None = None,
    ) -> Result[tuple[AudioEndpoint, ...], AgentXError]:
        if kind is not None and not isinstance(kind, AudioEndpointKind):
            raise TypeError("kind must be AudioEndpointKind or None")
        if sys.platform != "win32":
            return Result.failure(
                _failure(AudioFailureKind.PROVIDER_UNAVAILABLE, "WinMM provider requires Windows")
            )
        endpoints = (
            AudioEndpoint(
                endpoint_id=_SOURCE_ID,
                kind=AudioEndpointKind.SOURCE,
                label="Default Windows microphone",
                support=_SUPPORT,
                is_default=True,
            ),
            AudioEndpoint(
                endpoint_id=_SINK_ID,
                kind=AudioEndpointKind.SINK,
                label="Default Windows speaker",
                support=_SUPPORT,
                is_default=True,
            ),
        )
        if kind is None:
            return Result.success(endpoints)
        return Result.success(tuple(endpoint for endpoint in endpoints if endpoint.kind is kind))

    def open(
        self,
        descriptor: AudioStreamDescriptor,
        *,
        deadline: Deadline | None = None,
    ) -> Result[AudioCaptureStream | AudioPlaybackStream, AgentXError]:
        if not isinstance(descriptor, AudioStreamDescriptor):
            raise TypeError("descriptor must be AudioStreamDescriptor")
        if deadline is not None and not isinstance(deadline, Deadline):
            raise TypeError("deadline must be Deadline or None")
        if sys.platform != "win32":
            return Result.failure(
                _failure(AudioFailureKind.PROVIDER_UNAVAILABLE, "WinMM provider requires Windows")
            )
        if deadline is not None and deadline.is_expired():
            return Result.failure(_failure(AudioFailureKind.TIMEOUT, "audio open deadline expired"))
        if descriptor.provider_id != _PROVIDER_ID:
            return Result.failure(
                _failure(
                    AudioFailureKind.UNSUPPORTED_ENDPOINT,
                    "audio endpoint belongs to another provider",
                )
            )
        if descriptor.format is not AudioFormat.PCM_S16LE:
            return Result.failure(
                _failure(
                    AudioFailureKind.UNSUPPORTED_CONFIGURATION,
                    "WinMM provider supports PCM S16LE",
                )
            )
        if descriptor.kind is AudioEndpointKind.SOURCE:
            return Result.success(_WinMmCaptureStream(descriptor, self._surface))
        return Result.success(_WinMmPlaybackStream(descriptor, self._surface))
