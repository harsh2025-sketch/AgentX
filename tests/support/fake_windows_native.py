"""Deterministic fake of the A5.02 read-only Windows native surface.

This fixture performs no OS read of any kind: it returns preloaded raw
entries and records every seam method call so tests can prove that discovery
invokes exactly the three read operations and nothing else. It exists so
every A5.02 behaviour is testable deterministically on Windows, Linux and
macOS alike, with hostile/duplicated/vanished metadata that would be
unreliable to reproduce against a real desktop.

It is deliberately *not* part of the ``agentx`` package: A5.02 ships the
discovery boundary, not test doubles.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentx.capabilities.windows import _native
from agentx.capabilities.windows.process_discovery import WindowsProcessDiscovery
from agentx.capabilities.windows.provider import (
    PlatformFacts,
    WindowsSupport,
    evaluate_windows_support,
)
from agentx.core.errors import AgentXError
from agentx.core.result import Result

WINDOWS_FACTS = PlatformFacts(
    system="Windows",
    release="11",
    version="10.0.26100",
    machine="AMD64",
)

LINUX_FACTS = PlatformFacts(
    system="Linux",
    release="6.8.0",
    version="#1 SMP",
    machine="x86_64",
)


def windows_support() -> WindowsSupport:
    """A supported-host verdict for tests (never reads the real host)."""
    return evaluate_windows_support(WINDOWS_FACTS)


def linux_support() -> WindowsSupport:
    """An unsupported-host verdict for tests (never reads the real host)."""
    return evaluate_windows_support(LINUX_FACTS)


def raw_process(
    process_id: int,
    *,
    parent_process_id: int | None = 100,
    executable_name: str = "app.exe",
) -> _native.RawProcessEntry:
    """Build one verbatim raw process entry."""
    return _native.RawProcessEntry(
        process_id=process_id,
        parent_process_id=parent_process_id,
        executable_name=executable_name,
    )


def raw_window(
    handle: int,
    *,
    process_id: int = 100,
    title: str | None = "Window",
    class_name: str | None = "Shell_TrayWnd",
    is_visible: bool = True,
    title_error_code: int = 0,
    class_name_error_code: int = 0,
) -> _native.RawWindowEntry:
    """Build one verbatim raw window entry."""
    return _native.RawWindowEntry(
        handle=handle,
        process_id=process_id,
        title=title,
        title_error_code=title_error_code,
        class_name=class_name,
        class_name_error_code=class_name_error_code,
        is_visible=is_visible,
    )


def path_ok(value: str = "C:\\Program Files\\app\\app.exe") -> _native.RawPathQuery:
    """A successful image-path query outcome."""
    return _native.RawPathQuery(value=value, error_code=0)


def path_denied() -> _native.RawPathQuery:
    """An access-denied image-path query outcome (elevated process)."""
    return _native.RawPathQuery(value=None, error_code=_native.WIN32_ERROR_ACCESS_DENIED)


def path_vanished() -> _native.RawPathQuery:
    """A vanished-process image-path query outcome (exit race)."""
    return _native.RawPathQuery(value=None, error_code=_native.WIN32_ERROR_INVALID_PARAMETER)


READ_SEAM_METHODS = frozenset(
    {
        "enumerate_processes",
        "enumerate_windows",
        "get_foreground_window",
        "query_executable_path",
    }
)


@dataclass
class FakeWindowsNative:
    """In-memory, call-recording stand-in for the native read seam.

    ``processes``/``windows`` are returned verbatim (in whatever order the
    test loads them, so OS ordering noise can be simulated). Per-PID path
    outcomes come from ``path_queries`` with ``default_path_query`` as the
    fallback. Any of ``fail_*`` short-circuits that seam method with an
    explicit failure instead of data.
    """

    processes: list[_native.RawProcessEntry] = field(default_factory=list)
    windows: list[_native.RawWindowEntry] = field(default_factory=list)
    path_queries: dict[int, _native.RawPathQuery] = field(default_factory=dict)
    default_path_query: _native.RawPathQuery = field(default_factory=path_ok)
    foreground_handle: int | None = None
    fail_processes: AgentXError | None = None
    fail_windows: AgentXError | None = None
    fail_foreground: AgentXError | None = None
    fail_path_query: AgentXError | None = None
    calls: list[str] = field(default_factory=list)

    # -- seam contract ----------------------------------------------------

    def enumerate_processes(
        self,
    ) -> Result[tuple[_native.RawProcessEntry, ...], AgentXError]:
        self.calls.append("enumerate_processes")
        if self.fail_processes is not None:
            return Result.failure(self.fail_processes)
        return Result.success(tuple(self.processes))

    def enumerate_windows(
        self,
    ) -> Result[tuple[_native.RawWindowEntry, ...], AgentXError]:
        self.calls.append("enumerate_windows")
        if self.fail_windows is not None:
            return Result.failure(self.fail_windows)
        return Result.success(tuple(self.windows))

    def get_foreground_window(self) -> Result[int | None, AgentXError]:
        self.calls.append("get_foreground_window")
        if self.fail_foreground is not None:
            return Result.failure(self.fail_foreground)
        return Result.success(self.foreground_handle)

    def query_executable_path(self, process_id: int) -> Result[_native.RawPathQuery, AgentXError]:
        self.calls.append("query_executable_path")
        if self.fail_path_query is not None:
            return Result.failure(self.fail_path_query)
        return Result.success(self.path_queries.get(process_id, self.default_path_query))

    # -- test assertions --------------------------------------------------

    @property
    def read_method_calls(self) -> list[str]:
        """All seam calls (the only surface discovery may ever touch)."""
        return list(self.calls)

    def assert_only_read_methods_called(self) -> None:
        """Assert that discovery never invoked anything but the read seam."""
        unexpected = [call for call in self.calls if call not in READ_SEAM_METHODS]
        assert not unexpected, f"non-read seam calls: {unexpected}"


def windows_discovery(fake: FakeWindowsNative) -> WindowsProcessDiscovery:
    """Build a supported-host discovery operation over ``fake``."""
    return WindowsProcessDiscovery(windows_support(), native_surface=fake)
