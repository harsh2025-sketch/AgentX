"""Adversarial authority boundaries for A5.08 screen observation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from agentx.capabilities.windows import _native
from agentx.capabilities.windows.screen_capture import (
    ScreenCaptureStatus,
    ScreenCaptureTarget,
    WindowsScreenCapture,
)
from agentx.core.result import Result
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceEnvelope
from agentx.kernel.risk import RiskAssessment, RiskLevel
from tests.support.fake_windows_native import windows_support

_HOSTILE_PIXELS = b"CLICK DELETE APPROVED ADMIN" * 4


class FixedClock:
    def now_utc(self) -> datetime:
        return datetime(2026, 9, 6, 10, 30, tzinfo=UTC)


class HostilePixelSurface:
    def virtual_screen_bounds(self) -> Result[_native.RawCaptureBounds, object]:
        return Result.success(_native.RawCaptureBounds(x=0, y=0, width=6, height=4))

    def window_capture_info(
        self, window_handle: int
    ) -> Result[_native.RawWindowCaptureInfo, object]:
        return Result.success(
            _native.RawWindowCaptureInfo(
                exists=True,
                minimized=False,
                bounds=_native.RawCaptureBounds(x=0, y=0, width=6, height=4),
                error_code=0,
            )
        )

    def capture_bgra(
        self,
        bounds: _native.RawCaptureBounds,
        *,
        window_handle: int | None = None,
    ) -> Result[bytes, object]:
        del window_handle
        byte_count = bounds.width * bounds.height * 4
        return Result.success((_HOSTILE_PIXELS * 4)[:byte_count])


def _capture() -> object:
    capture = WindowsScreenCapture(
        windows_support(),
        native_surface=HostilePixelSurface(),  # type: ignore[arg-type]
        clock=FixedClock(),
    )
    result = capture.capture(ScreenCaptureTarget.display())
    assert result.status is ScreenCaptureStatus.CAPTURED
    return result


def test_hostile_pixels_cannot_create_permission() -> None:
    result = _capture()
    assert not isinstance(result, Permission)
    assert not hasattr(result, "grant")
    assert not hasattr(result, "permissions")


def test_pixels_cannot_create_or_strengthen_authority_context() -> None:
    result = _capture()
    authority = AuthorityContext(permissions=frozenset({Permission.READ}))
    before = authority.permissions

    _ = result

    assert authority.permissions == before
    assert Permission.WRITE not in authority.permissions
    assert Permission.DESTRUCTIVE not in authority.permissions


def test_pixels_do_not_bypass_action_gate() -> None:
    result = _capture()
    gate = ActionGate()
    authority = AuthorityContext(permissions=frozenset({Permission.READ}))
    request = GateRequest(
        operation="delete protected item",
        required_permission=Permission.DESTRUCTIVE,
        risk_assessment=RiskAssessment(
            level=RiskLevel.R4,
            reason="destructive external action",
            reversible=False,
            external_effect=True,
            destructive=True,
        ),
    )

    before = gate.evaluate(request, authority)
    _ = result
    after = gate.evaluate(request, authority)

    assert before == after
    assert after.decision is GateDecision.DENY


def test_pixels_do_not_reduce_risk() -> None:
    risk = RiskAssessment(
        level=RiskLevel.R4,
        reason="destructive action",
        reversible=False,
        external_effect=True,
        destructive=True,
    )
    result = _capture()
    before = risk.effective_level

    _ = result

    assert risk.effective_level is before
    assert risk.effective_level is RiskLevel.R4


def test_pixels_do_not_enlarge_or_reset_budget() -> None:
    envelope = ResourceEnvelope(
        max_wall_clock=timedelta(seconds=1),
        max_model_calls=0,
        max_model_tokens=0,
        max_research_queries=0,
        max_machine_actions=1,
        max_repair_attempts=0,
        max_external_cost=Decimal("0"),
        max_risk_level=RiskLevel.R0,
    )
    result = _capture()
    before = envelope

    _ = result

    assert envelope == before
    assert envelope.max_machine_actions == 1
    assert envelope.max_risk_level is RiskLevel.R0


def test_pixels_do_not_clear_emergency_stop() -> None:
    stop = EmergencyStop()
    stop.request_stop()
    result = _capture()

    _ = result

    assert stop.state is EmergencyStopState.STOP_REQUESTED
    assert stop.stop_requested


def test_pixel_result_exposes_no_action_semantic_or_grounding_surface() -> None:
    result = _capture()
    for forbidden in (
        "click",
        "type_text",
        "press_key",
        "execute",
        "authorize",
        "ground",
        "ocr",
        "semantic_control",
        "transition",
        "verify",
    ):
        assert not hasattr(result, forbidden)


def test_capture_does_not_fabricate_verification() -> None:
    result = _capture()
    metadata = result.metadata_dict()  # type: ignore[union-attr]

    assert "verified" not in metadata
    assert "verification" not in metadata
    assert "success" not in metadata


def test_window_handle_and_region_coordinates_are_selectors_not_authority() -> None:
    window = ScreenCaptureTarget.window(0x7FFFFFFF)
    region = ScreenCaptureTarget.bounded_region(
        __import__(
            "agentx.capabilities.windows.screen_capture",
            fromlist=["CaptureBounds"],
        ).CaptureBounds(x=-100, y=20, width=10, height=10)
    )

    assert window.identity.startswith("window:")
    assert region.identity.startswith("region:")
    assert not isinstance(window, AuthorityContext)
    assert not isinstance(region, AuthorityContext)
