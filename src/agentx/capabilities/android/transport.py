"""Bounded ADB transport used only behind the M13 Android provider.

Host subprocess invocation is argv-only with shell=False. Remote-shell
arguments are POSIX-quoted token-by-token before they cross the ADB boundary.
The transport is infrastructure, not an AgentX capability: callers must expose
only fixed typed Android operations through the canonical Capability ABI.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from threading import BoundedSemaphore
from typing import Final, Protocol

from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result

__all__ = [
    "ADB_DEFAULT_MAX_CONCURRENCY",
    "ADB_DEFAULT_OUTPUT_LIMIT",
    "ADB_DEFAULT_TIMEOUT_SECONDS",
    "ADB_MAX_RETRIES",
    "AdbCommandResult",
    "AdbDeviceRecord",
    "AdbDeviceState",
    "AdbRunner",
    "AdbTransport",
    "SubprocessAdbRunner",
    "validate_adb_serial",
    "validate_android_component",
    "validate_android_package",
]

ADB_DEFAULT_TIMEOUT_SECONDS: Final[float] = 10.0
ADB_DEFAULT_OUTPUT_LIMIT: Final[int] = 1_048_576
ADB_DEFAULT_MAX_CONCURRENCY: Final[int] = 4
ADB_MAX_RETRIES: Final[int] = 0
_POLL_INTERVAL_SECONDS: Final[float] = 0.05
ADB_SCREENSHOT_OUTPUT_LIMIT: Final[int] = 16 * 1_048_576
_MAX_ADB_ARGUMENT: Final[int] = 16_384
_MAX_SERIAL: Final[int] = 256
_MAX_PACKAGE: Final[int] = 255
_MAX_COMPONENT: Final[int] = 512

_SERIAL_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._:-]+")
_PACKAGE_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+")
_COMPONENT_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z0-9_.]*/[A-Za-z0-9_.$]+")


class AdbDeviceState(StrEnum):
    DEVICE = "device"
    OFFLINE = "offline"
    UNAUTHORIZED = "unauthorized"
    BOOTLOADER = "bootloader"
    RECOVERY = "recovery"
    SIDELOAD = "sideload"
    NO_PERMISSIONS = "no_permissions"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AdbDeviceRecord:
    serial: str
    state: AdbDeviceState
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        validate_adb_serial(self.serial)
        if not isinstance(self.state, AdbDeviceState):
            raise TypeError("state must be AdbDeviceState")
        if not isinstance(self.metadata, tuple):
            raise TypeError("metadata must be a tuple")
        previous: tuple[str, str] | None = None
        for item in self.metadata:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("metadata entries must be (key, value) tuples")
            key, value = item
            _validate_argument(key, field_name="metadata key")
            _validate_argument(value, field_name="metadata value")
            if previous is not None and item <= previous:
                raise ValueError("metadata must be strictly sorted")
            previous = item


@dataclass(frozen=True, slots=True)
class AdbCommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.argv, tuple) or not self.argv:
            raise TypeError("argv must be a non-empty tuple")
        if type(self.returncode) is not int:
            raise TypeError("returncode must be int")
        if not isinstance(self.stdout, bytes) or not isinstance(self.stderr, bytes):
            raise TypeError("stdout and stderr must be bytes")

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


class AdbRunner(Protocol):
    def run(
        self,
        *,
        executable: str,
        args: tuple[str, ...],
        timeout_seconds: float,
        max_output_bytes: int,
        context: ExecutionContext,
    ) -> Result[AdbCommandResult, AgentXError]: ...


def _validate_argument(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty and trimmed")
    if len(value) > _MAX_ADB_ARGUMENT:
        raise ValueError(f"{field_name} exceeds bounded length")
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ValueError(f"{field_name} must not contain NUL/CR/LF")
    return value


def validate_adb_serial(value: object) -> str:
    serial = _validate_argument(value, field_name="ADB serial")
    if len(serial) > _MAX_SERIAL or _SERIAL_PATTERN.fullmatch(serial) is None:
        raise ValueError("ADB serial has invalid syntax")
    return serial


def validate_android_package(value: object) -> str:
    package = _validate_argument(value, field_name="Android package")
    if len(package) > _MAX_PACKAGE or _PACKAGE_PATTERN.fullmatch(package) is None:
        raise ValueError("Android package has invalid syntax")
    return package


def validate_android_component(value: object) -> str:
    component = _validate_argument(value, field_name="Android component")
    if len(component) > _MAX_COMPONENT or _COMPONENT_PATTERN.fullmatch(component) is None:
        raise ValueError("Android component has invalid syntax")
    package, _separator, _activity = component.partition("/")
    validate_android_package(package)
    return component


def _stopped_error(context: ExecutionContext) -> AgentXError | None:
    status = context.observe_stop()
    if status.cancellation_requested:
        return AgentXError(
            code="android.adb.cancelled",
            message="ADB operation cancelled by the execution context",
            category=ErrorCategory.CANCELLED,
            retryability=Retryability.NON_RETRYABLE,
        )
    if status.timed_out:
        return AgentXError(
            code="android.adb.deadline",
            message="ADB operation stopped because the execution deadline expired",
            category=ErrorCategory.TIMEOUT,
            retryability=Retryability.NON_RETRYABLE,
        )
    return None


class SubprocessAdbRunner:
    """Default bounded host runner. It never invokes a host shell."""

    __slots__ = ()

    def run(
        self,
        *,
        executable: str,
        args: tuple[str, ...],
        timeout_seconds: float,
        max_output_bytes: int,
        context: ExecutionContext,
    ) -> Result[AdbCommandResult, AgentXError]:
        _validate_argument(executable, field_name="ADB executable")
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be ExecutionContext")
        try:
            process = subprocess.Popen(
                [executable, *args],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except FileNotFoundError as exc:
            return Result.failure(
                AgentXError(
                    code="android.adb.missing",
                    message="ADB executable is unavailable",
                    category=ErrorCategory.DEPENDENCY,
                    retryability=Retryability.NON_RETRYABLE,
                    cause=exc,
                )
            )
        except OSError as exc:
            return Result.failure(
                AgentXError(
                    code="android.adb.host_failure",
                    message="host failed to invoke the ADB executable",
                    category=ErrorCategory.DEPENDENCY,
                    retryability=Retryability.RETRYABLE,
                    cause=exc,
                )
            )

        started = time.monotonic()
        stdout = b""
        stderr = b""
        while True:
            stop_error = _stopped_error(context)
            if stop_error is not None:
                process.kill()
                process.communicate()
                return Result.failure(stop_error)

            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                process.kill()
                process.communicate()
                return Result.failure(
                    AgentXError(
                        code="android.adb.timeout",
                        message="ADB command exceeded its bounded transport timeout",
                        category=ErrorCategory.TIMEOUT,
                        retryability=Retryability.RETRYABLE,
                    )
                )

            try:
                stdout, stderr = process.communicate(timeout=min(_POLL_INTERVAL_SECONDS, remaining))
                break
            except subprocess.TimeoutExpired as exc:
                partial_stdout = exc.output if isinstance(exc.output, bytes) else b""
                partial_stderr = exc.stderr if isinstance(exc.stderr, bytes) else b""
                if len(partial_stdout) + len(partial_stderr) > max_output_bytes:
                    process.kill()
                    process.communicate()
                    return Result.failure(_output_limit_error(max_output_bytes))

        if len(stdout) + len(stderr) > max_output_bytes:
            return Result.failure(_output_limit_error(max_output_bytes))
        returncode = process.returncode
        if returncode is None:
            return Result.failure(
                AgentXError(
                    code="android.adb.host_failure",
                    message="ADB process ended without a return code",
                    category=ErrorCategory.INTERNAL,
                    retryability=Retryability.NON_RETRYABLE,
                )
            )
        return Result.success(
            AdbCommandResult(
                argv=(executable, *args),
                returncode=returncode,
                stdout=bytes(stdout),
                stderr=bytes(stderr),
            )
        )


class AdbTransport:
    """Typed bounded transport. Command success is never task verification."""

    def __init__(
        self,
        *,
        executable: str = "adb",
        runner: AdbRunner | None = None,
        timeout_seconds: float = ADB_DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = ADB_DEFAULT_OUTPUT_LIMIT,
        max_concurrency: int = ADB_DEFAULT_MAX_CONCURRENCY,
    ) -> None:
        self._executable = _validate_argument(executable, field_name="ADB executable")
        if not isinstance(timeout_seconds, int | float) or isinstance(timeout_seconds, bool):
            raise TypeError("timeout_seconds must be a number")
        if timeout_seconds <= 0 or timeout_seconds > 120:
            raise ValueError("timeout_seconds must be in (0, 120]")
        if type(max_output_bytes) is not int or max_output_bytes < 1:
            raise TypeError("max_output_bytes must be a positive int")
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 32:
            raise ValueError("max_concurrency must be an int in [1, 32]")
        self._runner = SubprocessAdbRunner() if runner is None else runner
        self._timeout_seconds = float(timeout_seconds)
        self._max_output_bytes = max_output_bytes
        self._max_concurrency = max_concurrency
        self._slots = BoundedSemaphore(max_concurrency)

    @property
    def executable(self) -> str:
        return self._executable

    def command(
        self,
        args: tuple[str, ...],
        *,
        context: ExecutionContext,
        serial: str | None = None,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
    ) -> Result[AdbCommandResult, AgentXError]:
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be ExecutionContext")
        if not isinstance(args, tuple) or not args:
            raise TypeError("args must be a non-empty tuple")
        checked_args = tuple(_validate_argument(item, field_name="ADB argument") for item in args)
        stop_error = _stopped_error(context)
        if stop_error is not None:
            return Result.failure(stop_error)
        prefix: tuple[str, ...] = ()
        if serial is not None:
            prefix = ("-s", validate_adb_serial(serial))
        timeout = self._timeout_seconds if timeout_seconds is None else float(timeout_seconds)
        limit = self._max_output_bytes if max_output_bytes is None else max_output_bytes
        if timeout <= 0 or timeout > 120:
            raise ValueError("timeout_seconds must be in (0, 120]")
        if type(limit) is not int or limit < 1:
            raise TypeError("max_output_bytes must be a positive int")
        started = time.monotonic()
        while True:
            stop_error = _stopped_error(context)
            if stop_error is not None:
                return Result.failure(stop_error)
            queue_remaining = timeout - (time.monotonic() - started)
            if queue_remaining <= 0:
                return Result.failure(
                    AgentXError(
                        code="android.adb.concurrency_timeout",
                        message="ADB operation could not acquire a bounded transport slot",
                        category=ErrorCategory.RESOURCE,
                        retryability=Retryability.RETRYABLE,
                    )
                )
            if self._slots.acquire(timeout=min(_POLL_INTERVAL_SECONDS, queue_remaining)):
                break
        try:
            stop_error = _stopped_error(context)
            if stop_error is not None:
                return Result.failure(stop_error)
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                return Result.failure(
                    AgentXError(
                        code="android.adb.timeout",
                        message="ADB command exceeded its total transport timeout",
                        category=ErrorCategory.TIMEOUT,
                        retryability=Retryability.RETRYABLE,
                    )
                )
            result = self._runner.run(
                executable=self._executable,
                args=(*prefix, *checked_args),
                timeout_seconds=remaining,
                max_output_bytes=limit,
                context=context,
            )
        finally:
            self._slots.release()
        post_error = _stopped_error(context)
        if post_error is not None:
            return Result.failure(post_error)
        return result

    def devices(
        self,
        *,
        context: ExecutionContext,
    ) -> Result[tuple[AdbDeviceRecord, ...], AgentXError]:
        result = self.command(("devices", "-l"), context=context)
        if result.is_failure:
            return Result.failure(result.unwrap_error())
        command = result.unwrap()
        if not command.succeeded:
            return Result.failure(_command_failure("android.adb.devices_failed", command))
        try:
            text = command.stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            return Result.failure(
                AgentXError(
                    code="android.adb.malformed_devices",
                    message="ADB device enumeration returned non-UTF-8 output",
                    category=ErrorCategory.VALIDATION,
                    retryability=Retryability.NON_RETRYABLE,
                    cause=exc,
                )
            )
        records: list[AdbDeviceRecord] = []
        seen: set[str] = set()
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("List of devices attached"):
                continue
            parts = line.split()
            if len(parts) < 2:
                return Result.failure(_malformed_devices_error())
            try:
                serial = validate_adb_serial(parts[0])
            except (TypeError, ValueError):
                return Result.failure(_malformed_devices_error())
            if serial in seen:
                return Result.failure(
                    AgentXError(
                        code="android.adb.duplicate_device",
                        message="ADB enumeration returned a duplicate device serial",
                        category=ErrorCategory.CONFLICT,
                        retryability=Retryability.NON_RETRYABLE,
                    )
                )
            seen.add(serial)
            state_raw = parts[1]
            if state_raw == "no" and len(parts) > 2 and parts[2] == "permissions":
                state = AdbDeviceState.NO_PERMISSIONS
                metadata_parts = parts[3:]
            else:
                try:
                    state = AdbDeviceState(state_raw)
                except ValueError:
                    state = AdbDeviceState.UNKNOWN
                metadata_parts = parts[2:]
            metadata: list[tuple[str, str]] = []
            for token in metadata_parts:
                key, separator, value = token.partition(":")
                if not separator or not key or not value:
                    continue
                try:
                    metadata.append(
                        (
                            _validate_argument(key, field_name="ADB metadata key"),
                            _validate_argument(value, field_name="ADB metadata value"),
                        )
                    )
                except ValueError:
                    continue
            records.append(
                AdbDeviceRecord(
                    serial=serial,
                    state=state,
                    metadata=tuple(sorted(set(metadata))),
                )
            )
        return Result.success(tuple(sorted(records, key=lambda item: item.serial)))

    def shell(
        self,
        serial: str,
        tokens: tuple[str, ...],
        *,
        context: ExecutionContext,
        timeout_seconds: float | None = None,
        max_output_bytes: int | None = None,
    ) -> Result[AdbCommandResult, AgentXError]:
        """Run fixed remote tokens with conservative POSIX shell quoting."""
        validate_adb_serial(serial)
        if not isinstance(tokens, tuple) or not tokens:
            raise TypeError("tokens must be a non-empty tuple")
        checked = tuple(
            _validate_argument(item, field_name="remote shell token") for item in tokens
        )
        remote = " ".join(shlex.quote(item) for item in checked)
        return self.command(
            ("shell", remote),
            context=context,
            serial=serial,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )

    def exec_out(
        self,
        serial: str,
        tokens: tuple[str, ...],
        *,
        context: ExecutionContext,
        timeout_seconds: float | None = None,
        max_output_bytes: int = ADB_SCREENSHOT_OUTPUT_LIMIT,
    ) -> Result[AdbCommandResult, AgentXError]:
        """Run a bounded fixed exec-out operation with quoted remote tokens."""
        validate_adb_serial(serial)
        if not isinstance(tokens, tuple) or not tokens:
            raise TypeError("tokens must be a non-empty tuple")
        checked = tuple(_validate_argument(item, field_name="exec-out token") for item in tokens)
        remote = " ".join(shlex.quote(item) for item in checked)
        return self.command(
            ("exec-out", remote),
            context=context,
            serial=serial,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )


def _command_failure(code: str, command: AdbCommandResult) -> AgentXError:
    stderr = command.stderr.decode("utf-8", errors="replace").strip()
    detail = stderr[:512] if stderr else "ADB returned a non-zero exit status"
    return AgentXError(
        code=code,
        message="ADB command failed",
        category=ErrorCategory.EXECUTION,
        retryability=Retryability.RETRYABLE,
        details={"returncode": command.returncode, "detail": detail},
    )


def _malformed_devices_error() -> AgentXError:
    return AgentXError(
        code="android.adb.malformed_devices",
        message="ADB device enumeration output was malformed",
        category=ErrorCategory.VALIDATION,
        retryability=Retryability.NON_RETRYABLE,
    )


def _output_limit_error(limit_bytes: int) -> AgentXError:
    return AgentXError(
        code="android.adb.output_limit",
        message="ADB output exceeded the configured safety bound",
        category=ErrorCategory.RESOURCE,
        retryability=Retryability.NON_RETRYABLE,
        details={"limit_bytes": limit_bytes},
    )
