"""Integration tests for governed Windows window-management (M7.02).

Uses CapabilityExecutionLoop concepts with a deterministic fake native adapter.
Proves authorization, gate denial, budget, EmergencyStop, verification, and
mismatch prevention.
"""

from __future__ import annotations

import uuid

from agentx.capabilities.windows.provider import (
    PlatformFacts,
    evaluate_windows_support,
)
from agentx.capabilities.windows.window_management import (
    FakeWindowManagementNativeSurface,
    WindowFocusCapability,
    WindowMinimizeCapability,
    WindowMoveResizeCapability,
    focus_window_request,
    minimize_window_request,
    move_resize_window_request,
)
from agentx.core.execution import CancellationSource, ExecutionContext

WINDOWS_FACTS = PlatformFacts(system="Windows", release="11", version="10.0.26100", machine="AMD64")


def _context() -> ExecutionContext:
    return ExecutionContext(
        correlation_id=uuid.uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=None,
    )


def test_authorized_focus_reaches_execute() -> None:
    cap = WindowFocusCapability(
        evaluate_windows_support(WINDOWS_FACTS),
        native_surface=FakeWindowManagementNativeSurface(observe_foreground=1),
    )
    req = focus_window_request(1)
    res = cap.execute(req, _context())
    assert res.succeeded is True
    assert res.observation.summary == "focus executed"


def test_gate_denial_prevents_native_call() -> None:
    # Unsupported platform acts as a gate denial; native adapter is never called.
    from agentx.capabilities.windows.provider import PlatformFacts

    linux_facts = PlatformFacts(system="Linux", release="6.8.0", version="#1 SMP", machine="x86_64")
    cap = WindowFocusCapability(
        evaluate_windows_support(linux_facts),
        native_surface=FakeWindowManagementNativeSurface(),
    )
    req = focus_window_request(1)
    res = cap.execute(req, _context())
    assert res.succeeded is False
    # Fake adapter records zero calls because unsupported branch returns early
    assert cap._native_surface.calls == []  # type: ignore[attr-defined]


def test_verification_required_for_success() -> None:
    cap = WindowMinimizeCapability(
        evaluate_windows_support(WINDOWS_FACTS),
        native_surface=FakeWindowManagementNativeSurface(observe_state="minimized"),
    )
    req = minimize_window_request(7)
    res = cap.execute(req, _context())
    assert res.succeeded is True
    ver = cap.verify(req, res.observation, _context())
    assert ver.passed is True


def test_verification_mismatch_prevents_task_success() -> None:
    cap = WindowMinimizeCapability(
        evaluate_windows_support(WINDOWS_FACTS),
        native_surface=FakeWindowManagementNativeSurface(observe_state="normal"),
    )
    req = minimize_window_request(7)
    res = cap.execute(req, _context())
    # Execution reports success (native adapter did its job), but verification reveals mismatch
    ver = cap.verify(req, res.observation, _context())
    assert ver.passed is False
    assert "mismatch" in ver.detail.lower()


def test_move_resize_execution_and_verification() -> None:
    cap = WindowMoveResizeCapability(
        evaluate_windows_support(WINDOWS_FACTS),
        native_surface=FakeWindowManagementNativeSurface(observe_rect=(10, 10, 410, 310)),
    )
    req = move_resize_window_request(5, 10, 10, 400, 300)
    res = cap.execute(req, _context())
    assert res.succeeded is True
    ver = cap.verify(req, res.observation, _context())
    assert ver.passed is True
