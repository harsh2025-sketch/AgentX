"""Canonical Windows screen-capture observation provider for M10.

The provider is read-only. It exposes a typed immutable frame with stable frame
identity, display topology, DPI metadata and a content digest. The ordinary
Capability wrapper declares READ/R0/bounded resources so production composition
can route capture through the same ActionGate/Executor boundary as other machine
operations. Pixels remain untrusted data and never authorize an action.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Final, Protocol

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
from agentx.capabilities.windows import _screen_native
from agentx.capabilities.windows.provider import WindowsSupport, unsupported_platform_error
from agentx.core.errors import AgentXError
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk

__all__ = [
    "WINDOWS_SCREEN_CAPTURE_DESCRIPTION",
    "WINDOWS_SCREEN_CAPTURE_IDENTITY",
    "DisplayObservation",
    "NativeScreenSurface",
    "ScreenFrame",
    "ScreenFrameId",
    "ScreenRect",
    "Win32ScreenSurface",
    "WindowsScreenCapture",
    "WindowsScreenCaptureCapability",
    "WindowsScreenCaptureParams",
    "screen_capture_request",
]

_DEFAULT_MAX_PIXELS: Final[int] = 33_177_600
_MAX_PIXELS_LIMIT: Final[int] = 67_108_864
_MAX_DISPLAYS: Final[int] = 32


def _require_int(value: object, *, field_name: str, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    return value


def _require_text(value: object, *, field_name: str, max_length: int = 1024) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty and trimmed")
    if len(value) > max_length or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{field_name} is malformed")
    return value


@dataclass(frozen=True, slots=True, order=True)
class ScreenRect:
    """Virtual-desktop rectangle in physical pixels."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        _require_int(self.x, field_name="x")
        _require_int(self.y, field_name="y")
        _require_int(self.width, field_name="width", minimum=1)
        _require_int(self.height, field_name="height", minimum=1)
        if self.width * self.height > _MAX_PIXELS_LIMIT:
            raise ValueError("screen rectangle exceeds defensive pixel bound")

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def contains_point(self, x: int, y: int) -> bool:
        return self.x <= x < self.right and self.y <= y < self.bottom

    def contains(self, other: ScreenRect) -> bool:
        return (
            self.x <= other.x
            and self.y <= other.y
            and self.right >= other.right
            and self.bottom >= other.bottom
        )

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


@dataclass(frozen=True, slots=True)
class DisplayObservation:
    """One physical display and its effective DPI normalization metadata."""

    display_id: str
    bounds: ScreenRect
    work_area: ScreenRect
    dpi_x: int
    dpi_y: int
    primary: bool

    def __post_init__(self) -> None:
        _require_text(self.display_id, field_name="display_id", max_length=256)
        if not isinstance(self.bounds, ScreenRect) or not isinstance(self.work_area, ScreenRect):
            raise TypeError("display bounds/work_area must be ScreenRect values")
        if not self.bounds.contains(self.work_area):
            raise ValueError("display work_area must lie within display bounds")
        _require_int(self.dpi_x, field_name="dpi_x", minimum=1)
        _require_int(self.dpi_y, field_name="dpi_y", minimum=1)
        if self.dpi_x > 960 or self.dpi_y > 960:
            raise ValueError("display DPI exceeds defensive limit")
        if type(self.primary) is not bool:
            raise TypeError("primary must be bool")

    @property
    def scale_x(self) -> float:
        return self.dpi_x / 96.0

    @property
    def scale_y(self) -> float:
        return self.dpi_y / 96.0

    def logical_to_physical(self, x: float, y: float) -> tuple[int, int]:
        return (
            self.bounds.x + round(x * self.scale_x),
            self.bounds.y + round(y * self.scale_y),
        )

    def physical_to_logical(self, x: int, y: int) -> tuple[float, float]:
        return (
            (x - self.bounds.x) / self.scale_x,
            (y - self.bounds.y) / self.scale_y,
        )

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "display_id": self.display_id,
            "bounds": self.bounds.to_dict(),
            "work_area": self.work_area.to_dict(),
            "dpi_x": self.dpi_x,
            "dpi_y": self.dpi_y,
            "primary": self.primary,
            "scale_x": self.scale_x,
            "scale_y": self.scale_y,
        }


@dataclass(frozen=True, slots=True, order=True)
class ScreenFrameId:
    """Immutable identity of one captured frame, never derived from screen text."""

    value: str

    def __post_init__(self) -> None:
        _require_text(self.value, field_name="frame_id", max_length=128)


@dataclass(frozen=True, slots=True)
class ScreenFrame:
    """One immutable virtual-desktop observation."""

    frame_id: ScreenFrameId
    environment_id: str
    surface_id: str
    captured_at_iso: str
    bounds: ScreenRect
    row_stride: int
    pixel_digest: str
    pixels_bgra: bytes
    displays: tuple[DisplayObservation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.frame_id, ScreenFrameId):
            raise TypeError("frame_id must be ScreenFrameId")
        _require_text(self.environment_id, field_name="environment_id")
        _require_text(self.surface_id, field_name="surface_id")
        _require_text(self.captured_at_iso, field_name="captured_at_iso")
        if not isinstance(self.bounds, ScreenRect):
            raise TypeError("bounds must be ScreenRect")
        _require_int(self.row_stride, field_name="row_stride", minimum=self.bounds.width * 4)
        if not isinstance(self.pixels_bgra, bytes):
            raise TypeError("pixels_bgra must be bytes")
        expected = self.row_stride * self.bounds.height
        if len(self.pixels_bgra) != expected:
            raise ValueError("pixel buffer length does not match frame geometry")
        if len(self.pixel_digest) != 64 or any(
            char not in "0123456789abcdef" for char in self.pixel_digest
        ):
            raise ValueError("pixel_digest must be a lowercase SHA-256 hex digest")
        if hashlib.sha256(self.pixels_bgra).hexdigest() != self.pixel_digest:
            raise ValueError("pixel_digest does not match pixel buffer")
        if not isinstance(self.displays, tuple) or not self.displays:
            raise ValueError("frame requires at least one display")
        if len(self.displays) > _MAX_DISPLAYS:
            raise ValueError("frame has too many displays")
        ids = tuple(display.display_id for display in self.displays)
        if len(ids) != len(set(ids)):
            raise ValueError("display identities must be unique")
        if sum(1 for display in self.displays if display.primary) != 1:
            raise ValueError("frame must identify exactly one primary display")
        for display in self.displays:
            if not self.bounds.contains(display.bounds):
                raise ValueError("display lies outside virtual desktop bounds")

    def display_for_point(self, x: int, y: int) -> DisplayObservation | None:
        for display in self.displays:
            if display.bounds.contains_point(x, y):
                return display
        return None

    def descriptor(self) -> dict[str, JsonValue]:
        return {
            "frame_id": self.frame_id.value,
            "environment_id": self.environment_id,
            "surface_id": self.surface_id,
            "captured_at": self.captured_at_iso,
            "bounds": self.bounds.to_dict(),
            "row_stride": self.row_stride,
            "pixel_digest": self.pixel_digest,
            "pixel_bytes": len(self.pixels_bgra),
            "displays": [display.to_dict() for display in self.displays],
        }


class NativeScreenSurface(Protocol):
    """Injected read-only native screen observation seam."""

    def capture(self, *, max_pixels: int) -> Result[_screen_native.RawScreenFrame, AgentXError]: ...


class Win32ScreenSurface:
    """Default adapter over the isolated lazy-native screen seam."""

    __slots__ = ()

    def capture(self, *, max_pixels: int) -> Result[_screen_native.RawScreenFrame, AgentXError]:
        return _screen_native.capture_screen_raw(max_pixels=max_pixels)


class WindowsScreenCapture:
    """Bounded, explicit one-shot screen observer with no background polling."""

    __slots__ = ("_native_surface", "_support")

    def __init__(
        self,
        support: WindowsSupport,
        *,
        native_surface: NativeScreenSurface | None = None,
    ) -> None:
        if not isinstance(support, WindowsSupport):
            raise TypeError("support must be WindowsSupport")
        object.__setattr__(
            self,
            "_native_surface",
            native_surface if native_surface is not None else Win32ScreenSurface(),
        )
        object.__setattr__(self, "_support", support)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"WindowsScreenCapture is immutable; cannot set {name!r}")

    @property
    def support(self) -> WindowsSupport:
        return self._support

    def capture(
        self,
        *,
        environment_id: str,
        surface_id: str = "virtual-desktop",
        max_pixels: int = _DEFAULT_MAX_PIXELS,
    ) -> Result[ScreenFrame, AgentXError]:
        _require_text(environment_id, field_name="environment_id")
        _require_text(surface_id, field_name="surface_id")
        if type(max_pixels) is not int or not 1 <= max_pixels <= _MAX_PIXELS_LIMIT:
            raise ValueError("max_pixels is outside the supported bound")
        if not self._support.is_supported:
            return Result.failure(unsupported_platform_error(self._support))
        observed = self._native_surface.capture(max_pixels=max_pixels)
        if observed.is_failure:
            return Result.failure(observed.unwrap_error())
        raw = observed.unwrap()
        bounds = ScreenRect(raw.left, raw.top, raw.width, raw.height)
        displays: list[DisplayObservation] = []
        for monitor in raw.monitors:
            monitor_bounds = ScreenRect(
                monitor.left,
                monitor.top,
                monitor.right - monitor.left,
                monitor.bottom - monitor.top,
            )
            work = ScreenRect(
                monitor.work_left,
                monitor.work_top,
                monitor.work_right - monitor.work_left,
                monitor.work_bottom - monitor.work_top,
            )
            displays.append(
                DisplayObservation(
                    display_id=monitor.device_name,
                    bounds=monitor_bounds,
                    work_area=work,
                    dpi_x=monitor.dpi_x,
                    dpi_y=monitor.dpi_y,
                    primary=monitor.primary,
                )
            )
        displays.sort(key=lambda item: (0 if item.primary else 1, item.bounds.x, item.bounds.y))
        digest = hashlib.sha256(raw.pixels_bgra).hexdigest()
        captured_at = raw.captured_at.isoformat()
        identity_payload = json.dumps(
            {
                "environment_id": environment_id,
                "surface_id": surface_id,
                "captured_at": captured_at,
                "bounds": bounds.to_dict(),
                "pixel_digest": digest,
                "displays": [item.to_dict() for item in displays],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        frame_id = ScreenFrameId(hashlib.sha256(identity_payload).hexdigest())
        return Result.success(
            ScreenFrame(
                frame_id=frame_id,
                environment_id=environment_id,
                surface_id=surface_id,
                captured_at_iso=captured_at,
                bounds=bounds,
                row_stride=raw.row_stride,
                pixel_digest=digest,
                pixels_bgra=raw.pixels_bgra,
                displays=tuple(displays),
            )
        )


WINDOWS_SCREEN_CAPTURE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.screen.capture"),
    version=CapabilityVersion(1, 0, 0),
)
WINDOWS_SCREEN_CAPTURE_DESCRIPTION: Final[str] = (
    "Read-only bounded capture of the Windows virtual desktop with frame identity, "
    "display topology and effective DPI metadata."
)


@dataclass(frozen=True, slots=True)
class WindowsScreenCaptureParams(CapabilityParams):
    environment_id: str
    surface_id: str = "virtual-desktop"
    max_pixels: int = _DEFAULT_MAX_PIXELS

    def __post_init__(self) -> None:
        _require_text(self.environment_id, field_name="environment_id")
        _require_text(self.surface_id, field_name="surface_id")
        if type(self.max_pixels) is not int or not 1 <= self.max_pixels <= _MAX_PIXELS_LIMIT:
            raise ValueError("max_pixels is outside the supported bound")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "environment_id": self.environment_id,
            "surface_id": self.surface_id,
            "max_pixels": self.max_pixels,
        }


class WindowsScreenCaptureCapability:
    """Screen observation as an ordinary governed READ capability."""

    __slots__ = ("_capture", "_descriptor")

    def __init__(self, capture: WindowsScreenCapture) -> None:
        if not isinstance(capture, WindowsScreenCapture):
            raise TypeError("capture must be WindowsScreenCapture")
        object.__setattr__(self, "_capture", capture)
        object.__setattr__(
            self,
            "_descriptor",
            CapabilityDescriptor(
                identity=WINDOWS_SCREEN_CAPTURE_IDENTITY,
                description=WINDOWS_SCREEN_CAPTURE_DESCRIPTION,
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
                        description=(
                            "A supported Windows host with an observable desktop is required."
                        ),
                    ),
                ),
                rollback=RollbackDeclaration(
                    support=RollbackSupport.NOT_APPLICABLE,
                    detail="Screen capture is a read-only observation.",
                ),
                estimate=ResourceEstimate(
                    wall_clock=timedelta(milliseconds=500),
                    machine_actions=1,
                    external_cost=Decimal("0"),
                ),
            ),
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[WindowsScreenCaptureParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, WindowsScreenCaptureParams):
            raise TypeError("screen capture requires WindowsScreenCaptureParams")
        if context.observe_stop().should_stop:
            return ExecutionResult(
                succeeded=False,
                message="screen observation cancelled before capture",
                observation=CapabilityObservation(
                    summary="screen observation cancelled",
                    data={"cancelled_before_start": True},
                ),
            )
        outcome = self._capture.capture(
            environment_id=request.params.environment_id,
            surface_id=request.params.surface_id,
            max_pixels=request.params.max_pixels,
        )
        if outcome.is_failure:
            error = outcome.unwrap_error()
            return ExecutionResult(
                succeeded=False,
                message=f"screen observation failed: {error.message}",
                observation=CapabilityObservation(
                    summary="screen observation failed",
                    data={"error": error.to_dict()},
                ),
            )
        frame = outcome.unwrap()
        return ExecutionResult(
            succeeded=True,
            message=(
                f"captured {frame.bounds.width}x{frame.bounds.height} virtual desktop "
                f"across {len(frame.displays)} display(s)"
            ),
            observation=CapabilityObservation(
                summary="read-only Windows screen frame descriptor",
                data=frame.descriptor(),
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowsScreenCaptureParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        data = observation.to_dict()["data"]
        if not isinstance(data, dict) or "error" in data:
            return VerificationResult(passed=False, detail="screen capture did not produce a frame")
        required = {
            "frame_id",
            "environment_id",
            "surface_id",
            "captured_at",
            "bounds",
            "row_stride",
            "pixel_digest",
            "pixel_bytes",
            "displays",
        }
        if set(data) != required:
            return VerificationResult(passed=False, detail="screen frame descriptor is malformed")
        if data["environment_id"] != request.params.environment_id:
            return VerificationResult(passed=False, detail="screen frame crossed environment scope")
        if data["surface_id"] != request.params.surface_id:
            return VerificationResult(passed=False, detail="screen frame crossed surface scope")
        bounds = data["bounds"]
        if not isinstance(bounds, dict):
            return VerificationResult(passed=False, detail="screen bounds are malformed")
        width = bounds.get("width")
        height = bounds.get("height")
        row_stride = data["row_stride"]
        pixel_bytes = data["pixel_bytes"]
        if (
            type(width) is not int
            or type(height) is not int
            or type(row_stride) is not int
            or type(pixel_bytes) is not int
            or width <= 0
            or height <= 0
            or row_stride < width * 4
            or pixel_bytes != row_stride * height
        ):
            return VerificationResult(
                passed=False, detail="screen frame dimensions are inconsistent"
            )
        return VerificationResult(
            passed=True,
            detail="screen observation descriptor is structurally consistent",
        )


def screen_capture_request(
    *,
    environment_id: str,
    surface_id: str = "virtual-desktop",
    max_pixels: int = _DEFAULT_MAX_PIXELS,
) -> CapabilityRequest[WindowsScreenCaptureParams]:
    return CapabilityRequest(
        identity=WINDOWS_SCREEN_CAPTURE_IDENTITY,
        params=WindowsScreenCaptureParams(
            environment_id=environment_id,
            surface_id=surface_id,
            max_pixels=max_pixels,
        ),
    )
