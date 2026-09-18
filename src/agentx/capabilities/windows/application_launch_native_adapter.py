"""Concrete binding from application-launch V2 to the canonical native mutation seam."""

from __future__ import annotations

from agentx.capabilities.windows.application_launch_v2 import NativeLaunchResult
from agentx.capabilities.windows.native_mutation import (
    NativeMutationSurface,
    NativeProcessLaunchRequest,
)

__all__ = ["CanonicalNativeApplicationLaunchAdapter"]


class CanonicalNativeApplicationLaunchAdapter:
    """Translate the structured launch port to the canonical native process seam."""

    __slots__ = ("_surface",)

    def __init__(self, surface: NativeMutationSurface) -> None:
        if not isinstance(surface, NativeMutationSurface):
            raise TypeError("surface must implement NativeMutationSurface")
        self._surface = surface

    def launch(
        self,
        executable: str,
        argv: tuple[str, ...],
        working_directory: str | None = None,
    ) -> NativeLaunchResult:
        try:
            request = NativeProcessLaunchRequest(
                executable_path=executable,
                argv=argv,
                working_directory=working_directory,
            )
        except (TypeError, ValueError) as exc:
            return NativeLaunchResult(
                launched=False,
                detail=f"native launch request rejected: {type(exc).__name__}",
            )
        result = self._surface.launch_process(request)
        if result.is_failure:
            error = result.unwrap_error()
            return NativeLaunchResult(
                launched=False,
                detail=f"native launch failed ({error.code})",
            )
        outcome = result.unwrap()
        if not outcome.process_handle_closed or not outcome.thread_handle_closed:
            return NativeLaunchResult(
                launched=False,
                process_id=outcome.process_id,
                detail="process started but native handle cleanup was incomplete",
            )
        return NativeLaunchResult(
            launched=True,
            process_id=outcome.process_id,
            detail="native process launch succeeded and owned handles were closed",
        )
