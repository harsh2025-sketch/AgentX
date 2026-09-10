"""Authority-A seam regressions for accepted Wave-2 tasks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from agentx.capabilities.windows.native_mutation import (
    NativeClipboardMutationOutcome,
    NativeClipboardTextRequest,
    NativeInputInjectionOutcome,
    NativeKeyInputRequest,
    NativeProcessLaunchOutcome,
    NativeProcessLaunchRequest,
    NativeTextInputRequest,
    NativeWindowActivationOutcome,
    NativeWindowActivationRequest,
    NativeWindowMoveResizeOutcome,
    NativeWindowMoveResizeRequest,
    NativeWindowStateOutcome,
    NativeWindowStateRequest,
)
from agentx.capabilities.windows.window_management_native_adapter import (
    CanonicalNativeWindowManagementAdapter,
)
from agentx.cognition.model_provider import ModelId, ModelUsage, ProviderId
from agentx.cognition.router import ExecutionLevel
from agentx.core.errors import AgentXError
from agentx.core.ids import TaskId
from agentx.core.result import Result
from agentx.execution_metrics import ExecutionMetricsRecorder, ModelCallEvent

_T0 = datetime(2026, 9, 9, 0, 0, tzinfo=UTC)


def _model(name: str) -> ModelId:
    return ModelId(ProviderId("authority-a"), name)


def test_partial_model_usage_cannot_masquerade_as_exact_run_aggregate() -> None:
    recorder = ExecutionMetricsRecorder(
        task_id=TaskId.create(),
        correlation_id=uuid4(),
        execution_level=ExecutionLevel.L3_GUIDED,
        cost_unit="USD",
    )
    recorder.start(started_at=_T0)
    recorder.record_model_call(
        ModelCallEvent(
            model_id=_model("reported"),
            usage=ModelUsage(
                input_tokens=10,
                output_tokens=5,
                external_cost=Decimal("0.01"),
            ),
        )
    )
    recorder.record_model_call(ModelCallEvent(model_id=_model("missing"), usage=None))
    record = recorder.finish(ended_at=_T0 + timedelta(seconds=1))

    assert record.model_calls == 2
    assert record.model_input_tokens is None
    assert record.model_output_tokens is None
    assert record.model_tokens is None
    assert record.external_cost is None
    assert record.cost_unit is None


class _NativeSurfaceFake:
    def __init__(self) -> None:
        self.move_request: NativeWindowMoveResizeRequest | None = None

    def launch_process(
        self, request: NativeProcessLaunchRequest
    ) -> Result[NativeProcessLaunchOutcome, AgentXError]:
        raise AssertionError("not used")

    def set_window_state(
        self, request: NativeWindowStateRequest
    ) -> Result[NativeWindowStateOutcome, AgentXError]:
        return Result.success(NativeWindowStateOutcome(request.window_handle, request.state, False))

    def activate_window(
        self, request: NativeWindowActivationRequest
    ) -> Result[NativeWindowActivationOutcome, AgentXError]:
        return Result.success(NativeWindowActivationOutcome(request.window_handle, True, 0))

    def move_resize_window(
        self, request: NativeWindowMoveResizeRequest
    ) -> Result[NativeWindowMoveResizeOutcome, AgentXError]:
        self.move_request = request
        return Result.success(
            NativeWindowMoveResizeOutcome(
                request.window_handle,
                request.x,
                request.y,
                request.width,
                request.height,
                True,
                0,
            )
        )

    def send_text(
        self, request: NativeTextInputRequest
    ) -> Result[NativeInputInjectionOutcome, AgentXError]:
        raise AssertionError("not used")

    def send_key_strokes(
        self, request: NativeKeyInputRequest
    ) -> Result[NativeInputInjectionOutcome, AgentXError]:
        raise AssertionError("not used")

    def set_clipboard_text(
        self, request: NativeClipboardTextRequest
    ) -> Result[NativeClipboardMutationOutcome, AgentXError]:
        raise AssertionError("not used")


def test_n2_20_move_resize_binds_only_through_n2_19_typed_surface() -> None:
    native = _NativeSurfaceFake()
    adapter = CanonicalNativeWindowManagementAdapter(native)
    result = adapter.move_resize_window(77, -10, 20, 640, 480)

    assert result.is_success
    receipt = result.unwrap()
    assert receipt.handle == 77
    assert receipt.operation == "move_resize"
    assert receipt.native_accepted is True
    assert native.move_request == NativeWindowMoveResizeRequest(77, -10, 20, 640, 480)
