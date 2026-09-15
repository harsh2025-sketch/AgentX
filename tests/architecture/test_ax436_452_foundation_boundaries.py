"""Campaign acceptance guards for AX-436 audio and AX-452 UI telemetry."""

from __future__ import annotations

import ast
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


def test_audio_core_does_not_import_vendor_or_start_always_on_runtime() -> None:
    source = (_ROOT / "src/agentx/core/audio.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called_names.add(node.func.attr)

    forbidden_import_roots = {"openai", "whisper", "kokoro", "threading", "asyncio"}
    assert not {
        name for name in imported_modules if name.partition(".")[0] in forbidden_import_roots
    }
    assert not ({"create_task", "create_task_group"} & called_names)
    assert "while True:" not in source


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
