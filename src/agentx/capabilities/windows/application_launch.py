"""M7.04: narrow governed Windows application startup, not shell execution.

Composition registers WindowsApplicationLaunchCapability with the canonical
registry; only CapabilityExecutionLoop is execution authority. The internal
native adapter is lazy, stdlib-only and injectable. No handles survive a call.
Verification reopens one PID and checks creation time, image path and liveness;
it does not assert application readiness. See docs/windows_application_launch.md.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import PureWindowsPath
from typing import Any, Final, Protocol

from agentx.capabilities.abi import (
    CapabilityDescriptor,
    CapabilityIdentity,
    CapabilityName,
    CapabilityObservation,
    CapabilityParams,
    CapabilityPlatform,
    CapabilityPrecondition,
    CapabilityRequest,
    CapabilityScope,
    CapabilityValidationError,
    CapabilityVersion,
    ExecutionResult,
    ResourceEstimate,
    RollbackDeclaration,
    RollbackSupport,
    VerificationResult,
)
from agentx.capabilities.windows._native import is_native_surface_available
from agentx.capabilities.windows.provider import (
    WindowsSupport,
    detect_platform_facts,
    evaluate_windows_support,
    unsupported_platform_error,
)
from agentx.core.errors import AgentXError, AgentXException, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk

__all__ = [
    "WINDOWS_APPLICATION_LAUNCH_IDENTITY",
    "WindowsApplicationLaunchCapability",
    "WindowsApplicationLaunchParams",
    "launch_application_request",
]

WINDOWS_APPLICATION_LAUNCH_IDENTITY: Final = CapabilityIdentity(
    CapabilityName("windows.application.launch_application"), CapabilityVersion(1, 0, 0)
)
_MAX_PATH: Final = 259  # Conservative ordinary Win32 paths, including the drive.
_MAX_ARGUMENTS: Final = 128
_MAX_ARGUMENT: Final = 4096
_MAX_COMMAND_LINE: Final = 32766  # UTF-16 units, excluding terminating NUL.
_FORBIDDEN_HOSTS: Final = frozenset(
    {
        "cmd.exe",
        "powershell.exe",
        "powershell_ise.exe",
        "pwsh.exe",
        "wscript.exe",
        "cscript.exe",
        "mshta.exe",
        "rundll32.exe",
        "regsvr32.exe",
        "wsl.exe",
        "bash.exe",
        "sh.exe",
    }
)
_DOS_DEVICES: Final = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"{prefix}{n}" for prefix in ("com", "lpt") for n in "123456789¹²³"}
)


def _utf16_length(value: str, code: str) -> int:
    try:
        return len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        raise CapabilityValidationError(code) from None


def _validate_path(value: object, *, executable: bool) -> str:
    code = "invalid_executable_path" if executable else "invalid_working_directory"
    if type(value) is not str:
        raise CapabilityValidationError(code)
    if (
        not value
        or len(value) > _MAX_PATH
        or _utf16_length(value, code) > _MAX_PATH
        or len(value) < 3
        or not value[0].isascii()
        or not value[0].isalpha()
        or value[1:3] != ":\\"
        or any(ord(c) < 32 or c in '/<>"|?*' for c in value)
        or ":" in value[2:]
    ):
        raise CapabilityValidationError(code)
    # Validate raw components before PureWindowsPath can normalize ambiguity.
    parts = value[3:].split("\\") if len(value) > 3 else []
    if any(
        not p
        or p in {".", ".."}
        or p.endswith((" ", "."))
        or p.split(".")[0].casefold() in _DOS_DEVICES
        for p in parts
    ):
        raise CapabilityValidationError(code)
    path = PureWindowsPath(value)
    if not path.is_absolute():
        raise CapabilityValidationError(code)
    if executable:
        if path.suffix.casefold() != ".exe":
            raise CapabilityValidationError(code)
        if path.name.casefold() in _FORBIDDEN_HOSTS:
            raise CapabilityValidationError("forbidden_shell_host")
    return value


def _quote_argument(value: str) -> str:
    """Encode one CRT-style argv element, never a shell expression.

    Windows exposes a command-line buffer, not argv. Double backslashes before
    a quote and before the closing quote. Always quote, including empty args.
    The target executable remains a separate, non-null lpApplicationName.
    """
    output = ['"']
    slashes = 0
    for char in value:
        if char == "\\":
            slashes += 1
            continue
        output.append("\\" * (slashes * 2 + 1 if char == '"' else slashes))
        output.append(char)
        slashes = 0
    output.extend(("\\" * (slashes * 2), '"'))
    return "".join(output)


def _command_line(params: WindowsApplicationLaunchParams) -> str:
    return " ".join(_quote_argument(arg) for arg in (params.executable, *params.arguments))


@dataclass(frozen=True, slots=True, init=False)
class WindowsApplicationLaunchParams(CapabilityParams):
    """Explicit local absolute .exe path, immutable argv, optional absolute cwd.

    No environment overrides, PATH lookup, shell text, elevation or launch mode.
    Lists are accepted at construction and defensively converted to tuples.
    """

    executable: str
    arguments: tuple[str, ...] = ()
    working_directory: str | None = None

    def __init__(
        self,
        executable: str,
        arguments: tuple[str, ...] | list[str] = (),
        working_directory: str | None = None,
    ) -> None:
        if type(arguments) not in (tuple, list) or len(arguments) > _MAX_ARGUMENTS:
            raise CapabilityValidationError("invalid_arguments")
        object.__setattr__(self, "executable", executable)
        object.__setattr__(self, "arguments", tuple(arguments))
        object.__setattr__(self, "working_directory", working_directory)
        self.__post_init__()

    def __post_init__(self) -> None:
        _validate_path(self.executable, executable=True)
        if type(self.arguments) not in (tuple, list) or len(self.arguments) > _MAX_ARGUMENTS:
            raise CapabilityValidationError("invalid_arguments")
        for argument in self.arguments:
            if (
                type(argument) is not str
                or "\x00" in argument
                or len(argument) > _MAX_ARGUMENT
                or _utf16_length(argument, "invalid_arguments") > _MAX_ARGUMENT
            ):
                raise CapabilityValidationError("invalid_arguments")
        object.__setattr__(self, "arguments", tuple(self.arguments))
        if self.working_directory is not None:
            _validate_path(self.working_directory, executable=False)
        if _utf16_length(_command_line(self), "invalid_arguments") > _MAX_COMMAND_LINE:
            raise CapabilityValidationError("invalid_arguments")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "executable": self.executable,
            "arguments": list(self.arguments),
            "working_directory": self.working_directory,
        }


@dataclass(frozen=True, slots=True)
class _ProcessInstance:
    process_id: int
    creation_time: int  # Windows FILETIME, guards against PID reuse.

    def __post_init__(self) -> None:
        if type(self.process_id) is not int or not 0 < self.process_id <= 0xFFFFFFFF:
            raise ValueError("invalid process identity")
        if type(self.creation_time) is not int or not 0 < self.creation_time < 1 << 64:
            raise ValueError("invalid process creation time")


@dataclass(frozen=True, slots=True)
class _ProcessState:
    instance: _ProcessInstance
    executable: str
    running: bool


class _NativeLaunchSurface(Protocol):
    """Trusted composition-time seam; requests cannot supply an implementation."""

    def create(
        self, params: WindowsApplicationLaunchParams
    ) -> Result[_ProcessInstance, AgentXError]:
        """Create one process, record its creation time, close both handles."""
        ...

    def inspect(self, process_id: int) -> Result[_ProcessState, AgentXError]:
        """Independently open/query/close one process; never create or retry."""
        ...


def _error(code: str, category: ErrorCategory, *, win32_error: int = 0) -> AgentXError:
    return AgentXError(
        code=f"capabilities.windows.application_launch.{code}",
        message=f"Windows application launch: {code}",
        category=category,
        retryability=Retryability.NON_RETRYABLE,
        details={"win32_error": win32_error},
    )


def _win32_error(operation: str, number: int) -> AgentXError:
    if number == 5:
        return _error("access_denied", ErrorCategory.PERMISSION, win32_error=number)
    if number in {8, 14, 1450, 1455, 1816} or operation == "handle_cleanup_failed":
        return _error("resource_failure", ErrorCategory.RESOURCE, win32_error=number)
    if operation == "native_creation_failed" and number in {2, 3}:
        return _error("missing_executable", ErrorCategory.NOT_FOUND, win32_error=number)
    if operation == "native_creation_failed" and number == 267:
        return _error("invalid_working_directory", ErrorCategory.VALIDATION, win32_error=number)
    return _error(
        operation,
        ErrorCategory.VERIFICATION
        if operation == "verification_failed"
        else ErrorCategory.EXECUTION,
        win32_error=number,
    )


def _api() -> tuple[Any, Any, Any]:
    """Lazy, explicitly typed Win32 ABI; never called off Windows."""
    import ctypes
    from ctypes import wintypes

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    native_ctypes: Any = ctypes
    api = native_ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateProcessW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(STARTUPINFOW),
        ctypes.POINTER(ProcessInformation),
    )
    api.CreateProcessW.restype = wintypes.BOOL
    api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    api.GetProcessTimes.restype = wintypes.BOOL
    api.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    api.QueryFullProcessImageNameW.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    return api, STARTUPINFOW, ProcessInformation


def _raise_native(operation: str) -> None:
    import ctypes

    native_ctypes: Any = ctypes
    raise AgentXException(_win32_error(operation, native_ctypes.get_last_error()))


@contextmanager
def _owned_handle(api: Any, handle: int) -> Iterator[int]:
    try:
        yield handle
    finally:
        if not api.CloseHandle(handle):
            _raise_native("handle_cleanup_failed")


def _creation_time(api: Any, handle: int) -> int:
    import ctypes
    from ctypes import wintypes

    times = [wintypes.FILETIME() for _ in range(4)]
    if not api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
        _raise_native("verification_failed")
    return int(times[0].dwHighDateTime) << 32 | int(times[0].dwLowDateTime)


def _is_running(api: Any, handle: int) -> bool:
    status = api.WaitForSingleObject(handle, 0)
    if status == 0xFFFFFFFF:
        _raise_native("verification_failed")
    return bool(status == 258)  # WAIT_TIMEOUT: process handle not signalled.


class _Win32LaunchSurface:
    """No association lookup, inherited handles, elevation, or child supervision."""

    def create(
        self, params: WindowsApplicationLaunchParams
    ) -> Result[_ProcessInstance, AgentXError]:
        if not is_native_surface_available():
            return Result.failure(
                unsupported_platform_error(evaluate_windows_support(detect_platform_facts()))
            )
        import ctypes

        try:
            api, startup_type, information_type = _api()
            startup = startup_type()
            startup.cb = ctypes.sizeof(startup_type)
            information = information_type()
            command_line = ctypes.create_unicode_buffer(_command_line(params))
            # Explicit image path eliminates ambiguous executable parsing/PATH lookup.
            # Default cwd is the executable's parent, not AgentX's ambient cwd.
            cwd = params.working_directory or str(PureWindowsPath(params.executable).parent)
            if not api.CreateProcessW(
                params.executable,
                command_line,
                None,
                None,
                False,
                0,
                None,
                cwd,
                ctypes.byref(startup),
                ctypes.byref(information),
            ):
                _raise_native("native_creation_failed")
            with _owned_handle(api, information.hProcess), _owned_handle(api, information.hThread):
                instance = _ProcessInstance(
                    int(information.dwProcessId), _creation_time(api, information.hProcess)
                )
            return Result.success(instance)
        except AgentXException as exc:
            return Result.failure(exc.error)
        except MemoryError:
            return Result.failure(_error("resource_failure", ErrorCategory.RESOURCE))
        except OSError:
            return Result.failure(_error("native_creation_failed", ErrorCategory.EXECUTION))

    def inspect(self, process_id: int) -> Result[_ProcessState, AgentXError]:
        if not is_native_surface_available():
            return Result.failure(
                unsupported_platform_error(evaluate_windows_support(detect_platform_facts()))
            )
        import ctypes
        from ctypes import wintypes

        try:
            api, _, _ = _api()
            # PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, no mutation rights.
            handle = api.OpenProcess(0x1000 | 0x100000, False, process_id)
            if not handle:
                _raise_native("verification_failed")
            with _owned_handle(api, handle):
                running_before = _is_running(api, handle)
                created = _creation_time(api, handle)
                buffer = ctypes.create_unicode_buffer(_MAX_PATH + 1)
                size = wintypes.DWORD(len(buffer))
                if not api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    _raise_native("verification_failed")
                state = _ProcessState(
                    _ProcessInstance(process_id, created),
                    str(buffer.value),
                    _is_running(api, handle) and running_before,
                )
            return Result.success(state)
        except AgentXException as exc:
            return Result.failure(exc.error)
        except MemoryError:
            return Result.failure(_error("resource_failure", ErrorCategory.RESOURCE))
        except OSError:
            return Result.failure(_error("verification_failed", ErrorCategory.VERIFICATION))


@dataclass(frozen=True, slots=True, init=False)
class WindowsApplicationLaunchCapability:
    """Canonical ABI capability. Construction grants no permission.

    Receipt MACs bind execution evidence to this instance/request/context without
    retaining handles or an unbounded receipt cache. They prove provenance only;
    independent native inspection is still mandatory and is the only state check.
    """

    _support: WindowsSupport
    _native_surface: _NativeLaunchSurface
    _receipt_key: bytes = field(repr=False)

    def __init__(
        self,
        support: WindowsSupport,
        *,
        native_surface: _NativeLaunchSurface | None = None,
    ) -> None:
        if not isinstance(support, WindowsSupport):
            raise TypeError("support must be WindowsSupport")
        object.__setattr__(self, "_support", support)
        object.__setattr__(
            self,
            "_native_surface",
            native_surface if native_surface is not None else _Win32LaunchSurface(),
        )
        object.__setattr__(self, "_receipt_key", secrets.token_bytes(32))

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            identity=WINDOWS_APPLICATION_LAUNCH_IDENTITY,
            description="Launch one explicit Windows executable and verify its process instance.",
            scope=CapabilityScope(CapabilityPlatform.WINDOWS),
            required_permissions=frozenset({Permission.EXECUTE}),
            risk_assessment=assess_risk(
                read_only=False,
                modifies_state=True,
                reversible=False,
                external_effect=False,
            ),
            preconditions=(
                CapabilityPrecondition(
                    name="windows.local_application",
                    description="Supported Windows host and trusted local executable for startup.",
                ),
            ),
            rollback=RollbackDeclaration(
                RollbackSupport.UNSUPPORTED,
                "No termination, rollback or process supervision.",
            ),
            estimate=ResourceEstimate(timedelta(seconds=1), 1, Decimal("0")),
        )

    def _receipt(
        self,
        params: WindowsApplicationLaunchParams,
        context: ExecutionContext,
        instance: _ProcessInstance,
    ) -> str:
        payload = json.dumps(
            [
                params.to_dict(),
                str(context.correlation_id),
                str(context.task_id),
                instance.process_id,
                instance.creation_time,
            ],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        return hmac.new(self._receipt_key, payload, hashlib.sha256).hexdigest()

    @staticmethod
    def _params(
        request: CapabilityRequest[WindowsApplicationLaunchParams],
    ) -> WindowsApplicationLaunchParams:
        if request.identity != WINDOWS_APPLICATION_LAUNCH_IDENTITY:
            raise CapabilityValidationError("invalid_launch_operation")
        if type(request.params) is not WindowsApplicationLaunchParams:
            raise TypeError("launch requires WindowsApplicationLaunchParams")
        # Revalidate at invocation even if a caller bypassed frozen construction.
        return WindowsApplicationLaunchParams(
            request.params.executable,
            request.params.arguments,
            request.params.working_directory,
        )

    def execute(
        self,
        request: CapabilityRequest[WindowsApplicationLaunchParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        params = self._params(request)
        if not self._support.is_supported:
            return _execution_failure(unsupported_platform_error(self._support))
        if context.observe_stop().should_stop:
            return _execution_failure(_error("context_stopped", ErrorCategory.CANCELLED))
        result = self._native_surface.create(params)
        if result.is_failure:
            return _execution_failure(result.unwrap_error())
        instance = result.unwrap()
        return ExecutionResult(
            succeeded=True,
            message="Process creation returned an instance; independent verification is pending.",
            observation=CapabilityObservation(
                summary="Windows application process creation evidence, not application readiness",
                data={
                    "process_id": instance.process_id,
                    "creation_time": instance.creation_time,
                    "receipt": self._receipt(params, context, instance),
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowsApplicationLaunchParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        params = self._params(request)
        if not self._support.is_supported or context.observe_stop().should_stop:
            return VerificationResult(False, "verification_failed: unsupported or stopped context")
        data = observation.data
        pid, created, receipt = (
            data.get("process_id"),
            data.get("creation_time"),
            data.get("receipt"),
        )
        if type(pid) is not int or type(created) is not int or type(receipt) is not str:
            return VerificationResult(
                False, "verification_failed: missing process creation evidence"
            )
        try:
            instance = _ProcessInstance(pid, created)
        except ValueError:
            return VerificationResult(
                False, "verification_failed: invalid process creation evidence"
            )
        if (
            len(receipt) != 64
            or not receipt.isascii()
            or not hmac.compare_digest(receipt, self._receipt(params, context, instance))
        ):
            return VerificationResult(
                False, "verification_failed: receipt does not match this launch"
            )
        result = self._native_surface.inspect(pid)
        if result.is_failure:
            return VerificationResult(False, f"verification_failed: {result.unwrap_error().code}")
        state = result.unwrap()
        matched = (
            state.instance == instance
            and state.running is True
            and PureWindowsPath(state.executable) == PureWindowsPath(params.executable)
            and not context.observe_stop().should_stop
        )
        return VerificationResult(
            matched,
            "Process instance is running with the expected image; readiness is not verified."
            if matched
            else "verification_failed: process exited or instance/image identity mismatched",
        )


def _execution_failure(error: AgentXError) -> ExecutionResult:
    return ExecutionResult(
        False,
        "Windows application launch failed; see structured error evidence.",
        CapabilityObservation("Windows application launch failed", {"error": error.to_dict()}),
    )


def launch_application_request(
    params: WindowsApplicationLaunchParams,
) -> CapabilityRequest[WindowsApplicationLaunchParams]:
    """Build inert typed launch data; this does not execute or grant authority."""
    return CapabilityRequest(WINDOWS_APPLICATION_LAUNCH_IDENTITY, params)
