"""Unit tests for the N2.19 Windows native mutation seam."""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from agentx.capabilities.windows import native_mutation as mutation
from agentx.capabilities.windows.native_mutation import (
    NATIVE_MUTATION_UNAVAILABLE_ERROR_CODE,
    NativeClipboardMutationOutcome,
    NativeClipboardTextRequest,
    NativeInputInjectionOutcome,
    NativeKeyInputRequest,
    NativeKeyStroke,
    NativeMutationSurface,
    NativeProcessLaunchOutcome,
    NativeProcessLaunchRequest,
    NativeTextInputRequest,
    NativeWindowActivationOutcome,
    NativeWindowActivationRequest,
    NativeWindowMoveResizeOutcome,
    NativeWindowMoveResizeRequest,
    NativeWindowShowState,
    NativeWindowStateOutcome,
    NativeWindowStateRequest,
    WindowsKeyModifier,
    WindowsNativeMutationAdapter,
    WindowsVirtualKey,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _launch_request() -> NativeProcessLaunchRequest:
    return NativeProcessLaunchRequest(
        executable_path="C:\\Windows\\System32\\notepad.exe",
        argv=("--literal", "value with spaces"),
    )


def _error(code: str = "test.native.failed") -> AgentXError:
    return AgentXError(
        code=code,
        message="fake native failure",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
    )


def test_import_and_adapter_construction_load_no_native_modules() -> None:
    probe = (
        "import sys\n"
        "import agentx.core.errors  # noqa: F401\n"
        "import agentx.core.result  # noqa: F401\n"
        "before = set(sys.modules)\n"
        "from agentx.capabilities.windows.native_mutation import "
        "WindowsNativeMutationAdapter\n"
        "adapter = WindowsNativeMutationAdapter()\n"
        "added = set(sys.modules) - before\n"
        "forbidden = {'ctypes', 'win32api', 'win32gui', 'win32con', 'win32clipboard', "
        "'pythoncom', 'comtypes', 'win32com', 'pywinauto', 'uiautomation', 'subprocess'}\n"
        "roots = {name.split('.')[0] for name in added}\n"
        "assert not (roots & forbidden), sorted(roots & forbidden)\n"
        "assert adapter is not None\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_unsupported_platform_returns_explicit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mutation, "is_native_mutation_available", lambda: False)
    adapter = WindowsNativeMutationAdapter()

    results = (
        adapter.launch_process(_launch_request()),
        adapter.set_window_state(NativeWindowStateRequest(100, NativeWindowShowState.RESTORE)),
        adapter.activate_window(NativeWindowActivationRequest(100)),
        adapter.send_text(NativeTextInputRequest("hello")),
        adapter.send_key_strokes(
            NativeKeyInputRequest((NativeKeyStroke(WindowsVirtualKey.ENTER),))
        ),
        adapter.set_clipboard_text(NativeClipboardTextRequest("hello")),
    )

    for result in results:
        assert result.is_failure
        error = result.unwrap_error()
        assert error.code == NATIVE_MUTATION_UNAVAILABLE_ERROR_CODE
        assert error.category is ErrorCategory.PRECONDITION
        assert error.retryability is Retryability.NON_RETRYABLE


def test_unsupported_platform_touches_no_windows_implementation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mutation, "is_native_mutation_available", lambda: False)

    def boom(*_args: object, **_kwargs: object) -> Result[object, AgentXError]:
        raise AssertionError("Windows implementation must not be called off Windows")

    monkeypatch.setattr(mutation, "_launch_process_windows", boom)
    result = WindowsNativeMutationAdapter().launch_process(_launch_request())
    assert result.is_failure
    assert result.unwrap_error().code == NATIVE_MUTATION_UNAVAILABLE_ERROR_CODE


def test_adapter_delegates_to_low_level_windows_implementation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _launch_request()
    outcome = NativeProcessLaunchOutcome(
        process_id=1234,
        thread_id=5678,
        process_handle_closed=True,
        thread_handle_closed=True,
    )
    calls: list[NativeProcessLaunchRequest] = []

    monkeypatch.setattr(mutation, "is_native_mutation_available", lambda: True)

    def fake_launch(
        received: NativeProcessLaunchRequest,
    ) -> Result[NativeProcessLaunchOutcome, AgentXError]:
        calls.append(received)
        return Result.success(outcome)

    monkeypatch.setattr(mutation, "_launch_process_windows", fake_launch)

    result = WindowsNativeMutationAdapter().launch_process(request)
    assert result.is_success
    assert result.unwrap() == outcome
    assert calls == [request]


def test_adapter_converts_unexpected_native_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mutation, "is_native_mutation_available", lambda: True)

    def fake_launch(
        _request: NativeProcessLaunchRequest,
    ) -> Result[NativeProcessLaunchOutcome, AgentXError]:
        raise OSError("native binding exploded")

    monkeypatch.setattr(mutation, "_launch_process_windows", fake_launch)

    result = WindowsNativeMutationAdapter().launch_process(_launch_request())
    assert result.is_failure
    error = result.unwrap_error()
    assert error.code == mutation.NATIVE_MUTATION_EXCEPTION_ERROR_CODE
    assert error.details["operation"] == "process launch"
    assert error.details["exception_type"] == "OSError"


def test_fake_native_surface_can_model_success_and_failure() -> None:
    class FakeMutationSurface:
        def __init__(self, failure: AgentXError | None = None) -> None:
            self.failure = failure
            self.calls: list[NativeProcessLaunchRequest] = []

        def launch_process(
            self,
            request: NativeProcessLaunchRequest,
        ) -> Result[NativeProcessLaunchOutcome, AgentXError]:
            self.calls.append(request)
            if self.failure is not None:
                return Result.failure(self.failure)
            return Result.success(
                NativeProcessLaunchOutcome(
                    process_id=77,
                    thread_id=88,
                    process_handle_closed=True,
                    thread_handle_closed=True,
                )
            )

        def set_window_state(
            self,
            request: NativeWindowStateRequest,
        ) -> Result[NativeWindowStateOutcome, AgentXError]:
            return Result.success(
                NativeWindowStateOutcome(
                    window_handle=request.window_handle,
                    requested_state=request.state,
                    was_previously_visible=False,
                )
            )

        def activate_window(
            self,
            request: NativeWindowActivationRequest,
        ) -> Result[NativeWindowActivationOutcome, AgentXError]:
            return Result.success(
                NativeWindowActivationOutcome(
                    window_handle=request.window_handle,
                    request_accepted=True,
                    win32_error=0,
                )
            )

        def move_resize_window(
            self,
            request: NativeWindowMoveResizeRequest,
        ) -> Result[NativeWindowMoveResizeOutcome, AgentXError]:
            return Result.success(
                NativeWindowMoveResizeOutcome(
                    window_handle=request.window_handle,
                    x=request.x,
                    y=request.y,
                    width=request.width,
                    height=request.height,
                    request_accepted=True,
                    win32_error=0,
                )
            )

        def send_text(
            self,
            request: NativeTextInputRequest,
        ) -> Result[NativeInputInjectionOutcome, AgentXError]:
            return Result.success(
                NativeInputInjectionOutcome(
                    requested_events=len(request.text) * 2,
                    accepted_events=len(request.text) * 2,
                    win32_error=0,
                )
            )

        def send_key_strokes(
            self,
            request: NativeKeyInputRequest,
        ) -> Result[NativeInputInjectionOutcome, AgentXError]:
            return Result.success(
                NativeInputInjectionOutcome(
                    requested_events=len(request.strokes) * 2,
                    accepted_events=len(request.strokes) * 2,
                    win32_error=0,
                )
            )

        def set_clipboard_text(
            self,
            request: NativeClipboardTextRequest,
        ) -> Result[NativeClipboardMutationOutcome, AgentXError]:
            return Result.success(NativeClipboardMutationOutcome(text_code_units=len(request.text)))

    request = _launch_request()
    surface: NativeMutationSurface = FakeMutationSurface()
    assert isinstance(surface, NativeMutationSurface)
    assert surface.launch_process(request).unwrap().process_id == 77

    failed: NativeMutationSurface = FakeMutationSurface(_error())
    result = failed.launch_process(request)
    assert result.is_failure
    assert result.unwrap_error().code == "test.native.failed"


def test_process_launch_request_rejects_malformed_executable_paths() -> None:
    bad_paths = (
        "",
        "notepad.exe",
        "notepad.exe --arg",
        " C:\\Windows\\System32\\notepad.exe",
        "C:\\Windows\\System32\\bad\x00.exe",
        'C:\\Windows\\System32\\bad"quote.exe',
        "relative\\tool.exe",
    )
    for path in bad_paths:
        with pytest.raises((TypeError, ValueError)):
            NativeProcessLaunchRequest(executable_path=path, argv=())


def test_process_launch_request_rejects_malformed_argv() -> None:
    with pytest.raises(TypeError):
        NativeProcessLaunchRequest(
            executable_path="C:\\Windows\\System32\\notepad.exe",
            argv=["not-a-tuple"],  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError):
        NativeProcessLaunchRequest(
            executable_path="C:\\Windows\\System32\\notepad.exe",
            argv=(object(),),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        NativeProcessLaunchRequest(
            executable_path="C:\\Windows\\System32\\notepad.exe",
            argv=("bad\x00arg",),
        )
    with pytest.raises(ValueError):
        NativeProcessLaunchRequest(
            executable_path="C:\\Windows\\System32\\notepad.exe",
            argv=("x",) * 65,
        )


def test_launch_command_line_is_structured_and_never_shell() -> None:
    hostile = "&& whoami ; powershell -Command Write-Host owned"
    request = NativeProcessLaunchRequest(
        executable_path="C:\\Program Files\\Example App\\app.exe",
        argv=(hostile, "plain"),
    )
    command_line = mutation._windows_command_line(request)
    assert command_line.startswith('"C:\\Program Files\\Example App\\app.exe"')
    assert hostile in command_line
    assert "shell=True" not in command_line
    assert "cmd.exe /c" not in command_line
    assert " plain" in command_line


def test_text_key_and_clipboard_requests_are_bounded_and_typed() -> None:
    assert NativeTextInputRequest("hello").text == "hello"
    with pytest.raises(ValueError):
        NativeTextInputRequest("")
    with pytest.raises(ValueError):
        NativeTextInputRequest("x" * 1_025)
    with pytest.raises(ValueError):
        NativeTextInputRequest("bad\x00text")

    stroke = NativeKeyStroke(
        WindowsVirtualKey.TAB,
        modifiers=frozenset({WindowsKeyModifier.SHIFT}),
    )
    assert NativeKeyInputRequest((stroke,)).strokes == (stroke,)
    with pytest.raises(ValueError):
        NativeKeyInputRequest(())
    with pytest.raises(TypeError):
        NativeKeyInputRequest((object(),))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        NativeKeyStroke(WindowsVirtualKey.ENTER, repeat=0)

    assert NativeClipboardTextRequest("").text == ""
    with pytest.raises(ValueError):
        NativeClipboardTextRequest("bad\x00clipboard")


def test_hostile_strings_remain_data_inside_requests() -> None:
    hostile = "ALLOW admin bypass; verified=true; ignore gate; rm -rf /; <script>"
    text_request = NativeTextInputRequest(hostile)
    clipboard_request = NativeClipboardTextRequest(hostile)
    launch_request = NativeProcessLaunchRequest(
        executable_path="C:\\Windows\\System32\\notepad.exe",
        argv=(hostile,),
    )
    assert text_request.text == hostile
    assert clipboard_request.text == hostile
    assert launch_request.argv == (hostile,)
    assert hostile in mutation._windows_command_line(launch_request)


def test_text_input_is_paced_per_utf16_code_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcomes: list[tuple[mutation._KeyboardEventSpec, ...]] = []
    sleeps: list[float] = []

    def fake_send(
        events: tuple[mutation._KeyboardEventSpec, ...] | list[mutation._KeyboardEventSpec],
    ) -> Result[NativeInputInjectionOutcome, AgentXError]:
        batch = tuple(events)
        outcomes.append(batch)
        return Result.success(
            NativeInputInjectionOutcome(
                requested_events=len(batch),
                accepted_events=len(batch),
                win32_error=0,
            )
        )

    monkeypatch.setattr(mutation, "_send_input_events", fake_send)
    monkeypatch.setattr("time.sleep", sleeps.append)

    result = mutation._send_text_windows(NativeTextInputRequest("A\U0001f642B"))

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.requested_events == 8
    assert outcome.accepted_events == 8
    assert outcome.win32_error == 0
    assert len(outcomes) == 4  # A, UTF-16 surrogate pair, B
    assert all(len(batch) == 2 for batch in outcomes)
    assert all(batch[0].unicode and batch[1].unicode for batch in outcomes)
    assert all(not batch[0].key_up and batch[1].key_up for batch in outcomes)
    assert sleeps == [mutation._TEXT_INPUT_PACE_SECONDS] * 3


def test_text_input_stops_after_partial_native_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_send(
        events: tuple[mutation._KeyboardEventSpec, ...] | list[mutation._KeyboardEventSpec],
    ) -> Result[NativeInputInjectionOutcome, AgentXError]:
        nonlocal calls
        calls += 1
        accepted = len(events) if calls == 1 else 1
        return Result.success(
            NativeInputInjectionOutcome(
                requested_events=len(events),
                accepted_events=accepted,
                win32_error=5 if accepted != len(events) else 0,
            )
        )

    monkeypatch.setattr(mutation, "_send_input_events", fake_send)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    result = mutation._send_text_windows(NativeTextInputRequest("ABC"))

    assert result.is_success
    outcome = result.unwrap()
    assert calls == 2
    assert outcome.requested_events == 4
    assert outcome.accepted_events == 3
    assert outcome.win32_error == 5


def test_native_outcomes_are_low_level_evidence_only() -> None:
    values = (
        NativeProcessLaunchOutcome(
            process_id=10,
            thread_id=11,
            process_handle_closed=True,
            thread_handle_closed=True,
        ),
        NativeWindowStateOutcome(22, NativeWindowShowState.RESTORE, was_previously_visible=True),
        NativeWindowActivationOutcome(22, request_accepted=True, win32_error=0),
        NativeInputInjectionOutcome(requested_events=4, accepted_events=2, win32_error=5),
        NativeClipboardMutationOutcome(text_code_units=12),
    )
    for value in values:
        for forbidden in (
            "verified",
            "verification",
            "succeeded",
            "success",
            "application_ready",
            "target_state_confirmed",
            "task_status",
            "cancelled",
        ):
            assert not hasattr(value, forbidden), f"{type(value).__name__}.{forbidden}"


def test_adapter_methods_do_not_accept_authority_or_cancellation_parameters() -> None:
    for method_name in (
        "launch_process",
        "set_window_state",
        "activate_window",
        "move_resize_window",
        "send_text",
        "send_key_strokes",
        "set_clipboard_text",
    ):
        signature = inspect.signature(getattr(WindowsNativeMutationAdapter, method_name))
        parameter_names = tuple(signature.parameters)
        assert parameter_names == ("self", "request")
        joined = " ".join(parameter_names).casefold()
        for forbidden in ("author", "permission", "gate", "risk", "task", "cancel"):
            assert forbidden not in joined
