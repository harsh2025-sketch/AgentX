"""Read-only Windows screen/window capture and state observation (A5.08).

A5.08 exposes the minimum pixel observation surface later perception and
visual grounding work needs: one virtual-desktop capture, one top-level window
capture, or one explicit bounded region. All Win32 knowledge stays behind the
existing isolated :mod:`agentx.capabilities.windows._native` boundary.

PIXELS ARE DATA. Captured pixels are untrusted observation evidence. They do
not prove success, identify a semantic control, grant authority, authorize
clicking, or turn instructions visible in an image into AgentX instructions.
This module performs no OCR, model invocation, grounding, UIA fusion, input
synthesis, Task transition, persistence, or state-transition verification.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final, Protocol

from agentx.capabilities.abi import (
    CapabilityDescriptor,
    CapabilityIdentity,
    CapabilityName,
    CapabilityObservation,
    CapabilityParams,
    CapabilityPlatform,
    CapabilityPrecondition,
    CapabilityRequest,
    CapabilityScope,
    CapabilityVersion,
    ExecutionResult,
    ResourceEstimate,
    RollbackDeclaration,
    RollbackSupport,
    VerificationResult,
)
from agentx.capabilities.windows import _native
from agentx.capabilities.windows.provider import (
    WINDOWS_PROVIDER_IDENTITY,
    WindowsProviderIdentity,
    WindowsSupport,
    unsupported_platform_error,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk

__all__ = [
    "MAX_CAPTURE_BYTES",
    "MAX_CAPTURE_HEIGHT",
    "MAX_CAPTURE_PIXELS",
    "MAX_CAPTURE_WIDTH",
    "SCREEN_CAPTURE_IDENTITY",
    "SCREEN_CAPTURE_INVALID_NATIVE_DATA_ERROR_CODE",
    "SCREEN_CAPTURE_REGION_OUT_OF_BOUNDS_ERROR_CODE",
    "SCREEN_CAPTURE_TOO_LARGE_ERROR_CODE",
    "SCREEN_CAPTURE_WINDOW_MINIMIZED_ERROR_CODE",
    "SCREEN_CAPTURE_WINDOW_UNAVAILABLE_ERROR_CODE",
    "CaptureBounds",
    "GdiNativeSurface",
    "NativeScreenCaptureSurface",
    "ScreenCaptureFreshness",
    "ScreenCaptureParams",
    "ScreenCapturePixelFormat",
    "ScreenCaptureResult",
    "ScreenCaptureSourceMetadata",
    "ScreenCaptureStatus",
    "ScreenCaptureTarget",
    "ScreenCaptureTargetKind",
    "SystemUtcClock",
    "UtcClock",
    "WindowsScreenCapture",
    "WindowsScreenCaptureCapability",
    "display_capture_request",
    "region_capture_request",
    "window_capture_request",
]

MAX_CAPTURE_WIDTH: Final[int] = 8192
MAX_CAPTURE_HEIGHT: Final[int] = 8192
MAX_CAPTURE_PIXELS: Final[int] = 8_388_608
_BYTES_PER_PIXEL: Final[int] = 4
MAX_CAPTURE_BYTES: Final[int] = MAX_CAPTURE_PIXELS * _BYTES_PER_PIXEL
_MAX_COORDINATE_ABS: Final[int] = 1_000_000
_SOURCE_ADAPTER: Final[str] = "windows.native.gdi.read_only"

SCREEN_CAPTURE_TOO_LARGE_ERROR_CODE: Final[str] = (
    "capabilities.windows.screen_capture.capture_too_large"
)
SCREEN_CAPTURE_REGION_OUT_OF_BOUNDS_ERROR_CODE: Final[str] = (
    "capabilities.windows.screen_capture.region_out_of_bounds"
)
SCREEN_CAPTURE_WINDOW_UNAVAILABLE_ERROR_CODE: Final[str] = (
    "capabilities.windows.screen_capture.window_unavailable"
)
SCREEN_CAPTURE_WINDOW_MINIMIZED_ERROR_CODE: Final[str] = (
    "capabilities.windows.screen_capture.window_minimized"
)
SCREEN_CAPTURE_INVALID_NATIVE_DATA_ERROR_CODE: Final[str] = (
    "capabilities.windows.screen_capture.invalid_native_data"
)


def _require_int(value: object, *, field_name: str, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}, got {value}")
    return value


def _validate_coordinate(value: object, *, field_name: str) -> int:
    coordinate = _require_int(value, field_name=field_name)
    if abs(coordinate) > _MAX_COORDINATE_ABS:
        raise ValueError(f"{field_name} exceeds the supported coordinate bound")
    return coordinate


def _validate_short_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty and trimmed")
    if len(value) > 256:
        raise ValueError(f"{field_name} must not exceed 256 characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


@dataclass(frozen=True, slots=True)
class CaptureBounds:
    """Validated rectangle in Windows virtual-desktop coordinates."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        _validate_coordinate(self.x, field_name="bounds.x")
        _validate_coordinate(self.y, field_name="bounds.y")
        width = _require_int(self.width, field_name="bounds.width", minimum=1)
        height = _require_int(self.height, field_name="bounds.height", minimum=1)
        if width > MAX_CAPTURE_WIDTH:
            raise ValueError(f"bounds.width must not exceed {MAX_CAPTURE_WIDTH}")
        if height > MAX_CAPTURE_HEIGHT:
            raise ValueError(f"bounds.height must not exceed {MAX_CAPTURE_HEIGHT}")
        if width * height > MAX_CAPTURE_PIXELS:
            raise ValueError(f"capture area must not exceed {MAX_CAPTURE_PIXELS} pixels")

    @property
    def pixel_count(self) -> int:
        return self.width * self.height

    @property
    def byte_count(self) -> int:
        return self.pixel_count * _BYTES_PER_PIXEL

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def to_dict(self) -> dict[str, JsonValue]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


class ScreenCaptureTargetKind(StrEnum):
    DISPLAY = "display"
    WINDOW = "window"
    REGION = "region"


@dataclass(frozen=True, slots=True)
class ScreenCaptureTarget:
    """Typed ephemeral identity of one requested pixel source."""

    kind: ScreenCaptureTargetKind
    window_handle: int | None = None
    region: CaptureBounds | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ScreenCaptureTargetKind):
            raise TypeError("target.kind must be a ScreenCaptureTargetKind")
        if self.kind is ScreenCaptureTargetKind.DISPLAY:
            if self.window_handle is not None or self.region is not None:
                raise ValueError("display target cannot carry window_handle or region")
        elif self.kind is ScreenCaptureTargetKind.WINDOW:
            _require_int(self.window_handle, field_name="target.window_handle", minimum=1)
            if self.region is not None:
                raise ValueError("window target cannot carry a region")
        else:
            if self.window_handle is not None:
                raise ValueError("region target cannot carry a window_handle")
            if not isinstance(self.region, CaptureBounds):
                raise TypeError("region target requires CaptureBounds")

    @classmethod
    def display(cls) -> ScreenCaptureTarget:
        return cls(kind=ScreenCaptureTargetKind.DISPLAY)

    @classmethod
    def window(cls, window_handle: int) -> ScreenCaptureTarget:
        return cls(kind=ScreenCaptureTargetKind.WINDOW, window_handle=window_handle)

    @classmethod
    def bounded_region(cls, bounds: CaptureBounds) -> ScreenCaptureTarget:
        return cls(kind=ScreenCaptureTargetKind.REGION, region=bounds)

    @property
    def identity(self) -> str:
        if self.kind is ScreenCaptureTargetKind.DISPLAY:
            return "display:virtual-desktop"
        if self.kind is ScreenCaptureTargetKind.WINDOW:
            return f"window:{self.window_handle}"
        assert self.region is not None
        return (
            f"region:{self.region.x},{self.region.y},"
            f"{self.region.width}x{self.region.height}"
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": self.kind.value,
            "identity": self.identity,
            "window_handle": self.window_handle,
            "region": self.region.to_dict() if self.region is not None else None,
        }


class ScreenCapturePixelFormat(StrEnum):
    BGRA8 = "bgra8"


class ScreenCaptureStatus(StrEnum):
    CAPTURED = "captured"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class ScreenCaptureFreshness(StrEnum):
    FRESH = "fresh"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ScreenCaptureSourceMetadata:
    """Deterministic, non-authoritative capture provenance."""

    provider: WindowsProviderIdentity
    adapter: str
    target_identity: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider, WindowsProviderIdentity):
            raise TypeError("source.provider must be a WindowsProviderIdentity")
        _validate_short_text(self.adapter, field_name="source.adapter")
        _validate_short_text(self.target_identity, field_name="source.target_identity")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "provider": str(self.provider),
            "adapter": self.adapter,
            "target_identity": self.target_identity,
        }


@dataclass(frozen=True, slots=True)
class ScreenCaptureResult:
    """Bounded pixels plus metadata, or an explicit unavailable/error state.

    ``FRESH`` means only that pixels were read during this invocation rather
    than loaded from a cache. A5.08 keeps no screenshot history and makes no
    claim that pixels remain current after ``captured_at``.
    """

    status: ScreenCaptureStatus
    target: ScreenCaptureTarget
    bounds: CaptureBounds | None
    pixel_width: int | None
    pixel_height: int | None
    pixel_format: ScreenCapturePixelFormat | None
    pixels: bytes | None
    captured_at: datetime | None
    freshness: ScreenCaptureFreshness
    source: ScreenCaptureSourceMetadata
    error: AgentXError | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ScreenCaptureStatus):
            raise TypeError("result.status must be a ScreenCaptureStatus")
        if not isinstance(self.target, ScreenCaptureTarget):
            raise TypeError("result.target must be a ScreenCaptureTarget")
        if self.bounds is not None and not isinstance(self.bounds, CaptureBounds):
            raise TypeError("result.bounds must be CaptureBounds or None")
        if not isinstance(self.freshness, ScreenCaptureFreshness):
            raise TypeError("result.freshness must be a ScreenCaptureFreshness")
        if not isinstance(self.source, ScreenCaptureSourceMetadata):
            raise TypeError("result.source must be ScreenCaptureSourceMetadata")
        if self.source.target_identity != self.target.identity:
            raise ValueError("source.target_identity must match the result target")
        if self.status is ScreenCaptureStatus.CAPTURED:
            if not isinstance(self.bounds, CaptureBounds):
                raise ValueError("captured result requires bounds")
            width = _require_int(self.pixel_width, field_name="result.pixel_width", minimum=1)
            height = _require_int(self.pixel_height, field_name="result.pixel_height", minimum=1)
            if width != self.bounds.width or height != self.bounds.height:
                raise ValueError("captured pixel dimensions must match bounds")
            if self.pixel_format is not ScreenCapturePixelFormat.BGRA8:
                raise ValueError("captured result must use BGRA8")
            if not isinstance(self.pixels, bytes):
                raise TypeError("captured result requires bytes pixels")
            if len(self.pixels) != self.bounds.byte_count:
                raise ValueError("captured pixel byte length does not match dimensions")
            if not isinstance(self.captured_at, datetime):
                raise TypeError("captured result requires captured_at datetime")
            if self.captured_at.tzinfo is None or self.captured_at.utcoffset() != timedelta(0):
                raise ValueError("captured_at must be timezone-aware UTC")
            if self.freshness is not ScreenCaptureFreshness.FRESH:
                raise ValueError("captured result must be marked fresh")
            if self.error is not None:
                raise ValueError("captured result cannot carry an error")
        else:
            if self.pixel_width is not None or self.pixel_height is not None:
                raise ValueError("failed result cannot claim pixel dimensions")
            if self.pixel_format is not None or self.pixels is not None:
                raise ValueError("failed result cannot carry pixel data or format")
            if self.captured_at is not None:
                raise ValueError("failed result cannot claim a capture timestamp")
            if self.freshness is not ScreenCaptureFreshness.UNAVAILABLE:
                raise ValueError("failed result must mark freshness unavailable")
            if not isinstance(self.error, AgentXError):
                raise TypeError("failed result requires an AgentXError")

    @property
    def is_captured(self) -> bool:
        return self.status is ScreenCaptureStatus.CAPTURED

    def metadata_dict(self) -> dict[str, JsonValue]:
        return {
            "status": self.status.value,
            "target": self.target.to_dict(),
            "bounds": self.bounds.to_dict() if self.bounds is not None else None,
            "pixel_width": self.pixel_width,
            "pixel_height": self.pixel_height,
            "pixel_format": self.pixel_format.value if self.pixel_format is not None else None,
            "captured_at": self.captured_at.isoformat() if self.captured_at is not None else None,
            "freshness": self.freshness.value,
            "source": self.source.to_dict(),
            "error": self.error.to_dict() if self.error is not None else None,
        }

    def observation_dict(self) -> dict[str, JsonValue]:
        """Serialize bounded bytes as base64 for canonical JSON observations."""
        data = self.metadata_dict()
        data["pixels_base64"] = (
            base64.b64encode(self.pixels).decode("ascii") if self.pixels is not None else None
        )
        data["pixel_byte_count"] = len(self.pixels) if self.pixels is not None else 0
        return data


class UtcClock(Protocol):
    def now_utc(self) -> datetime:
        ...


class SystemUtcClock:
    __slots__ = ()

    def now_utc(self) -> datetime:
        return datetime.now(UTC)


class NativeScreenCaptureSurface(Protocol):
    """Mockable read-only seam; the default adapter delegates to ``_native``."""

    def virtual_screen_bounds(self) -> Result[_native.RawCaptureBounds, AgentXError]:
        ...

    def window_capture_info(
        self, window_handle: int
    ) -> Result[_native.RawWindowCaptureInfo, AgentXError]:
        ...

    def capture_bgra(
        self,
        bounds: _native.RawCaptureBounds,
        *,
        window_handle: int | None = None,
    ) -> Result[bytes, AgentXError]:
        ...


class GdiNativeSurface:
    __slots__ = ()

    def virtual_screen_bounds(self) -> Result[_native.RawCaptureBounds, AgentXError]:
        return _native.query_virtual_screen_bounds_raw()

    def window_capture_info(
        self, window_handle: int
    ) -> Result[_native.RawWindowCaptureInfo, AgentXError]:
        return _native.query_window_capture_info_raw(window_handle)

    def capture_bgra(
        self,
        bounds: _native.RawCaptureBounds,
        *,
        window_handle: int | None = None,
    ) -> Result[bytes, AgentXError]:
        return _native.capture_bgra_raw(bounds, window_handle=window_handle)


def _capture_error(
    *,
    code: str,
    message: str,
    category: ErrorCategory,
    retryability: Retryability,
    details: dict[str, Any],
) -> AgentXError:
    return AgentXError(
        code=code,
        message=message,
        category=category,
        retryability=retryability,
        details=details,
    )


def _source_for(target: ScreenCaptureTarget) -> ScreenCaptureSourceMetadata:
    return ScreenCaptureSourceMetadata(
        provider=WINDOWS_PROVIDER_IDENTITY,
        adapter=_SOURCE_ADAPTER,
        target_identity=target.identity,
    )


def _failed_result(
    *,
    target: ScreenCaptureTarget,
    status: ScreenCaptureStatus,
    error: AgentXError,
    bounds: CaptureBounds | None = None,
) -> ScreenCaptureResult:
    return ScreenCaptureResult(
        status=status,
        target=target,
        bounds=bounds,
        pixel_width=None,
        pixel_height=None,
        pixel_format=None,
        pixels=None,
        captured_at=None,
        freshness=ScreenCaptureFreshness.UNAVAILABLE,
        source=_source_for(target),
        error=error,
    )


def _native_bounds_problem(bounds: object) -> str | None:
    if not isinstance(bounds, _native.RawCaptureBounds):
        return "native capture bounds have the wrong type"
    if type(bounds.x) is not int or type(bounds.y) is not int:
        return "native capture coordinates must be integers"
    if abs(bounds.x) > _MAX_COORDINATE_ABS or abs(bounds.y) > _MAX_COORDINATE_ABS:
        return "native capture coordinates exceed the supported bound"
    if type(bounds.width) is not int or type(bounds.height) is not int:
        return "native capture dimensions must be integers"
    if bounds.width <= 0 or bounds.height <= 0:
        return "native capture dimensions must be positive"
    return None


def _fits_limits(bounds: _native.RawCaptureBounds) -> bool:
    return (
        bounds.width <= MAX_CAPTURE_WIDTH
        and bounds.height <= MAX_CAPTURE_HEIGHT
        and bounds.width * bounds.height <= MAX_CAPTURE_PIXELS
    )


def _canonical_bounds(raw: _native.RawCaptureBounds) -> CaptureBounds:
    return CaptureBounds(x=raw.x, y=raw.y, width=raw.width, height=raw.height)


def _contains(outer: CaptureBounds, inner: CaptureBounds) -> bool:
    return (
        inner.x >= outer.x
        and inner.y >= outer.y
        and inner.right <= outer.right
        and inner.bottom <= outer.bottom
    )


class WindowsScreenCapture:
    """One-shot, read-only screen/window pixel observation operation."""

    __slots__ = ("_clock", "_native_surface", "_support")

    _support: WindowsSupport
    _native_surface: NativeScreenCaptureSurface
    _clock: UtcClock

    def __init__(
        self,
        support: WindowsSupport,
        *,
        native_surface: NativeScreenCaptureSurface | None = None,
        clock: UtcClock | None = None,
    ) -> None:
        if not isinstance(support, WindowsSupport):
            raise TypeError("support must be a WindowsSupport")
        object.__setattr__(self, "_support", support)
        object.__setattr__(
            self,
            "_native_surface",
            native_surface if native_surface is not None else GdiNativeSurface(),
        )
        object.__setattr__(self, "_clock", clock if clock is not None else SystemUtcClock())

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"WindowsScreenCapture is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"WindowsScreenCapture is immutable; cannot delete {name!r}")

    @property
    def provider_identity(self) -> WindowsProviderIdentity:
        return WINDOWS_PROVIDER_IDENTITY

    def capture(self, target: ScreenCaptureTarget) -> ScreenCaptureResult:
        """Perform exactly one bounded read and return typed pixel evidence."""
        if not isinstance(target, ScreenCaptureTarget):
            raise TypeError("target must be a ScreenCaptureTarget")
        if not self._support.is_supported:
            return _failed_result(
                target=target,
                status=ScreenCaptureStatus.UNAVAILABLE,
                error=unsupported_platform_error(self._support),
            )

        selected_raw: _native.RawCaptureBounds
        window_handle: int | None = None

        if target.kind is ScreenCaptureTargetKind.DISPLAY:
            raw_result = self._native_surface.virtual_screen_bounds()
            if raw_result.is_failure:
                return self._native_failure(target, raw_result.unwrap_error())
            selected_raw = raw_result.unwrap()
        elif target.kind is ScreenCaptureTargetKind.WINDOW:
            assert target.window_handle is not None
            window_handle = target.window_handle
            info_result = self._native_surface.window_capture_info(window_handle)
            if info_result.is_failure:
                return self._native_failure(target, info_result.unwrap_error())
            info = info_result.unwrap()
            if not info.exists:
                return _failed_result(
                    target=target,
                    status=ScreenCaptureStatus.UNAVAILABLE,
                    error=_capture_error(
                        code=SCREEN_CAPTURE_WINDOW_UNAVAILABLE_ERROR_CODE,
                        message=f"window {window_handle} is unavailable or disappeared",
                        category=ErrorCategory.NOT_FOUND,
                        retryability=Retryability.NON_RETRYABLE,
                        details={"window_handle": window_handle},
                    ),
                )
            if info.minimized:
                return _failed_result(
                    target=target,
                    status=ScreenCaptureStatus.UNAVAILABLE,
                    error=_capture_error(
                        code=SCREEN_CAPTURE_WINDOW_MINIMIZED_ERROR_CODE,
                        message=f"window {window_handle} is minimized and unavailable for capture",
                        category=ErrorCategory.PRECONDITION,
                        retryability=Retryability.NON_RETRYABLE,
                        details={"window_handle": window_handle},
                    ),
                )
            if info.bounds is None:
                return _failed_result(
                    target=target,
                    status=ScreenCaptureStatus.ERROR,
                    error=_capture_error(
                        code=SCREEN_CAPTURE_INVALID_NATIVE_DATA_ERROR_CODE,
                        message=f"window {window_handle} did not provide valid capture bounds",
                        category=ErrorCategory.EXECUTION,
                        retryability=Retryability.UNKNOWN,
                        details={
                            "window_handle": window_handle,
                            "native_error": info.error_code,
                        },
                    ),
                )
            selected_raw = info.bounds
        else:
            assert target.region is not None
            screen_result = self._native_surface.virtual_screen_bounds()
            if screen_result.is_failure:
                return self._native_failure(target, screen_result.unwrap_error())
            screen_raw = screen_result.unwrap()
            problem = _native_bounds_problem(screen_raw)
            if problem is not None:
                return self._invalid_native_bounds(target, problem)
            screen_bounds = _canonical_bounds(screen_raw)
            if not _contains(screen_bounds, target.region):
                return _failed_result(
                    target=target,
                    status=ScreenCaptureStatus.ERROR,
                    error=_capture_error(
                        code=SCREEN_CAPTURE_REGION_OUT_OF_BOUNDS_ERROR_CODE,
                        message="requested capture region is outside the virtual desktop",
                        category=ErrorCategory.VALIDATION,
                        retryability=Retryability.NON_RETRYABLE,
                        details={"target_identity": target.identity},
                    ),
                    bounds=target.region,
                )
            selected_raw = _native.RawCaptureBounds(
                x=target.region.x,
                y=target.region.y,
                width=target.region.width,
                height=target.region.height,
            )

        problem = _native_bounds_problem(selected_raw)
        if problem is not None:
            return self._invalid_native_bounds(target, problem)
        if not _fits_limits(selected_raw):
            return _failed_result(
                target=target,
                status=ScreenCaptureStatus.ERROR,
                error=_capture_error(
                    code=SCREEN_CAPTURE_TOO_LARGE_ERROR_CODE,
                    message="capture dimensions exceed the bounded A5.08 resource limits",
                    category=ErrorCategory.RESOURCE,
                    retryability=Retryability.NON_RETRYABLE,
                    details={
                        "width": selected_raw.width,
                        "height": selected_raw.height,
                        "max_width": MAX_CAPTURE_WIDTH,
                        "max_height": MAX_CAPTURE_HEIGHT,
                        "max_pixels": MAX_CAPTURE_PIXELS,
                        "max_bytes": MAX_CAPTURE_BYTES,
                    },
                ),
            )

        bounds = _canonical_bounds(selected_raw)
        pixels_result = self._native_surface.capture_bgra(
            selected_raw,
            window_handle=window_handle,
        )
        if pixels_result.is_failure:
            return self._native_failure(target, pixels_result.unwrap_error(), bounds=bounds)
        pixels = pixels_result.unwrap()
        if not isinstance(pixels, bytes) or len(pixels) != bounds.byte_count:
            return _failed_result(
                target=target,
                status=ScreenCaptureStatus.ERROR,
                error=_capture_error(
                    code=SCREEN_CAPTURE_INVALID_NATIVE_DATA_ERROR_CODE,
                    message="native capture returned an invalid pixel payload length",
                    category=ErrorCategory.EXECUTION,
                    retryability=Retryability.UNKNOWN,
                    details={
                        "expected_bytes": bounds.byte_count,
                        "actual_bytes": len(pixels) if isinstance(pixels, bytes) else -1,
                    },
                ),
                bounds=bounds,
            )

        captured_at = self._clock.now_utc()
        if not isinstance(captured_at, datetime):
            raise TypeError("clock.now_utc() must return datetime")
        if captured_at.tzinfo is None or captured_at.utcoffset() != timedelta(0):
            raise ValueError("clock.now_utc() must return timezone-aware UTC")
        return ScreenCaptureResult(
            status=ScreenCaptureStatus.CAPTURED,
            target=target,
            bounds=bounds,
            pixel_width=bounds.width,
            pixel_height=bounds.height,
            pixel_format=ScreenCapturePixelFormat.BGRA8,
            pixels=pixels,
            captured_at=captured_at,
            freshness=ScreenCaptureFreshness.FRESH,
            source=_source_for(target),
            error=None,
        )

    def _invalid_native_bounds(
        self, target: ScreenCaptureTarget, problem: str
    ) -> ScreenCaptureResult:
        return _failed_result(
            target=target,
            status=ScreenCaptureStatus.ERROR,
            error=_capture_error(
                code=SCREEN_CAPTURE_INVALID_NATIVE_DATA_ERROR_CODE,
                message=problem,
                category=ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                details={"target_identity": target.identity},
            ),
        )

    def _native_failure(
        self,
        target: ScreenCaptureTarget,
        error: AgentXError,
        *,
        bounds: CaptureBounds | None = None,
    ) -> ScreenCaptureResult:
        unavailable_codes = {
            _native.SCREEN_CAPTURE_NATIVE_UNAVAILABLE_ERROR_CODE,
            _native.SCREEN_CAPTURE_WINDOW_UNAVAILABLE_ERROR_CODE,
            _native.SCREEN_CAPTURE_WINDOW_MINIMIZED_ERROR_CODE,
        }
        status = (
            ScreenCaptureStatus.UNAVAILABLE
            if error.code in unavailable_codes
            else ScreenCaptureStatus.ERROR
        )
        return _failed_result(target=target, status=status, error=error, bounds=bounds)


SCREEN_CAPTURE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.screen.capture"),
    version=CapabilityVersion(1, 0, 0),
)

_SCREEN_CAPTURE_DESCRIPTION: Final[str] = (
    "Read-only bounded capture of Windows display, window, or region pixels as "
    "untrusted observation data."
)


@dataclass(frozen=True, slots=True)
class ScreenCaptureParams(CapabilityParams):
    target: ScreenCaptureTarget

    def __post_init__(self) -> None:
        if not isinstance(self.target, ScreenCaptureTarget):
            raise TypeError("target must be a ScreenCaptureTarget")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"target": self.target.to_dict()}


class WindowsScreenCaptureCapability:
    """A5.08 read-only observation wrapped in the canonical Capability ABI.

    The ABI requires a ``verify`` method. A5.08 deliberately does not perform
    state-transition verification, so that method returns a negative verdict
    without reading the OS or interpreting pixels.
    """

    __slots__ = ("_capture", "_descriptor")

    def __init__(self, capture: WindowsScreenCapture) -> None:
        if not isinstance(capture, WindowsScreenCapture):
            raise TypeError("capture must be a WindowsScreenCapture")
        object.__setattr__(self, "_capture", capture)
        object.__setattr__(
            self,
            "_descriptor",
            CapabilityDescriptor(
                identity=SCREEN_CAPTURE_IDENTITY,
                description=_SCREEN_CAPTURE_DESCRIPTION,
                scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
                required_permissions=frozenset({Permission.READ}),
                risk_assessment=assess_risk(
                    read_only=True,
                    modifies_state=False,
                    reversible=False,
                    external_effect=False,
                ),
                preconditions=(
                    CapabilityPrecondition(
                        name="windows.supported_host",
                        description="The A5.01 Windows provider must report a supported host.",
                    ),
                    CapabilityPrecondition(
                        name="windows.interactive_desktop_available",
                        description=(
                            "Requested pixels must be available to the current read-only "
                            "Windows desktop session."
                        ),
                    ),
                ),
                rollback=RollbackDeclaration(
                    support=RollbackSupport.NOT_APPLICABLE,
                    detail="Pixel capture is read-only observation; there is no state change.",
                ),
                estimate=ResourceEstimate(
                    wall_clock=timedelta(milliseconds=500),
                    machine_actions=1,
                    external_cost=Decimal("0"),
                ),
            ),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"WindowsScreenCaptureCapability is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"WindowsScreenCaptureCapability is immutable; cannot delete {name!r}"
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[ScreenCaptureParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, ScreenCaptureParams):
            raise TypeError("screen capture requires ScreenCaptureParams")
        if context.observe_stop().should_stop:
            return ExecutionResult(
                succeeded=False,
                message="screen capture was cooperatively cancelled before any pixel read",
                observation=CapabilityObservation(
                    summary="screen capture cancelled before native observation",
                    data={"cancelled_before_start": True},
                ),
            )
        result = self._capture.capture(request.params.target)
        if not result.is_captured:
            assert result.error is not None
            return ExecutionResult(
                succeeded=False,
                message=f"screen capture unavailable or failed: {result.error.message}",
                observation=CapabilityObservation(
                    summary="read-only Windows screen capture did not produce pixels",
                    data=result.observation_dict(),
                ),
            )
        return ExecutionResult(
            succeeded=True,
            message=(
                f"captured {result.pixel_width}x{result.pixel_height} BGRA8 pixels from "
                f"{result.target.identity}"
            ),
            observation=CapabilityObservation(
                summary="read-only Windows screen/window pixel observation",
                data=result.observation_dict(),
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[ScreenCaptureParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        """Refuse state verification: pixels are observation evidence only."""
        if not isinstance(request.params, ScreenCaptureParams):
            raise TypeError("screen capture verification requires ScreenCaptureParams")
        if not isinstance(observation, CapabilityObservation):
            raise TypeError("observation must be a CapabilityObservation")
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        return VerificationResult(
            passed=False,
            detail=(
                "A5.08 does not verify state transitions; captured pixels are observation "
                "data only."
            ),
        )


def display_capture_request() -> CapabilityRequest[ScreenCaptureParams]:
    return CapabilityRequest(
        identity=SCREEN_CAPTURE_IDENTITY,
        params=ScreenCaptureParams(target=ScreenCaptureTarget.display()),
    )


def window_capture_request(window_handle: int) -> CapabilityRequest[ScreenCaptureParams]:
    return CapabilityRequest(
        identity=SCREEN_CAPTURE_IDENTITY,
        params=ScreenCaptureParams(target=ScreenCaptureTarget.window(window_handle)),
    )


def region_capture_request(bounds: CaptureBounds) -> CapabilityRequest[ScreenCaptureParams]:
    return CapabilityRequest(
        identity=SCREEN_CAPTURE_IDENTITY,
        params=ScreenCaptureParams(target=ScreenCaptureTarget.bounded_region(bounds)),
    )
