"""Minimal governed filesystem text capabilities for AgentX (M1.01).

This module exposes exactly two deterministic machine-state capabilities:
``filesystem.read_text`` and ``filesystem.write_text``. Paths and file content
are untrusted data. Capability metadata is fixed independently of request data,
and execution never consults the PermissionEngine or ActionGate directly; the
canonical CapabilityExecutionLoop owns authority, budget, stop, and Task-state
orchestration.

The implementation deliberately uses only structured Python filesystem APIs.
It performs no shell expansion, globbing, directory creation, delete, rename,
copy, move, permission changes, archive handling, process launch, or recursive
filesystem operation.

This is a governed primitive, not a filesystem sandbox. The operating system
may change files between checks and use, and symlink/mount behavior remains
host behavior. Verification therefore performs an independent bounded read-back
but cannot eliminate TOCTOU races.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final, NoReturn

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
    CapabilityVersion,
    ExecutionResult,
    ResourceEstimate,
    RollbackDeclaration,
    RollbackSupport,
    VerificationResult,
)
from agentx.core.errors import (
    AgentXError,
    AgentXException,
    ErrorCategory,
    Retryability,
)
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk

__all__ = [
    "CONTENT_TOO_LARGE_ERROR_CODE",
    "DIRECTORY_SUPPLIED_ERROR_CODE",
    "ENCODING_ERROR_CODE",
    "FILESYSTEM_READ_TEXT_IDENTITY",
    "FILESYSTEM_WRITE_TEXT_IDENTITY",
    "INVALID_INPUT_ERROR_CODE",
    "MAX_PATH_LENGTH",
    "MAX_TEXT_READ_BYTES",
    "MAX_TEXT_WRITE_BYTES",
    "NOT_FOUND_ERROR_CODE",
    "OS_ERROR_CODE",
    "OS_PERMISSION_ERROR_CODE",
    "OVERWRITE_PROHIBITED_ERROR_CODE",
    "PARENT_MISSING_ERROR_CODE",
    "UNSUPPORTED_PATH_ERROR_CODE",
    "VERIFICATION_MISMATCH_ERROR_CODE",
    "FilesystemReadTextCapability",
    "FilesystemWriteTextCapability",
    "ReadTextParams",
    "WriteTextParams",
    "read_text_request",
    "write_text_request",
]

MAX_TEXT_WRITE_BYTES: Final[int] = 1_048_576
MAX_TEXT_READ_BYTES: Final[int] = 1_048_576
MAX_PATH_LENGTH: Final[int] = 4_096

INVALID_INPUT_ERROR_CODE: Final[str] = "capabilities.filesystem.invalid_input"
UNSUPPORTED_PATH_ERROR_CODE: Final[str] = "capabilities.filesystem.unsupported_path"
NOT_FOUND_ERROR_CODE: Final[str] = "capabilities.filesystem.not_found"
PARENT_MISSING_ERROR_CODE: Final[str] = "capabilities.filesystem.parent_missing"
DIRECTORY_SUPPLIED_ERROR_CODE: Final[str] = "capabilities.filesystem.directory_supplied"
OS_PERMISSION_ERROR_CODE: Final[str] = "capabilities.filesystem.os_permission_error"
OS_ERROR_CODE: Final[str] = "capabilities.filesystem.os_error"
CONTENT_TOO_LARGE_ERROR_CODE: Final[str] = "capabilities.filesystem.content_too_large"
ENCODING_ERROR_CODE: Final[str] = "capabilities.filesystem.encoding_error"
OVERWRITE_PROHIBITED_ERROR_CODE: Final[str] = "capabilities.filesystem.overwrite_prohibited"
VERIFICATION_MISMATCH_ERROR_CODE: Final[str] = "capabilities.filesystem.verification_mismatch"
_CANCELLED_ERROR_CODE: Final[str] = "capabilities.filesystem.cancelled"

FILESYSTEM_READ_TEXT_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("filesystem.read_text"),
    version=CapabilityVersion(1, 0, 0),
)
FILESYSTEM_WRITE_TEXT_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("filesystem.write_text"),
    version=CapabilityVersion(1, 0, 0),
)


def _error(
    *,
    code: str,
    message: str,
    category: ErrorCategory,
    retryability: Retryability = Retryability.NON_RETRYABLE,
    details: dict[str, JsonValue] | None = None,
    cause: BaseException | None = None,
) -> AgentXError:
    return AgentXError(
        code=code,
        message=message,
        category=category,
        retryability=retryability,
        details=details,
        cause=cause,
    )


def _raise_validation(
    *,
    code: str,
    message: str,
    category: ErrorCategory = ErrorCategory.VALIDATION,
    details: dict[str, JsonValue] | None = None,
    cause: BaseException | None = None,
) -> NoReturn:
    raise AgentXException(
        _error(
            code=code,
            message=message,
            category=category,
            details=details,
            cause=cause,
        )
    )


def _validated_absolute_path(value: object) -> Path:
    if not isinstance(value, str):
        raise TypeError(f"path must be a str, got {type(value).__name__}")
    if not value:
        _raise_validation(code=INVALID_INPUT_ERROR_CODE, message="path must not be empty")
    if len(value) > MAX_PATH_LENGTH:
        _raise_validation(
            code=UNSUPPORTED_PATH_ERROR_CODE,
            message="path exceeds the governed maximum length",
            details={"max_path_length": MAX_PATH_LENGTH},
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _raise_validation(
            code=INVALID_INPUT_ERROR_CODE,
            message="path must not contain control characters",
        )
    path = Path(value)
    if not path.is_absolute():
        _raise_validation(
            code=INVALID_INPUT_ERROR_CODE,
            message="path must be an explicit absolute host path",
        )
    if ".." in path.parts or str(path) != value:
        _raise_validation(
            code=INVALID_INPUT_ERROR_CODE,
            message="path must already be lexically normalized for the current host",
        )
    return path


def _validated_max_read_bytes(value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"max_bytes must be an int, got {type(value).__name__}")
    if value < 0:
        _raise_validation(
            code=INVALID_INPUT_ERROR_CODE,
            message="max_bytes must not be negative",
        )
    if value > MAX_TEXT_READ_BYTES:
        _raise_validation(
            code=CONTENT_TOO_LARGE_ERROR_CODE,
            message="max_bytes exceeds the governed read limit",
            category=ErrorCategory.RESOURCE,
            details={"max_read_bytes": MAX_TEXT_READ_BYTES},
        )
    return value


def _validated_utf8_content(value: object) -> bytes:
    if not isinstance(value, str):
        raise TypeError(f"content must be a str, got {type(value).__name__}")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        _raise_validation(
            code=ENCODING_ERROR_CODE,
            message="content is not representable as strict UTF-8 text",
            cause=exc,
        )
    if len(encoded) > MAX_TEXT_WRITE_BYTES:
        _raise_validation(
            code=CONTENT_TOO_LARGE_ERROR_CODE,
            message="UTF-8 content exceeds the governed write limit",
            category=ErrorCategory.RESOURCE,
            details={"max_write_bytes": MAX_TEXT_WRITE_BYTES},
        )
    return encoded


@dataclass(frozen=True, slots=True)
class ReadTextParams(CapabilityParams):
    """Explicit bounded request parameters for ``filesystem.read_text``."""

    path: str
    max_bytes: int = MAX_TEXT_READ_BYTES

    def __post_init__(self) -> None:
        _validated_absolute_path(self.path)
        _validated_max_read_bytes(self.max_bytes)

    def to_dict(self) -> dict[str, JsonValue]:
        return {"path": self.path, "max_bytes": self.max_bytes}


@dataclass(frozen=True, slots=True)
class WriteTextParams(CapabilityParams):
    """Explicit bounded request parameters for ``filesystem.write_text``.

    ``overwrite=False`` is create-only semantics: an existing target fails
    explicitly. ``overwrite=True`` allows either creation or replacement of a
    regular file. Parent directories are never created implicitly.
    """

    path: str
    content: str
    overwrite: bool

    def __post_init__(self) -> None:
        _validated_absolute_path(self.path)
        _validated_utf8_content(self.content)
        if type(self.overwrite) is not bool:
            raise TypeError(f"overwrite must be bool, got {type(self.overwrite).__name__}")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "path": self.path,
            "content": self.content,
            "overwrite": self.overwrite,
        }


_READ_DESCRIPTOR: Final[CapabilityDescriptor] = CapabilityDescriptor(
    identity=FILESYSTEM_READ_TEXT_IDENTITY,
    description=(
        "Read one explicitly supplied absolute regular-file path as strict UTF-8 text "
        "within a caller-supplied bounded byte limit."
    ),
    scope=CapabilityScope(platform=CapabilityPlatform.ANY),
    required_permissions=frozenset({Permission.READ}),
    risk_assessment=assess_risk(
        read_only=True,
        modifies_state=False,
        reversible=False,
        external_effect=False,
    ),
    preconditions=(
        CapabilityPrecondition(
            name="filesystem.absolute_regular_file",
            description=(
                "The request path must be a normalized absolute host path naming an "
                "existing regular file whose bytes are strict UTF-8 text."
            ),
        ),
    ),
    rollback=RollbackDeclaration(
        support=RollbackSupport.NOT_APPLICABLE,
        detail="Reading text does not intentionally mutate filesystem state.",
    ),
    estimate=ResourceEstimate(
        wall_clock=timedelta(milliseconds=500),
        machine_actions=2,
        external_cost=Decimal("0"),
    ),
)

_WRITE_DESCRIPTOR: Final[CapabilityDescriptor] = CapabilityDescriptor(
    identity=FILESYSTEM_WRITE_TEXT_IDENTITY,
    description=(
        "Create or explicitly overwrite one regular file at an absolute path with exact "
        "bounded UTF-8 bytes, without creating directories."
    ),
    scope=CapabilityScope(platform=CapabilityPlatform.ANY),
    required_permissions=frozenset({Permission.WRITE}),
    risk_assessment=assess_risk(
        read_only=False,
        modifies_state=True,
        reversible=False,
        external_effect=False,
    ),
    preconditions=(
        CapabilityPrecondition(
            name="filesystem.parent_directory_exists",
            description="The target parent must already exist and be a directory.",
        ),
        CapabilityPrecondition(
            name="filesystem.target_regular_or_absent",
            description=(
                "The target must be absent or an existing regular file; directories and "
                "other file types are rejected."
            ),
        ),
    ),
    rollback=RollbackDeclaration(
        support=RollbackSupport.UNSUPPORTED,
        detail=(
            "M1.01 implements no rollback; overwrite may replace prior content and no prior "
            "bytes are retained."
        ),
    ),
    estimate=ResourceEstimate(
        wall_clock=timedelta(seconds=1),
        machine_actions=2,
        external_cost=Decimal("0"),
    ),
)


@dataclass(frozen=True, slots=True)
class _ReadValue:
    text: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class _WriteValue:
    byte_count: int


def _not_regular_error() -> AgentXError:
    return _error(
        code=UNSUPPORTED_PATH_ERROR_CODE,
        message="filesystem target is not a supported regular file",
        category=ErrorCategory.VALIDATION,
    )


def _read_regular_bytes(path: Path, *, max_bytes: int) -> Result[bytes, AgentXError]:
    try:
        target_stat = path.stat()
    except FileNotFoundError as exc:
        return Result.failure(
            _error(
                code=NOT_FOUND_ERROR_CODE,
                message="filesystem target does not exist",
                category=ErrorCategory.NOT_FOUND,
                cause=exc,
            )
        )
    except PermissionError as exc:
        return Result.failure(
            _error(
                code=OS_PERMISSION_ERROR_CODE,
                message="operating system denied access to the filesystem target",
                category=ErrorCategory.PERMISSION,
                cause=exc,
            )
        )
    except OSError as exc:
        return Result.failure(
            _error(
                code=OS_ERROR_CODE,
                message="operating system could not inspect the filesystem target",
                category=ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )

    if stat.S_ISDIR(target_stat.st_mode):
        return Result.failure(
            _error(
                code=DIRECTORY_SUPPLIED_ERROR_CODE,
                message="filesystem target is a directory, not a text file",
                category=ErrorCategory.VALIDATION,
            )
        )
    if not stat.S_ISREG(target_stat.st_mode):
        return Result.failure(_not_regular_error())

    try:
        with path.open("rb") as handle:
            payload = handle.read(max_bytes + 1)
    except FileNotFoundError as exc:
        return Result.failure(
            _error(
                code=NOT_FOUND_ERROR_CODE,
                message="filesystem target disappeared before it could be read",
                category=ErrorCategory.NOT_FOUND,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )
    except IsADirectoryError as exc:
        return Result.failure(
            _error(
                code=DIRECTORY_SUPPLIED_ERROR_CODE,
                message="filesystem target became a directory before it could be read",
                category=ErrorCategory.VALIDATION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )
    except PermissionError as exc:
        return Result.failure(
            _error(
                code=OS_PERMISSION_ERROR_CODE,
                message="operating system denied reading the filesystem target",
                category=ErrorCategory.PERMISSION,
                cause=exc,
            )
        )
    except OSError as exc:
        return Result.failure(
            _error(
                code=OS_ERROR_CODE,
                message="operating system could not read the filesystem target",
                category=ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )

    if len(payload) > max_bytes:
        return Result.failure(
            _error(
                code=CONTENT_TOO_LARGE_ERROR_CODE,
                message="filesystem text exceeds the requested read bound",
                category=ErrorCategory.RESOURCE,
                details={"max_bytes": max_bytes},
            )
        )
    return Result.success(payload)


def _read_text_value(path: Path, *, max_bytes: int) -> Result[_ReadValue, AgentXError]:
    raw = _read_regular_bytes(path, max_bytes=max_bytes)
    if raw.is_failure:
        return Result.failure(raw.unwrap_error())
    payload = raw.unwrap()
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        return Result.failure(
            _error(
                code=ENCODING_ERROR_CODE,
                message="filesystem target is not strict UTF-8 text",
                category=ErrorCategory.VALIDATION,
                cause=exc,
            )
        )
    return Result.success(_ReadValue(text=text, byte_count=len(payload)))


def _inspect_write_target(path: Path) -> Result[bool, AgentXError]:
    """Return whether a target regular file exists, or an explicit failure."""
    try:
        target_stat = path.stat()
    except FileNotFoundError:
        return Result.success(False)
    except PermissionError as exc:
        return Result.failure(
            _error(
                code=OS_PERMISSION_ERROR_CODE,
                message="operating system denied inspection of the filesystem target",
                category=ErrorCategory.PERMISSION,
                cause=exc,
            )
        )
    except OSError as exc:
        return Result.failure(
            _error(
                code=OS_ERROR_CODE,
                message="operating system could not inspect the filesystem target",
                category=ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )

    if stat.S_ISDIR(target_stat.st_mode):
        return Result.failure(
            _error(
                code=DIRECTORY_SUPPLIED_ERROR_CODE,
                message="filesystem target is a directory, not a text file",
                category=ErrorCategory.VALIDATION,
            )
        )
    if not stat.S_ISREG(target_stat.st_mode):
        return Result.failure(_not_regular_error())
    return Result.success(True)


def _parent_ready(path: Path) -> Result[None, AgentXError]:
    try:
        parent_stat = path.parent.stat()
    except FileNotFoundError as exc:
        return Result.failure(
            _error(
                code=PARENT_MISSING_ERROR_CODE,
                message="filesystem target parent directory does not exist",
                category=ErrorCategory.PRECONDITION,
                cause=exc,
            )
        )
    except PermissionError as exc:
        return Result.failure(
            _error(
                code=OS_PERMISSION_ERROR_CODE,
                message="operating system denied inspection of the target parent directory",
                category=ErrorCategory.PERMISSION,
                cause=exc,
            )
        )
    except OSError as exc:
        return Result.failure(
            _error(
                code=OS_ERROR_CODE,
                message="operating system could not inspect the target parent directory",
                category=ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )
    if not stat.S_ISDIR(parent_stat.st_mode):
        return Result.failure(
            _error(
                code=PARENT_MISSING_ERROR_CODE,
                message="filesystem target parent is not a directory",
                category=ErrorCategory.PRECONDITION,
            )
        )
    return Result.success(None)


def _write_text_value(
    path: Path, *, payload: bytes, overwrite: bool
) -> Result[_WriteValue, AgentXError]:
    parent = _parent_ready(path)
    if parent.is_failure:
        return Result.failure(parent.unwrap_error())

    inspected = _inspect_write_target(path)
    if inspected.is_failure:
        return Result.failure(inspected.unwrap_error())
    exists = inspected.unwrap()
    if exists and not overwrite:
        return Result.failure(
            _error(
                code=OVERWRITE_PROHIBITED_ERROR_CODE,
                message="filesystem target already exists and overwrite is prohibited",
                category=ErrorCategory.CONFLICT,
            )
        )

    mode = "wb" if overwrite else "xb"
    try:
        with path.open(mode) as handle:
            byte_count = handle.write(payload)
            handle.flush()
    except FileExistsError as exc:
        return Result.failure(
            _error(
                code=OVERWRITE_PROHIBITED_ERROR_CODE,
                message="filesystem target exists and create-only write cannot replace it",
                category=ErrorCategory.CONFLICT,
                cause=exc,
            )
        )
    except FileNotFoundError as exc:
        return Result.failure(
            _error(
                code=PARENT_MISSING_ERROR_CODE,
                message="filesystem target parent disappeared before the write",
                category=ErrorCategory.PRECONDITION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )
    except IsADirectoryError as exc:
        return Result.failure(
            _error(
                code=DIRECTORY_SUPPLIED_ERROR_CODE,
                message="filesystem target became a directory before the write",
                category=ErrorCategory.VALIDATION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )
    except PermissionError as exc:
        return Result.failure(
            _error(
                code=OS_PERMISSION_ERROR_CODE,
                message="operating system denied writing the filesystem target",
                category=ErrorCategory.PERMISSION,
                cause=exc,
            )
        )
    except OSError as exc:
        return Result.failure(
            _error(
                code=OS_ERROR_CODE,
                message="operating system could not write the filesystem target",
                category=ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )

    if byte_count != len(payload):
        return Result.failure(
            _error(
                code=OS_ERROR_CODE,
                message="filesystem write completed with an unexpected short byte count",
                category=ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                details={"expected_bytes": len(payload), "written_bytes": byte_count},
            )
        )
    return Result.success(_WriteValue(byte_count=byte_count))


def _execution_failure(error: AgentXError, *, summary: str) -> ExecutionResult:
    return ExecutionResult(
        succeeded=False,
        message=error.message,
        observation=CapabilityObservation(summary=summary, data={"error": error.to_dict()}),
    )


def _cancelled_execution(*, operation: str) -> ExecutionResult:
    error = _error(
        code=_CANCELLED_ERROR_CODE,
        message=f"{operation} was cooperatively cancelled before filesystem access",
        category=ErrorCategory.CANCELLED,
        retryability=Retryability.UNKNOWN,
    )
    return _execution_failure(error, summary=f"{operation} cancelled before filesystem access")


def _verification_failure(reason: str, *, cause_code: str | None = None) -> VerificationResult:
    suffix = "" if cause_code is None else f"; cause={cause_code}"
    return VerificationResult(
        passed=False,
        detail=f"{VERIFICATION_MISMATCH_ERROR_CODE}: {reason}{suffix}",
    )


class FilesystemReadTextCapability:
    """Governed bounded strict-UTF-8 regular-file read capability."""

    __slots__ = ()

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return _READ_DESCRIPTOR

    def execute(
        self,
        request: CapabilityRequest[ReadTextParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, ReadTextParams):
            raise TypeError(
                f"read_text requires ReadTextParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return _cancelled_execution(operation="read_text")
        path = _validated_absolute_path(request.params.path)
        result = _read_text_value(path, max_bytes=request.params.max_bytes)
        if result.is_failure:
            return _execution_failure(
                result.unwrap_error(),
                summary="filesystem read_text failed",
            )
        value = result.unwrap()
        return ExecutionResult(
            succeeded=True,
            message=f"read {value.byte_count} UTF-8 bytes from one regular file",
            observation=CapabilityObservation(
                summary="bounded filesystem UTF-8 text read",
                data={"text": value.text, "byte_count": value.byte_count},
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[ReadTextParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, ReadTextParams):
            raise TypeError(
                f"read_text requires ReadTextParams, got {type(request.params).__name__}"
            )
        if not isinstance(observation, CapabilityObservation):
            raise TypeError(
                f"observation must be a CapabilityObservation, got {type(observation).__name__}"
            )
        if context.observe_stop().should_stop:
            return _verification_failure("verification stop condition is active")

        data = observation.to_dict()["data"]
        if not isinstance(data, dict):
            return _verification_failure("read observation data is malformed")
        if "error" in data:
            return _verification_failure("read execution observation contains an error")
        text = data.get("text")
        byte_count = data.get("byte_count")
        if not isinstance(text, str) or type(byte_count) is not int:
            return _verification_failure("read observation lacks typed text/byte_count evidence")
        try:
            observed_bytes = text.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            return _verification_failure("read observation text is not strict UTF-8")
        if len(observed_bytes) != byte_count or byte_count > request.params.max_bytes:
            return _verification_failure("read observation byte accounting is inconsistent")

        path = _validated_absolute_path(request.params.path)
        current = _read_text_value(path, max_bytes=request.params.max_bytes)
        if current.is_failure:
            error = current.unwrap_error()
            return _verification_failure(
                "independent read-back could not confirm the observed file state",
                cause_code=error.code,
            )
        current_value = current.unwrap()
        if current_value.text != text or current_value.byte_count != byte_count:
            return _verification_failure("independent read-back differs from the observation")
        return VerificationResult(
            passed=True,
            detail="read_text evidence matches an independent bounded strict-UTF-8 read-back",
        )


class FilesystemWriteTextCapability:
    """Governed bounded exact-UTF-8 regular-file create/overwrite capability."""

    __slots__ = ()

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return _WRITE_DESCRIPTOR

    def execute(
        self,
        request: CapabilityRequest[WriteTextParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        if not isinstance(request.params, WriteTextParams):
            raise TypeError(
                f"write_text requires WriteTextParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return _cancelled_execution(operation="write_text")
        path = _validated_absolute_path(request.params.path)
        payload = _validated_utf8_content(request.params.content)
        result = _write_text_value(
            path,
            payload=payload,
            overwrite=request.params.overwrite,
        )
        if result.is_failure:
            return _execution_failure(
                result.unwrap_error(),
                summary="filesystem write_text failed",
            )
        value = result.unwrap()
        return ExecutionResult(
            succeeded=True,
            message=f"wrote {value.byte_count} exact UTF-8 bytes to one regular file target",
            observation=CapabilityObservation(
                summary="bounded filesystem UTF-8 text write attempt",
                data={
                    "byte_count": value.byte_count,
                    "overwrite_allowed": request.params.overwrite,
                },
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WriteTextParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, WriteTextParams):
            raise TypeError(
                f"write_text requires WriteTextParams, got {type(request.params).__name__}"
            )
        if not isinstance(observation, CapabilityObservation):
            raise TypeError(
                f"observation must be a CapabilityObservation, got {type(observation).__name__}"
            )
        if context.observe_stop().should_stop:
            return _verification_failure("verification stop condition is active")

        expected = _validated_utf8_content(request.params.content)
        data = observation.to_dict()["data"]
        if not isinstance(data, dict):
            return _verification_failure("write observation data is malformed")
        if "error" in data:
            return _verification_failure("write execution observation contains an error")
        byte_count = data.get("byte_count")
        overwrite_allowed = data.get("overwrite_allowed")
        if type(byte_count) is not int or type(overwrite_allowed) is not bool:
            return _verification_failure("write observation lacks typed byte-count evidence")
        if byte_count != len(expected) or overwrite_allowed is not request.params.overwrite:
            return _verification_failure("write observation is inconsistent with the request")

        path = _validated_absolute_path(request.params.path)
        current = _read_text_value(path, max_bytes=len(expected))
        if current.is_failure:
            error = current.unwrap_error()
            return _verification_failure(
                "independent read-back could not confirm the intended file state",
                cause_code=error.code,
            )
        current_value = current.unwrap()
        if (
            current_value.byte_count != len(expected)
            or current_value.text != request.params.content
        ):
            return _verification_failure(
                "independent read-back does not exactly match intended text"
            )
        return VerificationResult(
            passed=True,
            detail="write_text verified by independent exact strict-UTF-8 read-back",
        )


def read_text_request(
    path: str, *, max_bytes: int = MAX_TEXT_READ_BYTES
) -> CapabilityRequest[ReadTextParams]:
    return CapabilityRequest(
        identity=FILESYSTEM_READ_TEXT_IDENTITY,
        params=ReadTextParams(path=path, max_bytes=max_bytes),
    )


def write_text_request(
    path: str,
    *,
    content: str,
    overwrite: bool,
) -> CapabilityRequest[WriteTextParams]:
    return CapabilityRequest(
        identity=FILESYSTEM_WRITE_TEXT_IDENTITY,
        params=WriteTextParams(path=path, content=content, overwrite=overwrite),
    )
