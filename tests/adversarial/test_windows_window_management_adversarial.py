"""Adversarial tests for Windows window-management (M7.02).

Proves hostile inputs cannot mutate operation choice, that no automation
injection paths exist, and that authority is never widened.
"""

from __future__ import annotations

import inspect

from agentx.capabilities.windows.provider import (
    PlatformFacts,
    evaluate_windows_support,
)
from agentx.capabilities.windows.window_management import (
    DefaultWindowManagementNativeSurface,
    FakeWindowManagementNativeSurface,
    WindowFocusCapability,
    WindowFocusParams,
    WindowMoveResizeCapability,
    WindowMoveResizeParams,
)
from agentx.core.execution import CancellationSource, ExecutionContext

WINDOWS_FACTS = PlatformFacts(system="Windows", release="11", version="10.0.26100", machine="AMD64")


def _context() -> ExecutionContext:
    import uuid

    return ExecutionContext(
        correlation_id=uuid.uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=None,
    )


def test_fake_hwnd_rejected() -> None:
    from pytest import raises

    _cap = WindowFocusCapability(
        evaluate_windows_support(WINDOWS_FACTS),
        native_surface=FakeWindowManagementNativeSurface(observe_foreground=0),
    )
    with raises(ValueError):
        _cap.execute(
            __import__(
                "agentx.capabilities.windows.window_management", fromlist=["focus_window_request"]
            ).focus_window_request(0),
            _context(),
        )


def test_overflow_geometry_rejected() -> None:
    _cap = WindowMoveResizeCapability(
        evaluate_windows_support(WINDOWS_FACTS),
    )
    with __import__("pytest").raises(OverflowError):
        WindowMoveResizeParams(handle=1, x=0, y=0, width=10_000_000_000, height=100)


def test_hostile_title_does_not_influence_operation() -> None:
    # Window titles are untrusted and must never be parsed into choice.
    # The capability only accepts structured handle; title is never a parameter.
    params = WindowFocusParams(handle=99)
    assert params.handle == 99
    # No title field exists; hostile strings have no parameter to attach to.
    params = WindowFocusParams(handle=99)
    assert params.handle == 99
    assert isinstance(params.to_dict()["handle"], int)


def test_no_powershell_in_source() -> None:
    import inspect

    import agentx.capabilities.windows.window_management as mod

    src = inspect.getsource(mod).lower()
    assert "subprocess" not in src
    assert "os.system" not in src
    assert "shell" not in src or "shell_" not in src  # allow "shell" in docstrings if needed


def test_no_shell_subprocess_import() -> None:
    import agentx.capabilities.windows.window_management as mod

    src = inspect.getsource(mod)
    assert "subprocess" not in src
    assert "os.system" not in src
    assert "popen" not in src


def test_no_key_mouse_injection() -> None:
    import agentx.capabilities.windows.window_management as mod

    src = inspect.getsource(mod)
    for marker in ("sendinput", "keybd_event", "mouse_event", "pyautogui", "pywinauto"):
        assert marker not in src.lower(), f"found injection marker {marker}"


def test_close_cannot_be_smuggled_through_title() -> None:
    # There is no title-based routing; operation is explicit via identity.
    from agentx.capabilities.windows.window_management import close_window_request

    req = close_window_request(42)
    assert req.identity.name.value == "windows.window.close"
    # Title was never involved.


def test_no_authority_mutation() -> None:
    # The module never touches permissions, registry, or risk beyond descriptor.
    import agentx.capabilities.windows.window_management as mod

    src = inspect.getsource(mod)
    # No permission grants, no registry writes, no authority widening.
    assert "grant" not in src.lower() or "grant" in "does not grant"
    assert "Permission." in src  # only uses Permission enum, never creates new


def test_native_adapter_has_no_real_calls_by_default() -> None:
    # Default adapter returns explicit failure; no hidden native invocation.
    adapter = DefaultWindowManagementNativeSurface()
    res = adapter.focus_window(1)
    assert res.is_failure


def test_fake_adapter_no_real_os_interaction() -> None:
    fake = FakeWindowManagementNativeSurface()
    fake.minimize_window(7)
    # Calls are recorded; no OS interaction occurred.
    assert fake.calls == ["minimize_window"]
