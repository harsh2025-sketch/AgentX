"""Campaign acceptance guards for AX-436 audio and AX-452 UI telemetry."""

from __future__ import annotations

from pathlib import Path

from agentx.core.audio import (
    AudioCaptureStream,
    AudioEndpointKind,
    AudioPlaybackStream,
    AudioProvider,
)

_ROOT = Path(__file__).resolve().parents[2]


def test_audio_foundation_exposes_provider_neutral_resource_lifecycle_contracts() -> None:
    assert {item.value for item in AudioEndpointKind} == {"source", "sink"}
    assert hasattr(AudioCaptureStream, "read")
    assert hasattr(AudioCaptureStream, "close")
    assert hasattr(AudioCaptureStream, "cancel")
    assert hasattr(AudioCaptureStream, "cancellation_token")
    assert hasattr(AudioPlaybackStream, "write")
    assert hasattr(AudioPlaybackStream, "drain")
    assert hasattr(AudioPlaybackStream, "close")
    assert hasattr(AudioPlaybackStream, "cancel")
    assert hasattr(AudioProvider, "endpoints")
    assert hasattr(AudioProvider, "open")


def test_audio_core_does_not_name_vendor_or_start_always_on_runtime() -> None:
    source = (_ROOT / "src/agentx/core/audio.py").read_text(encoding="utf-8").lower()
    forbidden_runtime = (
        "openai import",
        "whisper import",
        "kokoro import",
        "threading",
        "asyncio.create_task",
        "while true",
        "audio history",
    )
    assert all(token not in source for token in forbidden_runtime)


def test_runtime_ui_protocol_cannot_import_machine_authority() -> None:
    source = (_ROOT / "src/agentx/core/runtime_ui_events.py").read_text(encoding="utf-8")
    forbidden = (
        "agentx.kernel.action_gate",
        "agentx.kernel.permissions",
        "agentx.kernel.risk",
        "agentx.capabilities.runtime",
        "agentx.capabilities.registry",
        "agentx.executor",
    )
    assert all(token not in source for token in forbidden)
