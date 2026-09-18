"""Read-only Windows clipboard observation seam used for independent verification."""

from __future__ import annotations

import sys
from typing import Any, Final

from agentx.capabilities.windows.keyboard_text_clipboard import RawClipboardText
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result

__all__ = ["Win32ClipboardReadbackPort"]

_ERROR: Final[str] = "capabilities.windows.clipboard_native.read_failed"
_UNAVAILABLE: Final[str] = "capabilities.windows.clipboard_native.unavailable"
_CF_UNICODETEXT: Final[int] = 13
_MAX_BYTES: Final[int] = 4_194_306


def _error(code: str, message: str, operation: str, win32_error: int = 0) -> AgentXError:
    return AgentXError(
        code=code,
        message=message,
        category=ErrorCategory.EXECUTION if win32_error else ErrorCategory.PRECONDITION,
        retryability=Retryability.UNKNOWN if win32_error else Retryability.NON_RETRYABLE,
        details={"operation": operation, "win32_error": win32_error},
    )


class Win32ClipboardReadbackPort:
    """One-shot CF_UNICODETEXT reader; no polling, hooks, history, or listeners."""

    __slots__ = ()

    @property
    def source(self) -> str:
        return "win32.GetClipboardData(CF_UNICODETEXT)"

    def read_text(self) -> Result[RawClipboardText, AgentXError]:
        if sys.platform != "win32":
            return Result.failure(
                _error(
                    _UNAVAILABLE,
                    "Windows clipboard observation is unavailable on this platform",
                    "read_text",
                )
            )
        return _read_text_windows()


def _read_text_windows() -> Result[RawClipboardText, AgentXError]:
    import ctypes
    from ctypes import wintypes

    ctypes_win: Any = ctypes
    user32 = ctypes_win.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes_win.WinDLL("kernel32", use_last_error=True)

    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.IsClipboardFormatAvailable.argtypes = (wintypes.UINT,)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = (wintypes.UINT,)
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = ()
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = (wintypes.HANDLE,)
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalUnlock.argtypes = (wintypes.HANDLE,)
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalSize.argtypes = (wintypes.HANDLE,)

    if not user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
        return Result.success(RawClipboardText(text=None))

    ctypes_win.set_last_error(0)
    if not user32.OpenClipboard(None):
        code = int(ctypes_win.get_last_error())
        return Result.failure(
            _error(_ERROR, f"OpenClipboard failed with Win32 error {code}", "OpenClipboard", code)
        )
    try:
        ctypes_win.set_last_error(0)
        handle = user32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            code = int(ctypes_win.get_last_error())
            return Result.failure(
                _error(
                    _ERROR,
                    f"GetClipboardData failed with Win32 error {code}",
                    "GetClipboardData",
                    code,
                )
            )
        size = int(kernel32.GlobalSize(handle))
        if size <= 0 or size > _MAX_BYTES:
            return Result.failure(
                AgentXError(
                    code=_ERROR,
                    message="clipboard Unicode text allocation has an invalid or excessive size",
                    category=ErrorCategory.RESOURCE,
                    retryability=Retryability.NON_RETRYABLE,
                    details={"operation": "GlobalSize", "size_bytes": size},
                )
            )
        locked = kernel32.GlobalLock(handle)
        if not locked:
            code = int(ctypes_win.get_last_error())
            return Result.failure(
                _error(_ERROR, f"GlobalLock failed with Win32 error {code}", "GlobalLock", code)
            )
        try:
            maximum_chars = size // 2
            value = ctypes.wstring_at(locked, maximum_chars)
            text = value.split("\x00", 1)[0]
            return Result.success(RawClipboardText(text=text))
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()
