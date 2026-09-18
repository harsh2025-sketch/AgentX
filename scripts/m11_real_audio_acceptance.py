"""Manual real Windows microphone/speaker acceptance for M11.

This is intentionally separate from deterministic CI acceptance. It records a
short bounded microphone sample and plays the same PCM through the production
WinMM provider. No audio bytes are printed or persisted.
"""

from __future__ import annotations

import json
import sys

from agentx.capabilities.windows.audio_provider import WinMmAudioProvider
from agentx.core.audio import (
    AudioBufferPolicy,
    AudioEndpointKind,
    AudioFormat,
    AudioStreamDescriptor,
)
from agentx.core.ids import AudioStreamId


def _descriptor(provider: WinMmAudioProvider, kind: AudioEndpointKind) -> AudioStreamDescriptor:
    endpoint_result = provider.endpoints(kind)
    if endpoint_result.is_failure:
        raise RuntimeError(endpoint_result.unwrap_error().code)
    endpoint = endpoint_result.unwrap()[0]
    return AudioStreamDescriptor(
        stream_id=AudioStreamId.create(),
        endpoint=endpoint,
        format=AudioFormat.PCM_S16LE,
        sample_rate_hz=16000,
        channel_count=1,
        buffer=AudioBufferPolicy(max_frames=4, max_bytes=64 * 1024),
    )


def main() -> int:
    if sys.platform != "win32":
        print(json.dumps({"accepted": False, "reason": "windows_required"}, sort_keys=True))
        return 2
    provider = WinMmAudioProvider()
    capture_result = provider.open(_descriptor(provider, AudioEndpointKind.SOURCE))
    playback_result = provider.open(_descriptor(provider, AudioEndpointKind.SINK))
    if capture_result.is_failure or playback_result.is_failure:
        print(json.dumps({"accepted": False, "reason": "audio_device_unavailable"}, sort_keys=True))
        return 2
    capture = capture_result.unwrap()
    playback = playback_result.unwrap()
    recorded = capture.read()
    if recorded.is_failure:
        print(
            json.dumps(
                {"accepted": False, "error_code": recorded.unwrap_error().code},
                sort_keys=True,
            )
        )
        return 1
    frame = recorded.unwrap()
    played = playback.write(frame)
    capture.close(reason="acceptance complete")
    playback.close(reason="acceptance complete")
    if played.is_failure:
        print(
            json.dumps(
                {"accepted": False, "error_code": played.unwrap_error().code},
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "accepted": True,
                "provider": provider.provider_id.value,
                "sample_rate_hz": frame.sample_rate_hz,
                "channel_count": frame.channel_count,
                "captured_bytes": frame.byte_count,
                "audio_exposed": False,
                "audio_persisted": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
