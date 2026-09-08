"""Governed Windows window-management capability (M7.02).

This module defines the smallest deterministic window-management Capability
surface: focus, minimize, maximize, restore, move/resize, and optional close.
Every operation binds to an explicit structured window identity (HWND/int
handle) and uses a native adapter interface that delegates to structured Win32
APIs through the isolated native seam (``agentx.capabilities.windows._native``
and future extensions of it).

Native APIs intended per operation (managed by the adapter interface, never
invoked directly in this pure-Python boundary):

  * focus: ``SetForegroundWindow`` / ``SetActiveWindow``
  * minimize: ``ShowWindow`` with ``SW_MINIMIZE``
  * maximize: ``ShowWindow`` with ``SW_MAXIMIZE``
  * restore: ``ShowWindow`` with ``SW_RESTORE``
  * move/resize: ``SetWindowPos`` / ``MoveWindow``
  * close (optional): ``PostMessage`` with ``WM_CLOSE``
  * verification: ``GetForegroundWindow``, ``IsIconic``, ``IsZoomed``,
    ``GetWindowRect``, ``IsWindow``

This module imports only the standard library plus canonical ``agentx``
contracts; it performs no machine action on import and touches no native
library at module level. The isolated native seam is the only permitted
location for ``ctypes``/Win32 knowledge.

Authority remains with CapabilityExecutionLoop / Trusted Kernel. This module
creates no permission, grants no authority, and performs no registry wiring.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Final, Protocol, cast

from agentx.capabilities.abi import (
    Capability,
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
from agentx.capabilities.windows.provider import (
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
    "WINDOW_MANAGEMENT_INVALID_GEOMETRY_ERROR_CODE",
    "WINDOW_MANAGEMENT_INVALID_HANDLE_ERROR_CODE",
    "WINDOW_MANAGEMENT_NATIVE_ERROR_CODE",
    "WINDOW_MANAGEMENT_STALE_WINDOW_ERROR_CODE",
    "WINDOW_MANAGEMENT_UNSUPPORTED_ERROR_CODE",
    "WINDOW_MANAGEMENT_VERIFICATION_MISMATCH_ERROR_CODE",
    "FakeWindowManagementNativeSurface",
    "WindowCloseCapability",
    "WindowCloseParams",
    "WindowFocusCapability",
    "WindowFocusParams",
    "WindowManagementNativeSurface",
    "WindowMaximizeCapability",
    "WindowMaximizeParams",
    "WindowMinimizeCapability",
    "WindowMinimizeParams",
    "WindowMoveResizeCapability",
    "WindowMoveResizeParams",
    "WindowRestoreCapability",
    "WindowRestoreParams",
    "close_window_request",
    "focus_window_request",
    "maximize_window_request",
    "minimize_window_request",
    "move_resize_window_request",
    "restore_window_request",
]

_WIN32_PLATFORM: Final[str] = "win32"

WINDOW_MANAGEMENT_UNSUPPORTED_ERROR_CODE: Final[str] = (
    "capabilities.windows.window_management.unsupported_platform"
)
WINDOW_MANAGEMENT_INVALID_HANDLE_ERROR_CODE: Final[str] = (
    "capabilities.windows.window_management.invalid_handle"
)
WINDOW_MANAGEMENT_INVALID_GEOMETRY_ERROR_CODE: Final[str] = (
    "capabilities.windows.window_management.invalid_geometry"
)
WINDOW_MANAGEMENT_STALE_WINDOW_ERROR_CODE: Final[str] = (
    "capabilities.windows.window_management.stale_window"
)
WINDOW_MANAGEMENT_VERIFICATION_MISMATCH_ERROR_CODE: Final[str] = (
    "capabilities.windows.window_management.verification_mismatch"
)
WINDOW_MANAGEMENT_NATIVE_ERROR_CODE: Final[str] = (
    "capabilities.windows.window_management.native_error"
)

_MAX_HANDLE_VALUE: Final[int] = 0x7FFFFFFF
_MAX_GEOMETRY_VALUE: Final[int] = 100_000


def _is_supported_platform() -> bool:
    return sys.platform == _WIN32_PLATFORM


def _require_handle(value: object, field_name: str = "handle") -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be int, got {type(value).__name__}")
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be int, bool rejected")
    if value < 1:
        raise ValueError(f"{field_name} must be >= 1, got {value}")
    if value > _MAX_HANDLE_VALUE:
        raise OverflowError(f"{field_name} exceeds maximum allowed value")
    return value


def _require_geometry(
    x: object, y: object, width: object, height: object
) -> tuple[int, int, int, int]:
    for name, val in (("x", x), ("y", y), ("width", width), ("height", height)):
        if type(val) is not int:
            raise TypeError(f"geometry.{name} must be int, got {type(val).__name__}")
        if isinstance(val, bool):
            raise TypeError(f"geometry.{name} must be int, bool rejected")
        if val < -_MAX_GEOMETRY_VALUE or val > _MAX_GEOMETRY_VALUE:
            raise OverflowError(f"geometry.{name} out of reasonable range: {val}")
    if type(width) is not int or isinstance(width, bool):
        pass  # already checked
    w = cast(int, width)
    h = cast(int, height)
    if w <= 0 or h <= 0:
        raise ValueError(f"geometry dimensions must be positive: width={w}, height={h}")
    return (cast(int, x), cast(int, y), w, h)


# --------------------------------------------------------------------------
# Native adapter protocol
# --------------------------------------------------------------------------


class WindowManagementNativeSurface(Protocol):
    """The governed native seam for window management operations.

    Real implementations delegate through ``agentx.capabilities.windows._native``
    or a future isolated native seam extension. This interface is pure Python
    and carries no native knowledge itself.
    """

    def focus_window(self, handle: int) -> Result[int, AgentXError]: ...

    def minimize_window(self, handle: int) -> Result[int, AgentXError]: ...

    def maximize_window(self, handle: int) -> Result[int, AgentXError]: ...

    def restore_window(self, handle: int) -> Result[int, AgentXError]: ...

    def move_resize_window(
        self, handle: int, x: int, y: int, width: int, height: int
    ) -> Result[int, AgentXError]: ...

    def close_window(self, handle: int) -> Result[int, AgentXError]: ...

    def observe_foreground(self) -> Result[int, AgentXError]:
        """Return the current foreground HWND, or 0 if none / unavailable."""
        ...

    def observe_window_rect(self, handle: int) -> Result[tuple[int, int, int, int], AgentXError]:
        """Return (left, top, right, bottom) from ``GetWindowRect``."""
        ...

    def observe_window_state(self, handle: int) -> Result[str, AgentXError]:
        """Return 'minimized', 'maximized', 'normal', or 'invalid'."""
        ...

    def observe_window_exists(self, handle: int) -> Result[bool, AgentXError]:
        """Return True if ``IsWindow`` confirms presence."""
        ...


class FakeWindowManagementNativeSurface:
    """Deterministic test double for window management.

    Records every call and returns configured responses. No OS interaction.
    """

    __slots__ = (
        "_close_result",
        "_focus_result",
        "_maximize_result",
        "_minimize_result",
        "_move_resize_result",
        "_observe_exists",
        "_observe_foreground",
        "_observe_rect",
        "_observe_state",
        "_restore_result",
        "calls",
    )

    def __init__(
        self,
        *,
        focus_result: Result[int, AgentXError] | None = None,
        minimize_result: Result[int, AgentXError] | None = None,
        maximize_result: Result[int, AgentXError] | None = None,
        restore_result: Result[int, AgentXError] | None = None,
        move_resize_result: Result[int, AgentXError] | None = None,
        close_result: Result[int, AgentXError] | None = None,
        observe_foreground: int = 0,
        observe_rect: tuple[int, int, int, int] | None = None,
        observe_state: str = "normal",
        observe_exists: bool = True,
    ) -> None:
        self._focus_result = focus_result
        self._minimize_result = minimize_result
        self._maximize_result = maximize_result
        self._restore_result = restore_result
        self._move_resize_result = move_resize_result
        self._close_result = close_result
        self._observe_foreground = observe_foreground
        self._observe_rect = observe_rect if observe_rect is not None else (0, 0, 100, 100)
        self._observe_state = observe_state
        self._observe_exists = observe_exists
        self.calls: list[str] = []

    def _record(self, name: str) -> None:
        self.calls.append(name)

    def focus_window(self, handle: int) -> Result[int, AgentXError]:
        self._record("focus_window")
        if self._focus_result is not None:
            return self._focus_result
        return Result.success(handle)

    def minimize_window(self, handle: int) -> Result[int, AgentXError]:
        self._record("minimize_window")
        if self._minimize_result is not None:
            return self._minimize_result
        return Result.success(handle)

    def maximize_window(self, handle: int) -> Result[int, AgentXError]:
        self._record("maximize_window")
        if self._maximize_result is not None:
            return self._maximize_result
        return Result.success(handle)

    def restore_window(self, handle: int) -> Result[int, AgentXError]:
        self._record("restore_window")
        if self._restore_result is not None:
            return self._restore_result
        return Result.success(handle)

    def move_resize_window(
        self, handle: int, x: int, y: int, width: int, height: int
    ) -> Result[int, AgentXError]:
        self._record("move_resize_window")
        if self._move_resize_result is not None:
            return self._move_resize_result
        return Result.success(handle)

    def close_window(self, handle: int) -> Result[int, AgentXError]:
        self._record("close_window")
        if self._close_result is not None:
            return self._close_result
        return Result.success(handle)

    def observe_foreground(self) -> Result[int, AgentXError]:
        self._record("observe_foreground")
        return Result.success(self._observe_foreground)

    def observe_window_rect(self, handle: int) -> Result[tuple[int, int, int, int], AgentXError]:
        self._record("observe_window_rect")
        return Result.success(self._observe_rect)

    def observe_window_state(self, handle: int) -> Result[str, AgentXError]:
        self._record("observe_window_state")
        return Result.success(self._observe_state)

    def observe_window_exists(self, handle: int) -> Result[bool, AgentXError]:
        self._record("observe_window_exists")
        return Result.success(self._observe_exists)


class DefaultWindowManagementNativeSurface:
    """Default adapter that attempts delegation through the isolated seam.

    The isolated native seam (``_native.py``) currently provides read-only
    discovery only; window-management native functions are expected as future
    extensions of that isolated module. Until they exist, this adapter returns
    explicit failure so the capability never pretends success.
    """

    __slots__ = ()

    def _unimplemented(self, op: str) -> AgentXError:
        return AgentXError(
            code=WINDOW_MANAGEMENT_NATIVE_ERROR_CODE,
            message=f"Window management native adapter not wired: {op}",
            category=ErrorCategory.EXECUTION,
            retryability=Retryability.NON_RETRYABLE,
            details={"operation": op, "note": "delegate to isolated native seam"},
        )

    def focus_window(self, handle: int) -> Result[int, AgentXError]:
        return Result.failure(self._unimplemented("focus_window"))

    def minimize_window(self, handle: int) -> Result[int, AgentXError]:
        return Result.failure(self._unimplemented("minimize_window"))

    def maximize_window(self, handle: int) -> Result[int, AgentXError]:
        return Result.failure(self._unimplemented("maximize_window"))

    def restore_window(self, handle: int) -> Result[int, AgentXError]:
        return Result.failure(self._unimplemented("restore_window"))

    def move_resize_window(
        self, handle: int, x: int, y: int, width: int, height: int
    ) -> Result[int, AgentXError]:
        return Result.failure(self._unimplemented("move_resize_window"))

    def close_window(self, handle: int) -> Result[int, AgentXError]:
        return Result.failure(self._unimplemented("close_window"))

    def observe_foreground(self) -> Result[int, AgentXError]:
        return Result.failure(self._unimplemented("observe_foreground"))

    def observe_window_rect(self, handle: int) -> Result[tuple[int, int, int, int], AgentXError]:
        return Result.failure(self._unimplemented("observe_window_rect"))

    def observe_window_state(self, handle: int) -> Result[str, AgentXError]:
        return Result.failure(self._unimplemented("observe_window_state"))

    def observe_window_exists(self, handle: int) -> Result[bool, AgentXError]:
        return Result.failure(self._unimplemented("observe_window_exists"))


# --------------------------------------------------------------------------
# Parameter definitions
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowFocusParams(CapabilityParams):
    handle: int

    def __post_init__(self) -> None:
        _require_handle(self.handle, "handle")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"handle": self.handle}


@dataclass(frozen=True, slots=True)
class WindowMinimizeParams(CapabilityParams):
    handle: int

    def __post_init__(self) -> None:
        _require_handle(self.handle, "handle")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"handle": self.handle}


@dataclass(frozen=True, slots=True)
class WindowMaximizeParams(CapabilityParams):
    handle: int

    def __post_init__(self) -> None:
        _require_handle(self.handle, "handle")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"handle": self.handle}


@dataclass(frozen=True, slots=True)
class WindowRestoreParams(CapabilityParams):
    handle: int

    def __post_init__(self) -> None:
        _require_handle(self.handle, "handle")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"handle": self.handle}


@dataclass(frozen=True, slots=True)
class WindowMoveResizeParams(CapabilityParams):
    handle: int
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        _require_handle(self.handle, "handle")
        (vx, vy, vw, vh) = _require_geometry(self.x, self.y, self.width, self.height)
        object.__setattr__(self, "x", vx)
        object.__setattr__(self, "y", vy)
        object.__setattr__(self, "width", vw)
        object.__setattr__(self, "height", vh)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "handle": self.handle,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True, slots=True)
class WindowCloseParams(CapabilityParams):
    handle: int

    def __post_init__(self) -> None:
        _require_handle(self.handle, "handle")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"handle": self.handle}


# --------------------------------------------------------------------------
# Capability descriptors and identities
# --------------------------------------------------------------------------

WINDOW_FOCUS_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.window.focus"),
    version=CapabilityVersion(1, 0, 0),
)
WINDOW_MINIMIZE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.window.minimize"),
    version=CapabilityVersion(1, 0, 0),
)
WINDOW_MAXIMIZE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.window.maximize"),
    version=CapabilityVersion(1, 0, 0),
)
WINDOW_RESTORE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.window.restore"),
    version=CapabilityVersion(1, 0, 0),
)
WINDOW_MOVE_RESIZE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.window.move_resize"),
    version=CapabilityVersion(1, 0, 0),
)
WINDOW_CLOSE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.window.close"),
    version=CapabilityVersion(1, 0, 0),
)

WINDOW_FOCUS_DESCRIPTION: Final[str] = (
    "Activate/focus one explicit top-level Windows window by structured HWND handle."
)
WINDOW_MINIMIZE_DESCRIPTION: Final[str] = (
    "Minimize one explicit top-level Windows window by structured HWND handle."
)
WINDOW_MAXIMIZE_DESCRIPTION: Final[str] = (
    "Maximize one explicit top-level Windows window by structured HWND handle."
)
WINDOW_RESTORE_DESCRIPTION: Final[str] = (
    "Restore (un-minimize / un-maximize) one explicit top-level Windows window by handle."
)
WINDOW_MOVE_RESIZE_DESCRIPTION: Final[str] = (
    "Move and resize one explicit top-level Windows window "
    "by structured handle and integer geometry."
)
WINDOW_CLOSE_DESCRIPTION: Final[str] = (
    "Request graceful close of one explicit top-level Windows window via WM_CLOSE delivery. "
    "Verification requires independent window absence confirmation; WM_CLOSE alone is never proof."
)


def _focus_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        identity=WINDOW_FOCUS_IDENTITY,
        description=WINDOW_FOCUS_DESCRIPTION,
        scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
        required_permissions=frozenset({Permission.EXTERNAL_EFFECT, Permission.WRITE}),
        risk_assessment=assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=True,
            external_effect=True,
        ),
        preconditions=(
            CapabilityPrecondition(
                name="windows.supported_host",
                description="Host must evaluate as supported Windows through A5.01.",
            ),
            CapabilityPrecondition(
                name="window.identity_explicit",
                description="Window must be targeted by explicit structured HWND handle, never by title/fuzzy match.",  # noqa: E501
            ),
        ),
        rollback=RollbackDeclaration(
            support=RollbackSupport.SUPPORTED,
            detail="Focus is reversible by focusing another window or restoring previous state.",
        ),
        estimate=ResourceEstimate(
            wall_clock=timedelta(milliseconds=50),
            machine_actions=1,
            external_cost=Decimal("0"),
        ),
    )


def _minimize_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        identity=WINDOW_MINIMIZE_IDENTITY,
        description=WINDOW_MINIMIZE_DESCRIPTION,
        scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
        required_permissions=frozenset({Permission.EXTERNAL_EFFECT, Permission.WRITE}),
        risk_assessment=assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=True,
            external_effect=True,
        ),
        preconditions=(
            CapabilityPrecondition(
                name="windows.supported_host",
                description="Host must evaluate as supported Windows through A5.01.",
            ),
            CapabilityPrecondition(
                name="window.identity_explicit",
                description="Window must be targeted by explicit structured HWND handle.",
            ),
        ),
        rollback=RollbackDeclaration(
            support=RollbackSupport.SUPPORTED,
            detail="Minimize is reversible by restore/maximize.",
        ),
        estimate=ResourceEstimate(
            wall_clock=timedelta(milliseconds=50),
            machine_actions=1,
            external_cost=Decimal("0"),
        ),
    )


def _maximize_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        identity=WINDOW_MAXIMIZE_IDENTITY,
        description=WINDOW_MAXIMIZE_DESCRIPTION,
        scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
        required_permissions=frozenset({Permission.EXTERNAL_EFFECT, Permission.WRITE}),
        risk_assessment=assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=True,
            external_effect=True,
        ),
        preconditions=(
            CapabilityPrecondition(
                name="windows.supported_host",
                description="Host must evaluate as supported Windows through A5.01.",
            ),
            CapabilityPrecondition(
                name="window.identity_explicit",
                description="Window must be targeted by explicit structured HWND handle.",
            ),
        ),
        rollback=RollbackDeclaration(
            support=RollbackSupport.SUPPORTED,
            detail="Maximize is reversible by restore.",
        ),
        estimate=ResourceEstimate(
            wall_clock=timedelta(milliseconds=50),
            machine_actions=1,
            external_cost=Decimal("0"),
        ),
    )


def _restore_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        identity=WINDOW_RESTORE_IDENTITY,
        description=WINDOW_RESTORE_DESCRIPTION,
        scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
        required_permissions=frozenset({Permission.EXTERNAL_EFFECT, Permission.WRITE}),
        risk_assessment=assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=True,
            external_effect=True,
        ),
        preconditions=(
            CapabilityPrecondition(
                name="windows.supported_host",
                description="Host must evaluate as supported Windows through A5.01.",
            ),
            CapabilityPrecondition(
                name="window.identity_explicit",
                description="Window must be targeted by explicit structured HWND handle.",
            ),
        ),
        rollback=RollbackDeclaration(
            support=RollbackSupport.SUPPORTED,
            detail="Restore is reversible by minimize/maximize.",
        ),
        estimate=ResourceEstimate(
            wall_clock=timedelta(milliseconds=50),
            machine_actions=1,
            external_cost=Decimal("0"),
        ),
    )


def _move_resize_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        identity=WINDOW_MOVE_RESIZE_IDENTITY,
        description=WINDOW_MOVE_RESIZE_DESCRIPTION,
        scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
        required_permissions=frozenset({Permission.EXTERNAL_EFFECT, Permission.WRITE}),
        risk_assessment=assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=True,
            external_effect=True,
        ),
        preconditions=(
            CapabilityPrecondition(
                name="windows.supported_host",
                description="Host must evaluate as supported Windows through A5.01.",
            ),
            CapabilityPrecondition(
                name="window.identity_explicit",
                description="Window must be targeted by explicit structured HWND handle with integer geometry.",  # noqa: E501
            ),
            CapabilityPrecondition(
                name="geometry.positive",
                description="Move/resize dimensions must be positive integers; boolean-as-int rejected.",  # noqa: E501
            ),
        ),
        rollback=RollbackDeclaration(
            support=RollbackSupport.SUPPORTED,
            detail="Geometry is reversible by moving/resizing back to previous rect.",
        ),
        estimate=ResourceEstimate(
            wall_clock=timedelta(milliseconds=100),
            machine_actions=1,
            external_cost=Decimal("0"),
        ),
    )


def _close_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        identity=WINDOW_CLOSE_IDENTITY,
        description=WINDOW_CLOSE_DESCRIPTION,
        scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
        required_permissions=frozenset({Permission.EXTERNAL_EFFECT, Permission.WRITE}),
        risk_assessment=assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=False,
            external_effect=True,
            critical=True,
        ),
        preconditions=(
            CapabilityPrecondition(
                name="windows.supported_host",
                description="Host must evaluate as supported Windows through A5.01.",
            ),
            CapabilityPrecondition(
                name="window.identity_explicit",
                description="Window must be targeted by explicit structured HWND handle.",
            ),
        ),
        rollback=RollbackDeclaration(
            support=RollbackSupport.UNSUPPORTED,
            detail="Close requests graceful window exit; reopening requires application relaunch and is out of scope.",  # noqa: E501
        ),
        estimate=ResourceEstimate(
            wall_clock=timedelta(milliseconds=100),
            machine_actions=1,
            external_cost=Decimal("0"),
        ),
    )


# --------------------------------------------------------------------------
# Capability implementations
# --------------------------------------------------------------------------


class _WindowCapabilityBase:
    """Shared immutability and provider support logic."""

    __slots__ = ("_descriptor", "_native_surface", "_support")

    _descriptor: CapabilityDescriptor
    _native_surface: WindowManagementNativeSurface
    _support: WindowsSupport

    def __init__(
        self,
        descriptor: CapabilityDescriptor,
        support: WindowsSupport,
        *,
        native_surface: WindowManagementNativeSurface | None = None,
    ) -> None:
        if not isinstance(descriptor, CapabilityDescriptor):
            raise TypeError(
                f"descriptor must be CapabilityDescriptor, got {type(descriptor).__name__}"
            )
        if not isinstance(support, WindowsSupport):
            raise TypeError(f"support must be WindowsSupport, got {type(support).__name__}")
        object.__setattr__(self, "_descriptor", descriptor)
        object.__setattr__(
            self,
            "_native_surface",
            native_surface
            if native_surface is not None
            else DefaultWindowManagementNativeSurface(),
        )
        object.__setattr__(self, "_support", support)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"Window capability is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"Window capability is immutable; cannot delete {name!r}")

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    @property
    def support(self) -> WindowsSupport:
        return self._support

    @property
    def is_supported(self) -> bool:
        return self._support.is_supported

    def _unsupported_result(self, message: str) -> ExecutionResult:
        error = unsupported_platform_error(self._support)
        return ExecutionResult(
            succeeded=False,
            message=message,
            observation=CapabilityObservation(
                summary="unsupported platform",
                data={"error_code": error.code, "platform": sys.platform},
            ),
        )


class WindowFocusCapability(_WindowCapabilityBase, Capability[WindowFocusParams]):
    def __init__(
        self,
        support: WindowsSupport,
        *,
        native_surface: WindowManagementNativeSurface | None = None,
    ) -> None:
        super().__init__(_focus_descriptor(), support, native_surface=native_surface)

    def execute(
        self,
        request: CapabilityRequest[WindowFocusParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, WindowFocusParams):
            raise TypeError(
                f"focus requires WindowFocusParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return ExecutionResult(
                succeeded=False,
                message="focus cooperatively cancelled",
                observation=CapabilityObservation(
                    summary="cancelled before execution",
                    data={"cancelled": True},
                ),
            )
        if not self.is_supported:
            return self._unsupported_result("focus requires supported Windows host")
        result = self._native_surface.focus_window(request.params.handle)
        if result.is_failure:
            error = result.unwrap_error()
            return ExecutionResult(
                succeeded=False,
                message=f"focus failed: {error.message}",
                observation=CapabilityObservation(
                    summary="focus native failure",
                    data={"error_code": error.code, "handle": request.params.handle},
                ),
            )
        observe = self._native_surface.observe_foreground()
        verified = False
        if observe.is_success:
            verified = observe.unwrap() == request.params.handle
        return ExecutionResult(
            succeeded=True,
            message=f"focus executed for handle {request.params.handle}",
            observation=CapabilityObservation(
                summary="focus executed",
                data={
                    "handle": request.params.handle,
                    "verified_foreground": verified,
                    "native_result": result.unwrap(),
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowFocusParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, WindowFocusParams):
            raise TypeError(
                f"verify requires WindowFocusParams, got {type(request.params).__name__}"
            )
        observe = self._native_surface.observe_foreground()
        if observe.is_failure:
            return VerificationResult(
                passed=False,
                detail="verification failed: could not observe foreground window",
            )
        actual = observe.unwrap()
        expected = request.params.handle
        if actual == expected:
            return VerificationResult(
                passed=True,
                detail=f"foreground HWND {actual} matches requested handle {expected}",
            )
        return VerificationResult(
            passed=False,
            detail=f"verification mismatch: foreground HWND {actual} != requested {expected}",
        )


class WindowMinimizeCapability(_WindowCapabilityBase, Capability[WindowMinimizeParams]):
    def __init__(
        self,
        support: WindowsSupport,
        *,
        native_surface: WindowManagementNativeSurface | None = None,
    ) -> None:
        super().__init__(_minimize_descriptor(), support, native_surface=native_surface)

    def execute(
        self,
        request: CapabilityRequest[WindowMinimizeParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, WindowMinimizeParams):
            raise TypeError(
                f"minimize requires WindowMinimizeParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return ExecutionResult(
                succeeded=False,
                message="minimize cooperatively cancelled",
                observation=CapabilityObservation(
                    summary="cancelled before execution",
                    data={"cancelled": True},
                ),
            )
        if not self.is_supported:
            return self._unsupported_result("minimize requires supported Windows host")
        result = self._native_surface.minimize_window(request.params.handle)
        if result.is_failure:
            error = result.unwrap_error()
            return ExecutionResult(
                succeeded=False,
                message=f"minimize failed: {error.message}",
                observation=CapabilityObservation(
                    summary="minimize native failure",
                    data={"error_code": error.code, "handle": request.params.handle},
                ),
            )
        observe = self._native_surface.observe_window_state(request.params.handle)
        verified = False
        if observe.is_success:
            verified = observe.unwrap() == "minimized"
        return ExecutionResult(
            succeeded=True,
            message=f"minimize executed for handle {request.params.handle}",
            observation=CapabilityObservation(
                summary="minimize executed",
                data={
                    "handle": request.params.handle,
                    "verified_minimized": verified,
                    "state": observe.unwrap() if observe.is_success else None,
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowMinimizeParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, WindowMinimizeParams):
            raise TypeError(
                f"verify requires WindowMinimizeParams, got {type(request.params).__name__}"
            )
        observe = self._native_surface.observe_window_state(request.params.handle)
        if observe.is_failure:
            return VerificationResult(
                passed=False,
                detail="verification failed: could not observe window state",
            )
        state = observe.unwrap()
        if state == "minimized":
            return VerificationResult(
                passed=True,
                detail=f"window {request.params.handle} is confirmed minimized",
            )
        return VerificationResult(
            passed=False,
            detail=f"verification mismatch: state={state}, expected minimized",
        )


class WindowMaximizeCapability(_WindowCapabilityBase, Capability[WindowMaximizeParams]):
    def __init__(
        self,
        support: WindowsSupport,
        *,
        native_surface: WindowManagementNativeSurface | None = None,
    ) -> None:
        super().__init__(_maximize_descriptor(), support, native_surface=native_surface)

    def execute(
        self,
        request: CapabilityRequest[WindowMaximizeParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, WindowMaximizeParams):
            raise TypeError(
                f"maximize requires WindowMaximizeParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return ExecutionResult(
                succeeded=False,
                message="maximize cooperatively cancelled",
                observation=CapabilityObservation(
                    summary="cancelled before execution",
                    data={"cancelled": True},
                ),
            )
        if not self.is_supported:
            return self._unsupported_result("maximize requires supported Windows host")
        result = self._native_surface.maximize_window(request.params.handle)
        if result.is_failure:
            error = result.unwrap_error()
            return ExecutionResult(
                succeeded=False,
                message=f"maximize failed: {error.message}",
                observation=CapabilityObservation(
                    summary="maximize native failure",
                    data={"error_code": error.code, "handle": request.params.handle},
                ),
            )
        observe = self._native_surface.observe_window_state(request.params.handle)
        verified = False
        if observe.is_success:
            verified = observe.unwrap() == "maximized"
        return ExecutionResult(
            succeeded=True,
            message=f"maximize executed for handle {request.params.handle}",
            observation=CapabilityObservation(
                summary="maximize executed",
                data={
                    "handle": request.params.handle,
                    "verified_maximized": verified,
                    "state": observe.unwrap() if observe.is_success else None,
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowMaximizeParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, WindowMaximizeParams):
            raise TypeError(
                f"verify requires WindowMaximizeParams, got {type(request.params).__name__}"
            )
        observe = self._native_surface.observe_window_state(request.params.handle)
        if observe.is_failure:
            return VerificationResult(
                passed=False,
                detail="verification failed: could not observe window state",
            )
        state = observe.unwrap()
        if state == "maximized":
            return VerificationResult(
                passed=True,
                detail=f"window {request.params.handle} is confirmed maximized",
            )
        return VerificationResult(
            passed=False,
            detail=f"verification mismatch: state={state}, expected maximized",
        )


class WindowRestoreCapability(_WindowCapabilityBase, Capability[WindowRestoreParams]):
    def __init__(
        self,
        support: WindowsSupport,
        *,
        native_surface: WindowManagementNativeSurface | None = None,
    ) -> None:
        super().__init__(_restore_descriptor(), support, native_surface=native_surface)

    def execute(
        self,
        request: CapabilityRequest[WindowRestoreParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, WindowRestoreParams):
            raise TypeError(
                f"restore requires WindowRestoreParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return ExecutionResult(
                succeeded=False,
                message="restore cooperatively cancelled",
                observation=CapabilityObservation(
                    summary="cancelled before execution",
                    data={"cancelled": True},
                ),
            )
        if not self.is_supported:
            return self._unsupported_result("restore requires supported Windows host")
        result = self._native_surface.restore_window(request.params.handle)
        if result.is_failure:
            error = result.unwrap_error()
            return ExecutionResult(
                succeeded=False,
                message=f"restore failed: {error.message}",
                observation=CapabilityObservation(
                    summary="restore native failure",
                    data={"error_code": error.code, "handle": request.params.handle},
                ),
            )
        observe = self._native_surface.observe_window_state(request.params.handle)
        verified = False
        if observe.is_success:
            verified = observe.unwrap() == "normal"
        return ExecutionResult(
            succeeded=True,
            message=f"restore executed for handle {request.params.handle}",
            observation=CapabilityObservation(
                summary="restore executed",
                data={
                    "handle": request.params.handle,
                    "verified_normal": verified,
                    "state": observe.unwrap() if observe.is_success else None,
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowRestoreParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, WindowRestoreParams):
            raise TypeError(
                f"verify requires WindowRestoreParams, got {type(request.params).__name__}"
            )
        observe = self._native_surface.observe_window_state(request.params.handle)
        if observe.is_failure:
            return VerificationResult(
                passed=False,
                detail="verification failed: could not observe window state",
            )
        state = observe.unwrap()
        if state == "normal":
            return VerificationResult(
                passed=True,
                detail=f"window {request.params.handle} is confirmed restored (normal)",
            )
        return VerificationResult(
            passed=False,
            detail=f"verification mismatch: state={state}, expected normal",
        )


class WindowMoveResizeCapability(_WindowCapabilityBase, Capability[WindowMoveResizeParams]):
    def __init__(
        self,
        support: WindowsSupport,
        *,
        native_surface: WindowManagementNativeSurface | None = None,
    ) -> None:
        super().__init__(_move_resize_descriptor(), support, native_surface=native_surface)

    def execute(
        self,
        request: CapabilityRequest[WindowMoveResizeParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, WindowMoveResizeParams):
            raise TypeError(
                f"move_resize requires WindowMoveResizeParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return ExecutionResult(
                succeeded=False,
                message="move_resize cooperatively cancelled",
                observation=CapabilityObservation(
                    summary="cancelled before execution",
                    data={"cancelled": True},
                ),
            )
        if not self.is_supported:
            return self._unsupported_result("move_resize requires supported Windows host")
        result = self._native_surface.move_resize_window(
            request.params.handle,
            request.params.x,
            request.params.y,
            request.params.width,
            request.params.height,
        )
        if result.is_failure:
            error = result.unwrap_error()
            return ExecutionResult(
                succeeded=False,
                message=f"move_resize failed: {error.message}",
                observation=CapabilityObservation(
                    summary="move_resize native failure",
                    data={
                        "error_code": error.code,
                        "handle": request.params.handle,
                        "geometry": request.params.to_dict(),
                    },
                ),
            )
        observe = self._native_surface.observe_window_rect(request.params.handle)
        verified = False
        if observe.is_success:
            rect = observe.unwrap()
            # Compare retrieved rect with expected geometry.
            # Note: Windows border metrics can cause minor variance; this
            # comparison uses exact equality and documents the limitation.
            expected_right = request.params.x + request.params.width
            expected_bottom = request.params.y + request.params.height
            verified = (
                rect[0] == request.params.x
                and rect[1] == request.params.y
                and rect[2] == expected_right
                and rect[3] == expected_bottom
            )
        return ExecutionResult(
            succeeded=True,
            message=f"move_resize executed for handle {request.params.handle}",
            observation=CapabilityObservation(
                summary="move_resize executed",
                data={
                    "handle": request.params.handle,
                    "geometry": request.params.to_dict(),
                    "verified_rect": verified,
                    "rect": observe.unwrap() if observe.is_success else None,
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowMoveResizeParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, WindowMoveResizeParams):
            raise TypeError(
                f"verify requires WindowMoveResizeParams, got {type(request.params).__name__}"
            )
        observe = self._native_surface.observe_window_rect(request.params.handle)
        if observe.is_failure:
            return VerificationResult(
                passed=False,
                detail="verification failed: could not observe window rect",
            )
        rect = observe.unwrap()
        expected_right = request.params.x + request.params.width
        expected_bottom = request.params.y + request.params.height
        if (
            rect[0] == request.params.x
            and rect[1] == request.params.y
            and rect[2] == expected_right
            and rect[3] == expected_bottom
        ):
            return VerificationResult(
                passed=True,
                detail=f"rect matches expected geometry for handle {request.params.handle}",
            )
        return VerificationResult(
            passed=False,
            detail=f"verification mismatch: rect={rect}, expected=({request.params.x},{request.params.y},{expected_right},{expected_bottom})",  # noqa: E501
        )


class WindowCloseCapability(_WindowCapabilityBase, Capability[WindowCloseParams]):
    def __init__(
        self,
        support: WindowsSupport,
        *,
        native_surface: WindowManagementNativeSurface | None = None,
    ) -> None:
        super().__init__(_close_descriptor(), support, native_surface=native_surface)

    def execute(
        self,
        request: CapabilityRequest[WindowCloseParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, WindowCloseParams):
            raise TypeError(
                f"close requires WindowCloseParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return ExecutionResult(
                succeeded=False,
                message="close cooperatively cancelled",
                observation=CapabilityObservation(
                    summary="cancelled before execution",
                    data={"cancelled": True},
                ),
            )
        if not self.is_supported:
            return self._unsupported_result("close requires supported Windows host")
        result = self._native_surface.close_window(request.params.handle)
        # Generic WM_CLOSE delivery is NOT verification; observation records the
        # delivery attempt but does not claim the window has closed.
        return ExecutionResult(
            succeeded=result.is_success,
            message=(
                "close request delivered"
                if result.is_success
                else f"close failed: {result.unwrap_error().message}"
            ),
            observation=CapabilityObservation(
                summary="close delivered" if result.is_success else "close delivery failed",
                data={
                    "handle": request.params.handle,
                    "delivery_confirmed": result.is_success,
                    "warning": "WM_CLOSE delivery does not prove window closed; independent absence check required",  # noqa: E501
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowCloseParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, WindowCloseParams):
            raise TypeError(
                f"verify requires WindowCloseParams, got {type(request.params).__name__}"
            )
        observe = self._native_surface.observe_window_exists(request.params.handle)
        if observe.is_failure:
            return VerificationResult(
                passed=False,
                detail="verification failed: could not observe window presence",
            )
        exists = observe.unwrap()
        if not exists:
            return VerificationResult(
                passed=True,
                detail=f"window {request.params.handle} independently confirmed absent",
            )
        return VerificationResult(
            passed=False,
            detail=f"verification mismatch: window {request.params.handle} still present after close request",  # noqa: E501
        )


# --------------------------------------------------------------------------
# Convenience request builders
# --------------------------------------------------------------------------


def focus_window_request(handle: int) -> CapabilityRequest[WindowFocusParams]:
    return CapabilityRequest(
        identity=WINDOW_FOCUS_IDENTITY,
        params=WindowFocusParams(handle=handle),
    )


def minimize_window_request(handle: int) -> CapabilityRequest[WindowMinimizeParams]:
    return CapabilityRequest(
        identity=WINDOW_MINIMIZE_IDENTITY,
        params=WindowMinimizeParams(handle=handle),
    )


def maximize_window_request(handle: int) -> CapabilityRequest[WindowMaximizeParams]:
    return CapabilityRequest(
        identity=WINDOW_MAXIMIZE_IDENTITY,
        params=WindowMaximizeParams(handle=handle),
    )


def restore_window_request(handle: int) -> CapabilityRequest[WindowRestoreParams]:
    return CapabilityRequest(
        identity=WINDOW_RESTORE_IDENTITY,
        params=WindowRestoreParams(handle=handle),
    )


def move_resize_window_request(
    handle: int, x: int, y: int, width: int, height: int
) -> CapabilityRequest[WindowMoveResizeParams]:
    return CapabilityRequest(
        identity=WINDOW_MOVE_RESIZE_IDENTITY,
        params=WindowMoveResizeParams(handle=handle, x=x, y=y, width=width, height=height),
    )


def close_window_request(handle: int) -> CapabilityRequest[WindowCloseParams]:
    return CapabilityRequest(
        identity=WINDOW_CLOSE_IDENTITY,
        params=WindowCloseParams(handle=handle),
    )
