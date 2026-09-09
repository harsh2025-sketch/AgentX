"""Single low-level Windows native mutation seam (N2.19).

This module is the only AgentX Windows-package surface that performs narrowly
scoped Win32 mutations. It is deliberately a native adapter, not a capability:
callers must complete all governance before invoking it, and this layer
reports only low-level native outcomes.

Importing this module is safe on every host. It imports no native library,
starts no process, changes no window, sends no input, and touches no clipboard
until one adapter method is called. Native binding is lazy and call-local.
Off-Windows calls return an explicit unsupported-platform failure.

The surface is intentionally small and typed:

* structured process launch by absolute executable path plus argv only;
* basic window show-state request and foreground activation request;
* bounded Unicode text injection and bounded controlled-key injection;
* bounded Unicode clipboard text replacement.

It exposes no generic command string, command interpreter, arbitrary DLL load,
raw function invocation, process injection, memory editing, hook installation,
or public capability registration surface. A successful result from this
module means only that the native call reported that low-level outcome; it is
never proof that an application is ready, that text appeared, that a window
ended in the intended state, or that a consumer observed clipboard contents.
"""

from __future__ import annotations

# C/Win32 ABI spellings are intentionally preserved inside the isolated seam.
# ruff: noqa: N801, N806
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol, TypeVar, runtime_checkable

from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result

__all__ = [
    "CLIPBOARD_MUTATION_FAILED_ERROR_CODE",
    "INPUT_INJECTION_FAILED_ERROR_CODE",
    "NATIVE_MUTATION_EXCEPTION_ERROR_CODE",
    "NATIVE_MUTATION_UNAVAILABLE_ERROR_CODE",
    "PROCESS_LAUNCH_FAILED_ERROR_CODE",
    "WINDOW_ACTIVATION_FAILED_ERROR_CODE",
    "WINDOW_MOVE_RESIZE_FAILED_ERROR_CODE",
    "NativeClipboardMutationOutcome",
    "NativeClipboardTextRequest",
    "NativeInputInjectionOutcome",
    "NativeKeyInputRequest",
    "NativeKeyStroke",
    "NativeMutationSurface",
    "NativeProcessLaunchOutcome",
    "NativeProcessLaunchRequest",
    "NativeTextInputRequest",
    "NativeWindowActivationOutcome",
    "NativeWindowActivationRequest",
    "NativeWindowMoveResizeOutcome",
    "NativeWindowMoveResizeRequest",
    "NativeWindowShowState",
    "NativeWindowStateOutcome",
    "NativeWindowStateRequest",
    "WindowsKeyModifier",
    "WindowsNativeMutationAdapter",
    "WindowsVirtualKey",
    "is_native_mutation_available",
]

NATIVE_MUTATION_UNAVAILABLE_ERROR_CODE: Final[str] = (
    "capabilities.windows.native_mutation.native_unavailable"
)
NATIVE_MUTATION_EXCEPTION_ERROR_CODE: Final[str] = (
    "capabilities.windows.native_mutation.native_exception"
)
PROCESS_LAUNCH_FAILED_ERROR_CODE: Final[str] = (
    "capabilities.windows.native_mutation.process_launch_failed"
)
WINDOW_ACTIVATION_FAILED_ERROR_CODE: Final[str] = (
    "capabilities.windows.native_mutation.window_activation_failed"
)
WINDOW_MOVE_RESIZE_FAILED_ERROR_CODE: Final[str] = (
    "capabilities.windows.native_mutation.window_move_resize_failed"
)
INPUT_INJECTION_FAILED_ERROR_CODE: Final[str] = (
    "capabilities.windows.native_mutation.input_injection_failed"
)
CLIPBOARD_MUTATION_FAILED_ERROR_CODE: Final[str] = (
    "capabilities.windows.native_mutation.clipboard_mutation_failed"
)

_WIN32_PLATFORM: Final[str] = "win32"
_MAX_WINDOWS_PATH_CHARS: Final[int] = 32_767
_MAX_ARGV_ITEMS: Final[int] = 64
_MAX_ARG_CHARS: Final[int] = 4_096
_MAX_COMMAND_LINE_CHARS: Final[int] = 32_767
_MAX_TEXT_INPUT_CODE_UNITS: Final[int] = 1_024
_MAX_KEY_STROKES: Final[int] = 64
_MAX_INPUT_EVENTS: Final[int] = 512
_MAX_CLIPBOARD_CODE_UNITS: Final[int] = 65_536
_MIN_WINDOW_POSITION: Final[int] = -32_768
_MAX_WINDOW_POSITION: Final[int] = 32_767
_MIN_WINDOW_SIZE: Final[int] = 1
_MAX_WINDOW_SIZE: Final[int] = 32_767

T = TypeVar("T")


class NativeWindowShowState(StrEnum):
    """Controlled show-state requests supported by the mutation seam."""

    SHOW = "show"
    RESTORE = "restore"
    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


class WindowsVirtualKey(StrEnum):
    """Small controlled key set for future governed keyboard callers."""

    ENTER = "enter"
    TAB = "tab"
    ESCAPE = "escape"
    BACKSPACE = "backspace"
    DELETE = "delete"
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"
    HOME = "home"
    END = "end"
    PAGE_UP = "page_up"
    PAGE_DOWN = "page_down"


class WindowsKeyModifier(StrEnum):
    """Controlled modifier keys; no raw virtual-key escape hatch exists."""

    SHIFT = "shift"
    CONTROL = "control"
    ALT = "alt"


_SHOW_STATE_FLAGS: Final[dict[NativeWindowShowState, int]] = {
    NativeWindowShowState.SHOW: 5,  # SW_SHOW
    NativeWindowShowState.RESTORE: 9,  # SW_RESTORE
    NativeWindowShowState.MINIMIZE: 6,  # SW_MINIMIZE
    NativeWindowShowState.MAXIMIZE: 3,  # SW_MAXIMIZE
}

_VIRTUAL_KEY_CODES: Final[dict[WindowsVirtualKey, int]] = {
    WindowsVirtualKey.ENTER: 0x0D,
    WindowsVirtualKey.TAB: 0x09,
    WindowsVirtualKey.ESCAPE: 0x1B,
    WindowsVirtualKey.BACKSPACE: 0x08,
    WindowsVirtualKey.DELETE: 0x2E,
    WindowsVirtualKey.LEFT: 0x25,
    WindowsVirtualKey.UP: 0x26,
    WindowsVirtualKey.RIGHT: 0x27,
    WindowsVirtualKey.DOWN: 0x28,
    WindowsVirtualKey.HOME: 0x24,
    WindowsVirtualKey.END: 0x23,
    WindowsVirtualKey.PAGE_UP: 0x21,
    WindowsVirtualKey.PAGE_DOWN: 0x22,
}

_MODIFIER_KEY_CODES: Final[dict[WindowsKeyModifier, int]] = {
    WindowsKeyModifier.SHIFT: 0x10,
    WindowsKeyModifier.CONTROL: 0x11,
    WindowsKeyModifier.ALT: 0x12,
}

_MODIFIER_ORDER: Final[tuple[WindowsKeyModifier, ...]] = (
    WindowsKeyModifier.CONTROL,
    WindowsKeyModifier.ALT,
    WindowsKeyModifier.SHIFT,
)


def is_native_mutation_available() -> bool:
    """Return whether the Win32 mutation seam can be called on this host."""
    return sys.platform == _WIN32_PLATFORM


def _has_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _contains_nul(value: str) -> bool:
    return "\x00" in value


def _require_windows_absolute_path(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value:
        raise ValueError(f"{field_name} must be non-empty")
    if value != value.strip():
        raise ValueError(f"{field_name} must be trimmed")
    if len(value) > _MAX_WINDOWS_PATH_CHARS:
        raise ValueError(f"{field_name} must not exceed {_MAX_WINDOWS_PATH_CHARS} characters")
    if _contains_nul(value):
        raise ValueError(f"{field_name} must not contain NUL")
    if _has_control_characters(value):
        raise ValueError(f"{field_name} must not contain control characters")
    if '"' in value:
        raise ValueError(f"{field_name} must not contain quotes")
    if not _is_windows_absolute_path(value):
        raise ValueError(f"{field_name} must be an absolute Windows path")
    return value


def _is_windows_absolute_path(value: str) -> bool:
    if _is_drive_absolute_path(value):
        return True
    if value.startswith("\\\\?\\"):
        tail = value[4:]
        if _is_drive_absolute_path(tail):
            return True
        if tail.startswith("UNC\\"):
            return _has_unc_server_share_tail(tail[4:])
        return False
    if value.startswith("\\\\"):
        return _has_unc_server_share_tail(value[2:])
    return False


def _is_drive_absolute_path(value: str) -> bool:
    return len(value) >= 3 and value[0].isalpha() and value[1] == ":" and value[2] in "\\/"


def _has_unc_server_share_tail(value: str) -> bool:
    normalized = value.replace("/", "\\")
    parts = [part for part in normalized.split("\\") if part]
    return len(parts) >= 3


def _require_window_handle(value: object, *, field_name: str = "window_handle") -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if value <= 0 or value > 2**63 - 1:
        raise ValueError(f"{field_name} must be a positive pointer-sized integer")
    return value



def _require_bounded_window_int(
    value: object,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if value < minimum or value > maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return value

def _require_code_unit_bound(
    value: object,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not allow_empty and not value:
        raise ValueError(f"{field_name} must be non-empty")
    if _contains_nul(value):
        raise ValueError(f"{field_name} must not contain NUL")
    code_units = _utf16_code_unit_count(value)
    if code_units > maximum:
        raise ValueError(f"{field_name} must not exceed {maximum} UTF-16 code units")
    return value


def _utf16_code_units(value: str) -> tuple[int, ...]:
    encoded = value.encode("utf-16-le")
    return tuple(
        int.from_bytes(encoded[index : index + 2], "little") for index in range(0, len(encoded), 2)
    )


def _utf16_code_unit_count(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _quote_windows_argument(argument: str) -> str:
    """Quote one CreateProcess argument using the documented argv rules."""
    if argument == "":
        return '""'
    if not any(character in argument for character in (" ", "\t", '"')):
        return argument

    quoted = ['"']
    backslashes = 0
    for character in argument:
        if character == "\\":
            backslashes += 1
            continue
        if character == '"':
            quoted.append("\\" * (backslashes * 2 + 1))
            quoted.append('"')
            backslashes = 0
            continue
        if backslashes:
            quoted.append("\\" * backslashes)
            backslashes = 0
        quoted.append(character)
    if backslashes:
        quoted.append("\\" * (backslashes * 2))
    quoted.append('"')
    return "".join(quoted)


def _windows_command_line(request: NativeProcessLaunchRequest) -> str:
    parts = (
        _quote_windows_argument(request.executable_path),
        *(_quote_windows_argument(argument) for argument in request.argv),
    )
    command_line = " ".join(parts)
    if len(command_line) > _MAX_COMMAND_LINE_CHARS:
        raise ValueError(
            f"command line must not exceed {_MAX_COMMAND_LINE_CHARS} characters after quoting"
        )
    return command_line


@dataclass(frozen=True, slots=True)
class NativeProcessLaunchRequest:
    """Structured process launch request: absolute executable path plus argv.

    ``argv`` contains arguments only; the seam supplies the executable as the
    argv-zero token when building the Windows command line for CreateProcessW.
    """

    executable_path: str
    argv: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_windows_absolute_path(self.executable_path, field_name="executable_path")
        if not isinstance(self.argv, tuple):
            raise TypeError("argv must be a tuple of strings")
        if len(self.argv) > _MAX_ARGV_ITEMS:
            raise ValueError(f"argv must not contain more than {_MAX_ARGV_ITEMS} items")
        for index, argument in enumerate(self.argv):
            if not isinstance(argument, str):
                raise TypeError(f"argv[{index}] must be a string")
            if _contains_nul(argument):
                raise ValueError(f"argv[{index}] must not contain NUL")
            if len(argument) > _MAX_ARG_CHARS:
                raise ValueError(f"argv[{index}] must not exceed {_MAX_ARG_CHARS} characters")
        _windows_command_line(self)


@dataclass(frozen=True, slots=True)
class NativeWindowStateRequest:
    """Request a basic show-state change for an existing HWND."""

    window_handle: int
    state: NativeWindowShowState

    def __post_init__(self) -> None:
        _require_window_handle(self.window_handle)
        if not isinstance(self.state, NativeWindowShowState):
            raise TypeError("state must be a NativeWindowShowState")


@dataclass(frozen=True, slots=True)
class NativeWindowActivationRequest:
    """Request foreground activation for an existing HWND."""

    window_handle: int

    def __post_init__(self) -> None:
        _require_window_handle(self.window_handle)


@dataclass(frozen=True, slots=True)
class NativeWindowMoveResizeRequest:
    """Explicit bounded geometry mutation for one existing HWND."""

    window_handle: int
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        _require_window_handle(self.window_handle)
        _require_bounded_window_int(
            self.x, field_name="x", minimum=_MIN_WINDOW_POSITION, maximum=_MAX_WINDOW_POSITION
        )
        _require_bounded_window_int(
            self.y, field_name="y", minimum=_MIN_WINDOW_POSITION, maximum=_MAX_WINDOW_POSITION
        )
        _require_bounded_window_int(
            self.width, field_name="width", minimum=_MIN_WINDOW_SIZE, maximum=_MAX_WINDOW_SIZE
        )
        _require_bounded_window_int(
            self.height, field_name="height", minimum=_MIN_WINDOW_SIZE, maximum=_MAX_WINDOW_SIZE
        )


@dataclass(frozen=True, slots=True)
class NativeTextInputRequest:
    """Bounded Unicode text to inject through the active keyboard target."""

    text: str

    def __post_init__(self) -> None:
        _require_code_unit_bound(
            self.text,
            field_name="text",
            maximum=_MAX_TEXT_INPUT_CODE_UNITS,
            allow_empty=False,
        )


@dataclass(frozen=True, slots=True)
class NativeKeyStroke:
    """One controlled virtual-key stroke with optional controlled modifiers."""

    key: WindowsVirtualKey
    modifiers: frozenset[WindowsKeyModifier] = frozenset()
    repeat: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.key, WindowsVirtualKey):
            raise TypeError("key must be a WindowsVirtualKey")
        if not isinstance(self.modifiers, frozenset):
            raise TypeError("modifiers must be a frozenset of WindowsKeyModifier")
        for modifier in self.modifiers:
            if not isinstance(modifier, WindowsKeyModifier):
                raise TypeError("modifiers must contain only WindowsKeyModifier values")
        if type(self.repeat) is not int:
            raise TypeError("repeat must be an int")
        if self.repeat < 1 or self.repeat > 32:
            raise ValueError("repeat must be between 1 and 32")


@dataclass(frozen=True, slots=True)
class NativeKeyInputRequest:
    """Bounded sequence of controlled key strokes."""

    strokes: tuple[NativeKeyStroke, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.strokes, tuple):
            raise TypeError("strokes must be a tuple of NativeKeyStroke")
        if not self.strokes:
            raise ValueError("strokes must be non-empty")
        if len(self.strokes) > _MAX_KEY_STROKES:
            raise ValueError(f"strokes must not contain more than {_MAX_KEY_STROKES} items")
        for index, stroke in enumerate(self.strokes):
            if not isinstance(stroke, NativeKeyStroke):
                raise TypeError(f"strokes[{index}] must be a NativeKeyStroke")
        event_count = _key_input_event_count(self.strokes)
        if event_count > _MAX_INPUT_EVENTS:
            raise ValueError(f"key input would exceed {_MAX_INPUT_EVENTS} input events")


@dataclass(frozen=True, slots=True)
class NativeClipboardTextRequest:
    """Bounded Unicode text replacement for the system clipboard."""

    text: str

    def __post_init__(self) -> None:
        _require_code_unit_bound(
            self.text,
            field_name="text",
            maximum=_MAX_CLIPBOARD_CODE_UNITS,
            allow_empty=True,
        )


@dataclass(frozen=True, slots=True)
class NativeProcessLaunchOutcome:
    """Low-level CreateProcessW outcome; not an application-readiness claim."""

    process_id: int
    thread_id: int
    process_handle_closed: bool
    thread_handle_closed: bool
    close_error_codes: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if type(self.process_id) is not int or self.process_id <= 0:
            raise ValueError("process_id must be a positive int")
        if type(self.thread_id) is not int or self.thread_id <= 0:
            raise ValueError("thread_id must be a positive int")
        if type(self.process_handle_closed) is not bool:
            raise TypeError("process_handle_closed must be bool")
        if type(self.thread_handle_closed) is not bool:
            raise TypeError("thread_handle_closed must be bool")
        if not isinstance(self.close_error_codes, tuple):
            raise TypeError("close_error_codes must be a tuple")
        for code in self.close_error_codes:
            if type(code) is not int or code < 0:
                raise ValueError("close_error_codes entries must be non-negative ints")


@dataclass(frozen=True, slots=True)
class NativeWindowStateOutcome:
    """Low-level ShowWindow outcome; final state is not verified here."""

    window_handle: int
    requested_state: NativeWindowShowState
    was_previously_visible: bool

    def __post_init__(self) -> None:
        _require_window_handle(self.window_handle)
        if not isinstance(self.requested_state, NativeWindowShowState):
            raise TypeError("requested_state must be a NativeWindowShowState")
        if type(self.was_previously_visible) is not bool:
            raise TypeError("was_previously_visible must be bool")


@dataclass(frozen=True, slots=True)
class NativeWindowActivationOutcome:
    """Low-level SetForegroundWindow outcome; no focus-state verification."""

    window_handle: int
    request_accepted: bool
    win32_error: int

    def __post_init__(self) -> None:
        _require_window_handle(self.window_handle)
        if type(self.request_accepted) is not bool:
            raise TypeError("request_accepted must be bool")
        if type(self.win32_error) is not int or self.win32_error < 0:
            raise ValueError("win32_error must be a non-negative int")


@dataclass(frozen=True, slots=True)
class NativeWindowMoveResizeOutcome:
    """Low-level SetWindowPos outcome; final geometry is not verified here."""

    window_handle: int
    x: int
    y: int
    width: int
    height: int
    request_accepted: bool
    win32_error: int

    def __post_init__(self) -> None:
        _require_window_handle(self.window_handle)
        _require_bounded_window_int(
            self.x, field_name="x", minimum=_MIN_WINDOW_POSITION, maximum=_MAX_WINDOW_POSITION
        )
        _require_bounded_window_int(
            self.y, field_name="y", minimum=_MIN_WINDOW_POSITION, maximum=_MAX_WINDOW_POSITION
        )
        _require_bounded_window_int(
            self.width, field_name="width", minimum=_MIN_WINDOW_SIZE, maximum=_MAX_WINDOW_SIZE
        )
        _require_bounded_window_int(
            self.height, field_name="height", minimum=_MIN_WINDOW_SIZE, maximum=_MAX_WINDOW_SIZE
        )
        if type(self.request_accepted) is not bool:
            raise TypeError("request_accepted must be bool")
        if type(self.win32_error) is not int or self.win32_error < 0:
            raise ValueError("win32_error must be a non-negative int")


@dataclass(frozen=True, slots=True)
class NativeInputInjectionOutcome:
    """Low-level SendInput count; target text/state is not verified here."""

    requested_events: int
    accepted_events: int
    win32_error: int

    def __post_init__(self) -> None:
        if type(self.requested_events) is not int or self.requested_events <= 0:
            raise ValueError("requested_events must be a positive int")
        if type(self.accepted_events) is not int or self.accepted_events < 0:
            raise ValueError("accepted_events must be a non-negative int")
        if self.accepted_events > self.requested_events:
            raise ValueError("accepted_events must not exceed requested_events")
        if type(self.win32_error) is not int or self.win32_error < 0:
            raise ValueError("win32_error must be a non-negative int")


@dataclass(frozen=True, slots=True)
class NativeClipboardMutationOutcome:
    """Low-level clipboard API outcome; consumer visibility is not verified."""

    text_code_units: int
    format_name: str = "CF_UNICODETEXT"

    def __post_init__(self) -> None:
        if type(self.text_code_units) is not int or self.text_code_units < 0:
            raise ValueError("text_code_units must be a non-negative int")
        if self.format_name != "CF_UNICODETEXT":
            raise ValueError("format_name must be CF_UNICODETEXT")


@runtime_checkable
class NativeMutationSurface(Protocol):
    """Injected seam shape used by future governed Windows callers."""

    def launch_process(
        self,
        request: NativeProcessLaunchRequest,
    ) -> Result[NativeProcessLaunchOutcome, AgentXError]: ...

    def set_window_state(
        self,
        request: NativeWindowStateRequest,
    ) -> Result[NativeWindowStateOutcome, AgentXError]: ...

    def activate_window(
        self,
        request: NativeWindowActivationRequest,
    ) -> Result[NativeWindowActivationOutcome, AgentXError]: ...

    def move_resize_window(
        self,
        request: NativeWindowMoveResizeRequest,
    ) -> Result[NativeWindowMoveResizeOutcome, AgentXError]: ...

    def send_text(
        self,
        request: NativeTextInputRequest,
    ) -> Result[NativeInputInjectionOutcome, AgentXError]: ...

    def send_key_strokes(
        self,
        request: NativeKeyInputRequest,
    ) -> Result[NativeInputInjectionOutcome, AgentXError]: ...

    def set_clipboard_text(
        self,
        request: NativeClipboardTextRequest,
    ) -> Result[NativeClipboardMutationOutcome, AgentXError]: ...


class WindowsNativeMutationAdapter:
    """Default adapter over the isolated lazy Win32 mutation implementation."""

    __slots__ = ()

    def launch_process(
        self,
        request: NativeProcessLaunchRequest,
    ) -> Result[NativeProcessLaunchOutcome, AgentXError]:
        if not isinstance(request, NativeProcessLaunchRequest):
            raise TypeError("request must be a NativeProcessLaunchRequest")
        return _call_when_available(
            "process launch",
            lambda: _launch_process_windows(request),
        )

    def set_window_state(
        self,
        request: NativeWindowStateRequest,
    ) -> Result[NativeWindowStateOutcome, AgentXError]:
        if not isinstance(request, NativeWindowStateRequest):
            raise TypeError("request must be a NativeWindowStateRequest")
        return _call_when_available(
            "window state mutation",
            lambda: _set_window_state_windows(request),
        )

    def activate_window(
        self,
        request: NativeWindowActivationRequest,
    ) -> Result[NativeWindowActivationOutcome, AgentXError]:
        if not isinstance(request, NativeWindowActivationRequest):
            raise TypeError("request must be a NativeWindowActivationRequest")
        return _call_when_available(
            "window activation",
            lambda: _activate_window_windows(request),
        )

    def move_resize_window(
        self,
        request: NativeWindowMoveResizeRequest,
    ) -> Result[NativeWindowMoveResizeOutcome, AgentXError]:
        if not isinstance(request, NativeWindowMoveResizeRequest):
            raise TypeError("request must be a NativeWindowMoveResizeRequest")
        return _call_when_available(
            "window move/resize",
            lambda: _move_resize_window_windows(request),
        )

    def send_text(
        self,
        request: NativeTextInputRequest,
    ) -> Result[NativeInputInjectionOutcome, AgentXError]:
        if not isinstance(request, NativeTextInputRequest):
            raise TypeError("request must be a NativeTextInputRequest")
        return _call_when_available(
            "text input injection",
            lambda: _send_text_windows(request),
        )

    def send_key_strokes(
        self,
        request: NativeKeyInputRequest,
    ) -> Result[NativeInputInjectionOutcome, AgentXError]:
        if not isinstance(request, NativeKeyInputRequest):
            raise TypeError("request must be a NativeKeyInputRequest")
        return _call_when_available(
            "key input injection",
            lambda: _send_key_strokes_windows(request),
        )

    def set_clipboard_text(
        self,
        request: NativeClipboardTextRequest,
    ) -> Result[NativeClipboardMutationOutcome, AgentXError]:
        if not isinstance(request, NativeClipboardTextRequest):
            raise TypeError("request must be a NativeClipboardTextRequest")
        return _call_when_available(
            "clipboard text mutation",
            lambda: _set_clipboard_text_windows(request),
        )


def _unsupported_error(operation: str) -> AgentXError:
    return AgentXError(
        code=NATIVE_MUTATION_UNAVAILABLE_ERROR_CODE,
        message=(
            f"Windows native mutation seam is unavailable for {operation}: "
            f"host platform is {sys.platform!r}, not Windows"
        ),
        category=ErrorCategory.PRECONDITION,
        retryability=Retryability.NON_RETRYABLE,
        details={"operation": operation, "sys_platform": sys.platform},
    )


def _native_exception(operation: str, exc: Exception) -> AgentXError:
    return AgentXError(
        code=NATIVE_MUTATION_EXCEPTION_ERROR_CODE,
        message=f"Windows native mutation raised during {operation}",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
        details={"operation": operation, "exception_type": type(exc).__name__},
    )


def _win32_error(code: str, operation: str, win32_error: int) -> AgentXError:
    return AgentXError(
        code=code,
        message=f"Windows native mutation failed during {operation} with Win32 error {win32_error}",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
        details={"operation": operation, "win32_error": win32_error},
    )


def _call_when_available(  # noqa: UP047
    operation: str,
    function: Callable[[], Result[T, AgentXError]],
) -> Result[T, AgentXError]:
    if not is_native_mutation_available():
        return Result.failure(_unsupported_error(operation))
    try:
        return function()
    except Exception as exc:  # pragma: no cover - defensive native boundary
        return Result.failure(_native_exception(operation, exc))


def _key_input_event_count(strokes: tuple[NativeKeyStroke, ...]) -> int:
    total = 0
    for stroke in strokes:
        # For each repeat: modifiers down, key down/up, modifiers up.
        total += stroke.repeat * (2 + 2 * len(stroke.modifiers))
    return total


def _launch_process_windows(
    request: NativeProcessLaunchRequest,
) -> Result[NativeProcessLaunchOutcome, AgentXError]:
    import ctypes
    from ctypes import wintypes

    ctypes_win: Any = ctypes

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    kernel32 = ctypes_win.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(PROCESS_INFORMATION),
    )
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(STARTUPINFOW)
    process_information = PROCESS_INFORMATION()
    command_line = ctypes.create_unicode_buffer(_windows_command_line(request))

    ctypes_win.set_last_error(0)
    reported_created = kernel32.CreateProcessW(
        request.executable_path,
        command_line,
        None,
        None,
        False,
        0,
        None,
        None,
        ctypes.byref(startup),
        ctypes.byref(process_information),
    )
    if not reported_created:
        return Result.failure(
            _win32_error(
                PROCESS_LAUNCH_FAILED_ERROR_CODE,
                "CreateProcessW",
                ctypes_win.get_last_error(),
            )
        )

    close_errors: list[int] = []
    process_closed = False
    thread_closed = False
    if process_information.hProcess:
        ctypes_win.set_last_error(0)
        process_closed = bool(kernel32.CloseHandle(process_information.hProcess))
        if not process_closed:
            close_errors.append(int(ctypes_win.get_last_error()))
    if process_information.hThread:
        ctypes_win.set_last_error(0)
        thread_closed = bool(kernel32.CloseHandle(process_information.hThread))
        if not thread_closed:
            close_errors.append(int(ctypes_win.get_last_error()))

    return Result.success(
        NativeProcessLaunchOutcome(
            process_id=int(process_information.dwProcessId),
            thread_id=int(process_information.dwThreadId),
            process_handle_closed=process_closed,
            thread_handle_closed=thread_closed,
            close_error_codes=tuple(close_errors),
        )
    )


def _set_window_state_windows(
    request: NativeWindowStateRequest,
) -> Result[NativeWindowStateOutcome, AgentXError]:
    import ctypes
    from ctypes import wintypes

    ctypes_win: Any = ctypes

    user32 = ctypes_win.WinDLL("user32", use_last_error=True)
    user32.ShowWindow.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)

    was_previously_visible = bool(
        user32.ShowWindow(request.window_handle, _SHOW_STATE_FLAGS[request.state])
    )
    return Result.success(
        NativeWindowStateOutcome(
            window_handle=request.window_handle,
            requested_state=request.state,
            was_previously_visible=was_previously_visible,
        )
    )


def _activate_window_windows(
    request: NativeWindowActivationRequest,
) -> Result[NativeWindowActivationOutcome, AgentXError]:
    import ctypes
    from ctypes import wintypes

    ctypes_win: Any = ctypes

    user32 = ctypes_win.WinDLL("user32", use_last_error=True)
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)

    ctypes_win.set_last_error(0)
    request_accepted = bool(user32.SetForegroundWindow(request.window_handle))
    win32_error = int(ctypes_win.get_last_error()) if not request_accepted else 0
    if not request_accepted:
        return Result.failure(
            _win32_error(
                WINDOW_ACTIVATION_FAILED_ERROR_CODE,
                "SetForegroundWindow",
                win32_error,
            )
        )
    return Result.success(
        NativeWindowActivationOutcome(
            window_handle=request.window_handle,
            request_accepted=True,
            win32_error=0,
        )
    )


def _move_resize_window_windows(
    request: NativeWindowMoveResizeRequest,
) -> Result[NativeWindowMoveResizeOutcome, AgentXError]:
    import ctypes
    from ctypes import wintypes

    ctypes_win: Any = ctypes
    swp_nozorder = 0x0004
    swp_noactivate = 0x0010

    user32 = ctypes_win.WinDLL("user32", use_last_error=True)
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = (
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    )

    ctypes_win.set_last_error(0)
    request_accepted = bool(
        user32.SetWindowPos(
            request.window_handle,
            None,
            request.x,
            request.y,
            request.width,
            request.height,
            swp_nozorder | swp_noactivate,
        )
    )
    win32_error = 0 if request_accepted else int(ctypes_win.get_last_error())
    if not request_accepted:
        return Result.failure(
            _win32_error(
                WINDOW_MOVE_RESIZE_FAILED_ERROR_CODE,
                "SetWindowPos",
                win32_error,
            )
        )
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


def _send_text_windows(
    request: NativeTextInputRequest,
) -> Result[NativeInputInjectionOutcome, AgentXError]:
    events = _text_input_events(_utf16_code_units(request.text))
    return _send_input_events(events)


def _send_key_strokes_windows(
    request: NativeKeyInputRequest,
) -> Result[NativeInputInjectionOutcome, AgentXError]:
    events: list[_KeyboardEventSpec] = []
    for stroke in request.strokes:
        modifiers = tuple(modifier for modifier in _MODIFIER_ORDER if modifier in stroke.modifiers)
        for _ in range(stroke.repeat):
            for modifier in modifiers:
                events.append(_KeyboardEventSpec(virtual_key=_MODIFIER_KEY_CODES[modifier]))
            events.append(_KeyboardEventSpec(virtual_key=_VIRTUAL_KEY_CODES[stroke.key]))
            events.append(
                _KeyboardEventSpec(virtual_key=_VIRTUAL_KEY_CODES[stroke.key], key_up=True)
            )
            for modifier in reversed(modifiers):
                events.append(
                    _KeyboardEventSpec(
                        virtual_key=_MODIFIER_KEY_CODES[modifier],
                        key_up=True,
                    )
                )
    return _send_input_events(tuple(events))


@dataclass(frozen=True, slots=True)
class _KeyboardEventSpec:
    virtual_key: int = 0
    scan_code: int = 0
    key_up: bool = False
    unicode: bool = False


def _text_input_events(code_units: tuple[int, ...]) -> tuple[_KeyboardEventSpec, ...]:
    events: list[_KeyboardEventSpec] = []
    for code_unit in code_units:
        events.append(_KeyboardEventSpec(scan_code=code_unit, unicode=True))
        events.append(_KeyboardEventSpec(scan_code=code_unit, key_up=True, unicode=True))
    return tuple(events)


def _send_input_events(
    events: tuple[_KeyboardEventSpec, ...] | list[_KeyboardEventSpec],
) -> Result[NativeInputInjectionOutcome, AgentXError]:
    import ctypes
    from ctypes import wintypes

    ctypes_win: Any = ctypes

    if not events:
        raise ValueError("events must be non-empty")
    if len(events) > _MAX_INPUT_EVENTS:
        raise ValueError(f"events must not exceed {_MAX_INPUT_EVENTS}")

    ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [
            ("type", wintypes.DWORD),
            ("u", INPUT_UNION),
        ]

    def to_input(spec: _KeyboardEventSpec) -> INPUT:
        flags = 0
        if spec.key_up:
            flags |= KEYEVENTF_KEYUP
        if spec.unicode:
            flags |= KEYEVENTF_UNICODE
        item = INPUT()
        item.type = INPUT_KEYBOARD
        item.ki = KEYBDINPUT(
            int(spec.virtual_key),
            int(spec.scan_code),
            flags,
            0,
            ULONG_PTR(0),
        )
        return item

    user32 = ctypes_win.WinDLL("user32", use_last_error=True)
    user32.SendInput.restype = wintypes.UINT
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int)

    array_type = INPUT * len(events)
    array = array_type(*(to_input(spec) for spec in events))
    ctypes_win.set_last_error(0)
    accepted_events = int(user32.SendInput(len(events), array, ctypes.sizeof(INPUT)))
    win32_error = 0 if accepted_events == len(events) else int(ctypes_win.get_last_error())
    return Result.success(
        NativeInputInjectionOutcome(
            requested_events=len(events),
            accepted_events=accepted_events,
            win32_error=win32_error,
        )
    )


def _set_clipboard_text_windows(
    request: NativeClipboardTextRequest,
) -> Result[NativeClipboardMutationOutcome, AgentXError]:
    import ctypes
    from ctypes import wintypes

    ctypes_win: Any = ctypes

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    user32 = ctypes_win.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes_win.WinDLL("kernel32", use_last_error=True)

    user32.OpenClipboard.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = ()
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = ()

    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = (wintypes.HANDLE,)
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalUnlock.argtypes = (wintypes.HANDLE,)
    kernel32.GlobalFree.restype = wintypes.HANDLE
    kernel32.GlobalFree.argtypes = (wintypes.HANDLE,)

    encoded = (request.text + "\x00").encode("utf-16-le")
    clipboard_open = False
    owns_memory = True
    ctypes_win.set_last_error(0)
    memory = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
    if not memory:
        return Result.failure(
            _win32_error(
                CLIPBOARD_MUTATION_FAILED_ERROR_CODE,
                "GlobalAlloc for clipboard text",
                ctypes_win.get_last_error(),
            )
        )

    try:
        ctypes_win.set_last_error(0)
        locked = kernel32.GlobalLock(memory)
        if not locked:
            return Result.failure(
                _win32_error(
                    CLIPBOARD_MUTATION_FAILED_ERROR_CODE,
                    "GlobalLock for clipboard text",
                    ctypes_win.get_last_error(),
                )
            )
        try:
            ctypes.memmove(locked, encoded, len(encoded))
        finally:
            kernel32.GlobalUnlock(memory)

        ctypes_win.set_last_error(0)
        if not user32.OpenClipboard(None):
            return Result.failure(
                _win32_error(
                    CLIPBOARD_MUTATION_FAILED_ERROR_CODE,
                    "OpenClipboard",
                    ctypes_win.get_last_error(),
                )
            )
        clipboard_open = True

        ctypes_win.set_last_error(0)
        if not user32.EmptyClipboard():
            return Result.failure(
                _win32_error(
                    CLIPBOARD_MUTATION_FAILED_ERROR_CODE,
                    "EmptyClipboard",
                    ctypes_win.get_last_error(),
                )
            )

        ctypes_win.set_last_error(0)
        if not user32.SetClipboardData(CF_UNICODETEXT, memory):
            return Result.failure(
                _win32_error(
                    CLIPBOARD_MUTATION_FAILED_ERROR_CODE,
                    "SetClipboardData",
                    ctypes_win.get_last_error(),
                )
            )
        owns_memory = False
        return Result.success(
            NativeClipboardMutationOutcome(text_code_units=_utf16_code_unit_count(request.text))
        )
    finally:
        if clipboard_open:
            user32.CloseClipboard()
        if owns_memory and memory:
            kernel32.GlobalFree(memory)
