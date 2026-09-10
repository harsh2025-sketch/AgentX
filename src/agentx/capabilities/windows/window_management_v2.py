"""Governed Windows window-management capability contract (N2.20).

This module owns the PUBLIC GOVERNED CAPABILITY CONTRACT / ADAPTER layer for
Windows window management. It exposes five explicitly governed mutations over
already-identified top-level windows:

    - ``activate``: bring one explicitly identified window forward for
      user interaction;
    - ``minimize`` / ``maximize`` / ``restore``: change one explicitly
      identified window's show state;
    - ``move_resize``: move and resize one explicitly identified window to
      explicit bounded coordinates and size.

Every operation is one canonical
:class:`~agentx.capabilities.abi.Capability` identity
(``windows.window.activate``, ``windows.window.minimize``,
``windows.window.maximize``, ``windows.window.restore``,
``windows.window.move_resize``), constructed per operation through
:class:`WindowManagementCapability` in the style of the canonical browser
action boundary: the descriptor is fixed at construction and request data can
never change permission, risk, or identity.

Native-port independence
------------------------

This module performs no native call and names no native API. The narrowest
sufficient low-level surface is declared here as the injected
:class:`NativeWindowManagementPort` protocol, and every capability requires an
explicit port implementation at construction: N2.20 ships no default native
implementation and has no dependency on any unmerged native-seam branch. The
Secret Integration Agent later binds the port to the canonical native seam;
production behaviour here is proven with deterministic fake ports.

Targeting
---------

Every request carries an explicit structured :class:`WindowTarget`: a
validated native window handle. There is no title, name, text, or query field
anywhere in the parameter contract, so targets are never resolved from
natural language, never inferred from window text, and never defaulted to
whatever window happens to be in the foreground. A null, negative, or
out-of-range handle is rejected at the typed boundary.

Governance
----------

Window mutations are externally observable state changes. Every operation
therefore declares ``WRITE`` plus ``EXTERNAL_EFFECT`` permission and an
``R3 EXTERNAL_EFFECT`` risk assessment, so the canonical ActionGate requires
confirmation and the run stays blocked pending a separate approval flow. The
capability itself never consults the PermissionEngine or the ActionGate: real
execution happens only through the canonical CapabilityExecutionLoop /
Trusted Kernel path. Rollback is explicitly unsupported (no prior window
state is captured), the resource estimate is bounded (one machine action),
and platform support is explicit through the canonical A5.01 verdict.

Verification truth boundary
---------------------------

State-transition verification belongs to N2.25 and is NOT implemented here.
``verify`` therefore always fails closed: a native acceptance report is
execution evidence only and can never become verified user-state success.

Hostile data
------------

Window text and native diagnostics are untrusted data. They are stored
verbatim in observation evidence and authorize nothing: they cannot grant
permission, change risk, select a target, bypass the ActionGate, or mark
success.

Deliberate non-scope
--------------------

No UI Automation element invocation, no keyboard/text input, no mouse click,
no coordinates outside an explicit move/resize geometry, no application
launch, no process lifecycle operation, no shell, no visual fallback, no
dialogs, no screenshots, and no state-transition verification.

Owner: N2.20. Belongs to ``agentx.capabilities.windows``; imports only the
standard library, canonical ``agentx.core``/``agentx.kernel`` contracts, the
canonical capability ABI, and the A5.01 provider boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
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
from agentx.capabilities.windows.provider import WindowsSupport, unsupported_platform_error
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk

__all__ = [
    "WINDOW_ACTIVATE_IDENTITY",
    "WINDOW_MANAGEMENT_INVALID_NATIVE_DATA_ERROR_CODE",
    "WINDOW_MANAGEMENT_OPERATION_MISMATCH_ERROR_CODE",
    "WINDOW_MANAGEMENT_SURFACE_EXCEPTION_ERROR_CODE",
    "WINDOW_MAXIMIZE_IDENTITY",
    "WINDOW_MINIMIZE_IDENTITY",
    "WINDOW_MOVE_RESIZE_IDENTITY",
    "WINDOW_RESTORE_IDENTITY",
    "NativeWindowManagementPort",
    "NativeWindowMutationReceipt",
    "WindowManagementCapability",
    "WindowManagementOperation",
    "WindowManagementParams",
    "WindowManagementValidationError",
    "WindowTarget",
    "window_management_request",
]

#: Canonical error code for a native port that raised instead of answering.
WINDOW_MANAGEMENT_SURFACE_EXCEPTION_ERROR_CODE: Final[str] = (
    "capabilities.windows.window_management.surface_exception"
)

#: Canonical error code for a native port answer that cannot be trusted.
WINDOW_MANAGEMENT_INVALID_NATIVE_DATA_ERROR_CODE: Final[str] = (
    "capabilities.windows.window_management.invalid_native_data"
)

#: Canonical error code for a request routed to the wrong operation instance.
WINDOW_MANAGEMENT_OPERATION_MISMATCH_ERROR_CODE: Final[str] = (
    "capabilities.windows.window_management.operation_mismatch"
)

#: Native window handles are positive and must fit a native pointer slot.
_MIN_WINDOW_HANDLE: Final[int] = 1
_MAX_WINDOW_HANDLE: Final[int] = (1 << 64) - 1

#: Conservative bounded geometry for an explicit move/resize request.
_MIN_POSITION: Final[int] = -32768
_MAX_POSITION: Final[int] = 32767
_MIN_SIZE: Final[int] = 1
_MAX_SIZE: Final[int] = 32767

#: Verification is owned by N2.25; this boundary never verifies window state.
_UNVERIFIED_DETAIL: Final[str] = (
    "Window-management state-transition verification is owned by N2.25 and is not "
    "implemented here; a native acceptance report never verifies user-visible window state."
)


class WindowManagementOperation(StrEnum):
    """Closed vocabulary of governed window mutations. One per request."""

    ACTIVATE = "activate"
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"
    RESTORE = "restore"
    MOVE_RESIZE = "move_resize"


def _require_handle(value: object, *, field_name: str) -> int:
    """Validate one explicit native window handle (bools rejected)."""
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    handle = value
    if not _MIN_WINDOW_HANDLE <= handle <= _MAX_WINDOW_HANDLE:
        raise ValueError(
            f"{field_name} must be between {_MIN_WINDOW_HANDLE} and {_MAX_WINDOW_HANDLE}, "
            f"got {handle}"
        )
    return handle


def _require_position(value: object, *, field_name: str) -> int:
    """Validate one explicit move/resize coordinate (bools rejected)."""
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    position = value
    if not _MIN_POSITION <= position <= _MAX_POSITION:
        raise ValueError(
            f"{field_name} must be between {_MIN_POSITION} and {_MAX_POSITION}, got {position}"
        )
    return position


def _require_size(value: object, *, field_name: str) -> int:
    """Validate one explicit move/resize extent (bools rejected)."""
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    size = value
    if not _MIN_SIZE <= size <= _MAX_SIZE:
        raise ValueError(f"{field_name} must be between {_MIN_SIZE} and {_MAX_SIZE}, got {size}")
    return size


@dataclass(frozen=True, slots=True)
class WindowTarget:
    """Explicit structured identity of one managed window.

    The handle names the window as of the request; the operating system may
    recycle handles, so a target is never trusted beyond the single governed
    invocation that carries it. There is deliberately no title, name, text,
    or query: window text is untrusted data and can never select a target.
    """

    handle: int

    def __post_init__(self) -> None:
        _require_handle(self.handle, field_name="target.handle")

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the JSON-compatible representation of this target."""
        return {"handle": self.handle}


@dataclass(frozen=True, slots=True)
class NativeWindowMutationReceipt:
    """Typed low-level answer from the injected native port.

    A receipt is execution evidence, never verification: ``native_accepted``
    reports only that the native layer accepted the call. The capability
    checks that the receipt names the requested handle and operation before
    trusting even that much; anything else is rejected as invalid native
    data. ``native_error_code`` is an untrusted diagnostic integer carried
    verbatim when present.
    """

    handle: int
    operation: str
    native_accepted: bool
    native_error_code: int | None

    def __post_init__(self) -> None:
        _require_handle(self.handle, field_name="receipt.handle")
        if not isinstance(self.operation, str):
            raise TypeError(
                f"receipt.operation must be a string, got {type(self.operation).__name__}"
            )
        if not self.operation or self.operation != self.operation.strip():
            raise ValueError("receipt.operation must be non-empty and trimmed")
        if type(self.native_accepted) is not bool:
            raise TypeError(
                f"receipt.native_accepted must be bool, got {type(self.native_accepted).__name__}"
            )
        if self.native_error_code is not None:
            if type(self.native_error_code) is not int:
                raise TypeError(
                    "receipt.native_error_code must be an int or None, "
                    f"got {type(self.native_error_code).__name__}"
                )
            if not 0 <= self.native_error_code <= 0xFFFFFFFF:
                raise ValueError(
                    "receipt.native_error_code must be between 0 and 4294967295, "
                    f"got {self.native_error_code}"
                )

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the JSON-compatible representation of this receipt."""
        return {
            "handle": self.handle,
            "operation": self.operation,
            "native_accepted": self.native_accepted,
            "native_error_code": self.native_error_code,
        }


class NativeWindowManagementPort(Protocol):
    """Narrow injected native port for the five governed window mutations.

    This protocol is the whole native surface N2.20 needs: one low-level
    call per governed operation, each answering with a typed receipt or a
    canonical error. It is implemented by fakes in tests today and bound to
    the canonical native seam by the Secret Integration Agent later. It
    exposes no invocation, input, launch, shell, capture, or query surface.
    """

    def activate_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        """Bring the identified window forward for user interaction."""
        ...

    def minimize_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        """Minimize the identified window."""
        ...

    def maximize_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        """Maximize the identified window."""
        ...

    def restore_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        """Restore the identified window to its normal size."""
        ...

    def move_resize_window(
        self, handle: int, x: int, y: int, width: int, height: int
    ) -> Result[NativeWindowMutationReceipt, AgentXError]:
        """Move and resize the identified window to explicit bounded geometry."""
        ...


class WindowManagementValidationError(ValueError):
    """Raised when window-management parameters are structurally inconsistent."""


@dataclass(frozen=True, slots=True)
class WindowManagementParams(CapabilityParams):
    """Typed parameters for exactly one governed window mutation.

    ``move_resize`` requires all four explicit bounded geometry fields;
    every other operation rejects geometry outright. No operation accepts
    text of any kind, so there is nothing to resolve and nothing to default.
    """

    operation: WindowManagementOperation
    target: WindowTarget
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, WindowManagementOperation):
            raise TypeError(
                "operation must be a WindowManagementOperation, "
                f"got {type(self.operation).__name__}"
            )
        if not isinstance(self.target, WindowTarget):
            raise TypeError(f"target must be a WindowTarget, got {type(self.target).__name__}")
        geometry = (self.x, self.y, self.width, self.height)
        if self.operation is WindowManagementOperation.MOVE_RESIZE:
            if self.x is None or self.y is None or self.width is None or self.height is None:
                raise WindowManagementValidationError(
                    "move_resize requires explicit x, y, width, and height"
                )
            _require_position(self.x, field_name="params.x")
            _require_position(self.y, field_name="params.y")
            _require_size(self.width, field_name="params.width")
            _require_size(self.height, field_name="params.height")
            return
        if any(field is not None for field in geometry):
            raise WindowManagementValidationError(
                f"{self.operation.value} does not accept window geometry"
            )

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the JSON-compatible representation of these parameters."""
        return {
            "operation": self.operation.value,
            "target": self.target.to_dict(),
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


#: Canonical identity of the activate operation.
WINDOW_ACTIVATE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.window.activate"),
    version=CapabilityVersion(1, 0, 0),
)

#: Canonical identity of the minimize operation.
WINDOW_MINIMIZE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.window.minimize"),
    version=CapabilityVersion(1, 0, 0),
)

#: Canonical identity of the maximize operation.
WINDOW_MAXIMIZE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.window.maximize"),
    version=CapabilityVersion(1, 0, 0),
)

#: Canonical identity of the restore operation.
WINDOW_RESTORE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.window.restore"),
    version=CapabilityVersion(1, 0, 0),
)

#: Canonical identity of the move/resize operation.
WINDOW_MOVE_RESIZE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.window.move_resize"),
    version=CapabilityVersion(1, 0, 0),
)


def _identity_for(operation: WindowManagementOperation) -> CapabilityIdentity:
    """Return the canonical identity of one window-management operation."""
    if operation is WindowManagementOperation.ACTIVATE:
        return WINDOW_ACTIVATE_IDENTITY
    if operation is WindowManagementOperation.MINIMIZE:
        return WINDOW_MINIMIZE_IDENTITY
    if operation is WindowManagementOperation.MAXIMIZE:
        return WINDOW_MAXIMIZE_IDENTITY
    if operation is WindowManagementOperation.RESTORE:
        return WINDOW_RESTORE_IDENTITY
    if operation is WindowManagementOperation.MOVE_RESIZE:
        return WINDOW_MOVE_RESIZE_IDENTITY
    raise TypeError(f"unsupported window-management operation: {operation!r}")


def _description_for(operation: WindowManagementOperation) -> str:
    """Return the inert human description of one window-management operation."""
    if operation is WindowManagementOperation.ACTIVATE:
        return (
            "Bring one explicitly identified window forward for user interaction. "
            "The window is named only by its validated handle."
        )
    if operation is WindowManagementOperation.MINIMIZE:
        return "Minimize one explicitly identified window, named only by its validated handle."
    if operation is WindowManagementOperation.MAXIMIZE:
        return "Maximize one explicitly identified window, named only by its validated handle."
    if operation is WindowManagementOperation.RESTORE:
        return (
            "Restore one explicitly identified minimized or maximized window to its "
            "normal size, named only by its validated handle."
        )
    if operation is WindowManagementOperation.MOVE_RESIZE:
        return (
            "Move and resize one explicitly identified window to explicit bounded "
            "coordinates and size, named only by its validated handle."
        )
    raise TypeError(f"unsupported window-management operation: {operation!r}")


def _descriptor_for(operation: WindowManagementOperation) -> CapabilityDescriptor:
    """Return the fixed canonical descriptor of one window-management operation.

    Every operation mutates externally observable window state, so every
    descriptor declares ``WRITE`` plus ``EXTERNAL_EFFECT`` permission with an
    ``R3 EXTERNAL_EFFECT`` risk assessment: the ActionGate must require
    confirmation and the run stays blocked pending a separate approval flow.
    """
    return CapabilityDescriptor(
        identity=_identity_for(operation),
        description=_description_for(operation),
        scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
        required_permissions=frozenset({Permission.WRITE, Permission.EXTERNAL_EFFECT}),
        risk_assessment=assess_risk(
            read_only=False,
            modifies_state=True,
            reversible=False,
            external_effect=True,
        ),
        preconditions=(
            CapabilityPrecondition(
                name="windows.supported_host",
                description=(
                    "The host must evaluate as a supported Windows host through the "
                    "A5.01 provider boundary before any window mutation may run."
                ),
            ),
            CapabilityPrecondition(
                name="windows.explicit_target",
                description=(
                    "The request must carry an explicitly validated window target; "
                    "targets are never resolved from text and never defaulted."
                ),
            ),
        ),
        rollback=RollbackDeclaration(
            support=RollbackSupport.UNSUPPORTED,
            detail=(
                "This capability captures no prior window state and performs no "
                "rollback; only a later explicit window-management request can "
                "change the window again."
            ),
        ),
        estimate=ResourceEstimate(
            wall_clock=timedelta(milliseconds=500),
            machine_actions=1,
            external_cost=Decimal("0"),
        ),
    )


def _surface_exception(operation: str, handle: int, exc: Exception) -> AgentXError:
    """Build the canonical failure for a native port that raised."""
    return AgentXError(
        code=WINDOW_MANAGEMENT_SURFACE_EXCEPTION_ERROR_CODE,
        message=f"window-management native port raised while attempting {operation}",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
        details={
            "exception_type": type(exc).__name__,
            "operation": operation,
            "handle": handle,
        },
    )


def _invalid_native_data(detail: str, *, operation: str, handle: int) -> AgentXError:
    """Build the canonical failure for an unusable native port answer."""
    return AgentXError(
        code=WINDOW_MANAGEMENT_INVALID_NATIVE_DATA_ERROR_CODE,
        message=f"window-management native port returned unusable data: {detail}",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
        details={"operation": operation, "handle": handle},
    )


class WindowManagementCapability:
    """One governed Windows window mutation as a canonical Capability.

    Construct one instance per operation. The descriptor is fixed at
    construction from the operation identity; request data cannot change
    permission, risk, or identity. The injected native port is required and
    explicit: N2.20 ships no default native implementation. Execution never
    consults the PermissionEngine or the ActionGate directly; the
    CapabilityExecutionLoop remains the only authority path.
    """

    __slots__ = ("_descriptor", "_native_port", "_operation", "_support")

    _descriptor: CapabilityDescriptor
    _native_port: NativeWindowManagementPort
    _operation: WindowManagementOperation
    _support: WindowsSupport

    def __init__(
        self,
        *,
        operation: WindowManagementOperation,
        support: WindowsSupport,
        native_port: NativeWindowManagementPort,
    ) -> None:
        """Bind one operation, one support verdict, and one explicit port."""
        if not isinstance(operation, WindowManagementOperation):
            raise TypeError(
                f"operation must be a WindowManagementOperation, got {type(operation).__name__}"
            )
        if not isinstance(support, WindowsSupport):
            raise TypeError(f"support must be a WindowsSupport, got {type(support).__name__}")
        if native_port is None:
            raise TypeError(
                "native_port must be an explicit NativeWindowManagementPort; "
                "N2.20 ships no default native implementation"
            )
        object.__setattr__(self, "_operation", operation)
        object.__setattr__(self, "_support", support)
        object.__setattr__(self, "_native_port", native_port)
        object.__setattr__(self, "_descriptor", _descriptor_for(operation))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"WindowManagementCapability is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"WindowManagementCapability is immutable; cannot delete {name!r}")

    @property
    def descriptor(self) -> CapabilityDescriptor:
        """The inert canonical descriptor governing this operation."""
        return self._descriptor

    @property
    def operation(self) -> WindowManagementOperation:
        """The single governed operation this instance performs."""
        return self._operation

    @property
    def support(self) -> WindowsSupport:
        """The explicit support verdict supplied at construction."""
        return self._support

    @property
    def is_supported(self) -> bool:
        """Whether the described host is a supported Windows host."""
        return self._support.is_supported

    def execute(
        self,
        request: CapabilityRequest[WindowManagementParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        """Attempt one window mutation through the injected native port."""
        if not isinstance(request, CapabilityRequest):
            raise TypeError(f"request must be a CapabilityRequest, got {type(request).__name__}")
        if not isinstance(context, ExecutionContext):
            raise TypeError(f"context must be an ExecutionContext, got {type(context).__name__}")
        params = request.params
        if not isinstance(params, WindowManagementParams):
            raise TypeError(
                f"window management requires WindowManagementParams, got {type(params).__name__}"
            )
        operation = self._operation.value
        target_data = params.target.to_dict()
        if params.operation is not self._operation:
            return ExecutionResult(
                succeeded=False,
                message=(
                    f"request operation {params.operation.value} does not match "
                    f"capability operation {operation}; refusing without touching "
                    "the native port"
                ),
                observation=CapabilityObservation(
                    summary="window-management operation mismatch refused",
                    data={
                        "operation": operation,
                        "requested_operation": params.operation.value,
                        "target": target_data,
                        "error": AgentXError(
                            code=WINDOW_MANAGEMENT_OPERATION_MISMATCH_ERROR_CODE,
                            message="request operation does not match capability operation",
                            category=ErrorCategory.VALIDATION,
                            retryability=Retryability.NON_RETRYABLE,
                        ).to_dict(),
                    },
                ),
            )
        if context.observe_stop().should_stop:
            reasons = ", ".join(reason.value for reason in context.observe_stop().reasons)
            return ExecutionResult(
                succeeded=False,
                message=(
                    f"window {operation} was cooperatively cancelled before any "
                    f"native call ({reasons})"
                ),
                observation=CapabilityObservation(
                    summary="window management cancelled before any native call",
                    data={
                        "operation": operation,
                        "target": target_data,
                        "cancelled_before_start": True,
                    },
                ),
            )
        if not self._support.is_supported:
            error = unsupported_platform_error(self._support)
            return ExecutionResult(
                succeeded=False,
                message=f"window {operation} refused: {error.message}",
                observation=CapabilityObservation(
                    summary="window management refused on an unsupported host",
                    data={
                        "operation": operation,
                        "target": target_data,
                        "error": error.to_dict(),
                    },
                ),
            )
        handle = params.target.handle
        try:
            outcome_object: object = self._dispatch(params)
        except Exception as exc:
            error = _surface_exception(operation, handle, exc)
            return ExecutionResult(
                succeeded=False,
                message=error.message,
                observation=CapabilityObservation(
                    summary="window-management native port raised",
                    data={
                        "operation": operation,
                        "target": target_data,
                        "error": error.to_dict(),
                    },
                ),
            )
        if not isinstance(outcome_object, Result):
            error = _invalid_native_data(
                "native port returned a non-Result answer",
                operation=operation,
                handle=handle,
            )
            return ExecutionResult(
                succeeded=False,
                message=error.message,
                observation=CapabilityObservation(
                    summary="window-management native port returned unusable data",
                    data={
                        "operation": operation,
                        "target": target_data,
                        "error": error.to_dict(),
                    },
                ),
            )
        if outcome_object.is_failure:
            failure_object: object = outcome_object.unwrap_error()
            if not isinstance(failure_object, AgentXError):
                error = _invalid_native_data(
                    "native port returned invalid failure data",
                    operation=operation,
                    handle=handle,
                )
                return ExecutionResult(
                    succeeded=False,
                    message=error.message,
                    observation=CapabilityObservation(
                        summary="window-management native port returned unusable data",
                        data={
                            "operation": operation,
                            "target": target_data,
                            "error": error.to_dict(),
                        },
                    ),
                )
            # The native message stays verbatim in the observation data below;
            # it is never interpolated into the result message, whose contract
            # forbids control characters and bounds its length.
            return ExecutionResult(
                succeeded=False,
                message=(
                    f"window {operation} failed for handle {handle}; see observation error details"
                ),
                observation=CapabilityObservation(
                    summary=f"window {operation} failed at the native port",
                    data={
                        "operation": operation,
                        "target": target_data,
                        "error": failure_object.to_dict(),
                    },
                ),
            )
        receipt_object: object = outcome_object.unwrap()
        if (
            not isinstance(receipt_object, NativeWindowMutationReceipt)
            or receipt_object.handle != handle
            or receipt_object.operation != operation
        ):
            error = _invalid_native_data(
                "native port receipt names a different target or operation",
                operation=operation,
                handle=handle,
            )
            return ExecutionResult(
                succeeded=False,
                message=error.message,
                observation=CapabilityObservation(
                    summary="window-management native port returned unusable data",
                    data={
                        "operation": operation,
                        "target": target_data,
                        "error": error.to_dict(),
                    },
                ),
            )
        if not receipt_object.native_accepted:
            return ExecutionResult(
                succeeded=False,
                message=(
                    f"native window {operation} reported refusal for handle {handle} "
                    f"(native_error_code={receipt_object.native_error_code})"
                ),
                observation=CapabilityObservation(
                    summary=f"window {operation} refused at the native port",
                    data={
                        "operation": operation,
                        "target": target_data,
                        "native_accepted": False,
                        "native_error_code": receipt_object.native_error_code,
                    },
                ),
            )
        return ExecutionResult(
            succeeded=True,
            message=(
                f"native window {operation} accepted for handle {handle}; "
                "user-visible state remains unverified"
            ),
            observation=CapabilityObservation(
                summary="native window-management execution evidence (unverified)",
                data={
                    "operation": operation,
                    "target": target_data,
                    "geometry": {
                        "x": params.x,
                        "y": params.y,
                        "width": params.width,
                        "height": params.height,
                    },
                    "native_accepted": True,
                    "native_error_code": receipt_object.native_error_code,
                    "verified": False,
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowManagementParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        """Fail closed: window-state verification belongs to N2.25.

        A native acceptance report is execution evidence only. This boundary
        never re-reads window state and never claims the intended user-visible
        state was achieved, so every verdict here is ``passed=False``.
        """
        if not isinstance(request, CapabilityRequest):
            raise TypeError(f"request must be a CapabilityRequest, got {type(request).__name__}")
        if not isinstance(request.params, WindowManagementParams):
            raise TypeError(
                "window management requires WindowManagementParams, got "
                f"{type(request.params).__name__}"
            )
        if not isinstance(observation, CapabilityObservation):
            raise TypeError(
                f"observation must be a CapabilityObservation, got {type(observation).__name__}"
            )
        if not isinstance(context, ExecutionContext):
            raise TypeError(f"context must be an ExecutionContext, got {type(context).__name__}")
        return VerificationResult(passed=False, detail=_UNVERIFIED_DETAIL)

    def _dispatch(
        self, params: WindowManagementParams
    ) -> Result[NativeWindowMutationReceipt, AgentXError]:
        """Call the single native port method for this instance's operation."""
        handle = params.target.handle
        if self._operation is WindowManagementOperation.ACTIVATE:
            return self._native_port.activate_window(handle)
        if self._operation is WindowManagementOperation.MINIMIZE:
            return self._native_port.minimize_window(handle)
        if self._operation is WindowManagementOperation.MAXIMIZE:
            return self._native_port.maximize_window(handle)
        if self._operation is WindowManagementOperation.RESTORE:
            return self._native_port.restore_window(handle)
        x, y, width, height = params.x, params.y, params.width, params.height
        if x is None or y is None or width is None or height is None:
            raise WindowManagementValidationError(
                "move_resize requires explicit x, y, width, and height"
            )
        return self._native_port.move_resize_window(handle, x, y, width, height)


def window_management_request(
    operation: WindowManagementOperation,
    target: WindowTarget,
    *,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> CapabilityRequest[WindowManagementParams]:
    """Build a canonical request for one window-management operation."""
    if not isinstance(operation, WindowManagementOperation):
        raise TypeError(
            f"operation must be a WindowManagementOperation, got {type(operation).__name__}"
        )
    return CapabilityRequest(
        identity=_identity_for(operation),
        params=WindowManagementParams(
            operation=operation, target=target, x=x, y=y, width=width, height=height
        ),
    )
