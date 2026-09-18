"""Read-only Win32 virtual-desktop capture seam for M10 perception.

Importing this module performs no native work. ctypes and Win32 libraries are
loaded only inside capture_screen_raw(). Captured pixels and display metadata
are untrusted observation data and grant no authority.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result

__all__ = [
    "SCREEN_CAPTURE_FAILED_ERROR_CODE",
    "SCREEN_NATIVE_UNAVAILABLE_ERROR_CODE",
    "RawMonitorObservation",
    "RawScreenFrame",
    "capture_screen_raw",
    "is_screen_surface_available",
]

SCREEN_NATIVE_UNAVAILABLE_ERROR_CODE: Final[str] = (
    "capabilities.windows.screen_capture.native_unavailable"
)
SCREEN_CAPTURE_FAILED_ERROR_CODE: Final[str] = "capabilities.windows.screen_capture.capture_failed"

_WIN32_PLATFORM: Final[str] = "win32"
_SM_XVIRTUALSCREEN: Final[int] = 76
_SM_YVIRTUALSCREEN: Final[int] = 77
_SM_CXVIRTUALSCREEN: Final[int] = 78
_SM_CYVIRTUALSCREEN: Final[int] = 79
_MONITORINFOF_PRIMARY: Final[int] = 1
_MDT_EFFECTIVE_DPI: Final[int] = 0
_SRCCOPY: Final[int] = 0x00CC0020
_CAPTUREBLT: Final[int] = 0x40000000
_DIB_RGB_COLORS: Final[int] = 0
_BI_RGB: Final[int] = 0
_MAX_MONITORS: Final[int] = 32
_MAX_DEVICE_NAME_CHARS: Final[int] = 32


@dataclass(frozen=True, slots=True)
class RawMonitorObservation:
    """One monitor geometry/DPI observation from the native display surface."""

    device_name: str
    left: int
    top: int
    right: int
    bottom: int
    work_left: int
    work_top: int
    work_right: int
    work_bottom: int
    dpi_x: int
    dpi_y: int
    primary: bool


@dataclass(frozen=True, slots=True)
class RawScreenFrame:
    """One virtual-desktop BGRA frame plus the display topology seen with it."""

    captured_at: datetime
    left: int
    top: int
    width: int
    height: int
    row_stride: int
    pixels_bgra: bytes
    monitors: tuple[RawMonitorObservation, ...]


def is_screen_surface_available() -> bool:
    """Report whether the native screen-observation seam can run on this host."""
    return sys.platform == _WIN32_PLATFORM


def _capture_error(
    message: str,
    *,
    operation: str,
    win32_error: int | None = None,
    category: ErrorCategory = ErrorCategory.EXECUTION,
    retryability: Retryability = Retryability.UNKNOWN,
) -> AgentXError:
    details: dict[str, object] = {"operation": operation}
    if win32_error is not None:
        details["win32_error"] = win32_error
    return AgentXError(
        code=SCREEN_CAPTURE_FAILED_ERROR_CODE,
        message=message,
        category=category,
        retryability=retryability,
        details=details,
    )


def _unavailable_error() -> AgentXError:
    return AgentXError(
        code=SCREEN_NATIVE_UNAVAILABLE_ERROR_CODE,
        message=f"Windows screen observation is unavailable on platform {sys.platform!r}",
        category=ErrorCategory.PRECONDITION,
        retryability=Retryability.NON_RETRYABLE,
        details={"sys_platform": sys.platform},
    )


def capture_screen_raw(*, max_pixels: int) -> Result[RawScreenFrame, AgentXError]:
    """Capture one bounded read-only virtual-desktop frame and display topology."""
    if type(max_pixels) is not int or max_pixels < 1:
        raise ValueError("max_pixels must be a positive int")
    if not is_screen_surface_available():
        return Result.failure(_unavailable_error())

    import ctypes
    from ctypes import wintypes

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * _MAX_DEVICE_NAME_CHARS),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    class RGBQUAD(ctypes.Structure):
        _fields_ = [
            ("rgbBlue", wintypes.BYTE),
            ("rgbGreen", wintypes.BYTE),
            ("rgbRed", wintypes.BYTE),
            ("rgbReserved", wintypes.BYTE),
        ]

    class BITMAPINFO(ctypes.Structure):
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]

    user32: Any = ctypes.WinDLL("user32", use_last_error=True)
    gdi32: Any = ctypes.WinDLL("gdi32", use_last_error=True)

    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
    left = int(user32.GetSystemMetrics(_SM_XVIRTUALSCREEN))
    top = int(user32.GetSystemMetrics(_SM_YVIRTUALSCREEN))
    width = int(user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN))
    height = int(user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN))
    if width <= 0 or height <= 0:
        return Result.failure(
            _capture_error("virtual desktop has invalid dimensions", operation="GetSystemMetrics")
        )
    if width * height > max_pixels:
        return Result.failure(
            _capture_error(
                "virtual desktop exceeds configured capture pixel bound",
                operation="capture_bounds",
                category=ErrorCategory.RESOURCE,
                retryability=Retryability.NON_RETRYABLE,
            )
        )

    def _system_dpi() -> tuple[int, int]:
        try:
            user32.GetDpiForSystem.restype = wintypes.UINT
            user32.GetDpiForSystem.argtypes = ()
            value = int(user32.GetDpiForSystem())
        except AttributeError:
            value = 96
        return (value, value) if value > 0 else (96, 96)

    default_dpi = _system_dpi()
    shcore: Any | None
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.GetDpiForMonitor.restype = ctypes.c_long
        shcore.GetDpiForMonitor.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
        )
    except OSError:
        shcore = None

    monitors: list[RawMonitorObservation] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HANDLE,
        wintypes.HDC,
        ctypes.POINTER(RECT),
        wintypes.LPARAM,
    )

    @callback_type
    def _monitor_callback(
        monitor: object,
        _device_context: object,
        _monitor_rect: object,
        _data: object,
    ) -> bool:
        if len(monitors) >= _MAX_MONITORS:
            return False
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return True
        dpi_x, dpi_y = default_dpi
        if shcore is not None:
            raw_x = wintypes.UINT()
            raw_y = wintypes.UINT()
            hresult = int(
                shcore.GetDpiForMonitor(
                    monitor,
                    _MDT_EFFECTIVE_DPI,
                    ctypes.byref(raw_x),
                    ctypes.byref(raw_y),
                )
            )
            if hresult == 0 and raw_x.value > 0 and raw_y.value > 0:
                dpi_x, dpi_y = int(raw_x.value), int(raw_y.value)
        monitors.append(
            RawMonitorObservation(
                device_name=str(info.szDevice),
                left=int(info.rcMonitor.left),
                top=int(info.rcMonitor.top),
                right=int(info.rcMonitor.right),
                bottom=int(info.rcMonitor.bottom),
                work_left=int(info.rcWork.left),
                work_top=int(info.rcWork.top),
                work_right=int(info.rcWork.right),
                work_bottom=int(info.rcWork.bottom),
                dpi_x=dpi_x,
                dpi_y=dpi_y,
                primary=bool(info.dwFlags & _MONITORINFOF_PRIMARY),
            )
        )
        return True

    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = (
        wintypes.HDC,
        ctypes.POINTER(RECT),
        callback_type,
        wintypes.LPARAM,
    )
    if not user32.EnumDisplayMonitors(None, None, _monitor_callback, 0) and not monitors:
        return Result.failure(
            _capture_error(
                "display monitor enumeration failed",
                operation="EnumDisplayMonitors",
                win32_error=int(ctypes.get_last_error()),
            )
        )
    if not monitors:
        monitors.append(
            RawMonitorObservation(
                device_name="virtual-desktop",
                left=left,
                top=top,
                right=left + width,
                bottom=top + height,
                work_left=left,
                work_top=top,
                work_right=left + width,
                work_bottom=top + height,
                dpi_x=default_dpi[0],
                dpi_y=default_dpi[1],
                primary=True,
            )
        )

    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = (wintypes.HWND,)
    user32.ReleaseDC.restype = ctypes.c_int
    user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = (wintypes.HDC, ctypes.c_int, ctypes.c_int)
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
    gdi32.BitBlt.restype = wintypes.BOOL
    gdi32.BitBlt.argtypes = (
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    )
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.GetDIBits.argtypes = (
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
    )
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = (wintypes.HDC,)

    screen_dc = user32.GetDC(None)
    if not screen_dc:
        return Result.failure(
            _capture_error(
                "GetDC failed for virtual desktop",
                operation="GetDC",
                win32_error=int(ctypes.get_last_error()),
            )
        )
    memory_dc = gdi32.CreateCompatibleDC(screen_dc)
    bitmap: object | None = None
    old_object: object | None = None
    try:
        if not memory_dc:
            return Result.failure(
                _capture_error(
                    "CreateCompatibleDC failed",
                    operation="CreateCompatibleDC",
                    win32_error=int(ctypes.get_last_error()),
                )
            )
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not bitmap:
            return Result.failure(
                _capture_error(
                    "CreateCompatibleBitmap failed",
                    operation="CreateCompatibleBitmap",
                    win32_error=int(ctypes.get_last_error()),
                )
            )
        old_object = gdi32.SelectObject(memory_dc, bitmap)
        if not old_object:
            return Result.failure(
                _capture_error(
                    "SelectObject failed",
                    operation="SelectObject",
                    win32_error=int(ctypes.get_last_error()),
                )
            )
        if not gdi32.BitBlt(
            memory_dc,
            0,
            0,
            width,
            height,
            screen_dc,
            left,
            top,
            _SRCCOPY | _CAPTUREBLT,
        ):
            return Result.failure(
                _capture_error(
                    "BitBlt failed while observing the virtual desktop",
                    operation="BitBlt",
                    win32_error=int(ctypes.get_last_error()),
                )
            )
        row_stride = width * 4
        byte_count = row_stride * height
        buffer = (ctypes.c_ubyte * byte_count)()
        bitmap_info = BITMAPINFO()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bitmap_info.bmiHeader.biWidth = width
        bitmap_info.bmiHeader.biHeight = -height
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = _BI_RGB
        lines = int(
            gdi32.GetDIBits(
                memory_dc,
                bitmap,
                0,
                height,
                buffer,
                ctypes.byref(bitmap_info),
                _DIB_RGB_COLORS,
            )
        )
        if lines != height:
            return Result.failure(
                _capture_error(
                    "GetDIBits returned an incomplete frame",
                    operation="GetDIBits",
                    win32_error=int(ctypes.get_last_error()),
                )
            )
        return Result.success(
            RawScreenFrame(
                captured_at=datetime.now(UTC),
                left=left,
                top=top,
                width=width,
                height=height,
                row_stride=row_stride,
                pixels_bgra=bytes(buffer),
                monitors=tuple(
                    sorted(
                        monitors,
                        key=lambda item: (
                            0 if item.primary else 1,
                            item.left,
                            item.top,
                            item.device_name.casefold(),
                        ),
                    )
                ),
            )
        )
    finally:
        if old_object is not None and memory_dc:
            gdi32.SelectObject(memory_dc, old_object)
        if bitmap is not None:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)
