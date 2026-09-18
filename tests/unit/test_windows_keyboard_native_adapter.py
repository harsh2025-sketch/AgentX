"""Closed-vocabulary keyboard/native binding tests."""

from __future__ import annotations

from typing import NoReturn, cast

from agentx.capabilities.windows.keyboard_native_adapter import CanonicalNativeKeyboardPort
from agentx.capabilities.windows.keyboard_text_clipboard import Key, KeyChord, Modifier
from agentx.capabilities.windows.native_mutation import (
    NativeInputInjectionOutcome,
    NativeKeyInputRequest,
    NativeMutationSurface,
    NativeTextInputRequest,
)
from agentx.core.errors import AgentXError
from agentx.core.result import Result


class Surface:
    def __init__(self) -> None:
        self.text_request: NativeTextInputRequest | None = None
        self.key_request: NativeKeyInputRequest | None = None

    def send_text(
        self, request: NativeTextInputRequest
    ) -> Result[NativeInputInjectionOutcome, AgentXError]:
        self.text_request = request
        units = len(request.text.encode("utf-16-le")) // 2
        return Result.success(NativeInputInjectionOutcome(units * 2, units * 2, 0))

    def send_key_strokes(
        self, request: NativeKeyInputRequest
    ) -> Result[NativeInputInjectionOutcome, AgentXError]:
        self.key_request = request
        events = sum(stroke.repeat * (2 + 2 * len(stroke.modifiers)) for stroke in request.strokes)
        return Result.success(NativeInputInjectionOutcome(events, events, 0))

    def launch_process(self, request: object) -> NoReturn:
        raise AssertionError

    def set_window_state(self, request: object) -> NoReturn:
        raise AssertionError

    def activate_window(self, request: object) -> NoReturn:
        raise AssertionError

    def move_resize_window(self, request: object) -> NoReturn:
        raise AssertionError

    def set_clipboard_text(self, request: object) -> NoReturn:
        raise AssertionError

    def clear_clipboard(self, request: object) -> NoReturn:
        raise AssertionError


def test_all_public_keys_and_modifiers_map_without_raw_integer_escape_hatch() -> None:
    surface = Surface()
    port = CanonicalNativeKeyboardPort(cast(NativeMutationSurface, surface))
    for key in Key:
        chord = KeyChord(key=key, modifiers=frozenset({Modifier.SHIFT, Modifier.WIN}))
        result = port.send_keys((chord,))
        assert result.unwrap().chord_count == 1
        assert surface.key_request is not None
        assert len(surface.key_request.strokes) == 1
        assert surface.key_request.strokes[0].key.value == key.value


def test_unicode_character_count_is_public_character_count_not_utf16_units() -> None:
    surface = Surface()
    port = CanonicalNativeKeyboardPort(surface)
    assert port.send_text("a😀").unwrap().character_count == 2
