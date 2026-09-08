"""Windows-only non-destructive smoke checks for the N2.19 native seam."""

from __future__ import annotations

import sys

import pytest

from agentx.capabilities.windows import native_mutation as mutation
from agentx.capabilities.windows.native_mutation import (
    NativeClipboardTextRequest,
    NativeKeyInputRequest,
    NativeKeyStroke,
    NativeProcessLaunchRequest,
    NativeTextInputRequest,
    NativeWindowActivationRequest,
    NativeWindowShowState,
    NativeWindowStateRequest,
    WindowsNativeMutationAdapter,
    WindowsVirtualKey,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only smoke test")


def test_windows_native_mutation_import_and_request_construction_are_inert() -> None:
    adapter = WindowsNativeMutationAdapter()
    assert adapter is not None
    assert mutation.is_native_mutation_available() is True

    launch = NativeProcessLaunchRequest(
        executable_path="C:\\Windows\\System32\\notepad.exe",
        argv=("literal argument",),
    )
    assert mutation._windows_command_line(launch).startswith("C:\\Windows\\System32\\notepad.exe")
    assert NativeWindowStateRequest(1, NativeWindowShowState.RESTORE).window_handle == 1
    assert NativeWindowActivationRequest(1).window_handle == 1
    assert NativeTextInputRequest("hello").text == "hello"
    assert NativeKeyInputRequest((NativeKeyStroke(WindowsVirtualKey.ENTER),)).strokes
    assert NativeClipboardTextRequest("hello").text == "hello"
