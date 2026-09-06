"""Unit coverage for A5.08 read-only Windows pixel observation."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agentx.capabilities.windows import _native
from agentx.capabilities.windows.screen_capture import (
    MAX_CAPTURE_HEIGHT,
    MAX_CAPTURE_PIXELS,
    MAX_CAPTURE_WIDTH,
    SCREEN_CAPTURE_IDENTITY,
    SCREEN_CAPTURE_INVALID_NATIVE_DATA_ERROR_CODE,
    SCREEN_CAPTURE_REGION_OUT_OF_BOUNDS_ERROR_CODE,
    SCREEN_CAPTURE_TOO_LARGE_ERROR_CODE,
    SCREEN_CAPTURE_WINDOW_MINIMIZED_ERROR_CODE,
    SCREEN_CAPTURE_WINDOW_UNAVAILABLE_ERROR_CODE,
    CaptureBounds,
    ScreenCaptureFreshness,
    ScreenCapturePixelFormat,
    ScreenCaptureStatus,
    ScreenCaptureTarget,
    ScreenCaptureTargetKind,
    WindowsScreenCapture,
    WindowsScreenCaptureCapability,
    display_capture_request,
    region_capture_request,
    window_capture_request,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskLevel
from tests.support.fake_windows_native import linux_support, windows_support

_FIXED_TIME = datetime(2026, 9, 6, 10, 30, tzinfo=UTC)


class FixedClock:
    def __init__(self, value: datetime = _FIXED_TIME) -> None:
        self.value = value
        self.calls = 0

    def now_utc(self) -> datetime:
        self.calls += 1
        return self.value


class FakeSurface:
    def __init__(self) -> None:
        self.screen = _native.RawCaptureBounds(x=-100, y=0, width=200, height=100)
        self.window = _native.RawWindowCaptureInfo(
            exists=True,
            minimized=False,
            bounds=_native.RawCaptureBounds(x=10, y=20, width=3, height=2),
            error_code=0,
        )
        self.fail_screen: AgentXError | None = None
        self.fail_window: AgentXError | None = None
        self.fail_capture: AgentXError | None = None
        self.pixel_seed = b"\x01\x02\x03\x04"
        self.calls: list[tuple[str, object]] = []

    def virtual_screen_bounds(self) -> Result[_native.RawCaptureBounds, AgentXError]:
        self.calls.append(("screen", None))
        if self.fail_screen is not None:
            return Result[_native.RawCaptureBounds, AgentXError].failure(self.fail_screen)
        return Result.success(self.screen)

    def window_capture_info(
        self, window_handle: int
    ) -> Result[_native.RawWindowCaptureInfo, AgentXError]:
        self.calls.append(("window", window_handle))
        if self.fail_window is not None:
            return Result[_native.RawWindowCaptureInfo, AgentXError].failure(self.fail_window)
        return Result.success(self.window)

    def capture_bgra(
        self,
        bounds: _native.RawCaptureBounds,
        *,
        window_handle: int | None = None,
    ) -> Result[bytes, AgentXError]:
        self.calls.append(("capture", (bounds, window_handle)))
        if self.fail_capture is not None:
            return Result[bytes, AgentXError].failure(self.fail_capture)
        return Result.success(self.pixel_seed * (bounds.width * bounds.height))


def _error(code: str = "test.native_failure") -> AgentXError:
    return AgentXError(
        code=code,
        message="native read failed",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
    )


def _capture(fake: FakeSurface | None = None) -> tuple[WindowsScreenCapture, FakeSurface, FixedClock]:
    surface = FakeSurface() if fake is None else fake
    clock = FixedClock()
    return (
        WindowsScreenCapture(windows_support(), native_surface=surface, clock=clock),
        surface,
        clock,
    )


def _context(*, cancelled: bool = False) -> ExecutionContext:
    source = CancellationSource()
    if cancelled:
        source.request_cancellation("test cancellation")
    return ExecutionContext(correlation_id=uuid4(), cancellation_token=source.token)


def test_target_vocabulary_and_identity_are_closed_and_deterministic() -> None:
    region = CaptureBounds(x=-10, y=4, width=20, height=30)
    display = ScreenCaptureTarget.display()
    window = ScreenCaptureTarget.window(1234)
    bounded = ScreenCaptureTarget.bounded_region(region)

    assert tuple(ScreenCaptureTargetKind) == (
        ScreenCaptureTargetKind.DISPLAY,
        ScreenCaptureTargetKind.WINDOW,
        ScreenCaptureTargetKind.REGION,
    )
    assert display.identity == "display:virtual-desktop"
    assert window.identity == "window:1234"
    assert bounded.identity == "region:-10,4,20x30"


def test_target_shapes_reject_ambiguous_or_invalid_selectors() -> None:
    with pytest.raises(ValueError):
        ScreenCaptureTarget(kind=ScreenCaptureTargetKind.DISPLAY, window_handle=1)
    with pytest.raises((TypeError, ValueError)):
        ScreenCaptureTarget.window(0)
    with pytest.raises(TypeError):
        ScreenCaptureTarget(kind=ScreenCaptureTargetKind.REGION)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"x": 0, "y": 0, "width": 0, "height": 1}, ValueError),
        ({"x": 0, "y": 0, "width": 1, "height": 0}, ValueError),
        ({"x": True, "y": 0, "width": 1, "height": 1}, TypeError),
        ({"x": 0, "y": 0, "width": True, "height": 1}, TypeError),
        ({"x": 1_000_001, "y": 0, "width": 1, "height": 1}, ValueError),
    ],
)
def test_region_validation_rejects_invalid_bounds(
    kwargs: dict[str, object], error: type[Exception]
) -> None:
    with pytest.raises(error):
        CaptureBounds(**kwargs)  # type: ignore[arg-type]


def test_region_validation_rejects_absurd_dimensions_and_area() -> None:
    with pytest.raises(ValueError):
        CaptureBounds(x=0, y=0, width=MAX_CAPTURE_WIDTH + 1, height=1)
    with pytest.raises(ValueError):
        CaptureBounds(x=0, y=0, width=1, height=MAX_CAPTURE_HEIGHT + 1)
    with pytest.raises(ValueError):
        CaptureBounds(x=0, y=0, width=4097, height=2048)
    assert 4097 * 2048 > MAX_CAPTURE_PIXELS


def test_display_capture_contract_includes_pixels_dimensions_time_and_freshness() -> None:
    capture, fake, clock = _capture()
    fake.screen = _native.RawCaptureBounds(x=-1, y=2, width=2, height=2)

    result = capture.capture(ScreenCaptureTarget.display())

    assert result.status is ScreenCaptureStatus.CAPTURED
    assert result.bounds == CaptureBounds(x=-1, y=2, width=2, height=2)
    assert result.pixel_width == 2
    assert result.pixel_height == 2
    assert result.pixel_format is ScreenCapturePixelFormat.BGRA8
    assert result.pixels == fake.pixel_seed * 4
    assert result.captured_at == _FIXED_TIME
    assert result.freshness is ScreenCaptureFreshness.FRESH
    assert result.source.adapter == "windows.native.gdi.read_only"
    assert result.source.target_identity == "display:virtual-desktop"
    assert clock.calls == 1


def test_window_capture_uses_exact_handle_and_window_bounds() -> None:
    capture, fake, _ = _capture()

    result = capture.capture(ScreenCaptureTarget.window(77))

    assert result.status is ScreenCaptureStatus.CAPTURED
    assert result.bounds == CaptureBounds(x=10, y=20, width=3, height=2)
    assert ("window", 77) in fake.calls
    capture_call = next(call for call in fake.calls if call[0] == "capture")
    assert capture_call[1] == (fake.window.bounds, 77)


def test_region_capture_is_bounded_by_current_virtual_desktop() -> None:
    capture, fake, _ = _capture()
    region = CaptureBounds(x=-20, y=10, width=40, height=20)

    result = capture.capture(ScreenCaptureTarget.bounded_region(region))

    assert result.status is ScreenCaptureStatus.CAPTURED
    assert result.bounds == region
    assert fake.calls[0] == ("screen", None)


def test_region_outside_virtual_desktop_is_explicit_error_without_capture() -> None:
    capture, fake, _ = _capture()
    region = CaptureBounds(x=90, y=0, width=20, height=20)

    result = capture.capture(ScreenCaptureTarget.bounded_region(region))

    assert result.status is ScreenCaptureStatus.ERROR
    assert result.error is not None
    assert result.error.code == SCREEN_CAPTURE_REGION_OUT_OF_BOUNDS_ERROR_CODE
    assert result.bounds == region
    assert not any(call[0] == "capture" for call in fake.calls)


def test_oversized_dynamic_display_is_rejected_before_pixel_allocation() -> None:
    fake = FakeSurface()
    fake.screen = _native.RawCaptureBounds(x=0, y=0, width=8192, height=8192)
    capture, fake, clock = _capture(fake)

    result = capture.capture(ScreenCaptureTarget.display())

    assert result.status is ScreenCaptureStatus.ERROR
    assert result.error is not None
    assert result.error.code == SCREEN_CAPTURE_TOO_LARGE_ERROR_CODE
    assert not any(call[0] == "capture" for call in fake.calls)
    assert clock.calls == 0


def test_invalid_native_bounds_fail_closed_as_explicit_error() -> None:
    fake = FakeSurface()
    fake.screen = _native.RawCaptureBounds(x=0, y=0, width=-1, height=2)
    capture, _, _ = _capture(fake)

    result = capture.capture(ScreenCaptureTarget.display())

    assert result.status is ScreenCaptureStatus.ERROR
    assert result.error is not None
    assert result.error.code == SCREEN_CAPTURE_INVALID_NATIVE_DATA_ERROR_CODE


def test_unsupported_platform_is_explicit_and_performs_no_native_read() -> None:
    fake = FakeSurface()
    clock = FixedClock()
    capture = WindowsScreenCapture(linux_support(), native_surface=fake, clock=clock)

    result = capture.capture(ScreenCaptureTarget.display())

    assert result.status is ScreenCaptureStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.category is ErrorCategory.PRECONDITION
    assert fake.calls == []
    assert clock.calls == 0


def test_minimized_window_is_explicitly_unavailable() -> None:
    fake = FakeSurface()
    fake.window = _native.RawWindowCaptureInfo(
        exists=True, minimized=True, bounds=None, error_code=0
    )
    capture, fake, _ = _capture(fake)

    result = capture.capture(ScreenCaptureTarget.window(44))

    assert result.status is ScreenCaptureStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == SCREEN_CAPTURE_WINDOW_MINIMIZED_ERROR_CODE
    assert not any(call[0] == "capture" for call in fake.calls)


def test_disappearing_window_is_explicitly_unavailable() -> None:
    fake = FakeSurface()
    fake.window = _native.RawWindowCaptureInfo(
        exists=False, minimized=False, bounds=None, error_code=1400
    )
    capture, fake, _ = _capture(fake)

    result = capture.capture(ScreenCaptureTarget.window(55))

    assert result.status is ScreenCaptureStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == SCREEN_CAPTURE_WINDOW_UNAVAILABLE_ERROR_CODE
    assert not any(call[0] == "capture" for call in fake.calls)


def test_window_can_disappear_between_state_read_and_pixel_read() -> None:
    fake = FakeSurface()
    fake.fail_capture = _error(_native.SCREEN_CAPTURE_WINDOW_UNAVAILABLE_ERROR_CODE)
    capture, _, clock = _capture(fake)

    result = capture.capture(ScreenCaptureTarget.window(66))

    assert result.status is ScreenCaptureStatus.UNAVAILABLE
    assert result.error is not None
    assert result.error.code == _native.SCREEN_CAPTURE_WINDOW_UNAVAILABLE_ERROR_CODE
    assert clock.calls == 0


def test_native_failure_is_explicit_error_and_has_no_timestamp() -> None:
    fake = FakeSurface()
    fake.fail_capture = _error()
    capture, _, clock = _capture(fake)

    result = capture.capture(ScreenCaptureTarget.display())

    assert result.status is ScreenCaptureStatus.ERROR
    assert result.error is not None
    assert result.captured_at is None
    assert result.freshness is ScreenCaptureFreshness.UNAVAILABLE
    assert clock.calls == 0


def test_invalid_native_pixel_length_fails_closed() -> None:
    fake = FakeSurface()
    fake.pixel_seed = b"x"
    fake.screen = _native.RawCaptureBounds(x=0, y=0, width=2, height=2)
    capture, _, _ = _capture(fake)

    result = capture.capture(ScreenCaptureTarget.display())

    assert result.status is ScreenCaptureStatus.ERROR
    assert result.error is not None
    assert result.error.code == SCREEN_CAPTURE_INVALID_NATIVE_DATA_ERROR_CODE


def test_fixed_inputs_produce_deterministic_metadata() -> None:
    first, _, _ = _capture()
    second, _, _ = _capture()
    target = ScreenCaptureTarget.window(77)

    first_result = first.capture(target)
    second_result = second.capture(target)

    assert first_result.metadata_dict() == second_result.metadata_dict()


def test_pixels_serialize_as_bounded_data_without_interpretation() -> None:
    capture, fake, _ = _capture()
    fake.screen = _native.RawCaptureBounds(x=0, y=0, width=1, height=1)
    fake.pixel_seed = b"DOIT"

    result = capture.capture(ScreenCaptureTarget.display())
    observation = result.observation_dict()

    assert result.pixels == b"DOIT"
    assert observation["pixels_base64"] == base64.b64encode(b"DOIT").decode("ascii")
    assert observation["pixel_byte_count"] == 4
    assert "instruction" not in observation
    assert "action" not in observation


def test_capability_descriptor_is_read_only_r0_and_bounded() -> None:
    capture, _, _ = _capture()
    capability = WindowsScreenCaptureCapability(capture)
    descriptor = capability.descriptor

    assert descriptor.identity == SCREEN_CAPTURE_IDENTITY
    assert descriptor.required_permissions == frozenset({Permission.READ})
    assert descriptor.risk_assessment.effective_level is RiskLevel.R0
    assert descriptor.risk_assessment.read_only
    assert not descriptor.risk_assessment.modifies_state
    assert descriptor.estimate.machine_actions == 1


def test_request_helpers_keep_target_identity_typed() -> None:
    display = display_capture_request()
    window = window_capture_request(99)
    region = region_capture_request(CaptureBounds(x=1, y=2, width=3, height=4))

    assert display.identity == SCREEN_CAPTURE_IDENTITY
    assert display.params.target.kind is ScreenCaptureTargetKind.DISPLAY
    assert window.params.target.identity == "window:99"
    assert region.params.target.identity == "region:1,2,3x4"


def test_capability_execute_returns_observation_not_state_verification() -> None:
    capture, fake, _ = _capture()
    fake.screen = _native.RawCaptureBounds(x=0, y=0, width=1, height=1)
    capability = WindowsScreenCaptureCapability(capture)

    execution = capability.execute(display_capture_request(), _context())

    assert execution.succeeded
    assert execution.observation.data["status"] == "captured"
    assert execution.observation.data["freshness"] == "fresh"
    assert "verification" not in execution.observation.data


def test_capability_honors_cancellation_before_native_read() -> None:
    capture, fake, _ = _capture()
    capability = WindowsScreenCaptureCapability(capture)

    execution = capability.execute(display_capture_request(), _context(cancelled=True))

    assert not execution.succeeded
    assert fake.calls == []


def test_capability_verify_refuses_to_fabricate_state_success() -> None:
    capture, _, _ = _capture()
    capability = WindowsScreenCaptureCapability(capture)
    execution = capability.execute(display_capture_request(), _context())

    verdict = capability.verify(display_capture_request(), execution.observation, _context())

    assert not verdict.passed
    assert "does not verify state transitions" in verdict.detail
