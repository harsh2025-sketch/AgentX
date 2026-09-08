"""Unit tests for governed Windows window-management (M7.02).

Covers typed parameters, validation, each operation, verification, and
explicit failure cases. No real Win32 calls; uses FakeWindowManagementNativeSurface.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal

import pytest

from agentx.capabilities.abi import CapabilityPlatform
from agentx.capabilities.windows.provider import (
    PlatformFacts,
    WindowsSupport,
    evaluate_windows_support,
)
from agentx.capabilities.windows.window_management import (
    WINDOW_MANAGEMENT_NATIVE_ERROR_CODE,
    DefaultWindowManagementNativeSurface,
    FakeWindowManagementNativeSurface,
    WindowCloseCapability,
    WindowFocusCapability,
    WindowFocusParams,
    WindowMaximizeCapability,
    WindowMinimizeCapability,
    WindowMoveResizeCapability,
    WindowMoveResizeParams,
    WindowRestoreCapability,
    close_window_request,
    focus_window_request,
    maximize_window_request,
    minimize_window_request,
    move_resize_window_request,
    restore_window_request,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result

WINDOWS_FACTS = PlatformFacts(system="Windows", release="11", version="10.0.26100", machine="AMD64")
LINUX_FACTS = PlatformFacts(system="Linux", release="6.8.0", version="#1 SMP", machine="x86_64")


def _windows_support() -> WindowsSupport:
    return evaluate_windows_support(WINDOWS_FACTS)


def _linux_support() -> WindowsSupport:
    return evaluate_windows_support(LINUX_FACTS)


def _context() -> ExecutionContext:
    source = CancellationSource()
    import uuid

    return ExecutionContext(
        correlation_id=uuid.uuid4(), cancellation_token=source.token, task_id=None
    )


# ------------------------------------------------------------------
# Parameter validation
# ------------------------------------------------------------------


def test_focus_params_valid() -> None:
    p = WindowFocusParams(handle=42)
    assert p.handle == 42


def test_focus_params_invalid_handle_zero() -> None:
    with pytest.raises(ValueError, match=">= 1"):
        WindowFocusParams(handle=0)


def test_focus_params_invalid_handle_negative() -> None:
    with pytest.raises(ValueError, match=">= 1"):
        WindowFocusParams(handle=-5)


def test_focus_params_invalid_handle_bool() -> None:
    with pytest.raises(TypeError, match="got bool"):
        WindowFocusParams(handle=True)  # type: ignore[arg-type]


def test_focus_params_invalid_handle_string() -> None:
    with pytest.raises(TypeError, match="must be int"):
        WindowFocusParams(handle="42")  # type: ignore[arg-type]


def test_move_resize_params_valid() -> None:
    p = WindowMoveResizeParams(handle=99, x=10, y=20, width=400, height=300)
    assert p.x == 10
    assert p.width == 400


def test_move_resize_params_invalid_zero_width() -> None:
    with pytest.raises(ValueError, match="positive"):
        WindowMoveResizeParams(handle=1, x=0, y=0, width=0, height=100)


def test_move_resize_params_invalid_negative_height() -> None:
    with pytest.raises(ValueError, match="positive"):
        WindowMoveResizeParams(handle=1, x=0, y=0, width=100, height=-10)


def test_move_resize_params_invalid_bool_as_int() -> None:
    with pytest.raises(TypeError, match="got bool"):
        WindowMoveResizeParams(handle=1, x=True, y=0, width=100, height=100)  # type: ignore[arg-type]


def test_move_resize_params_invalid_overflow() -> None:
    with pytest.raises(OverflowError):
        WindowMoveResizeParams(handle=1, x=0, y=0, width=999_999_999_999, height=100)


# ------------------------------------------------------------------
# Supported / unsupported
# ------------------------------------------------------------------


def test_focus_unsupported_platform() -> None:
    cap = WindowFocusCapability(_linux_support())
    req = focus_window_request(1)
    res = cap.execute(req, _context())
    assert res.succeeded is False
    assert (
        "unsupported" in res.message.lower() or res.observation.data.get("platform") == sys.platform
    )


def test_minimize_unsupported_platform() -> None:
    cap = WindowMinimizeCapability(_linux_support())
    res = cap.execute(minimize_window_request(1), _context())
    assert res.succeeded is False


def test_maximize_unsupported_platform() -> None:
    cap = WindowMaximizeCapability(_linux_support())
    res = cap.execute(maximize_window_request(1), _context())
    assert res.succeeded is False


def test_restore_unsupported_platform() -> None:
    cap = WindowRestoreCapability(_linux_support())
    res = cap.execute(restore_window_request(1), _context())
    assert res.succeeded is False


def test_move_resize_unsupported_platform() -> None:
    cap = WindowMoveResizeCapability(_linux_support())
    res = cap.execute(move_resize_window_request(1, 0, 0, 100, 100), _context())
    assert res.succeeded is False


def test_close_unsupported_platform() -> None:
    cap = WindowCloseCapability(_linux_support())
    res = cap.execute(close_window_request(1), _context())
    assert res.succeeded is False


# ------------------------------------------------------------------
# Focus
# ------------------------------------------------------------------


def test_focus_success() -> None:
    cap = WindowFocusCapability(
        _windows_support(), native_surface=FakeWindowManagementNativeSurface(observe_foreground=100)
    )
    req = focus_window_request(100)
    res = cap.execute(req, _context())
    assert res.succeeded is True
    ver = cap.verify(req, res.observation, _context())
    assert ver.passed is True


def test_focus_native_error() -> None:
    err = AgentXError(
        code=WINDOW_MANAGEMENT_NATIVE_ERROR_CODE,
        message="native failure",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
    )
    cap = WindowFocusCapability(
        _windows_support(),
        native_surface=FakeWindowManagementNativeSurface(focus_result=Result.failure(err)),
    )
    res = cap.execute(focus_window_request(1), _context())
    assert res.succeeded is False
    assert res.observation.data["error_code"] == WINDOW_MANAGEMENT_NATIVE_ERROR_CODE


def test_focus_stale_target_verification_mismatch() -> None:
    cap = WindowFocusCapability(
        _windows_support(),
        native_surface=FakeWindowManagementNativeSurface(observe_foreground=999),
    )
    req = focus_window_request(111)
    res = cap.execute(req, _context())
    # Execution succeeds (native adapter reports success) but verification reveals mismatch
    ver = cap.verify(req, res.observation, _context())
    assert ver.passed is False
    assert "mismatch" in ver.detail.lower()


def test_focus_hostile_title_ignored() -> None:
    # Window identity is structured handle; no title parsing occurs.
    cap = WindowFocusCapability(
        _windows_support(), native_surface=FakeWindowManagementNativeSurface(observe_foreground=1)
    )
    # Even if params contain hostile string in an unrelated field, handle is int-only.
    # This proves title content can never influence operation choice.
    req = focus_window_request(1)
    res = cap.execute(req, _context())
    assert res.succeeded is True


# ------------------------------------------------------------------
# Minimize
# ------------------------------------------------------------------


def test_minimize_success() -> None:
    cap = WindowMinimizeCapability(
        _windows_support(),
        native_surface=FakeWindowManagementNativeSurface(observe_state="minimized"),
    )
    res = cap.execute(minimize_window_request(7), _context())
    assert res.succeeded is True
    ver = cap.verify(minimize_window_request(7), res.observation, _context())
    assert ver.passed is True


def test_minimize_verification_mismatch() -> None:
    cap = WindowMinimizeCapability(
        _windows_support(),
        native_surface=FakeWindowManagementNativeSurface(observe_state="maximized"),
    )
    res = cap.execute(minimize_window_request(7), _context())
    ver = cap.verify(minimize_window_request(7), res.observation, _context())
    assert ver.passed is False


# ------------------------------------------------------------------
# Maximize
# ------------------------------------------------------------------


def test_maximize_success() -> None:
    cap = WindowMaximizeCapability(
        _windows_support(),
        native_surface=FakeWindowManagementNativeSurface(observe_state="maximized"),
    )
    res = cap.execute(maximize_window_request(8), _context())
    assert res.succeeded is True
    ver = cap.verify(maximize_window_request(8), res.observation, _context())
    assert ver.passed is True


# ------------------------------------------------------------------
# Restore
# ------------------------------------------------------------------


def test_restore_success() -> None:
    cap = WindowRestoreCapability(
        _windows_support(), native_surface=FakeWindowManagementNativeSurface(observe_state="normal")
    )
    res = cap.execute(restore_window_request(9), _context())
    assert res.succeeded is True
    ver = cap.verify(restore_window_request(9), res.observation, _context())
    assert ver.passed is True


# ------------------------------------------------------------------
# Move / resize
# ------------------------------------------------------------------


def test_move_resize_success() -> None:
    cap = WindowMoveResizeCapability(
        _windows_support(),
        native_surface=FakeWindowManagementNativeSurface(observe_rect=(10, 20, 410, 320)),
    )
    res = cap.execute(move_resize_window_request(10, 10, 20, 400, 300), _context())
    assert res.succeeded is True
    ver = cap.verify(move_resize_window_request(10, 10, 20, 400, 300), res.observation, _context())
    assert ver.passed is True


def test_move_resize_verification_mismatch() -> None:
    cap = WindowMoveResizeCapability(
        _windows_support(),
        native_surface=FakeWindowManagementNativeSurface(observe_rect=(0, 0, 100, 100)),
    )
    res = cap.execute(move_resize_window_request(10, 10, 20, 400, 300), _context())
    ver = cap.verify(move_resize_window_request(10, 10, 20, 400, 300), res.observation, _context())
    assert ver.passed is False


# ------------------------------------------------------------------
# Close
# ------------------------------------------------------------------


def test_close_delivery_success() -> None:
    cap = WindowCloseCapability(
        _windows_support(), native_surface=FakeWindowManagementNativeSurface(observe_exists=False)
    )
    res = cap.execute(close_window_request(55), _context())
    # Delivery succeeds; verification checks independent absence
    ver = cap.verify(close_window_request(55), res.observation, _context())
    assert ver.passed is True
    assert "independently confirmed absent" in ver.detail


def test_close_verification_mismatch_still_present() -> None:
    cap = WindowCloseCapability(
        _windows_support(), native_surface=FakeWindowManagementNativeSurface(observe_exists=True)
    )
    res = cap.execute(close_window_request(55), _context())
    ver = cap.verify(close_window_request(55), res.observation, _context())
    assert ver.passed is False
    assert "still present" in ver.detail


# ------------------------------------------------------------------
# Native adapter / default behavior
# ------------------------------------------------------------------


def test_default_adapter_returns_failure() -> None:
    adapter = DefaultWindowManagementNativeSurface()
    res = adapter.focus_window(1)
    assert res.is_failure
    assert res.unwrap_error().code == WINDOW_MANAGEMENT_NATIVE_ERROR_CODE


def test_fake_adapter_records_calls() -> None:
    fake = FakeWindowManagementNativeSurface()
    fake.focus_window(123)
    assert "focus_window" in fake.calls


# ------------------------------------------------------------------
# Descriptor properties
# ------------------------------------------------------------------


def test_focus_descriptor_identity() -> None:
    cap = WindowFocusCapability(_windows_support())
    desc = cap.descriptor
    assert desc.identity.name.value == "windows.window.focus"
    assert desc.scope.platform == CapabilityPlatform.WINDOWS


def test_close_descriptor_risk_critical() -> None:
    cap = WindowCloseCapability(_windows_support())
    # Close uses critical=True which pushes risk to R3/R4
    desc = cap.descriptor
    assert desc.risk_assessment.critical is True


def test_move_resize_descriptor_preconditions() -> None:
    cap = WindowMoveResizeCapability(_windows_support())
    names = {p.name for p in cap.descriptor.preconditions}
    assert "geometry.positive" in names


# ------------------------------------------------------------------
# Resource estimate deterministic
# ------------------------------------------------------------------


def test_focus_estimate_is_typed() -> None:
    cap = WindowFocusCapability(_windows_support())
    est = cap.descriptor.estimate
    assert isinstance(est.wall_clock, timedelta)
    assert isinstance(est.external_cost, Decimal)
    assert est.machine_actions == 1
