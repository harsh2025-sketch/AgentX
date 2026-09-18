"""Concrete keyboard port binding over the canonical Windows mutation seam."""

from __future__ import annotations

from agentx.capabilities.windows.keyboard_text_clipboard import (
    Key,
    KeyboardNativePort,
    KeyChord,
    Modifier,
    RawKeySend,
    RawTextSend,
)
from agentx.capabilities.windows.native_mutation import (
    NativeKeyInputRequest,
    NativeKeyStroke,
    NativeMutationSurface,
    NativeTextInputRequest,
    WindowsKeyModifier,
    WindowsVirtualKey,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result

__all__ = ["CanonicalNativeKeyboardPort"]

_KEY_MAP = {key: WindowsVirtualKey(key.value) for key in Key}
_MODIFIER_MAP = {
    Modifier.SHIFT: WindowsKeyModifier.SHIFT,
    Modifier.CTRL: WindowsKeyModifier.CONTROL,
    Modifier.ALT: WindowsKeyModifier.ALT,
    Modifier.WIN: WindowsKeyModifier.WIN,
}


class CanonicalNativeKeyboardPort(KeyboardNativePort):
    """Map only the governed closed key vocabulary to native input API."""

    __slots__ = ("_surface",)

    def __init__(self, surface: NativeMutationSurface) -> None:
        if not isinstance(surface, NativeMutationSurface):
            raise TypeError("surface must implement NativeMutationSurface")
        self._surface = surface

    def send_text(self, text: str) -> Result[RawTextSend, AgentXError]:
        try:
            request = NativeTextInputRequest(text)
        except (TypeError, ValueError) as exc:
            return Result.failure(_invalid("send_text", exc))
        result = self._surface.send_text(request)
        if result.is_failure:
            return Result.failure(result.unwrap_error())
        outcome = result.unwrap()
        if outcome.accepted_events != outcome.requested_events:
            return Result.failure(
                AgentXError(
                    code="capabilities.windows.keyboard_native.partial_input",
                    message=(
                        "native input API accepted only part of the requested text event sequence"
                    ),
                    category=ErrorCategory.EXECUTION,
                    retryability=Retryability.UNKNOWN,
                    details={
                        "requested_events": outcome.requested_events,
                        "accepted_events": outcome.accepted_events,
                        "win32_error": outcome.win32_error,
                    },
                )
            )
        return Result.success(RawTextSend(character_count=len(text)))

    def send_keys(self, chords: tuple[KeyChord, ...]) -> Result[RawKeySend, AgentXError]:
        try:
            strokes = tuple(
                NativeKeyStroke(
                    key=_KEY_MAP[chord.key],
                    modifiers=frozenset(_MODIFIER_MAP[item] for item in chord.modifiers),
                )
                for chord in chords
            )
            request = NativeKeyInputRequest(strokes)
        except (KeyError, TypeError, ValueError) as exc:
            return Result.failure(_invalid("send_keys", exc))
        result = self._surface.send_key_strokes(request)
        if result.is_failure:
            return Result.failure(result.unwrap_error())
        outcome = result.unwrap()
        if outcome.accepted_events != outcome.requested_events:
            return Result.failure(
                AgentXError(
                    code="capabilities.windows.keyboard_native.partial_input",
                    message=(
                        "native input API accepted only part of the requested key event sequence"
                    ),
                    category=ErrorCategory.EXECUTION,
                    retryability=Retryability.UNKNOWN,
                    details={
                        "requested_events": outcome.requested_events,
                        "accepted_events": outcome.accepted_events,
                        "win32_error": outcome.win32_error,
                    },
                )
            )
        return Result.success(RawKeySend(chord_count=len(chords)))


def _invalid(operation: str, exc: Exception) -> AgentXError:
    return AgentXError(
        code="capabilities.windows.keyboard_native.invalid_request",
        message=f"{operation} request rejected by the native boundary",
        category=ErrorCategory.VALIDATION,
        retryability=Retryability.NON_RETRYABLE,
        details={"operation": operation, "exception_type": type(exc).__name__},
    )
