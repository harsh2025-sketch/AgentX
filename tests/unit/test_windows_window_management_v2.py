"""Unit tests for the governed N2.20 window-management contract.

Every behaviour is exercised through a deterministic recording fake of the
injected native port: no test touches a real desktop, a real Windows host, or
any timing. Platform support is an explicit injected verdict, so both the
supported and the unsupported branch are pinned on every host.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from agentx.capabilities.abi import (
    CapabilityObservation,
    CapabilityParams,
    CapabilityPlatform,
    CapabilityRequest,
    ExecutionResult,
    RollbackSupport,
)
from agentx.capabilities.windows.provider import (
    WINDOWS_UNSUPPORTED_ERROR_CODE,
    PlatformFacts,
    WindowsSupport,
    evaluate_windows_support,
)
from agentx.capabilities.windows.window_management_v2 import (
    WINDOW_ACTIVATE_IDENTITY,
    WINDOW_MANAGEMENT_INVALID_NATIVE_DATA_ERROR_CODE,
    WINDOW_MANAGEMENT_OPERATION_MISMATCH_ERROR_CODE,
    WINDOW_MANAGEMENT_SURFACE_EXCEPTION_ERROR_CODE,
    WINDOW_MAXIMIZE_IDENTITY,
    WINDOW_MINIMIZE_IDENTITY,
    WINDOW_MOVE_RESIZE_IDENTITY,
    WINDOW_RESTORE_IDENTITY,
    NativeWindowManagementPort,
    NativeWindowMutationReceipt,
    WindowManagementCapability,
    WindowManagementOperation,
    WindowManagementParams,
    WindowManagementValidationError,
    WindowTarget,
    window_management_request,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskLevel

_ALL_OPERATIONS = tuple(WindowManagementOperation)
_SIMPLE_OPERATIONS = (
    WindowManagementOperation.ACTIVATE,
    WindowManagementOperation.MINIMIZE,
    WindowManagementOperation.MAXIMIZE,
    WindowManagementOperation.RESTORE,
)

_OPERATION_IDENTITIES = {
    WindowManagementOperation.ACTIVATE: WINDOW_ACTIVATE_IDENTITY,
    WindowManagementOperation.MINIMIZE: WINDOW_MINIMIZE_IDENTITY,
    WindowManagementOperation.MAXIMIZE: WINDOW_MAXIMIZE_IDENTITY,
    WindowManagementOperation.RESTORE: WINDOW_RESTORE_IDENTITY,
    WindowManagementOperation.MOVE_RESIZE: WINDOW_MOVE_RESIZE_IDENTITY,
}


class FakeWindowPort(NativeWindowManagementPort):
    """Deterministic recording fake of the injected N2.20 native port."""

    def __init__(self, answer: Result[NativeWindowMutationReceipt, AgentXError]) -> None:
        self.calls: list[tuple[str, int, tuple[int, int, int, int] | None]] = []
        self.answer = answer
        self.to_raise: Exception | None = None

    def _respond(
        self,
        name: str,
        handle: int,
        geometry: tuple[int, int, int, int] | None = None,
    ) -> Result[NativeWindowMutationReceipt, AgentXError]:
        self.calls.append((name, handle, geometry))
        if self.to_raise is not None:
            raise self.to_raise
        return self.answer

    def activate_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        return self._respond("activate", handle)

    def minimize_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        return self._respond("minimize", handle)

    def maximize_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        return self._respond("maximize", handle)

    def restore_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        return self._respond("restore", handle)

    def move_resize_window(
        self, handle: int, x: int, y: int, width: int, height: int
    ) -> Result[NativeWindowMutationReceipt, AgentXError]:
        return self._respond("move_resize", handle, (x, y, width, height))


class RawPort:
    """Non-conforming stub answering with whatever raw object the test arms."""

    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.calls: list[str] = []

    def _respond(self, name: str) -> object:
        self.calls.append(name)
        return self.answer

    def activate_window(self, handle: int) -> object:
        del handle
        return self._respond("activate_window")

    def minimize_window(self, handle: int) -> object:
        del handle
        return self._respond("minimize_window")

    def maximize_window(self, handle: int) -> object:
        del handle
        return self._respond("maximize_window")

    def restore_window(self, handle: int) -> object:
        del handle
        return self._respond("restore_window")

    def move_resize_window(self, handle: int, x: int, y: int, width: int, height: int) -> object:
        del handle, x, y, width, height
        return self._respond("move_resize_window")


@dataclass(frozen=True, slots=True)
class _OtherParams(CapabilityParams):
    """A foreign typed params object for boundary-mismatch tests."""

    def to_dict(self) -> dict[str, JsonValue]:
        return {}


def _windows_support() -> WindowsSupport:
    return evaluate_windows_support(
        PlatformFacts(system="Windows", release="11", version="10.0", machine="AMD64")
    )


def _linux_support() -> WindowsSupport:
    return evaluate_windows_support(
        PlatformFacts(system="Linux", release="6.8", version="#1 SMP", machine="x86_64")
    )


def _context() -> ExecutionContext:
    return ExecutionContext(correlation_id=uuid4(), cancellation_token=CancellationSource().token)


def _cancelled_context() -> ExecutionContext:
    source = CancellationSource()
    assert source.request_cancellation("unit test cancels") is True
    return ExecutionContext(correlation_id=uuid4(), cancellation_token=source.token)


def _receipt(
    handle: int,
    operation: WindowManagementOperation,
    *,
    accepted: bool = True,
    error_code: int | None = None,
) -> NativeWindowMutationReceipt:
    return NativeWindowMutationReceipt(
        handle=handle,
        operation=operation.value,
        native_accepted=accepted,
        native_error_code=error_code,
    )


def _port_for(handle: int, operation: WindowManagementOperation) -> FakeWindowPort:
    return FakeWindowPort(Result.success(_receipt(handle, operation)))


def _capability(
    operation: WindowManagementOperation,
    port: NativeWindowManagementPort,
    *,
    support: WindowsSupport | None = None,
) -> WindowManagementCapability:
    return WindowManagementCapability(
        operation=operation,
        support=support if support is not None else _windows_support(),
        native_port=port,
    )


def _request(
    operation: WindowManagementOperation,
    handle: int = 4242,
    *,
    x: int | None = None,
    y: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> CapabilityRequest[WindowManagementParams]:
    return window_management_request(
        operation, WindowTarget(handle=handle), x=x, y=y, width=width, height=height
    )


# --------------------------------------------------------------------------
# Explicit structured window targets.
# --------------------------------------------------------------------------


def test_window_target_accepts_an_explicit_positive_handle() -> None:
    target = WindowTarget(handle=4242)

    assert target.handle == 4242
    assert target.to_dict() == {"handle": 4242}


def test_window_target_accepts_the_full_native_handle_range() -> None:
    assert WindowTarget(handle=1).handle == 1
    assert WindowTarget(handle=(1 << 64) - 1).handle == (1 << 64) - 1


@pytest.mark.parametrize("handle", [0, -1, -(1 << 40), 1 << 64, 1 << 100])
def test_window_target_rejects_null_or_out_of_range_handles(handle: int) -> None:
    with pytest.raises(ValueError, match=r"target\.handle"):
        WindowTarget(handle=handle)


@pytest.mark.parametrize("handle", [True, False, "4242", None, 42.0, (4242,)])
def test_window_target_rejects_non_integer_handles(handle: object) -> None:
    with pytest.raises(TypeError, match=r"target\.handle"):
        WindowTarget(handle=handle)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Strict typed request parameters.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("operation", _SIMPLE_OPERATIONS)
def test_simple_operations_accept_a_target_without_geometry(
    operation: WindowManagementOperation,
) -> None:
    params = WindowManagementParams(operation=operation, target=WindowTarget(handle=7))

    assert params.operation is operation
    assert params.target == WindowTarget(handle=7)
    assert (params.x, params.y, params.width, params.height) == (None, None, None, None)
    assert params.to_dict() == {
        "operation": operation.value,
        "target": {"handle": 7},
        "x": None,
        "y": None,
        "width": None,
        "height": None,
    }


def test_move_resize_params_require_explicit_bounded_geometry() -> None:
    params = WindowManagementParams(
        operation=WindowManagementOperation.MOVE_RESIZE,
        target=WindowTarget(handle=7),
        x=-10,
        y=20,
        width=800,
        height=600,
    )

    assert params.to_dict()["x"] == -10
    assert params.to_dict()["height"] == 600


@pytest.mark.parametrize(
    "geometry",
    [
        {"x": None, "y": 0, "width": 10, "height": 10},
        {"x": 0, "y": None, "width": 10, "height": 10},
        {"x": 0, "y": 0, "width": None, "height": 10},
        {"x": 0, "y": 0, "width": 10, "height": None},
        {},
    ],
)
def test_move_resize_params_reject_missing_geometry(geometry: dict[str, int | None]) -> None:
    with pytest.raises(WindowManagementValidationError, match="explicit"):
        WindowManagementParams(
            operation=WindowManagementOperation.MOVE_RESIZE,
            target=WindowTarget(handle=7),
            **geometry,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("x", -32769),
        ("x", 32768),
        ("y", -32769),
        ("y", 32768),
        ("width", 0),
        ("width", -5),
        ("width", 32768),
        ("height", 0),
        ("height", 32768),
    ],
)
def test_move_resize_params_enforce_geometry_bounds(field: str, value: int) -> None:
    geometry: dict[str, int | None] = {"x": 0, "y": 0, "width": 10, "height": 10}
    geometry[field] = value

    with pytest.raises(ValueError, match=field):
        WindowManagementParams(
            operation=WindowManagementOperation.MOVE_RESIZE,
            target=WindowTarget(handle=7),
            **geometry,
        )


@pytest.mark.parametrize("field", ["x", "y", "width", "height"])
def test_move_resize_params_reject_boolean_geometry(field: str) -> None:
    geometry: dict[str, object] = {"x": 0, "y": 0, "width": 10, "height": 10}
    geometry[field] = True

    with pytest.raises(TypeError, match=field):
        WindowManagementParams(
            operation=WindowManagementOperation.MOVE_RESIZE,
            target=WindowTarget(handle=7),
            **geometry,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("operation", _SIMPLE_OPERATIONS)
def test_simple_operations_reject_any_geometry(operation: WindowManagementOperation) -> None:
    with pytest.raises(WindowManagementValidationError, match="does not accept"):
        WindowManagementParams(
            operation=operation, target=WindowTarget(handle=7), x=0, y=0, width=1, height=1
        )


def test_params_reject_a_non_enum_operation() -> None:
    with pytest.raises(TypeError, match="operation"):
        WindowManagementParams(
            operation="activate",  # type: ignore[arg-type]
            target=WindowTarget(handle=7),
        )


def test_params_reject_a_non_target_target() -> None:
    with pytest.raises(TypeError, match="target"):
        WindowManagementParams(
            operation=WindowManagementOperation.ACTIVATE,
            target=4242,  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------
# Canonical per-operation identities and request construction.
# --------------------------------------------------------------------------


def test_each_operation_has_a_distinct_canonical_identity() -> None:
    identities = list(_OPERATION_IDENTITIES.values())

    assert len(set(identities)) == 5
    assert WINDOW_ACTIVATE_IDENTITY.name.value == "windows.window.activate"
    assert WINDOW_MINIMIZE_IDENTITY.name.value == "windows.window.minimize"
    assert WINDOW_MAXIMIZE_IDENTITY.name.value == "windows.window.maximize"
    assert WINDOW_RESTORE_IDENTITY.name.value == "windows.window.restore"
    assert WINDOW_MOVE_RESIZE_IDENTITY.name.value == "windows.window.move_resize"
    for identity in identities:
        assert (identity.version.major, identity.version.minor, identity.version.patch) == (
            1,
            0,
            0,
        )


@pytest.mark.parametrize("operation", _ALL_OPERATIONS)
def test_request_builder_binds_each_operation_to_its_identity(
    operation: WindowManagementOperation,
) -> None:
    geometry: dict[str, int | None] = (
        {"x": 1, "y": 2, "width": 3, "height": 4}
        if operation is WindowManagementOperation.MOVE_RESIZE
        else {}
    )

    request = window_management_request(
        operation,
        WindowTarget(handle=9),
        **geometry,
    )

    assert request.identity == _OPERATION_IDENTITIES[operation]
    assert request.params.operation is operation
    assert request.params.target == WindowTarget(handle=9)


def test_request_builder_rejects_a_non_enum_operation() -> None:
    with pytest.raises(TypeError, match="operation"):
        window_management_request(
            "activate",  # type: ignore[arg-type]
            WindowTarget(handle=9),
        )


# --------------------------------------------------------------------------
# Capability governance: scope, permission, risk, rollback, estimate.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("operation", _ALL_OPERATIONS)
def test_descriptor_declares_windows_scope_and_external_permissions(
    operation: WindowManagementOperation,
) -> None:
    descriptor = _capability(operation, _port_for(1, operation)).descriptor

    assert descriptor.identity == _OPERATION_IDENTITIES[operation]
    assert descriptor.scope.platform is CapabilityPlatform.WINDOWS
    assert descriptor.required_permissions == frozenset(
        {Permission.WRITE, Permission.EXTERNAL_EFFECT}
    )
    assert descriptor.description.strip() == descriptor.description
    assert descriptor.description != ""


@pytest.mark.parametrize("operation", _ALL_OPERATIONS)
def test_descriptor_declares_r3_external_effect_risk(operation: WindowManagementOperation) -> None:
    risk = _capability(operation, _port_for(1, operation)).descriptor.risk_assessment

    assert risk.read_only is False
    assert risk.modifies_state is True
    assert risk.external_effect is True
    assert risk.level is RiskLevel.R3
    assert risk.effective_level is RiskLevel.R3


@pytest.mark.parametrize("operation", _ALL_OPERATIONS)
def test_descriptor_declares_no_rollback_and_a_bounded_estimate(
    operation: WindowManagementOperation,
) -> None:
    descriptor = _capability(operation, _port_for(1, operation)).descriptor

    assert descriptor.rollback.support is RollbackSupport.UNSUPPORTED
    assert descriptor.rollback.detail.strip() != ""
    assert descriptor.estimate.machine_actions == 1
    assert descriptor.estimate.wall_clock == timedelta(milliseconds=500)
    assert descriptor.estimate.external_cost == Decimal("0")
    assert len(descriptor.preconditions) == 2


def test_governance_facets_are_identical_across_operations() -> None:
    descriptors = [
        _capability(operation, _port_for(1, operation)).descriptor for operation in _ALL_OPERATIONS
    ]

    for descriptor in descriptors[1:]:
        assert descriptor.required_permissions == descriptors[0].required_permissions
        assert descriptor.risk_assessment == descriptors[0].risk_assessment
        assert descriptor.rollback == descriptors[0].rollback
        assert descriptor.estimate == descriptors[0].estimate
        assert descriptor.preconditions == descriptors[0].preconditions


# --------------------------------------------------------------------------
# Construction and immutability.
# --------------------------------------------------------------------------


def test_capability_rejects_a_non_enum_operation() -> None:
    with pytest.raises(TypeError, match="operation"):
        WindowManagementCapability(
            operation="activate",  # type: ignore[arg-type]
            support=_windows_support(),
            native_port=_port_for(1, WindowManagementOperation.ACTIVATE),
        )


def test_capability_rejects_a_non_support_verdict() -> None:
    with pytest.raises(TypeError, match="support"):
        WindowManagementCapability(
            operation=WindowManagementOperation.ACTIVATE,
            support="supported",  # type: ignore[arg-type]
            native_port=_port_for(1, WindowManagementOperation.ACTIVATE),
        )


def test_capability_requires_an_explicit_native_port() -> None:
    with pytest.raises(TypeError, match="native_port"):
        WindowManagementCapability(
            operation=WindowManagementOperation.ACTIVATE,
            support=_windows_support(),
            native_port=None,  # type: ignore[arg-type]
        )


def test_capability_is_immutable_after_construction() -> None:
    capability = _capability(
        WindowManagementOperation.ACTIVATE,
        _port_for(1, WindowManagementOperation.ACTIVATE),
    )

    with pytest.raises(AttributeError, match="immutable"):
        capability._operation = WindowManagementOperation.MINIMIZE
    with pytest.raises(AttributeError, match="immutable"):
        del capability._operation


def test_capability_exposes_operation_and_support() -> None:
    support = _windows_support()
    capability = _capability(
        WindowManagementOperation.MINIMIZE,
        _port_for(1, WindowManagementOperation.MINIMIZE),
        support=support,
    )

    assert capability.operation is WindowManagementOperation.MINIMIZE
    assert capability.support is support
    assert capability.is_supported is True


# --------------------------------------------------------------------------
# Execution through the injected port.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("operation", _SIMPLE_OPERATIONS)
def test_execute_reports_native_acceptance_without_claiming_verification(
    operation: WindowManagementOperation,
) -> None:
    port = _port_for(4242, operation)
    capability = _capability(operation, port)

    result = capability.execute(_request(operation), _context())

    assert result.succeeded is True
    assert "unverified" in result.message
    data = result.observation.to_dict()["data"]
    assert isinstance(data, dict)
    assert data["operation"] == operation.value
    assert data["target"] == {"handle": 4242}
    assert data["native_accepted"] is True
    assert data["verified"] is False
    assert port.calls == [(operation.value, 4242, None)]


def test_execute_move_resize_passes_explicit_geometry_to_the_port() -> None:
    port = _port_for(99, WindowManagementOperation.MOVE_RESIZE)
    capability = _capability(WindowManagementOperation.MOVE_RESIZE, port)

    result = capability.execute(
        _request(WindowManagementOperation.MOVE_RESIZE, 99, x=-5, y=6, width=70, height=80),
        _context(),
    )

    assert result.succeeded is True
    assert port.calls == [("move_resize", 99, (-5, 6, 70, 80))]
    data = result.observation.to_dict()["data"]
    assert isinstance(data, dict)
    assert data["geometry"] == {"x": -5, "y": 6, "width": 70, "height": 80}


def test_execute_refuses_a_request_for_another_operation() -> None:
    port = _port_for(4242, WindowManagementOperation.ACTIVATE)
    capability = _capability(WindowManagementOperation.ACTIVATE, port)

    result = capability.execute(_request(WindowManagementOperation.MINIMIZE), _context())

    assert result.succeeded is False
    assert "does not match" in result.message
    data = result.observation.to_dict()["data"]
    assert isinstance(data, dict)
    error = data["error"]
    assert isinstance(error, dict)
    assert error["code"] == WINDOW_MANAGEMENT_OPERATION_MISMATCH_ERROR_CODE
    assert port.calls == []


def test_execute_reports_a_native_failure_verbatim() -> None:
    failure = AgentXError(
        code="native.window.gone",
        message="the window vanished mid-call",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
    )
    port = FakeWindowPort(Result.failure(failure))
    capability = _capability(WindowManagementOperation.MAXIMIZE, port)

    result = capability.execute(_request(WindowManagementOperation.MAXIMIZE), _context())

    assert result.succeeded is False
    assert "window maximize failed for handle 4242" in result.message
    assert "the window vanished mid-call" not in result.message
    data = result.observation.to_dict()["data"]
    assert isinstance(data, dict)
    assert data["error"] == failure.to_dict()
    assert port.calls == [("maximize", 4242, None)]


def test_execute_reports_a_native_refusal_with_its_diagnostic_code() -> None:
    receipt = _receipt(4242, WindowManagementOperation.RESTORE, accepted=False, error_code=5)
    port = FakeWindowPort(Result.success(receipt))
    capability = _capability(WindowManagementOperation.RESTORE, port)

    result = capability.execute(_request(WindowManagementOperation.RESTORE), _context())

    assert result.succeeded is False
    assert "native_error_code=5" in result.message
    data = result.observation.to_dict()["data"]
    assert isinstance(data, dict)
    assert data["native_accepted"] is False
    assert data["native_error_code"] == 5


def test_execute_converts_a_port_exception_into_an_explicit_failure() -> None:
    port = _port_for(4242, WindowManagementOperation.ACTIVATE)
    port.to_raise = RuntimeError("adapter blew up")
    capability = _capability(WindowManagementOperation.ACTIVATE, port)

    result = capability.execute(_request(WindowManagementOperation.ACTIVATE), _context())

    assert result.succeeded is False
    data = result.observation.to_dict()["data"]
    assert isinstance(data, dict)
    error = data["error"]
    assert isinstance(error, dict)
    assert error["code"] == WINDOW_MANAGEMENT_SURFACE_EXCEPTION_ERROR_CODE
    details = error["details"]
    assert isinstance(details, dict)
    assert details["exception_type"] == "RuntimeError"


@pytest.mark.parametrize("answer", [None, "accepted", 1, {"accepted": True}])
def test_execute_rejects_a_non_result_port_answer(answer: object) -> None:
    port = RawPort(answer)
    capability = WindowManagementCapability(
        operation=WindowManagementOperation.MINIMIZE,
        support=_windows_support(),
        native_port=port,  # type: ignore[arg-type]
    )

    result = capability.execute(_request(WindowManagementOperation.MINIMIZE), _context())

    assert result.succeeded is False
    data = result.observation.to_dict()["data"]
    assert isinstance(data, dict)
    error = data["error"]
    assert isinstance(error, dict)
    assert error["code"] == WINDOW_MANAGEMENT_INVALID_NATIVE_DATA_ERROR_CODE
    assert port.calls == ["minimize_window"]


def test_execute_rejects_a_non_receipt_success_value() -> None:
    port = RawPort(Result.success("accepted"))
    capability = WindowManagementCapability(
        operation=WindowManagementOperation.MINIMIZE,
        support=_windows_support(),
        native_port=port,  # type: ignore[arg-type]
    )

    result = capability.execute(_request(WindowManagementOperation.MINIMIZE), _context())

    assert result.succeeded is False
    data = result.observation.to_dict()["data"]
    assert isinstance(data, dict)
    error = data["error"]
    assert isinstance(error, dict)
    assert error["code"] == WINDOW_MANAGEMENT_INVALID_NATIVE_DATA_ERROR_CODE


def test_execute_rejects_a_receipt_for_another_handle() -> None:
    receipt = _receipt(7777, WindowManagementOperation.ACTIVATE)
    port = FakeWindowPort(Result.success(receipt))
    capability = _capability(WindowManagementOperation.ACTIVATE, port)

    result = capability.execute(_request(WindowManagementOperation.ACTIVATE), _context())

    assert result.succeeded is False
    data = result.observation.to_dict()["data"]
    assert isinstance(data, dict)
    error = data["error"]
    assert isinstance(error, dict)
    assert error["code"] == WINDOW_MANAGEMENT_INVALID_NATIVE_DATA_ERROR_CODE


def test_execute_rejects_a_receipt_for_another_operation() -> None:
    receipt = _receipt(4242, WindowManagementOperation.MINIMIZE)
    port = FakeWindowPort(Result.success(receipt))
    capability = _capability(WindowManagementOperation.ACTIVATE, port)

    result = capability.execute(_request(WindowManagementOperation.ACTIVATE), _context())

    assert result.succeeded is False
    data = result.observation.to_dict()["data"]
    assert isinstance(data, dict)
    error = data["error"]
    assert isinstance(error, dict)
    assert error["code"] == WINDOW_MANAGEMENT_INVALID_NATIVE_DATA_ERROR_CODE


def test_execute_rejects_invalid_failure_data_from_the_port() -> None:
    port = RawPort(Result.failure("boom"))
    capability = WindowManagementCapability(
        operation=WindowManagementOperation.ACTIVATE,
        support=_windows_support(),
        native_port=port,  # type: ignore[arg-type]
    )

    result = capability.execute(_request(WindowManagementOperation.ACTIVATE), _context())

    assert result.succeeded is False
    data = result.observation.to_dict()["data"]
    assert isinstance(data, dict)
    error = data["error"]
    assert isinstance(error, dict)
    assert error["code"] == WINDOW_MANAGEMENT_INVALID_NATIVE_DATA_ERROR_CODE


def test_execute_honors_cancellation_before_touching_the_port() -> None:
    port = _port_for(4242, WindowManagementOperation.ACTIVATE)
    capability = _capability(WindowManagementOperation.ACTIVATE, port)

    result = capability.execute(_request(WindowManagementOperation.ACTIVATE), _cancelled_context())

    assert result.succeeded is False
    assert "cancelled" in result.message
    data = result.observation.to_dict()["data"]
    assert isinstance(data, dict)
    assert data["cancelled_before_start"] is True
    assert port.calls == []


def test_execute_refuses_an_unsupported_host_without_touching_the_port() -> None:
    port = _port_for(4242, WindowManagementOperation.ACTIVATE)
    capability = _capability(WindowManagementOperation.ACTIVATE, port, support=_linux_support())

    result = capability.execute(_request(WindowManagementOperation.ACTIVATE), _context())

    assert result.succeeded is False
    data = result.observation.to_dict()["data"]
    assert isinstance(data, dict)
    error = data["error"]
    assert isinstance(error, dict)
    assert error["code"] == WINDOWS_UNSUPPORTED_ERROR_CODE
    assert port.calls == []


def test_execute_rejects_foreign_typed_params() -> None:
    port = _port_for(4242, WindowManagementOperation.ACTIVATE)
    capability = _capability(WindowManagementOperation.ACTIVATE, port)
    foreign = CapabilityRequest(identity=WINDOW_ACTIVATE_IDENTITY, params=_OtherParams())

    with pytest.raises(TypeError, match="WindowManagementParams"):
        capability.execute(foreign, _context())  # type: ignore[arg-type]


def test_execute_rejects_a_non_request() -> None:
    port = _port_for(4242, WindowManagementOperation.ACTIVATE)
    capability = _capability(WindowManagementOperation.ACTIVATE, port)

    with pytest.raises(TypeError, match="CapabilityRequest"):
        capability.execute("request", _context())  # type: ignore[arg-type]


def test_execute_rejects_a_non_context() -> None:
    port = _port_for(4242, WindowManagementOperation.ACTIVATE)
    capability = _capability(WindowManagementOperation.ACTIVATE, port)

    with pytest.raises(TypeError, match="ExecutionContext"):
        capability.execute(
            _request(WindowManagementOperation.ACTIVATE),
            "context",  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------
# Verification truth boundary: N2.25 owns verification, so verify fails closed.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("operation", _ALL_OPERATIONS)
def test_verify_fails_closed_even_for_native_acceptance(
    operation: WindowManagementOperation,
) -> None:
    geometry: dict[str, int | None] = (
        {"x": 0, "y": 0, "width": 10, "height": 10}
        if operation is WindowManagementOperation.MOVE_RESIZE
        else {}
    )
    port = _port_for(4242, operation)
    capability = _capability(operation, port)
    request = window_management_request(
        operation,
        WindowTarget(handle=4242),
        **geometry,
    )
    execution = capability.execute(request, _context())
    assert execution.succeeded is True

    verdict = capability.verify(request, execution.observation, _context())

    assert verdict.passed is False
    assert "N2.25" in verdict.detail


def test_verify_fails_closed_for_failed_execution_evidence() -> None:
    port = FakeWindowPort(
        Result.failure(
            AgentXError(
                code="native.window.gone",
                message="gone",
                category=ErrorCategory.EXECUTION,
            )
        )
    )
    capability = _capability(WindowManagementOperation.MINIMIZE, port)
    request = _request(WindowManagementOperation.MINIMIZE)
    execution = capability.execute(request, _context())
    assert execution.succeeded is False

    verdict = capability.verify(request, execution.observation, _context())

    assert verdict.passed is False


def test_verify_rejects_wrongly_typed_inputs() -> None:
    port = _port_for(4242, WindowManagementOperation.ACTIVATE)
    capability = _capability(WindowManagementOperation.ACTIVATE, port)
    request = _request(WindowManagementOperation.ACTIVATE)
    observation = CapabilityObservation(summary="evidence", data={})
    context = _context()

    with pytest.raises(TypeError, match="CapabilityRequest"):
        capability.verify("request", observation, context)  # type: ignore[arg-type]
    foreign = CapabilityRequest(identity=WINDOW_ACTIVATE_IDENTITY, params=_OtherParams())
    with pytest.raises(TypeError, match="WindowManagementParams"):
        capability.verify(foreign, observation, context)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="CapabilityObservation"):
        capability.verify(request, "observation", context)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ExecutionContext"):
        capability.verify(request, observation, "context")  # type: ignore[arg-type]


def test_native_success_without_verification_cannot_become_task_success() -> None:
    """Execution evidence plus a fail-closed verdict is never a success claim."""
    port = _port_for(4242, WindowManagementOperation.ACTIVATE)
    capability = _capability(WindowManagementOperation.ACTIVATE, port)
    request = _request(WindowManagementOperation.ACTIVATE)
    execution = capability.execute(request, _context())
    assert isinstance(execution, ExecutionResult)
    assert execution.succeeded is True

    verdict = capability.verify(request, execution.observation, _context())

    assert verdict.passed is False
    assert execution.observation.to_dict()["data"] != {"verified": True}
