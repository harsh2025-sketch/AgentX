"""Production bindings for governed keyboard/clipboard native ports."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentx.capabilities.windows.clipboard_native import Win32ClipboardReadbackPort
from agentx.capabilities.windows.keyboard_text_clipboard import (
    RawClipboardClear,
    RawClipboardText,
    RawClipboardWrite,
)
from agentx.capabilities.windows.native_mutation import (
    NativeClipboardClearOutcome,
    NativeClipboardClearRequest,
    NativeClipboardMutationOutcome,
    NativeClipboardTextRequest,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result

__all__ = [
    "CanonicalNativeClipboardPort",
    "ClipboardMutationSurface",
    "ClipboardReadbackSurface",
]


@runtime_checkable
class ClipboardReadbackSurface(Protocol):
    """Minimal independent read-only clipboard observation surface."""

    @property
    def source(self) -> str: ...

    def read_text(self) -> Result[RawClipboardText, AgentXError]: ...


@runtime_checkable
class ClipboardMutationSurface(Protocol):
    """Minimal native mutation surface required by the clipboard binding."""

    def set_clipboard_text(
        self,
        request: NativeClipboardTextRequest,
    ) -> Result[NativeClipboardMutationOutcome, AgentXError]: ...

    def clear_clipboard(
        self,
        request: NativeClipboardClearRequest,
    ) -> Result[NativeClipboardClearOutcome, AgentXError]: ...


class CanonicalNativeClipboardPort:
    """Clipboard port using the canonical mutation seam plus a read-only observer."""

    __slots__ = ("_readback", "_surface")

    def __init__(
        self,
        surface: ClipboardMutationSurface,
        *,
        readback: ClipboardReadbackSurface | None = None,
    ) -> None:
        if not isinstance(surface, ClipboardMutationSurface):
            raise TypeError("surface must implement ClipboardMutationSurface")
        self._surface = surface
        self._readback = readback if readback is not None else Win32ClipboardReadbackPort()

    def read_text(self) -> Result[RawClipboardText, AgentXError]:
        return self._readback.read_text()

    def write_text(self, text: str) -> Result[RawClipboardWrite, AgentXError]:
        try:
            request = NativeClipboardTextRequest(text)
        except (TypeError, ValueError) as exc:
            return Result.failure(
                AgentXError(
                    code="capabilities.windows.clipboard_native.invalid_write",
                    message=f"clipboard write request rejected: {type(exc).__name__}",
                    category=ErrorCategory.VALIDATION,
                    retryability=Retryability.NON_RETRYABLE,
                )
            )
        result = self._surface.set_clipboard_text(request)
        if result.is_failure:
            return Result.failure(result.unwrap_error())
        expected_units = len(text.encode("utf-16-le")) // 2
        if result.unwrap().text_code_units != expected_units:
            return Result.failure(
                AgentXError(
                    code="capabilities.windows.clipboard_native.count_mismatch",
                    message="native clipboard outcome does not match the exact input",
                    category=ErrorCategory.EXECUTION,
                    retryability=Retryability.NON_RETRYABLE,
                )
            )
        return Result.success(RawClipboardWrite(character_count=len(text)))

    def clear(self) -> Result[RawClipboardClear, AgentXError]:
        result = self._surface.clear_clipboard(NativeClipboardClearRequest())
        if result.is_failure:
            return Result.failure(result.unwrap_error())
        return Result.success(
            RawClipboardClear(previous_was_empty=result.unwrap().previous_was_empty)
        )
