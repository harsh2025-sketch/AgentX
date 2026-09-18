"""Deterministic provider-level acceptance for the M11 WinMM audio adapter."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from agentx.capabilities.windows.audio_provider import WinMmAudioProvider
from agentx.core.audio import (
    AudioBufferPolicy,
    AudioEndpointKind,
    AudioFormat,
    AudioFrame,
    AudioStreamDescriptor,
    AudioStreamState,
)
from agentx.core.errors import AgentXError
from agentx.core.execution import CancellationToken, Deadline
from agentx.core.ids import AudioStreamId
from agentx.core.result import Result


class _FakeSurface:
    def __init__(self) -> None:
        self.played: list[bytes] = []

    def capture(
        self,
        *,
        sample_rate_hz: int,
        channel_count: int,
        milliseconds: int,
        cancellation_token: CancellationToken,
    ) -> Result[bytes, AgentXError]:
        del sample_rate_hz, channel_count, milliseconds
        if cancellation_token.is_cancelled:
            raise AssertionError("capture should not start after cancellation")
        return Result.success(b"\x00\x00" * 1600)

    def play(
        self,
        payload: bytes,
        *,
        sample_rate_hz: int,
        channel_count: int,
        cancellation_token: CancellationToken,
    ) -> Result[None, AgentXError]:
        del sample_rate_hz, channel_count
        if cancellation_token.is_cancelled:
            raise AssertionError("play should not start after cancellation")
        self.played.append(payload)
        return Result.success(None)


def _descriptor(provider: WinMmAudioProvider, kind: AudioEndpointKind) -> AudioStreamDescriptor:
    endpoint = provider.endpoints(kind).unwrap()[0]
    return AudioStreamDescriptor(
        stream_id=AudioStreamId.create(),
        endpoint=endpoint,
        format=AudioFormat.PCM_S16LE,
        sample_rate_hz=16000,
        channel_count=1,
        buffer=AudioBufferPolicy(max_frames=4, max_bytes=64 * 1024),
    )


def test_concrete_provider_capture_playback_and_cancellation(monkeypatch: object) -> None:
    monkeypatch.setattr(sys, "platform", "win32")  # type: ignore[attr-defined]
    surface = _FakeSurface()
    provider = WinMmAudioProvider(surface)
    capture = provider.open(_descriptor(provider, AudioEndpointKind.SOURCE)).unwrap()
    playback = provider.open(_descriptor(provider, AudioEndpointKind.SINK)).unwrap()

    assert capture.status.state is AudioStreamState.OPEN
    captured = capture.read().unwrap()
    assert captured.sample_rate_hz == 16000
    assert captured.channel_count == 1
    assert captured.payload == b"\x00\x00" * 1600

    frame = AudioFrame(
        stream_id=playback.descriptor.stream_id,
        format=AudioFormat.PCM_S16LE,
        sample_rate_hz=16000,
        channel_count=1,
        sequence=0,
        timestamp=datetime.now(UTC),
        payload=b"\x01\x00" * 160,
    )
    assert playback.write(frame).unwrap().is_accepted
    assert surface.played == [frame.payload]

    first = playback.cancel(reason="barge-in").unwrap()
    second = playback.cancel(reason="duplicate").unwrap()
    assert first.state is AudioStreamState.CANCELLED
    assert second.state is AudioStreamState.CANCELLED


def test_provider_fails_closed_on_non_windows_and_expired_deadline() -> None:
    provider = WinMmAudioProvider(_FakeSurface())
    if sys.platform != "win32":
        assert provider.endpoints().is_failure
        return

    descriptor = _descriptor(provider, AudioEndpointKind.SOURCE)
    assert provider.open(descriptor, deadline=Deadline.after(0)).is_failure
