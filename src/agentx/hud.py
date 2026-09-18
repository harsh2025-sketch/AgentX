"""Runtime-facing HUD state, controls, reducer and minimal Tk surface for M11."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from agentx.core.runtime_ui_events import RuntimeUiEvent, RuntimeUiEventKind

__all__ = [
    "HudCommand",
    "HudCommandGateway",
    "HudControlAction",
    "HudController",
    "HudModel",
    "HudSnapshot",
    "HudState",
    "TkHudApp",
]


class HudState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    REASONING = "reasoning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    SPEAKING = "speaking"
    RECOVERING = "recovering"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class HudSnapshot:
    state: HudState
    runtime_instance_id: UUID | None
    last_sequence: int
    task_id: str | None
    verified: bool | None = None


class HudModel:
    """Reduce AX-452 telemetry into ordered HUD truth; late events are ignored."""

    __slots__ = ("_snapshot",)

    def __init__(self) -> None:
        self._snapshot = HudSnapshot(
            state=HudState.IDLE,
            runtime_instance_id=None,
            last_sequence=-1,
            task_id=None,
            verified=None,
        )

    @property
    def snapshot(self) -> HudSnapshot:
        return self._snapshot

    def apply(self, event: RuntimeUiEvent) -> bool:
        if not isinstance(event, RuntimeUiEvent):
            raise TypeError("event must be RuntimeUiEvent")
        current = self._snapshot
        if (
            current.runtime_instance_id is not None
            and event.runtime_instance_id != current.runtime_instance_id
        ):
            if event.kind is not RuntimeUiEventKind.RUNTIME_RESTARTED:
                return False
            current = HudSnapshot(
                state=HudState.IDLE,
                runtime_instance_id=event.runtime_instance_id,
                last_sequence=-1,
                task_id=None,
                verified=None,
            )
        if (
            event.runtime_instance_id == current.runtime_instance_id
            and event.sequence <= current.last_sequence
        ):
            return False

        state = self._state_for(event.state, current.state)
        verified: bool | None = current.verified
        raw_verified = event.payload.get("verified")
        if type(raw_verified) is bool:
            verified = raw_verified
        self._snapshot = HudSnapshot(
            state=state,
            runtime_instance_id=event.runtime_instance_id,
            last_sequence=event.sequence,
            task_id=event.task_id if event.task_id is not None else current.task_id,
            verified=verified,
        )
        return True

    @staticmethod
    def _state_for(raw: str, fallback: HudState) -> HudState:
        value = raw.casefold()
        if "listening" in value:
            return HudState.LISTENING
        if "awaiting_confirmation" in value or "approval" in value:
            return HudState.AWAITING_CONFIRMATION
        if "reasoning" in value or "processing" in value:
            return HudState.REASONING
        if "verification" in value or "verifying" in value:
            return HudState.VERIFYING
        if "repair" in value or "recover" in value:
            return HudState.RECOVERING
        if "speaking" in value:
            return HudState.SPEAKING
        if "cancel" in value or "interrupt" in value:
            return HudState.CANCELLED
        if "failed" in value or "error" in value:
            return HudState.FAILED
        if (
            "action." in value
            or "capability." in value
            or "execut" in value
            or "task.started" in value
        ):
            return HudState.EXECUTING
        if "verified" in value or "task.completed" in value or value.endswith(".idle"):
            return HudState.IDLE
        return fallback


class HudControlAction(StrEnum):
    CANCEL = "cancel"
    CONFIRM = "confirm"
    REJECT = "reject"
    DISMISS = "dismiss"
    RETRY = "retry"


@dataclass(frozen=True, slots=True, kw_only=True)
class HudCommand:
    command_id: UUID
    action: HudControlAction
    task_id: str | None = None
    confirmation_nonce: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.command_id, UUID) or self.command_id.int == 0:
            raise ValueError("command_id must be a non-nil UUID")
        if not isinstance(self.action, HudControlAction):
            raise TypeError("action must be HudControlAction")
        if self.task_id is not None and (not self.task_id.strip() or len(self.task_id) > 512):
            raise ValueError("task_id must be bounded non-empty text or None")
        if self.confirmation_nonce is not None and (
            not self.confirmation_nonce.strip() or len(self.confirmation_nonce) > 64
        ):
            raise ValueError("confirmation_nonce must be bounded text or None")


@runtime_checkable
class HudController(Protocol):
    def cancel(self, task_id: str | None) -> bool: ...

    def confirm(self, task_id: str | None, nonce: str | None) -> bool: ...

    def reject(self, task_id: str | None, nonce: str | None) -> bool: ...

    def retry(self, task_id: str | None) -> bool: ...

    def dismiss(self, task_id: str | None) -> bool: ...


class HudCommandGateway:
    """Deduplicate/stale-check UI commands before delegating to governed control APIs."""

    __slots__ = ("_controller", "_seen")

    def __init__(self, controller: HudController) -> None:
        if not isinstance(controller, HudController):
            raise TypeError("controller must satisfy HudController")
        self._controller = controller
        self._seen: set[UUID] = set()

    def dispatch(self, command: HudCommand, snapshot: HudSnapshot) -> bool:
        if not isinstance(command, HudCommand):
            raise TypeError("command must be HudCommand")
        if not isinstance(snapshot, HudSnapshot):
            raise TypeError("snapshot must be HudSnapshot")
        if command.command_id in self._seen:
            return False
        self._seen.add(command.command_id)
        if command.task_id is not None and snapshot.task_id != command.task_id:
            return False
        if command.action is HudControlAction.CANCEL:
            return self._controller.cancel(command.task_id)
        if command.action is HudControlAction.CONFIRM:
            if snapshot.state is not HudState.AWAITING_CONFIRMATION:
                return False
            return self._controller.confirm(command.task_id, command.confirmation_nonce)
        if command.action is HudControlAction.REJECT:
            if snapshot.state is not HudState.AWAITING_CONFIRMATION:
                return False
            return self._controller.reject(command.task_id, command.confirmation_nonce)
        if command.action is HudControlAction.RETRY:
            if snapshot.state is not HudState.FAILED:
                return False
            return self._controller.retry(command.task_id)
        return self._controller.dismiss(command.task_id)


class TkHudApp:
    """Small real desktop HUD surface over the canonical model/gateway.

    Tk is imported only when `run` is called so headless CI can test the state
    and command architecture without requiring an interactive desktop.
    """

    __slots__ = ("_gateway", "_model")

    def __init__(self, *, model: HudModel, gateway: HudCommandGateway) -> None:
        if not isinstance(model, HudModel):
            raise TypeError("model must be HudModel")
        if not isinstance(gateway, HudCommandGateway):
            raise TypeError("gateway must be HudCommandGateway")
        self._model = model
        self._gateway = gateway

    def accept(self, event: RuntimeUiEvent) -> bool:
        return self._model.apply(event)

    def run(self) -> None:
        import tkinter as tk

        root = tk.Tk()
        root.title("AgentX HUD")
        root.geometry("360x180")
        status = tk.StringVar(value=self._model.snapshot.state.value)
        label = tk.Label(root, textvariable=status, font=("Segoe UI", 18))
        label.pack(pady=16)

        def invoke(action: HudControlAction) -> None:
            snapshot = self._model.snapshot
            command = HudCommand(
                command_id=uuid4(),
                action=action,
                task_id=snapshot.task_id,
            )
            self._gateway.dispatch(command, snapshot)
            status.set(self._model.snapshot.state.value)

        controls = tk.Frame(root)
        controls.pack()
        tk.Button(controls, text="Cancel", command=lambda: invoke(HudControlAction.CANCEL)).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(controls, text="Confirm", command=lambda: invoke(HudControlAction.CONFIRM)).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(controls, text="Reject", command=lambda: invoke(HudControlAction.REJECT)).pack(
            side=tk.LEFT, padx=4
        )
        tk.Button(controls, text="Retry", command=lambda: invoke(HudControlAction.RETRY)).pack(
            side=tk.LEFT, padx=4
        )
        root.mainloop()
