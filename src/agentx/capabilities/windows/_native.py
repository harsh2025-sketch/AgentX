"""Isolated stdlib Win32 surface for AgentX Windows capabilities.

A5.02 uses this module for read-only process/window discovery. A5.06 extends
this same seam with explicit keyboard/text injection and bounded Unicode-text
clipboard operations. Keeping both behind one module preserves the repository
rule that no other AgentX source imports ``ctypes`` or knows Win32 call details.

Importing this module is inert on every host. ``ctypes`` and native libraries
are loaded lazily inside explicit functions only. A5.06 registers no global
hotkeys, installs no keyboard hooks, captures no keystrokes, resolves no UIA
targets, changes no focus, and retains no clipboard history.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Final

from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result

__all__ = [
    "CLIPBOARD_CLEAR_FAILED_ERROR_CODE",
    "CLIPBOARD_OPEN_FAILED_ERROR_CODE",
    "CLIPBOARD_READ_FAILED_ERROR_CODE",
    "CLIPBOARD_TEXT_TOO_LARGE_ERROR_CODE",
    "CLIPBOARD_WRITE_FAILED_ERROR_CODE",
    "INPUT_NATIVE_UNAVAILABLE_ERROR_CODE",
    "KEY_INPUT_FAILED_ERROR_CODE",
    "NATIVE_UNAVAILABLE_ERROR_CODE",
    "PROCESS_ENUMERATION_FAILED_ERROR_CODE",
    "TEXT_INPUT_FAILED_ERROR_CODE",
    "WINDOW_ENUMERATION_FAILED_ERROR_CODE",
    "WIN32_ERROR_ACCESS_DENIED",
    "WIN32_ERROR_FILE_NOT_FOUND",
    "WIN32_ERROR_INSUFFICIENT_BUFFER",
    "WIN32_ERROR_INVALID_PARAMETER",
    "WIN32_ERROR_NO_MORE_FILES",
    "WIN32_ERROR_PATH_NOT_FOUND",
    "RawClipboardRead",
    "RawInputReceipt",
    "RawPathQuery",
    "RawProcessEntry",
    "RawWindowEntry",
    "clear_clipboard_raw",
    "enumerate_processes_raw",
    "enumerate_windows_raw",
    "is_native_surface_available",
    "query_executable_path_raw",
    "read_clipboard_text_raw",
    "send_key_raw",
    "send_text_raw",
    "write_clipboard_text_raw",
]

# A5.02 discovery error vocabulary. These values are compatibility contracts.
NATIVE_UNAVAILABLE_ERROR_CODE: Final[str] = (
    "capabilities.windows.process_discovery.native_unavailable"
)
PROCESS_ENUMERATION_FAILED_ERROR_CODE: Final[str] = (
    "capabilities.windows.process_discovery.process_enumeration_failed"
)
WINDOW_ENUMERATION_FAILED_ERROR_CODE: Final[str] = (
    "capabilities.windows.process_discovery.window_enumeration_failed"
)

WIN32_ERROR_ACCESS_DENIED: Final[int] = 5
WIN32_ERROR_FILE_NOT_FOUND: Final[int] = 2
WIN32_ERROR_PATH_NOT_FOUND: Final[int] = 3
WIN32_ERROR_INSUFFICIENT_BUFFER: Final[int] = 122
WIN32_ERROR_NO_MORE_FILES: Final[int] = 18
WIN32_ERROR_INVALID_PARAMETER: Final[int] = 87

_WIN32_PLATFORM: Final[str] = "win32"
_TH32CS_SNAPPROCESS: Final[int] = 0x2
_PROCESS_NAME_WIN32: Final[int] = 0
_PROCESS_QUERY_LIMITED_INFORMATION: Final[int] = 0x1000
_MAX_PATH_CHARS: Final[int] = 1024
_MAX_CLASS_NAME_CHARS: Final[int] = 256
_MAX_TITLE_CHARS: Final[int] = 512


def is_native_surface_available() -> bool:
    """Return whether this process can call the Win32 seam."""
    return sys.platform == _WIN32_PLATFORM


def _native_unavailable_error(operation: str) -> AgentXError:
    return AgentXError(
        code=NATIVE_UNAVAILABLE_ERROR_CODE,
        message=(
            f"Windows native discovery surface is unavailable for {operation}: "
            f"host platform is {sys.platform!r}, not Windows"
        ),
        category=ErrorCategory.PRECONDITION,
        retryability=Retryability.NON_RETRYABLE,
        details={"operation": operation, "sys_platform": sys.platform},
    )


def _enumeration_error(code: str, operation: str, win32_error: int) -> AgentXError:
    return AgentXError(
        code=code,
        message=f"Windows {operation} failed with Win32 error {win32_error}",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
        details={"operation": operation, "win32_error": win32_error},
    )


@dataclass(frozen=True, slots=True)
class RawProcessEntry:
    process_id: int
    parent_process_id: int | None
    executable_name: str


@dataclass(frozen=True, slots=True)
class RawPathQuery:
    value: str | None
    error_code: int


@dataclass(frozen=True, slots=True)
class RawWindowEntry:
    handle: int
    process_id: int
    title: str | None
    title_error_code: int
    class_name: str | None
    class_name_error_code: int
    is_visible: bool


def enumerate_processes_raw() -> Result[tuple[RawProcessEntry, ...], AgentXError]:
    """Take one read-only Toolhelp32 process snapshot."""
    if not is_native_surface_available():
        return Result.failure(_native_unavailable_error("process enumeration"))

    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    invalid_handle = wintypes.HANDLE(-1).value
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == invalid_handle:
        return Result.failure(
            _enumeration_error(
                PROCESS_ENUMERATION_FAILED_ERROR_CODE,
                "process snapshot creation",
                ctypes.get_last_error(),
            )
        )
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        entries: list[RawProcessEntry] = []
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            entries.append(
                RawProcessEntry(
                    process_id=int(entry.th32ProcessID),
                    parent_process_id=int(entry.th32ParentProcessID),
                    executable_name=str(entry.szExeFile),
                )
            )
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        walk_error = ctypes.get_last_error()
        if walk_error != WIN32_ERROR_NO_MORE_FILES:
            return Result.failure(
                _enumeration_error(
                    PROCESS_ENUMERATION_FAILED_ERROR_CODE,
                    "process snapshot walk",
                    walk_error,
                )
            )
        return Result.success(tuple(entries))
    finally:
        kernel32.CloseHandle(snapshot)


def query_executable_path_raw(process_id: int) -> Result[RawPathQuery, AgentXError]:
    """Best-effort read-only full image-path query for one process."""
    if not is_native_surface_available():
        return Result.failure(_native_unavailable_error("image path query"))

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not handle:
        return Result.success(RawPathQuery(value=None, error_code=ctypes.get_last_error()))
    try:
        size = wintypes.DWORD(_MAX_PATH_CHARS)
        buffer = ctypes.create_unicode_buffer(_MAX_PATH_CHARS)
        if not kernel32.QueryFullProcessImageNameW(
            handle, _PROCESS_NAME_WIN32, buffer, ctypes.byref(size)
        ):
            return Result.success(RawPathQuery(value=None, error_code=ctypes.get_last_error()))
        return Result.success(RawPathQuery(value=str(buffer.value), error_code=0))
    finally:
        kernel32.CloseHandle(handle)


def _read_window_title(user32: Any, handle: int) -> tuple[str | None, int]:
    import ctypes

    length = user32.GetWindowTextLengthW(handle)
    if length <= 0:
        return ("", 0)
    if length > _MAX_TITLE_CHARS:
        return (None, WIN32_ERROR_INSUFFICIENT_BUFFER)
    buffer = ctypes.create_unicode_buffer(length + 1)
    copied = user32.GetWindowTextW(handle, buffer, length + 1)
    if copied <= 0:
        return (None, ctypes.get_last_error())
    return (str(buffer.value[:copied]), 0)


def _read_window_class(user32: Any, handle: int) -> tuple[str | None, int]:
    import ctypes

    buffer = ctypes.create_unicode_buffer(_MAX_CLASS_NAME_CHARS)
    copied = user32.GetClassNameW(handle, buffer, _MAX_CLASS_NAME_CHARS)
    if copied <= 0:
        return (None, ctypes.get_last_error())
    return (str(buffer.value[:copied]), 0)


def enumerate_windows_raw() -> Result[tuple[RawWindowEntry, ...], AgentXError]:
    """Read the current top-level Windows window set without changing it."""
    if not is_native_surface_available():
        return Result.failure(_native_unavailable_error("window enumeration"))

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)

    entries: list[RawWindowEntry] = []

    def record(handle: int | None, _lparam: int) -> bool:
        if not handle:
            return True
        owner = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        title, title_error = _read_window_title(user32, handle)
        class_name, class_error = _read_window_class(user32, handle)
        entries.append(
            RawWindowEntry(
                handle=int(handle),
                process_id=int(owner.value),
                title=title,
                title_error_code=title_error,
                class_name=class_name,
                class_name_error_code=class_error,
                is_visible=bool(user32.IsWindowVisible(handle)),
            )
        )
        return True

    collect: Any = callback_type(record)
    if not user32.EnumWindows(collect, 0):
        return Result.failure(
            _enumeration_error(
                WINDOW_ENUMERATION_FAILED_ERROR_CODE,
                "top-level window walk",
                ctypes.get_last_error(),
            )
        )
    return Result.success(tuple(entries))


# A5.06 keyboard/text/clipboard error vocabulary.
INPUT_NATIVE_UNAVAILABLE_ERROR_CODE: Final[str] = (
    "capabilities.windows.keyboard_clipboard.native_unavailable"
)
TEXT_INPUT_FAILED_ERROR_CODE: Final[str] = "capabilities.windows.text.enter.native_failed"
KEY_INPUT_FAILED_ERROR_CODE: Final[str] = "capabilities.windows.keyboard.press.native_failed"
CLIPBOARD_OPEN_FAILED_ERROR_CODE: Final[str] = "capabilities.windows.clipboard.open_failed"
CLIPBOARD_READ_FAILED_ERROR_CODE: Final[str] = (
    "capabilities.windows.clipboard.read_text.native_failed"
)
CLIPBOARD_WRITE_FAILED_ERROR_CODE: Final[str] = (
    "capabilities.windows.clipboard.write_text.native_failed"
)
CLIPBOARD_CLEAR_FAILED_ERROR_CODE: Final[str] = "capabilities.windows.clipboard.clear.native_failed"
CLIPBOARD_TEXT_TOO_LARGE_ERROR_CODE: Final[str] = (
    "capabilities.windows.clipboard.read_text.too_large"
)

_CF_UNICODETEXT: Final[int] = 13
_GMEM_MOVEABLE: Final[int] = 0x0002
_INPUT_KEYBOARD: Final[int] = 1
_KEYEVENTF_EXTENDEDKEY: Final[int] = 0x0001
_KEYEVENTF_KEYUP: Final[int] = 0x0002
_KEYEVENTF_UNICODE: Final[int] = 0x0004
_MAX_CLIPBOARD_TEXT_BYTES: Final[int] = (262_144 + 1) * 2
_MAX_RAW_KEY_REPEAT: Final[int] = 20

_SPECIAL_VIRTUAL_KEYS: Final[dict[str, int]] = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "escape": 0x1B,
    "space": 0x20,
    "page_up": 0x21,
    "page_down": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
}
_MODIFIER_VIRTUAL_KEYS: Final[dict[str, int]] = {
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "windows": 0x5B,
}
_EXTENDED_KEYS: Final[frozenset[str]] = frozenset(
    {
        "page_up",
        "page_down",
        "end",
        "home",
        "left",
        "up",
        "right",
        "down",
        "insert",
        "delete",
    }
)
_EXTENDED_MODIFIERS: Final[frozenset[str]] = frozenset({"windows"})


@dataclass(frozen=True, slots=True)
class RawInputReceipt:
    """Exact SendInput acceptance count; not state verification."""

    events_requested: int
    events_inserted: int


@dataclass(frozen=True, slots=True)
class RawClipboardRead:
    """One raw CF_UNICODETEXT read; text remains untrusted data."""

    has_text: bool
    text: str | None


def _a506_unavailable_error(operation: str) -> AgentXError:
    return AgentXError(
        code=INPUT_NATIVE_UNAVAILABLE_ERROR_CODE,
        message=(
            f"Windows keyboard/clipboard native surface is unavailable for {operation}: "
            f"host platform is {sys.platform!r}, not Windows"
        ),
        category=ErrorCategory.PRECONDITION,
        retryability=Retryability.NON_RETRYABLE,
        details={"operation": operation, "sys_platform": sys.platform},
    )


def _a506_native_error(
    *,
    code: str,
    operation: str,
    win32_error: int,
    retryability: Retryability = Retryability.UNKNOWN,
    details: dict[str, Any] | None = None,
) -> AgentXError:
    merged: dict[str, Any] = {"operation": operation, "win32_error": win32_error}
    if details is not None:
        merged.update(details)
    return AgentXError(
        code=code,
        message=f"Windows {operation} failed with Win32 error {win32_error}",
        category=ErrorCategory.EXECUTION,
        retryability=retryability,
        details=merged,
    )


def _virtual_key(key: str) -> int | None:
    if len(key) == 1 and "a" <= key <= "z":
        return ord(key.upper())
    if len(key) == 1 and "0" <= key <= "9":
        return ord(key)
    if key.startswith("f") and key[1:].isdigit():
        number = int(key[1:])
        if 1 <= number <= 24:
            return 0x70 + number - 1
    return _SPECIAL_VIRTUAL_KEYS.get(key)


def _send_keyboard_inputs(
    events: list[tuple[int, int, int]],
    *,
    operation: str,
) -> Result[RawInputReceipt, AgentXError]:
    if not is_native_surface_available():
        return Result.failure(_a506_unavailable_error(operation))

    import ctypes
    from ctypes import wintypes

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class INPUTUNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("value", INPUTUNION)]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.restype = wintypes.UINT
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)

    array_type = INPUT * len(events)
    inputs = array_type()
    for index, (virtual_key, scan_code, flags) in enumerate(events):
        inputs[index].type = _INPUT_KEYBOARD
        inputs[index].value.ki = KEYBDINPUT(
            wVk=virtual_key,
            wScan=scan_code,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        )

    ctypes.set_last_error(0)
    inserted = int(user32.SendInput(len(events), inputs, ctypes.sizeof(INPUT)))
    if inserted != len(events):
        return Result.failure(
            _a506_native_error(
                code=(
                    TEXT_INPUT_FAILED_ERROR_CODE
                    if operation == "text entry"
                    else KEY_INPUT_FAILED_ERROR_CODE
                ),
                operation=operation,
                win32_error=ctypes.get_last_error(),
                details={"events_requested": len(events), "events_inserted": inserted},
            )
        )
    return Result.success(RawInputReceipt(events_requested=len(events), events_inserted=inserted))


def send_text_raw(text: str) -> Result[RawInputReceipt, AgentXError]:
    """Inject Unicode text using KEYEVENTF_UNICODE without changing focus."""
    if not isinstance(text, str):
        raise TypeError(f"text must be a string, got {type(text).__name__}")
    if not text:
        raise ValueError("text must not be empty")
    if "\x00" in text:
        raise ValueError("text must not contain NUL characters")
    try:
        encoded = text.encode("utf-16-le")
    except UnicodeEncodeError as exc:
        raise ValueError("text must contain valid Unicode scalar values") from exc
    events: list[tuple[int, int, int]] = []
    for offset in range(0, len(encoded), 2):
        unit = int.from_bytes(encoded[offset : offset + 2], "little")
        events.append((0, unit, _KEYEVENTF_UNICODE))
        events.append((0, unit, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP))
    return _send_keyboard_inputs(events, operation="text entry")


def send_key_raw(
    key: str,
    modifiers: tuple[str, ...],
    repeat: int,
) -> Result[RawInputReceipt, AgentXError]:
    """Inject one validated virtual key/chord without hotkey registration."""
    if not isinstance(key, str):
        raise TypeError(f"key must be a string, got {type(key).__name__}")
    if not isinstance(modifiers, tuple) or not all(isinstance(item, str) for item in modifiers):
        raise TypeError("modifiers must be a tuple of strings")
    if len(set(modifiers)) != len(modifiers):
        raise ValueError("modifiers must not contain duplicates")
    if any(item not in _MODIFIER_VIRTUAL_KEYS for item in modifiers):
        raise ValueError("modifiers contain an unsupported value")
    if type(repeat) is not int or not 1 <= repeat <= _MAX_RAW_KEY_REPEAT:
        raise ValueError(f"repeat must be an int between 1 and {_MAX_RAW_KEY_REPEAT}")
    virtual_key = _virtual_key(key)
    if virtual_key is None:
        raise ValueError(f"unsupported key: {key!r}")

    modifier_order = ("control", "alt", "shift", "windows")
    normalized = tuple(item for item in modifier_order if item in modifiers)
    events: list[tuple[int, int, int]] = []
    for modifier in normalized:
        flags = _KEYEVENTF_EXTENDEDKEY if modifier in _EXTENDED_MODIFIERS else 0
        events.append((_MODIFIER_VIRTUAL_KEYS[modifier], 0, flags))
    key_flags = _KEYEVENTF_EXTENDEDKEY if key in _EXTENDED_KEYS else 0
    for _ in range(repeat):
        events.append((virtual_key, 0, key_flags))
        events.append((virtual_key, 0, key_flags | _KEYEVENTF_KEYUP))
    for modifier in reversed(normalized):
        flags = _KEYEVENTF_KEYUP
        if modifier in _EXTENDED_MODIFIERS:
            flags |= _KEYEVENTF_EXTENDEDKEY
        events.append((_MODIFIER_VIRTUAL_KEYS[modifier], 0, flags))
    return _send_keyboard_inputs(events, operation="key input")


def _open_clipboard(user32: Any, ctypes_module: Any, *, operation: str) -> AgentXError | None:
    ctypes_module.set_last_error(0)
    if user32.OpenClipboard(None):
        return None
    return _a506_native_error(
        code=CLIPBOARD_OPEN_FAILED_ERROR_CODE,
        operation=operation,
        win32_error=ctypes_module.get_last_error(),
        retryability=Retryability.RETRYABLE,
    )


def _find_utf16_terminator(raw: bytes) -> int | None:
    for offset in range(0, len(raw) - 1, 2):
        if raw[offset : offset + 2] == b"\x00\x00":
            return offset
    return None


def read_clipboard_text_raw() -> Result[RawClipboardRead, AgentXError]:
    """Read bounded CF_UNICODETEXT once; returned text remains untrusted."""
    if not is_native_surface_available():
        return Result.failure(_a506_unavailable_error("clipboard text read"))

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = ()
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = (wintypes.UINT,)
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = (wintypes.UINT,)
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalSize.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)

    open_error = _open_clipboard(user32, ctypes, operation="clipboard text read")
    if open_error is not None:
        return Result.failure(open_error)
    try:
        if not user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
            return Result.success(RawClipboardRead(has_text=False, text=None))
        handle = user32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return Result.failure(
                _a506_native_error(
                    code=CLIPBOARD_READ_FAILED_ERROR_CODE,
                    operation="clipboard text handle read",
                    win32_error=ctypes.get_last_error(),
                )
            )
        size = int(kernel32.GlobalSize(handle))
        if size <= 0:
            return Result.failure(
                _a506_native_error(
                    code=CLIPBOARD_READ_FAILED_ERROR_CODE,
                    operation="clipboard text size read",
                    win32_error=ctypes.get_last_error(),
                )
            )
        if size > _MAX_CLIPBOARD_TEXT_BYTES:
            return Result.failure(
                AgentXError(
                    code=CLIPBOARD_TEXT_TOO_LARGE_ERROR_CODE,
                    message="Windows clipboard Unicode text exceeds the bounded A5.06 read limit",
                    category=ErrorCategory.RESOURCE,
                    retryability=Retryability.NON_RETRYABLE,
                    details={"clipboard_bytes": size, "max_bytes": _MAX_CLIPBOARD_TEXT_BYTES},
                )
            )
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return Result.failure(
                _a506_native_error(
                    code=CLIPBOARD_READ_FAILED_ERROR_CODE,
                    operation="clipboard text memory lock",
                    win32_error=ctypes.get_last_error(),
                )
            )
        try:
            raw = ctypes.string_at(pointer, size)
        finally:
            kernel32.GlobalUnlock(handle)
        terminator = _find_utf16_terminator(raw)
        if terminator is None:
            return Result.failure(
                AgentXError(
                    code=CLIPBOARD_READ_FAILED_ERROR_CODE,
                    message=(
                        "Windows clipboard Unicode text was not NUL terminated within its "
                        "allocation"
                    ),
                    category=ErrorCategory.EXECUTION,
                    retryability=Retryability.NON_RETRYABLE,
                )
            )
        text = raw[:terminator].decode("utf-16-le", errors="surrogatepass")
        return Result.success(RawClipboardRead(has_text=True, text=text))
    finally:
        user32.CloseClipboard()


def write_clipboard_text_raw(text: str) -> Result[None, AgentXError]:
    """Replace the clipboard with bounded CF_UNICODETEXT without history."""
    if not isinstance(text, str):
        raise TypeError(f"text must be a string, got {type(text).__name__}")
    if "\x00" in text:
        raise ValueError("clipboard text must not contain NUL characters")
    try:
        payload = text.encode("utf-16-le") + b"\x00\x00"
    except UnicodeEncodeError as exc:
        raise ValueError("clipboard text must contain valid Unicode scalar values") from exc
    if len(payload) > _MAX_CLIPBOARD_TEXT_BYTES:
        raise ValueError("clipboard text exceeds the bounded native write limit")
    if not is_native_surface_available():
        return Result.failure(_a506_unavailable_error("clipboard text write"))

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = ()
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = ()
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)

    handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(payload))
    if not handle:
        return Result.failure(
            _a506_native_error(
                code=CLIPBOARD_WRITE_FAILED_ERROR_CODE,
                operation="clipboard text memory allocation",
                win32_error=ctypes.get_last_error(),
            )
        )
    transferred = False
    try:
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return Result.failure(
                _a506_native_error(
                    code=CLIPBOARD_WRITE_FAILED_ERROR_CODE,
                    operation="clipboard text memory lock",
                    win32_error=ctypes.get_last_error(),
                )
            )
        try:
            ctypes.memmove(pointer, payload, len(payload))
        finally:
            kernel32.GlobalUnlock(handle)

        open_error = _open_clipboard(user32, ctypes, operation="clipboard text write")
        if open_error is not None:
            return Result.failure(open_error)
        try:
            ctypes.set_last_error(0)
            if not user32.EmptyClipboard():
                return Result.failure(
                    _a506_native_error(
                        code=CLIPBOARD_WRITE_FAILED_ERROR_CODE,
                        operation="clipboard empty before text write",
                        win32_error=ctypes.get_last_error(),
                    )
                )
            ctypes.set_last_error(0)
            if not user32.SetClipboardData(_CF_UNICODETEXT, handle):
                return Result.failure(
                    _a506_native_error(
                        code=CLIPBOARD_WRITE_FAILED_ERROR_CODE,
                        operation="clipboard Unicode text write",
                        win32_error=ctypes.get_last_error(),
                    )
                )
            transferred = True
            return Result.success(None)
        finally:
            user32.CloseClipboard()
    finally:
        if not transferred:
            kernel32.GlobalFree(handle)


def clear_clipboard_raw() -> Result[None, AgentXError]:
    """Clear the clipboard once without reading or retaining prior content."""
    if not is_native_surface_available():
        return Result.failure(_a506_unavailable_error("clipboard clear"))

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = ()
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = ()

    open_error = _open_clipboard(user32, ctypes, operation="clipboard clear")
    if open_error is not None:
        return Result.failure(open_error)
    try:
        ctypes.set_last_error(0)
        if not user32.EmptyClipboard():
            return Result.failure(
                _a506_native_error(
                    code=CLIPBOARD_CLEAR_FAILED_ERROR_CODE,
                    operation="clipboard clear",
                    win32_error=ctypes.get_last_error(),
                )
            )
        return Result.success(None)
    finally:
        user32.CloseClipboard()
