from __future__ import annotations

import os
import sys

import pytest

from agentx.capabilities.windows.provider import detect_platform_facts, evaluate_windows_support
from agentx.capabilities.windows.screen_capture import WindowsScreenCapture

_REAL_HOST = os.environ.get("AGENTX_M6_REAL_HOST") == "1"
pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows-only real-host acceptance"),
    pytest.mark.skipif(not _REAL_HOST, reason="real-host acceptance requires explicit opt-in"),
]


def test_m10_real_windows_host_captures_current_virtual_desktop() -> None:
    capture = WindowsScreenCapture(evaluate_windows_support(detect_platform_facts()))

    outcome = capture.capture(
        environment_id="github-actions-windows-host",
        surface_id="virtual-desktop",
    )

    assert outcome.is_success, outcome.unwrap_error()
    frame = outcome.unwrap()
    assert frame.bounds.width > 0
    assert frame.bounds.height > 0
    assert len(frame.pixels_bgra) == frame.row_stride * frame.bounds.height
    assert len(frame.pixel_digest) == 64
    assert frame.displays
    assert sum(1 for display in frame.displays if display.primary) == 1
    assert all(frame.bounds.contains(display.bounds) for display in frame.displays)
