"""Guarded integration coverage for the real A5.03 Windows UIA native seam."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import pytest

from agentx.capabilities.windows import _uia_native
from agentx.capabilities.windows.provider import PlatformFacts, WindowsSupport, evaluate_windows_support
from agentx.capabilities.windows.uia_tree import UIATreeLimits, WindowsUIATreeInspection


def _is_windows_runtime() -> bool:
    return sys.platform == "win32"


def _windows_support() -> WindowsSupport:
    return evaluate_windows_support(
        PlatformFacts(system="Windows", release="test", version="test", machine="AMD64")
    )


def test_default_native_seam_is_explicitly_unavailable_off_windows() -> None:
    if _is_windows_runtime():
        pytest.skip("off-Windows contract test")

    outcome = _uia_native.inspect_uia_tree_raw(1, max_depth=0, max_nodes=1)

    assert outcome.is_failure
    assert outcome.unwrap_error().code == _uia_native.UIA_NATIVE_UNAVAILABLE_ERROR_CODE


@pytest.mark.skipif(not _is_windows_runtime(), reason="real Windows UI Automation integration")
def test_real_windows_uia_can_read_one_bounded_desktop_root() -> None:
    import ctypes

    user32 = ctypes.WinDLL("user32")
    user32.GetDesktopWindow.restype = ctypes.c_void_p
    handle = int(user32.GetDesktopWindow() or 0)
    if handle <= 0:
        pytest.skip("Windows runner exposes no desktop HWND")

    inspection = WindowsUIATreeInspection(
        _windows_support(),
        clock=lambda: datetime.now(UTC),
    )
    outcome = inspection.inspect(handle, limits=UIATreeLimits(max_depth=0, max_nodes=1))

    if outcome.is_failure:
        error = outcome.unwrap_error()
        if error.code == _uia_native.UIA_NATIVE_FAILURE_ERROR_CODE:
            pytest.skip(f"runner has no usable interactive UIA desktop: {error.message}")

    assert outcome.is_success
    snapshot = outcome.unwrap()
    assert snapshot.root_window_handle == handle
    assert snapshot.node_count <= 1
    assert snapshot.limits == UIATreeLimits(max_depth=0, max_nodes=1)
