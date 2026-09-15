"""Isolated read-only Win32 surface for Windows discovery (A5.02).

This module is the **only** module in the AgentX package that is allowed to
know about ``ctypes`` or any Win32 API. A5.02 needs exactly one native
mechanism — enumerating what is already running — and it keeps that mechanism
behind this deliberately boring boundary so that every other module stays pure
Python and testable on any host:

* :func:`enumerate_processes_raw` — one Toolhelp32 process snapshot
  (``CreateToolhelp32Snapshot`` + ``Process32FirstW`` / ``Process32NextW``),
  the smallest reliable, stdlib-only, read-only Windows process enumeration.
* :func:`query_executable_path_raw` — best-effort full image path for one PID
  (``OpenProcess`` with ``PROCESS_QUERY_LIMITED_INFORMATION`` +
  ``QueryFullProcessImageNameW``). Best effort means the outcome may be an OS
  error code; it is reported as data, never as an exception.
* :func:`enumerate_windows_raw` — top-level windows with their owning PID,
  visibility, title and class name (``EnumWindows``, ``GetWindowThreadProcessId``,
  ``IsWindowVisible``, ``GetWindowTextLengthW`` / ``GetWindowTextW``,
  ``GetClassNameW``). These are currently available, stable, read-only
  desktop APIs; they are how discovery associates *applications* (windows a
  user can see) with the processes that own them.

Everything here is a **read**. This module performs no UI Automation
traversal, no control discovery, no input synthesis, no window manipulation,
no focus change, no capture, and no process lifecycle operation of any kind.

Import safety
-------------

Importing this module performs **no machine action on any host**:

* it imports only :mod:`sys`, stdlib value typing, and canonical
  :mod:`agentx.core` error/result contracts;
* :mod:`ctypes` is imported lazily **inside** each native function, so even on
  Windows the import of this module loads no native library, and on
  Linux/macOS nothing native is ever touched;
* every function first checks :func:`is_native_surface_available` and returns
  an explicit canonical failure when the host is not Windows, so calling this
  module on a non-Windows host never raises ``ImportError``/``AttributeError``.

Failure semantics
-----------------

Expected operational failures are returned as canonical
:class:`~agentx.core.errors.AgentXError` values inside
:class:`~agentx.core.result.Result`, never raised. Per-item metadata failures
(a title or image path that could not be read) are *data*: the raw entry
carries the Win32 error code (``0`` means success) and the caller —
:mod:`agentx.capabilities.windows.process_discovery` — translates it into
explicit availability semantics. Raw entries are untrusted OS data and are
returned verbatim; interpretation and validation live entirely in the
discovery layer.

Raw results are returned in whatever order the OS produced. Deterministic
normalization (ordering, deduplication, validation) is the discovery layer's
job, not the native layer's.

Owner: A5.02. Belongs to ``agentx.capabilities.windows``; imports only the
standard library and ``agentx.core`` contracts.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Final

from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result

__all__ = [
    "NATIVE_UNAVAILABLE_ERROR_CODE",
    "PROCESS_ENUMERATION_FAILED_ERROR_CODE",
    "WIN32_ERROR_ACCESS_DENIED",
    "WIN32_ERROR_FILE_NOT_FOUND",
    "WIN32_ERROR_INSUFFICIENT_BUFFER",
    "WIN32_ERROR_INVALID_PARAMETER",
    "WIN32_ERROR_NO_MORE_FILES",
    "WIN32_ERROR_PATH_NOT_FOUND",
    "WINDOW_ENUMERATION_FAILED_ERROR_CODE",
    "RawPathQuery",
    "RawProcessEntry",
    "RawWindowEntry",
    "enumerate_processes_raw",
    "enumerate_windows_raw",
    "get_foreground_window_raw",
    "is_native_surface_available",
    "query_executable_path_raw",
]

#: Canonical error code for "the Win32 surface cannot be used on this host".
NATIVE_UNAVAILABLE_ERROR_CODE: Final[str] = (
    "capabilities.windows.process_discovery.native_unavailable"
)

#: Canonical error code for a failed process snapshot.
PROCESS_ENUMERATION_FAILED_ERROR_CODE: Final[str] = (
    "capabilities.windows.process_discovery.process_enumeration_failed"
)

#: Canonical error code for a failed top-level window walk.
WINDOW_ENUMERATION_FAILED_ERROR_CODE: Final[str] = (
    "capabilities.windows.process_discovery.window_enumeration_failed"
)

# Win32 error codes (``winerror.h``) that the discovery layer translates into
# explicit per-item availability semantics. They are plain ints so tests can
# construct them without any native library.
WIN32_ERROR_ACCESS_DENIED: Final[int] = 5
WIN32_ERROR_FILE_NOT_FOUND: Final[int] = 2
WIN32_ERROR_PATH_NOT_FOUND: Final[int] = 3
WIN32_ERROR_INSUFFICIENT_BUFFER: Final[int] = 122
WIN32_ERROR_NO_MORE_FILES: Final[int] = 18
WIN32_ERROR_INVALID_PARAMETER: Final[int] = 87

_WIN32_PLATFORM: Final[str] = "win32"

# Toolhelp32 snapshot flag: include the process list (and nothing else).
_TH32CS_SNAPPROCESS: Final[int] = 0x2

# QueryFullProcessImageNameW name format: the Win32 path form.
_PROCESS_NAME_WIN32: Final[int] = 0

# OpenProcess access right: the least-privileged right that still allows
# querying the full image name. This is a read-only query right; no process
# memory, termination, suspension, or creation right is ever requested.
_PROCESS_QUERY_LIMITED_INFORMATION: Final[int] = 0x1000

# Buffer bounds. These bound untrusted OS strings defensively; the discovery
# layer applies its own, stricter value validation on top.
_MAX_PATH_CHARS: Final[int] = 1024
_MAX_CLASS_NAME_CHARS: Final[int] = 256
_MAX_TITLE_CHARS: Final[int] = 512


def is_native_surface_available() -> bool:
    """Report whether the read-only Win32 surface can be used on this host.

    This reads only ``sys.platform``; it imports nothing native and performs
    no machine action. It is the sole availability check used by every native
    function below.
    """
    return sys.platform == _WIN32_PLATFORM


def _native_unavailable_error(operation: str) -> AgentXError:
    """Build the canonical failure for using the native surface off-Windows."""
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
    """Build the canonical failure for an OS-level enumeration error."""
    return AgentXError(
        code=code,
        message=f"Windows {operation} failed with Win32 error {win32_error}",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.UNKNOWN,
        details={"operation": operation, "win32_error": win32_error},
    )


# --------------------------------------------------------------------------
# Raw value types (untrusted OS data, verbatim).
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawProcessEntry:
    """One verbatim Toolhelp32 process entry.

    ``executable_name`` is the basename (e.g. ``"chrome.exe"``) exactly as the
    OS reported it, possibly empty; it is untrusted data and is never
    interpreted here. ``parent_process_id`` is ``None`` only if the OS did not
    report one, which the real snapshot never does (fakes may).
    """

    process_id: int
    parent_process_id: int | None
    executable_name: str


@dataclass(frozen=True, slots=True)
class RawPathQuery:
    """Outcome of one best-effort full-image-path query.

    ``error_code`` is the Win32 error code; ``0`` means ``value`` was read.
    When ``error_code`` is non-zero, ``value`` is ``None``.
    """

    value: str | None
    error_code: int


@dataclass(frozen=True, slots=True)
class RawWindowEntry:
    """One verbatim top-level window entry.

    ``title``/``class_name`` are ``None`` when that individual read failed, in
    which case the matching ``*_error_code`` carries the Win32 error. A
    successfully read empty title is the empty string. All fields are
    untrusted data stored verbatim.
    """

    handle: int
    process_id: int
    title: str | None
    title_error_code: int
    class_name: str | None
    class_name_error_code: int
    is_visible: bool


# --------------------------------------------------------------------------
# Toolhelp32 process snapshot.
# --------------------------------------------------------------------------


def enumerate_processes_raw() -> Result[tuple[RawProcessEntry, ...], AgentXError]:
    """Take one read-only Toolhelp32 snapshot of the process list.

    Returns the entries in OS order; the discovery layer normalizes. Fails
    with an explicit canonical error when the host is not Windows, when the
    snapshot cannot be taken, or when the walk terminates abnormally. The
    snapshot handle is always closed; no process is opened, changed, or
    started.
    """
    if not is_native_surface_available():
        return Result.failure(_native_unavailable_error("process enumeration"))

    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        """Layout of the wide Toolhelp32 process entry."""

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


# --------------------------------------------------------------------------
# Best-effort full image path for one process.
# --------------------------------------------------------------------------


def query_executable_path_raw(process_id: int) -> Result[RawPathQuery, AgentXError]:
    """Query the full image path of one process, best effort.

    Opens the process with ``PROCESS_QUERY_LIMITED_INFORMATION`` only — the
    least-privileged read-only query right — and asks for its image name. The
    handle is always closed. Per-query failures (including access denial for
    elevated processes and "process already gone") are **data**:
    :class:`RawPathQuery` with the Win32 error code. Only a non-Windows host
    fails the ``Result`` itself.
    """
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


# --------------------------------------------------------------------------
# Top-level window walk.
# --------------------------------------------------------------------------


def _read_window_title(user32: Any, handle: int) -> tuple[str | None, int]:
    """Read one window title verbatim; ``None`` plus a Win32 error on failure.

    A reported length of zero is treated as "no title" (the empty string,
    error ``0``): Win32 does not reliably update the last error on success,
    so consulting it there would read a stale value. Only a failed copy yields
    ``None``. Titles longer than the bounded buffer are reported as
    ``WIN32_ERROR_INSUFFICIENT_BUFFER`` rather than silently truncated.
    """
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
    """Read one window class name; ``None`` plus a Win32 error on failure."""
    import ctypes

    buffer = ctypes.create_unicode_buffer(_MAX_CLASS_NAME_CHARS)
    copied = user32.GetClassNameW(handle, buffer, _MAX_CLASS_NAME_CHARS)
    if copied <= 0:
        return (None, ctypes.get_last_error())
    return (str(buffer.value[:copied]), 0)


def get_foreground_window_raw() -> Result[int | None, AgentXError]:
    """Read the current foreground HWND without changing focus.

    ``None`` means Windows reported no foreground window. The handle is
    point-in-time evidence only and may become stale immediately.
    """
    if not is_native_surface_available():
        return Result.failure(_native_unavailable_error("foreground window query"))

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetForegroundWindow.argtypes = ()
    handle = user32.GetForegroundWindow()
    if not handle:
        return Result.success(None)
    return Result.success(int(handle))


def enumerate_windows_raw() -> Result[tuple[RawWindowEntry, ...], AgentXError]:
    """Walk the current top-level windows, read-only.

    For each window the owning PID, visibility, title and class name are read
    with read-only desktop APIs. Windows are **not** activated, focused,
    moved, closed, or queried through UI Automation. Entries are returned in
    OS order; per-window read failures stay on the entry as Win32 error
    codes. Only a non-Windows host or a failed walk fails the ``Result``.
    """
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
        """Record one window; never fails so the walk always continues."""
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
