"""Unit coverage for concrete application-launch/native binding."""

from __future__ import annotations

from typing import NoReturn, cast

from agentx.capabilities.windows.application_launch_native_adapter import (
    CanonicalNativeApplicationLaunchAdapter,
)
from agentx.capabilities.windows.native_mutation import (
    NativeMutationSurface,
    NativeProcessLaunchOutcome,
    NativeProcessLaunchRequest,
)
from agentx.core.errors import AgentXError
from agentx.core.result import Result


class Surface:
    def __init__(self) -> None:
        self.request: NativeProcessLaunchRequest | None = None

    def launch_process(
        self, request: NativeProcessLaunchRequest
    ) -> Result[NativeProcessLaunchOutcome, AgentXError]:
        self.request = request
        return Result.success(
            NativeProcessLaunchOutcome(
                process_id=123,
                thread_id=456,
                process_handle_closed=True,
                thread_handle_closed=True,
            )
        )

    def set_window_state(self, request: object) -> NoReturn:
        raise AssertionError

    def activate_window(self, request: object) -> NoReturn:
        raise AssertionError

    def move_resize_window(self, request: object) -> NoReturn:
        raise AssertionError

    def send_text(self, request: object) -> NoReturn:
        raise AssertionError

    def send_key_strokes(self, request: object) -> NoReturn:
        raise AssertionError

    def set_clipboard_text(self, request: object) -> NoReturn:
        raise AssertionError


def test_binding_preserves_structured_launch_and_working_directory() -> None:
    surface = Surface()
    adapter = CanonicalNativeApplicationLaunchAdapter(cast(NativeMutationSurface, surface))
    result = adapter.launch(
        r"C:\Windows\System32\notepad.exe",
        ("literal argument", "a&b"),
        r"C:\Windows",
    )
    assert result.launched is True
    assert result.process_id == 123
    assert surface.request == NativeProcessLaunchRequest(
        executable_path=r"C:\Windows\System32\notepad.exe",
        argv=("literal argument", "a&b"),
        working_directory=r"C:\Windows",
    )


def test_binding_rejects_relative_executable_without_shell_fallback() -> None:
    surface = Surface()
    result = CanonicalNativeApplicationLaunchAdapter(cast(NativeMutationSurface, surface)).launch(
        "notepad.exe", ()
    )
    assert result.launched is False
    assert surface.request is None
