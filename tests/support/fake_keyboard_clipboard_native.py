"""Deterministic cross-platform fake for A5.06 native operations."""

from __future__ import annotations

from dataclasses import dataclass, field

from agentx.capabilities.windows import _native
from agentx.core.errors import AgentXError
from agentx.core.result import Result


@dataclass(slots=True)
class FakeKeyboardClipboardNative:
    calls: list[tuple[object, ...]] = field(default_factory=list)
    clipboard_has_text: bool = False
    clipboard_text: str | None = None
    failure: AgentXError | None = None
    inconsistent_input_receipt: bool = False

    def _failure(self) -> Result[None, AgentXError] | None:
        if self.failure is None:
            return None
        return Result.failure(self.failure)

    def send_text(self, text: str) -> Result[_native.RawInputReceipt, AgentXError]:
        self.calls.append(("send_text", text))
        if self.failure is not None:
            return Result.failure(self.failure)
        units = len(text.encode("utf-16-le")) // 2
        requested = units * 2
        inserted = requested - 1 if self.inconsistent_input_receipt else requested
        return Result.success(
            _native.RawInputReceipt(events_requested=requested, events_inserted=inserted)
        )

    def send_key(
        self,
        key: str,
        modifiers: tuple[str, ...],
        repeat: int,
    ) -> Result[_native.RawInputReceipt, AgentXError]:
        self.calls.append(("send_key", key, modifiers, repeat))
        if self.failure is not None:
            return Result.failure(self.failure)
        requested = (2 * len(modifiers)) + (2 * repeat)
        inserted = requested - 1 if self.inconsistent_input_receipt else requested
        return Result.success(
            _native.RawInputReceipt(events_requested=requested, events_inserted=inserted)
        )

    def read_clipboard_text(self) -> Result[_native.RawClipboardRead, AgentXError]:
        self.calls.append(("read_clipboard_text",))
        if self.failure is not None:
            return Result.failure(self.failure)
        return Result.success(
            _native.RawClipboardRead(has_text=self.clipboard_has_text, text=self.clipboard_text)
        )

    def write_clipboard_text(self, text: str) -> Result[None, AgentXError]:
        self.calls.append(("write_clipboard_text", text))
        if self.failure is not None:
            return Result.failure(self.failure)
        return Result.success(None)

    def clear_clipboard(self) -> Result[None, AgentXError]:
        self.calls.append(("clear_clipboard",))
        if self.failure is not None:
            return Result.failure(self.failure)
        return Result.success(None)
