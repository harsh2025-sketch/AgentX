"""M7.04 contract and deterministic native ABI/handle tests; no process launches."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from typing import Any
from uuid import uuid4

import pytest

from agentx.capabilities.abi import CapabilityRequest, CapabilityValidationError, RollbackSupport
from agentx.capabilities.windows import application_launch as launch
from agentx.capabilities.windows.provider import PlatformFacts, evaluate_windows_support
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskLevel

EXE = r"C:\Program Files\Example\Example.exe"


class FakeLaunchSurface:
    """Shared test seam lives in an owned test file, not a new support module."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.params: launch.WindowsApplicationLaunchParams | None = None
        self.instance = launch._ProcessInstance(42, 123456789)
        self.state = launch._ProcessState(self.instance, EXE, True)
        self.create_error: AgentXError | None = None
        self.inspect_error: AgentXError | None = None
        self.after_create: Callable[[], object] = lambda: None
        self.after_inspect: Callable[[], object] = lambda: None

    def create(
        self,
        params: launch.WindowsApplicationLaunchParams,
    ) -> Result[launch._ProcessInstance, AgentXError]:
        self.calls.append("create")
        self.params = params
        self.after_create()
        if self.create_error is not None:
            return Result.failure(self.create_error)
        return Result.success(self.instance)

    def inspect(self, process_id: int) -> Result[launch._ProcessState, AgentXError]:
        assert process_id == self.instance.process_id
        self.calls.append("inspect")
        self.after_inspect()
        if self.inspect_error is not None:
            return Result.failure(self.inspect_error)
        return Result.success(self.state)


def context() -> ExecutionContext:
    return ExecutionContext(correlation_id=uuid4(), cancellation_token=CancellationSource().token)


def capability(
    fake: FakeLaunchSurface,
    *,
    system: str = "Windows",
) -> launch.WindowsApplicationLaunchCapability:
    return launch.WindowsApplicationLaunchCapability(
        evaluate_windows_support(PlatformFacts(system, "test", "test", "AMD64")),
        native_surface=fake,
    )


def request() -> CapabilityRequest[launch.WindowsApplicationLaunchParams]:
    return launch.launch_application_request(launch.WindowsApplicationLaunchParams(EXE))


def test_valid_unicode_argv_and_defensive_list_copy() -> None:
    arguments = ["--open", "日本語 😀", "", r"C:\some folder\file.txt"]
    params = launch.WindowsApplicationLaunchParams(EXE, arguments, r"C:\Data")
    arguments.append("not retained")
    assert params.arguments == ("--open", "日本語 😀", "", r"C:\some folder\file.txt")
    data = params.to_dict()
    data["arguments"] = []
    assert len(params.arguments) == 4
    fake = FakeLaunchSurface()
    result = capability(fake).execute(launch.launch_application_request(params), context())
    assert result.succeeded and fake.params == params
    assert "arguments" not in result.observation.data
    assert "日本語" not in str(result.observation.to_dict())


@pytest.mark.parametrize(
    "path",
    [
        "",
        "app.exe",
        r"C:app.exe",
        r"\app.exe",
        "/tmp/app.exe",
        "C:\\a\x00.exe",
        "C:\\" + "a" * 260 + ".exe",
        r"C:\x.cmd",
        r"C:\x.bat",
        r"C:\x.exe:stream",
        r"\\server\share\app.exe",
        r"\\?\C:\app.exe",
        r"\\.\C:\app.exe",
        r"C:\..\app.exe",
        r"C:\.\app.exe",
        r"C:\folder.\app.exe",
        r"C:\NUL.exe",
        r"C:\COM1\app.exe",
        r"C:\app.exe ",
        r"C:/app.exe",
        "C:\\bad\ud800.exe",
    ],
)
def test_invalid_executable_paths(path: str) -> None:
    with pytest.raises(CapabilityValidationError, match="invalid_executable_path"):
        launch.WindowsApplicationLaunchParams(path)


@pytest.mark.parametrize(
    "arguments",
    [
        "--one shell string",
        ("a",) * 129,
        ("x" * 4097,),
        ("😀" * 2049,),
        ("\x00",),
        ("\ud800",),
        (1,),
        {"a": "b"},
        ("x" * 4096,) * 8,
    ],
)
def test_invalid_arguments(arguments: Any) -> None:
    with pytest.raises(CapabilityValidationError, match="invalid_arguments"):
        launch.WindowsApplicationLaunchParams(EXE, arguments)


@pytest.mark.parametrize("directory", ["", "relative", r"C:relative", "C:\\nul", "C:\\a\x00"])
def test_invalid_working_directory(directory: str) -> None:
    with pytest.raises(CapabilityValidationError, match="invalid_working_directory"):
        launch.WindowsApplicationLaunchParams(EXE, working_directory=directory)


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        ("", '""'),
        ("a b", '"a b"'),
        ('a"b', '"a\\"b"'),
        ("a\\", '"a\\\\"'),
        ('a\\"b', '"a\\\\\\"b"'),
        ("日本語😀", '"日本語😀"'),
    ],
)
def test_windows_argv_encoding(argument: str, expected: str) -> None:
    assert launch._quote_argument(argument) == expected


def test_descriptor_has_fixed_permission_risk_and_estimate() -> None:
    cap = capability(FakeLaunchSurface())
    descriptor = cap.descriptor
    assert descriptor.required_permissions == frozenset({Permission.EXECUTE})
    assert descriptor.risk_assessment.effective_level is RiskLevel.R2
    assert descriptor.estimate.machine_actions == 1
    assert descriptor.estimate.wall_clock.total_seconds() == 1
    assert descriptor.estimate.external_cost == 0
    assert descriptor.rollback.support is RollbackSupport.UNSUPPORTED
    assert cap._receipt_key.hex() not in repr(cap)
    assert repr(cap._receipt_key) not in repr(cap)
    with pytest.raises(FrozenInstanceError):
        cap._support = evaluate_windows_support(PlatformFacts("Linux", "", "", ""))  # type: ignore[misc]


def test_creation_is_not_verification() -> None:
    fake = FakeLaunchSurface()
    cap, ctx, req = capability(fake), context(), request()
    result = cap.execute(req, ctx)
    assert result.succeeded and fake.calls == ["create"]
    assert result.observation.data["process_id"] == 42
    assert "verified" not in result.observation.data
    assert cap.verify(req, result.observation, ctx).passed
    assert fake.calls == ["create", "inspect"]


@pytest.mark.parametrize("mismatch", ["pid", "time", "path", "exit", "unknown_path"])
def test_independent_verification_mismatch(mismatch: str) -> None:
    fake = FakeLaunchSurface()
    cap, ctx, req = capability(fake), context(), request()
    result = cap.execute(req, ctx)
    if mismatch == "pid":
        fake.state = replace(fake.state, instance=launch._ProcessInstance(43, 123456789))
    elif mismatch == "time":
        fake.state = replace(fake.state, instance=launch._ProcessInstance(42, 987654321))
    elif mismatch == "path":
        fake.state = replace(fake.state, executable=r"C:\Other\Example.exe")
    elif mismatch == "unknown_path":
        fake.state = replace(fake.state, executable="")
    else:
        fake.state = replace(fake.state, running=False)
    assert not cap.verify(req, result.observation, ctx).passed


def test_case_insensitive_image_identity() -> None:
    fake = FakeLaunchSurface()
    fake.state = replace(fake.state, executable=EXE.upper())
    cap, ctx, req = capability(fake), context(), request()
    assert cap.verify(req, cap.execute(req, ctx).observation, ctx).passed


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
def test_unsupported_platform_never_calls_native(system: str) -> None:
    fake = FakeLaunchSurface()
    cap = capability(fake, system=system)
    result = cap.execute(request(), context())
    assert not result.succeeded
    assert "capabilities.windows.unsupported_platform" in str(result.observation.data)
    assert not cap.verify(request(), result.observation, context()).passed
    assert fake.calls == []


def test_real_adapter_checks_actual_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launch, "is_native_surface_available", lambda: False)
    monkeypatch.setattr(launch, "detect_platform_facts", lambda: PlatformFacts("Linux", "", "", ""))
    api = launch._Win32LaunchSurface()
    assert api.create(request().params).unwrap_error().code.endswith("unsupported_platform")
    assert api.inspect(42).unwrap_error().code.endswith("unsupported_platform")


def test_native_failures_are_explicit() -> None:
    fake = FakeLaunchSurface()
    fake.create_error = launch._error("access_denied", ErrorCategory.PERMISSION)
    result = capability(fake).execute(request(), context())
    assert not result.succeeded and "access_denied" in str(result.observation.data)
    fake.create_error = None
    fake.inspect_error = launch._error("verification_failed", ErrorCategory.VERIFICATION)
    cap, ctx, req = capability(fake), context(), request()
    assert not cap.verify(req, cap.execute(req, ctx).observation, ctx).passed


def test_cooperative_stop_at_capability_boundary() -> None:
    fake, source = FakeLaunchSurface(), CancellationSource()
    ctx = ExecutionContext(correlation_id=uuid4(), cancellation_token=source.token)
    source.request_cancellation()
    assert not capability(fake).execute(request(), ctx).succeeded
    assert fake.calls == []


class NativeFunction:
    def __init__(self, function: Callable[..., Any]) -> None:
        self.function = function
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> Any:
        return self.function(*args)


class FakeKernel32:
    """Exercise production structure, ABI wiring and finally blocks on any host."""

    def __init__(self) -> None:
        self.closed: list[int] = []
        self.created: list[tuple[Any, ...]] = []
        self.opened: list[tuple[int, bool, int]] = []
        self.error = 5
        self.fail: str | None = None
        self.waits: list[int] = [258, 258]
        self.CreateProcessW = NativeFunction(self.create)
        self.OpenProcess = NativeFunction(self.open)
        self.GetProcessTimes = NativeFunction(self.times)
        self.QueryFullProcessImageNameW = NativeFunction(self.image)
        self.WaitForSingleObject = NativeFunction(lambda *_: self.waits.pop(0))
        self.CloseHandle = NativeFunction(self.close)

    def create(self, *args: Any) -> bool:
        self.created.append(args)
        if self.fail == "create":
            return False
        info = args[-1]._obj
        info.hProcess, info.hThread = 100, 101
        info.dwProcessId, info.dwThreadId = 42, 43
        return True

    def open(self, rights: int, inherit: bool, pid: int) -> int:
        self.opened.append((rights, inherit, pid))
        return 0 if self.fail == "open" else 200

    def times(self, handle: int, created: Any, *args: Any) -> bool:
        if self.fail == "times":
            return False
        if self.fail == "memory":
            raise MemoryError
        created._obj.dwHighDateTime = 0
        created._obj.dwLowDateTime = 123456789
        return True

    def image(self, handle: int, flags: int, buffer: Any, size: Any) -> bool:
        buffer.value = EXE
        return self.fail != "image"

    def close(self, handle: int) -> bool:
        self.closed.append(handle)
        return self.fail != "close"


def install_kernel(monkeypatch: pytest.MonkeyPatch) -> FakeKernel32:
    kernel = FakeKernel32()
    monkeypatch.setattr(launch, "is_native_surface_available", lambda: True)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: kernel.error, raising=False)
    return kernel


def test_native_abi_creates_explicit_image_and_closes_all_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = install_kernel(monkeypatch)
    native = launch._Win32LaunchSurface()
    params = launch.WindowsApplicationLaunchParams(EXE, ("--open", "日本語😀"))
    instance = native.create(params).unwrap()
    assert kernel.closed == [101, 100]
    args = kernel.created[0]
    assert args[0] == EXE
    assert args[1].value == launch._command_line(params)
    assert args[2:7] == (None, None, False, 0, None)
    assert args[7] == r"C:\Program Files\Example"
    assert args[8]._obj.cb == ctypes.sizeof(args[8]._obj)
    assert kernel.CreateProcessW.argtypes is not None
    state = native.inspect(instance.process_id).unwrap()
    assert state == launch._ProcessState(instance, EXE, True)
    assert kernel.closed == [101, 100, 200]
    assert kernel.opened == [(0x101000, False, 42)]


def test_native_explicit_working_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = install_kernel(monkeypatch)
    assert (
        launch._Win32LaunchSurface()
        .create(launch.WindowsApplicationLaunchParams(EXE, working_directory="C:\\"))
        .is_success
    )
    assert kernel.created[0][7] == "C:\\"


@pytest.mark.parametrize(
    ("failure", "closed"),
    [
        ("create", []),
        ("times", [101, 100]),
        ("memory", [101, 100]),
        ("close", [101, 100]),
    ],
)
def test_creation_cleanup_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    closed: list[int],
) -> None:
    kernel = install_kernel(monkeypatch)
    kernel.fail = failure
    assert launch._Win32LaunchSurface().create(request().params).is_failure
    assert kernel.closed == closed


@pytest.mark.parametrize(
    ("failure", "closed"),
    [
        ("open", []),
        ("times", [200]),
        ("image", [200]),
        ("memory", [200]),
        ("close", [200]),
    ],
)
def test_inspection_cleanup_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    closed: list[int],
) -> None:
    kernel = install_kernel(monkeypatch)
    kernel.fail = failure
    assert launch._Win32LaunchSurface().inspect(42).is_failure
    assert kernel.closed == closed


@pytest.mark.parametrize("waits", [[0, 0], [258, 0], [0, 258], [0xFFFFFFFF]])
def test_native_exit_and_wait_failure(monkeypatch: pytest.MonkeyPatch, waits: list[int]) -> None:
    kernel = install_kernel(monkeypatch)
    kernel.waits = waits
    result = launch._Win32LaunchSurface().inspect(42)
    assert result.is_failure or not result.unwrap().running
    assert kernel.closed == [200]


@pytest.mark.parametrize(
    ("number", "code"),
    [
        (2, "missing_executable"),
        (3, "missing_executable"),
        (5, "access_denied"),
        (8, "resource_failure"),
        (14, "resource_failure"),
        (1450, "resource_failure"),
        (267, "invalid_working_directory"),
        (193, "native_creation_failed"),
    ],
)
def test_native_error_mapping(monkeypatch: pytest.MonkeyPatch, number: int, code: str) -> None:
    kernel = install_kernel(monkeypatch)
    kernel.fail, kernel.error = "create", number
    error = launch._Win32LaunchSurface().create(request().params).unwrap_error()
    assert error.code.endswith(code)
    assert error.details == {"win32_error": number}
    assert EXE not in str(error.to_dict())
