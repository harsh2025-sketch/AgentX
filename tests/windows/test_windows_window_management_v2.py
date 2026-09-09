"""Windows-headed checks for the N2.20 window-management contract.

N2.20 ships no native implementation — the native port is injected — so
these tests prove platform behavior without calling any native API. On a
real Windows host the provider verdict is supported and the governed
contract executes against a fake port exactly as on any other host; off
Windows the suite pins the unsupported branch and the import-safety probe
still applies.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from agentx.capabilities.windows.provider import (
    PlatformFacts,
    WindowsSupport,
    detect_platform_facts,
    evaluate_windows_support,
)
from agentx.capabilities.windows.window_management_v2 import (
    NativeWindowManagementPort,
    NativeWindowMutationReceipt,
    WindowManagementCapability,
    WindowManagementOperation,
    WindowTarget,
    window_management_request,
)
from agentx.core.errors import AgentXError
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result

_REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeWindowPort(NativeWindowManagementPort):
    """Deterministic recording fake of the injected N2.20 native port."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def _accept(
        self, operation: str, handle: int
    ) -> Result[NativeWindowMutationReceipt, AgentXError]:
        self.calls.append((operation, handle))
        return Result.success(
            NativeWindowMutationReceipt(
                handle=handle,
                operation=operation,
                native_accepted=True,
                native_error_code=None,
            )
        )

    def activate_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        return self._accept("activate", handle)

    def minimize_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        return self._accept("minimize", handle)

    def maximize_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        return self._accept("maximize", handle)

    def restore_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        return self._accept("restore", handle)

    def move_resize_window(
        self, handle: int, x: int, y: int, width: int, height: int
    ) -> Result[NativeWindowMutationReceipt, AgentXError]:
        del x, y, width, height
        return self._accept("move_resize", handle)


def _is_windows_runtime() -> bool:
    return sys.platform == "win32"


def _context() -> ExecutionContext:
    return ExecutionContext(correlation_id=uuid4(), cancellation_token=CancellationSource().token)


def test_importing_n2_20_loads_nothing_native() -> None:
    """Import safety on every host: importing the contract loads no native module."""
    probe = (
        "import sys\n"
        "import agentx.capabilities.abi  # noqa: F401\n"
        "import agentx.core.errors  # noqa: F401\n"
        "import agentx.core.result  # noqa: F401\n"
        "before = set(sys.modules)\n"
        "import agentx.capabilities.windows.window_management_v2  # noqa: F401\n"
        "added = set(sys.modules) - before\n"
        "forbidden = {'ctypes', 'win32api', 'win32gui', 'win32con', 'comtypes',\n"
        "             'pywinauto', 'uiautomation', 'winreg', 'pythoncom',\n"
        "             'win32com', 'msvcrt', 'subprocess'}\n"
        "roots = {name.split('.')[0] for name in added}\n"
        "assert not (roots & forbidden), sorted(roots & forbidden)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_unsupported_host_refuses_without_touching_the_port() -> None:
    """The off-Windows branch is explicit on every host, including Windows CI."""
    linux = evaluate_windows_support(
        PlatformFacts(system="Linux", release="6.8", version="#1 SMP", machine="x86_64")
    )
    assert linux.is_supported is False
    port = FakeWindowPort()
    capability = WindowManagementCapability(
        operation=WindowManagementOperation.MINIMIZE,
        support=linux,
        native_port=port,
    )

    result = capability.execute(
        window_management_request(WindowManagementOperation.MINIMIZE, WindowTarget(handle=4242)),
        _context(),
    )

    assert result.succeeded is False
    assert "capabilities.windows.unsupported_platform" in str(result.observation.to_dict())
    assert port.calls == []


@pytest.mark.skipif(not _is_windows_runtime(), reason="real Windows host behavior")
def test_real_windows_host_reports_supported() -> None:
    facts = detect_platform_facts()
    support: WindowsSupport = evaluate_windows_support(facts)

    assert facts.system == "Windows"
    assert support.is_supported is True


@pytest.mark.skipif(not _is_windows_runtime(), reason="real Windows host behavior")
def test_supported_windows_host_executes_the_fake_port_identically() -> None:
    """On Windows the governed contract behaves exactly as elsewhere.

    The port is still a fake: N2.20 binds no native seam, so this test
    proves host-verdict wiring — not a native call — on the Windows runner.
    """
    support = evaluate_windows_support(detect_platform_facts())
    assert support.is_supported is True
    port = FakeWindowPort()
    capability = WindowManagementCapability(
        operation=WindowManagementOperation.ACTIVATE,
        support=support,
        native_port=port,
    )
    request = window_management_request(
        WindowManagementOperation.ACTIVATE, WindowTarget(handle=4242)
    )

    execution = capability.execute(request, _context())

    assert execution.succeeded is True
    assert port.calls == [("activate", 4242)]
    assert capability.verify(request, execution.observation, _context()).passed is False
