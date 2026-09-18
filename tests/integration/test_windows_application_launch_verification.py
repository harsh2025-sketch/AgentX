"""Independent application-launch verification regressions."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.windows.application_launch_v2 import (
    APPLICATION_LAUNCH_V2_IDENTITY,
    ApplicationLaunchParams,
    NativeLaunchResult,
    WindowsApplicationLaunchV2Capability,
)
from agentx.capabilities.windows.process_discovery import (
    MetadataStatus,
    WindowsProcessIdentity,
    WindowsProcessSnapshot,
)
from agentx.core.errors import AgentXError
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.ids import TaskId
from agentx.core.result import Result


class Launch:
    def launch(
        self,
        executable: str,
        argv: tuple[str, ...],
        working_directory: str | None = None,
    ) -> NativeLaunchResult:
        return NativeLaunchResult(True, 4242)


@dataclass
class Observer:
    snapshot: WindowsProcessSnapshot

    def discover(self) -> Result[WindowsProcessSnapshot, AgentXError]:
        return Result.success(self.snapshot)


def _context() -> ExecutionContext:
    task_id = TaskId.create()
    return ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=task_id,
    )


def _snapshot(name: str) -> WindowsProcessSnapshot:
    return WindowsProcessSnapshot(
        processes=(
            WindowsProcessIdentity(
                process_id=4242,
                parent_process_id=1,
                executable_name=name,
                executable_name_status=MetadataStatus.AVAILABLE,
                executable_path="C:\\Windows\\System32\\" + name,
                executable_path_status=MetadataStatus.AVAILABLE,
                window_handles=(),
                visible_window_count=0,
            ),
        ),
        windows=(),
        dropped_invalid_entries=0,
        merged_duplicate_entries=0,
    )


def test_launch_verification_requires_independent_pid_and_executable_identity() -> None:
    capability = WindowsApplicationLaunchV2Capability(
        Launch(), process_observer=Observer(_snapshot("notepad.exe"))
    )
    request = CapabilityRequest(
        identity=APPLICATION_LAUNCH_V2_IDENTITY,
        params=ApplicationLaunchParams(r"C:\Windows\System32\notepad.exe", ()),
    )
    result = capability.execute(request, _context())
    assert result.succeeded is True
    assert capability.verify(request, result.observation, _context()).passed is True


def test_launch_verification_rejects_pid_reuse_with_different_executable() -> None:
    capability = WindowsApplicationLaunchV2Capability(
        Launch(), process_observer=Observer(_snapshot("calc.exe"))
    )
    request = CapabilityRequest(
        identity=APPLICATION_LAUNCH_V2_IDENTITY,
        params=ApplicationLaunchParams(r"C:\Windows\System32\notepad.exe", ()),
    )
    result = capability.execute(request, _context())
    verdict = capability.verify(request, result.observation, _context())
    assert verdict.passed is False
    assert "does not match" in verdict.detail
