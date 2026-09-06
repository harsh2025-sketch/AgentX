"""Safe real-Windows smoke coverage for the A5.06 native adapter."""

from __future__ import annotations

import sys

import pytest

from agentx.capabilities.windows import _native
from agentx.capabilities.windows.keyboard_clipboard import Win32KeyboardClipboardNativeSurface


@pytest.mark.skipif(sys.platform != "win32", reason="real adapter smoke test requires Windows")
def test_real_windows_clipboard_read_adapter_is_safe() -> None:
    """Exercise only a read; never inject input or mutate the CI runner clipboard."""
    surface = Win32KeyboardClipboardNativeSurface()
    result = surface.read_clipboard_text()
    if result.is_failure:
        error = result.unwrap_error()
        if error.code == _native.CLIPBOARD_OPEN_FAILED_ERROR_CODE:
            pytest.skip("Windows CI session does not currently expose an openable clipboard")
        pytest.fail(str(error))
    outcome = result.unwrap()
    assert type(outcome.has_text) is bool
    assert (outcome.text is None) is (not outcome.has_text)
