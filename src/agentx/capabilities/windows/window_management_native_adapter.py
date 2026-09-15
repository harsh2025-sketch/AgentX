"""Integration-owned binding from N2.20 to the canonical N2.19 native seam.

This module adds no native implementation. It translates the governed N2.20
port into the single typed N2.19 mutation surface. Native outcomes remain
low-level execution evidence and are never verification.
"""

from __future__ import annotations

from agentx.capabilities.windows.native_mutation import (
    NativeMutationSurface,
    NativeWindowActivationRequest,
    NativeWindowMoveResizeRequest,
    NativeWindowShowState,
    NativeWindowStateRequest,
)
from agentx.capabilities.windows.window_management_v2 import NativeWindowMutationReceipt
from agentx.core.errors import AgentXError
from agentx.core.result import Result

__all__ = ["CanonicalNativeWindowManagementAdapter"]


class CanonicalNativeWindowManagementAdapter:
    """Explicit N2.20 port adapter over an injected N2.19 mutation surface."""

    __slots__ = ("_surface",)

    def __init__(self, surface: NativeMutationSurface) -> None:
        if not isinstance(surface, NativeMutationSurface):
            raise TypeError("surface must implement NativeMutationSurface")
        self._surface = surface

    def activate_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        result = self._surface.activate_window(NativeWindowActivationRequest(handle))
        return _receipt(result, handle=handle, operation="activate")

    def minimize_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        result = self._surface.set_window_state(
            NativeWindowStateRequest(handle, NativeWindowShowState.MINIMIZE)
        )
        return _receipt(result, handle=handle, operation="minimize")

    def maximize_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        result = self._surface.set_window_state(
            NativeWindowStateRequest(handle, NativeWindowShowState.MAXIMIZE)
        )
        return _receipt(result, handle=handle, operation="maximize")

    def restore_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        result = self._surface.set_window_state(
            NativeWindowStateRequest(handle, NativeWindowShowState.RESTORE)
        )
        return _receipt(result, handle=handle, operation="restore")

    def move_resize_window(
        self, handle: int, x: int, y: int, width: int, height: int
    ) -> Result[NativeWindowMutationReceipt, AgentXError]:
        result = self._surface.move_resize_window(
            NativeWindowMoveResizeRequest(handle, x, y, width, height)
        )
        return _receipt(result, handle=handle, operation="move_resize")


def _receipt[NativeOutcomeT](
    result: Result[NativeOutcomeT, AgentXError],
    *,
    handle: int,
    operation: str,
) -> Result[NativeWindowMutationReceipt, AgentXError]:
    if result.is_failure:
        return Result.failure(result.unwrap_error())
    return Result.success(
        NativeWindowMutationReceipt(
            handle=handle,
            operation=operation,
            native_accepted=True,
            native_error_code=None,
        )
    )
