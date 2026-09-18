"""Unit tests for concrete clipboard bindings."""

from __future__ import annotations

from agentx.capabilities.windows.clipboard_native_adapter import CanonicalNativeClipboardPort
from agentx.capabilities.windows.keyboard_text_clipboard import RawClipboardText
from agentx.capabilities.windows.native_mutation import (
    NativeClipboardClearOutcome,
    NativeClipboardClearRequest,
    NativeClipboardMutationOutcome,
    NativeClipboardTextRequest,
)
from agentx.core.errors import AgentXError
from agentx.core.result import Result


class Readback:
    source = "test.readback"

    def read_text(self) -> Result[RawClipboardText, AgentXError]:
        return Result.success(RawClipboardText("hello"))


class Surface:
    def set_clipboard_text(
        self, request: NativeClipboardTextRequest
    ) -> Result[NativeClipboardMutationOutcome, AgentXError]:
        return Result.success(
            NativeClipboardMutationOutcome(
                text_code_units=len(request.text.encode("utf-16-le")) // 2
            )
        )

    def clear_clipboard(
        self, request: NativeClipboardClearRequest
    ) -> Result[NativeClipboardClearOutcome, AgentXError]:
        return Result.success(NativeClipboardClearOutcome(previous_was_empty=False))


def test_clipboard_adapter_reads_writes_unicode_and_clears() -> None:
    port = CanonicalNativeClipboardPort(Surface(), readback=Readback())
    assert port.read_text().unwrap().text == "hello"
    assert port.write_text("a😀").unwrap().character_count == 2
    assert port.clear().unwrap().previous_was_empty is False
