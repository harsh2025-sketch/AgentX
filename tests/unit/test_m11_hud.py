"""HUD state/telemetry/control acceptance for M11."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from agentx.core.runtime_ui_events import RuntimeUiEvent, RuntimeUiEventKind
from agentx.hud import (
    HudCommand,
    HudCommandGateway,
    HudControlAction,
    HudModel,
    HudSnapshot,
    HudState,
)


class _Controller:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, str | None]] = []

    def cancel(self, task_id: str | None) -> bool:
        self.calls.append(("cancel", task_id, None))
        return True

    def confirm(self, task_id: str | None, nonce: str | None) -> bool:
        self.calls.append(("confirm", task_id, nonce))
        return True

    def reject(self, task_id: str | None, nonce: str | None) -> bool:
        self.calls.append(("reject", task_id, nonce))
        return True

    def retry(self, task_id: str | None) -> bool:
        self.calls.append(("retry", task_id, None))
        return True

    def dismiss(self, task_id: str | None) -> bool:
        self.calls.append(("dismiss", task_id, None))
        return True


def _event(
    runtime: UUID,
    sequence: int,
    state: str,
    *,
    task_id: str = "task-1",
    verified: bool | None = None,
) -> RuntimeUiEvent:
    payload: dict[str, object] = {}
    if verified is not None:
        payload["verified"] = verified
    return RuntimeUiEvent(
        runtime_instance_id=runtime,
        sequence=sequence,
        timestamp=datetime.now(UTC),
        kind=RuntimeUiEventKind.EVENT,
        state=state,
        correlation_id=uuid4(),
        task_id=task_id,
        payload=payload,
    )


def _state(model: HudModel) -> HudState:
    return model.snapshot.state


def test_hud_reflects_ordered_runtime_truth_and_rejects_late_events() -> None:
    runtime = uuid4()
    model = HudModel()
    assert model.apply(_event(runtime, 0, "voice.listening"))
    assert _state(model) is HudState.LISTENING
    assert model.apply(_event(runtime, 1, "voice.reasoning"))
    assert _state(model) is HudState.REASONING
    assert model.apply(_event(runtime, 2, "action.requested"))
    assert _state(model) is HudState.EXECUTING
    assert model.apply(_event(runtime, 3, "voice.verifying"))
    assert _state(model) is HudState.VERIFYING
    assert model.apply(_event(runtime, 4, "voice.verified", verified=True))
    assert _state(model) is HudState.IDLE
    assert model.snapshot.verified is True

    assert not model.apply(_event(runtime, 2, "voice.failed"))
    assert _state(model) is HudState.IDLE
    assert model.snapshot.verified is True


def test_hud_controls_are_deduplicated_and_stale_checked() -> None:
    controller = _Controller()
    gateway = HudCommandGateway(controller)
    snapshot = HudSnapshot(
        state=HudState.AWAITING_CONFIRMATION,
        runtime_instance_id=uuid4(),
        last_sequence=5,
        task_id="task-1",
    )
    command_id = uuid4()
    command = HudCommand(
        command_id=command_id,
        action=HudControlAction.CONFIRM,
        task_id="task-1",
        confirmation_nonce="nonce-1",
    )
    assert gateway.dispatch(command, snapshot)
    assert not gateway.dispatch(command, snapshot)
    stale = HudCommand(
        command_id=uuid4(),
        action=HudControlAction.CANCEL,
        task_id="different-task",
    )
    assert not gateway.dispatch(stale, snapshot)
    assert controller.calls == [("confirm", "task-1", "nonce-1")]
