"""Real-host M12 process-state trigger acceptance on Windows."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from agentx.capabilities.windows.process_discovery import WindowsProcessDiscovery
from agentx.capabilities.windows.provider import (
    detect_platform_facts,
    evaluate_windows_support,
)
from agentx.core.execution import CancellationSource
from agentx.event_triggers import ProcessStateWatcherSource


@pytest.mark.skipif(sys.platform != "win32", reason="requires the real Win32 process surface")
def test_real_process_lifecycle_emits_appeared_and_exited() -> None:
    support = evaluate_windows_support(detect_platform_facts())
    assert support.is_supported
    source = ProcessStateWatcherSource(WindowsProcessDiscovery(support))
    cancellation = CancellationSource().token

    # Establish a real-host baseline before creating the controlled child.
    source.poll(cancellation=cancellation, max_items=256)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        appeared = False
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not appeared:
            events = source.poll(cancellation=cancellation, max_items=256)
            appeared = any(
                event.event_key == "process.appeared"
                and event.payload.get("process_id") == child.pid
                for event in events
            )
            if not appeared:
                time.sleep(0.05)
        assert appeared, f"controlled child PID {child.pid} was not observed"
    finally:
        child.terminate()
        child.wait(timeout=10)

    exited = False
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and not exited:
        events = source.poll(cancellation=cancellation, max_items=256)
        exited = any(
            event.event_key == "process.exited" and event.payload.get("process_id") == child.pid
            for event in events
        )
        if not exited:
            time.sleep(0.05)
    assert exited, f"controlled child PID {child.pid} exit was not observed"
