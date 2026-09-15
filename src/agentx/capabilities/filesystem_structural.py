"""Governed bounded filesystem structural capabilities (AX-261–265).

The capabilities in this module execute only through the canonical
CapabilityExecutionLoop. They contain no permission engine or ActionGate and
therefore cannot authorize themselves. Structural request semantics are
conservative by construction:

* directory creation is create-only and never creates parents implicitly;
* file move and rename are regular-file only, no-replacement operations;
* rename additionally requires source and destination to share one parent;
* file deletion is explicitly destructive;
* directory deletion is empty-only and uses ``rmdir`` -- never recursive.

Every file move/rename is bounded by ``MAX_STRUCTURAL_FILE_BYTES`` so an
independent pre/post SHA-256 fingerprint can prove identity preservation
without unbounded reads. Deletions and directory creation are independently
verified from post-state. Native/API return is never treated as verification.

The landed AX-260 request-sensitive structural-risk classifier is consulted
from typed facts immediately before each mutation. Descriptor risk is a
conservative governance ceiling; request data, filenames and hostile strings
such as ``safe=true`` or ``risk=R0`` cannot lower it.
"""

from __future__ import annotations

import hashlib
import os
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
from agentx.capabilities.filesystem_structural_risk import (
    DeletionScope,
    FilesystemStructuralOperationKind,
    FilesystemStructuralRiskRequest,
    TargetState,
    classify_filesystem_structural_risk,
)
from agentx.core.errors import AgentXError, AgentXException, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskAssessment, assess_risk

__all__ = [
    "FILESYSTEM_CREATE_DIRECTORY_IDENTITY",
    "FILESYSTEM_DELETE_DIRECTORY_IDENTITY",
    "FILESYSTEM_DELETE_FILE_IDENTITY",
    "FILESYSTEM_MOVE_FILE_IDENTITY",
    "FILESYSTEM_RENAME_FILE_IDENTITY",
    "MAX_STRUCTURAL_FILE_BYTES",
    "CreateDirectoryParams",
    "DeleteDirectoryParams",
    "DeleteFileParams",
    "FilesystemCreateDirectoryCapability",
    "FilesystemDeleteDirectoryCapability",
    "FilesystemDeleteFileCapability",
    "FilesystemMoveFileCapability",
    "FilesystemRenameFileCapability",
    "MoveFileParams",
    "RenameFileParams",
    "create_directory_request",
    "delete_directory_request",
    "delete_file_request",
    "move_file_request",
    "rename_file_request",
]

MAX_STRUCTURAL_FILE_BYTES: Final[int] = 8 * 1024 * 1024
MAX_STRUCTURAL_PATH_LENGTH: Final[int] = 4_096
_CHUNK_BYTES: Final[int] = 128 * 1024

INVALID_INPUT_ERROR_CODE: Final[str] = "capabilities.filesystem.structural.invalid_input"
NOT_FOUND_ERROR_CODE: Final[str] = "capabilities.filesystem.structural.not_found"
COLLISION_ERROR_CODE: Final[str] = "capabilities.filesystem.structural.collision"
UNSUPPORTED_TARGET_ERROR_CODE: Final[str] = "capabilities.filesystem.structural.unsupported_target"
PARENT_MISSING_ERROR_CODE: Final[str] = "capabilities.filesystem.structural.parent_missing"
FILE_TOO_LARGE_ERROR_CODE: Final[str] = "capabilities.filesystem.structural.file_too_large"
DIRECTORY_NOT_EMPTY_ERROR_CODE: Final[str] = (
    "capabilities.filesystem.structural.directory_not_empty"
)
CROSS_PARENT_RENAME_ERROR_CODE: Final[str] = (
    "capabilities.filesystem.structural.cross_parent_rename"
)
OS_ERROR_CODE: Final[str] = "capabilities.filesystem.structural.os_error"
VERIFICATION_ERROR_CODE: Final[str] = "capabilities.filesystem.structural.verification_failed"
RISK_INVARIANT_ERROR_CODE: Final[str] = "capabilities.filesystem.structural.risk_invariant"

FILESYSTEM_CREATE_DIRECTORY_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("filesystem.create_directory"),
    version=CapabilityVersion(1, 0, 0),
)
FILESYSTEM_MOVE_FILE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("filesystem.move_file"),
    version=CapabilityVersion(1, 0, 0),
)
FILESYSTEM_RENAME_FILE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("filesystem.rename_file"),
    version=CapabilityVersion(1, 0, 0),
)
FILESYSTEM_DELETE_FILE_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("filesystem.delete_file"),
    version=CapabilityVersion(1, 0, 0),
)
FILESYSTEM_DELETE_DIRECTORY_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("filesystem.delete_directory"),
    version=CapabilityVersion(1, 0, 0),
)


def _error(
    code: str,
    message: str,
    category: ErrorCategory,
    *,
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


def _raise_validation(code: str, message: str) -> NoReturn:
    raise AgentXException(_error(code, message, ErrorCategory.VALIDATION))


def _path(value: object, *, field_name: str) -> Path:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a str")
    if not value:
        _raise_validation(INVALID_INPUT_ERROR_CODE, f"{field_name} must not be empty")
    if value != value.strip():
        _raise_validation(INVALID_INPUT_ERROR_CODE, f"{field_name} must be trimmed")
    if len(value) > MAX_STRUCTURAL_PATH_LENGTH:
        _raise_validation(INVALID_INPUT_ERROR_CODE, f"{field_name} exceeds the governed bound")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _raise_validation(INVALID_INPUT_ERROR_CODE, f"{field_name} contains control characters")
    path = Path(value)
    if not path.is_absolute():
        _raise_validation(INVALID_INPUT_ERROR_CODE, f"{field_name} must be absolute")
    if ".." in path.parts or str(path) != value:
        _raise_validation(
            INVALID_INPUT_ERROR_CODE,
            f"{field_name} must already be lexically normalized for this host",
        )
    return path


@dataclass(frozen=True, slots=True)
class CreateDirectoryParams(CapabilityParams):
    path: str

    def __post_init__(self) -> None:
        _path(self.path, field_name="path")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"path": self.path}


@dataclass(frozen=True, slots=True)
class MoveFileParams(CapabilityParams):
    source_path: str
    destination_path: str

    def __post_init__(self) -> None:
        source = _path(self.source_path, field_name="source_path")
        destination = _path(self.destination_path, field_name="destination_path")
        if source == destination:
            _raise_validation(INVALID_INPUT_ERROR_CODE, "source and destination must differ")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"source_path": self.source_path, "destination_path": self.destination_path}


@dataclass(frozen=True, slots=True)
class RenameFileParams(CapabilityParams):
    source_path: str
    destination_path: str

    def __post_init__(self) -> None:
        source = _path(self.source_path, field_name="source_path")
        destination = _path(self.destination_path, field_name="destination_path")
        if source == destination:
            _raise_validation(INVALID_INPUT_ERROR_CODE, "source and destination must differ")
        if source.parent != destination.parent:
            _raise_validation(
                CROSS_PARENT_RENAME_ERROR_CODE,
                "rename requires source and destination to share one parent directory",
            )

    def to_dict(self) -> dict[str, JsonValue]:
        return {"source_path": self.source_path, "destination_path": self.destination_path}


@dataclass(frozen=True, slots=True)
class DeleteFileParams(CapabilityParams):
    path: str

    def __post_init__(self) -> None:
        _path(self.path, field_name="path")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"path": self.path}


@dataclass(frozen=True, slots=True)
class DeleteDirectoryParams(CapabilityParams):
    path: str

    def __post_init__(self) -> None:
        _path(self.path, field_name="path")

    def to_dict(self) -> dict[str, JsonValue]:
        return {"path": self.path}


_PRECONDITION_PARENT: Final[CapabilityPrecondition] = CapabilityPrecondition(
    name="filesystem.parent_exists",
    description="The explicit target parent must already exist; parents are never created implicitly.",
)
_PRECONDITION_NO_REPLACE: Final[CapabilityPrecondition] = CapabilityPrecondition(
    name="filesystem.destination_absent",
    description="The destination must be absent. Move and rename never replace an existing object.",
)
_PRECONDITION_REGULAR_FILE: Final[CapabilityPrecondition] = CapabilityPrecondition(
    name="filesystem.regular_file",
    description=(
        "The explicit source/target must be a regular file, not a directory, symlink or special file."
    ),
)
_PRECONDITION_EMPTY_DIRECTORY: Final[CapabilityPrecondition] = CapabilityPrecondition(
    name="filesystem.empty_directory",
    description="Directory deletion is allowed only for an independently observed empty directory.",
)

_CREATE_RISK: Final[RiskAssessment] = assess_risk(
    read_only=False,
    modifies_state=True,
    reversible=True,
    external_effect=False,
)
_MOVE_RISK: Final[RiskAssessment] = assess_risk(
    read_only=False,
    modifies_state=True,
    reversible=True,
    external_effect=False,
)
_DELETE_FILE_RISK: Final[RiskAssessment] = assess_risk(
    read_only=False,
    modifies_state=True,
    reversible=False,
    external_effect=False,
    destructive=True,
)
_DELETE_DIRECTORY_RISK: Final[RiskAssessment] = assess_risk(
    read_only=False,
    modifies_state=True,
    reversible=False,
    external_effect=False,
    destructive=True,
)


def _descriptor(
    identity: CapabilityIdentity,
    description: str,
    *,
    permission: Permission,
    risk: RiskAssessment,
    preconditions: tuple[CapabilityPrecondition, ...],
    machine_actions: int,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        identity=identity,
        description=description,
        scope=CapabilityScope(platform=CapabilityPlatform.ANY),
        required_permissions=frozenset({permission}),
        risk_assessment=risk,
        preconditions=preconditions,
        rollback=RollbackDeclaration(
            support=RollbackSupport.UNSUPPORTED,
            detail="AX-261–265 retain no hidden backup; verification reports the resulting state.",
        ),
        estimate=ResourceEstimate(
            wall_clock=timedelta(seconds=2),
            machine_actions=machine_actions,
            external_cost=Decimal("0"),
        ),
    )


_CREATE_DIRECTORY_DESCRIPTOR: Final = _descriptor(
    FILESYSTEM_CREATE_DIRECTORY_IDENTITY,
    "Create one explicit directory only when absent, without creating parents.",
    permission=Permission.WRITE,
    risk=_CREATE_RISK,
    preconditions=(_PRECONDITION_PARENT, _PRECONDITION_NO_REPLACE),
    machine_actions=2,
)
_MOVE_FILE_DESCRIPTOR: Final = _descriptor(
    FILESYSTEM_MOVE_FILE_IDENTITY,
    "Move one bounded regular file to an absent destination without replacement.",
    permission=Permission.WRITE,
    risk=_MOVE_RISK,
    preconditions=(_PRECONDITION_REGULAR_FILE, _PRECONDITION_PARENT, _PRECONDITION_NO_REPLACE),
    machine_actions=3,
)
_RENAME_FILE_DESCRIPTOR: Final = _descriptor(
    FILESYSTEM_RENAME_FILE_IDENTITY,
    "Rename one bounded regular file within its parent to an absent destination.",
    permission=Permission.WRITE,
    risk=_MOVE_RISK,
    preconditions=(_PRECONDITION_REGULAR_FILE, _PRECONDITION_NO_REPLACE),
    machine_actions=3,
)
_DELETE_FILE_DESCRIPTOR: Final = _descriptor(
    FILESYSTEM_DELETE_FILE_IDENTITY,
    "Delete one explicit bounded regular file and independently confirm absence.",
    permission=Permission.DESTRUCTIVE,
    risk=_DELETE_FILE_RISK,
    preconditions=(_PRECONDITION_REGULAR_FILE,),
    machine_actions=3,
)
_DELETE_DIRECTORY_DESCRIPTOR: Final = _descriptor(
    FILESYSTEM_DELETE_DIRECTORY_IDENTITY,
    "Delete one explicit empty directory with non-recursive rmdir semantics only.",
    permission=Permission.DESTRUCTIVE,
    risk=_DELETE_DIRECTORY_RISK,
    preconditions=(_PRECONDITION_EMPTY_DIRECTORY,),
    machine_actions=3,
)


@dataclass(frozen=True, slots=True)
class _Fingerprint:
    byte_count: int
    sha256: str


def _exists(path: Path) -> Result[bool, AgentXError]:
    try:
        path.lstat()
    except FileNotFoundError:
        return Result.success(False)
    except PermissionError as exc:
        return Result.failure(
            _error(
                OS_ERROR_CODE,
                "operating system denied structural path inspection",
                ErrorCategory.PERMISSION,
                cause=exc,
            )
        )
    except OSError as exc:
        return Result.failure(
            _error(
                OS_ERROR_CODE,
                "operating system could not inspect structural path",
                ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )
    return Result.success(True)


def _parent_ready(path: Path) -> Result[None, AgentXError]:
    try:
        info = path.parent.stat()
    except (FileNotFoundError, NotADirectoryError) as exc:
        return Result.failure(
            _error(
                PARENT_MISSING_ERROR_CODE,
                "destination parent does not exist",
                ErrorCategory.PRECONDITION,
                cause=exc,
            )
        )
    except PermissionError as exc:
        return Result.failure(
            _error(OS_ERROR_CODE, "parent inspection denied", ErrorCategory.PERMISSION, cause=exc)
        )
    except OSError as exc:
        return Result.failure(
            _error(
                OS_ERROR_CODE,
                "parent inspection failed",
                ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )
    if not stat.S_ISDIR(info.st_mode):
        return Result.failure(
            _error(
                PARENT_MISSING_ERROR_CODE,
                "destination parent is not a directory",
                ErrorCategory.PRECONDITION,
            )
        )
    return Result.success(None)


def _fingerprint(path: Path) -> Result[_Fingerprint, AgentXError]:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        return Result.failure(
            _error(
                NOT_FOUND_ERROR_CODE,
                "regular file does not exist",
                ErrorCategory.NOT_FOUND,
                cause=exc,
            )
        )
    except PermissionError as exc:
        return Result.failure(
            _error(
                OS_ERROR_CODE, "regular-file inspection denied", ErrorCategory.PERMISSION, cause=exc
            )
        )
    except OSError as exc:
        return Result.failure(
            _error(
                OS_ERROR_CODE,
                "regular-file inspection failed",
                ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )
    if not stat.S_ISREG(info.st_mode):
        return Result.failure(
            _error(
                UNSUPPORTED_TARGET_ERROR_CODE,
                "structural file operation requires a regular file",
                ErrorCategory.VALIDATION,
            )
        )
    if info.st_size > MAX_STRUCTURAL_FILE_BYTES:
        return Result.failure(
            _error(
                FILE_TOO_LARGE_ERROR_CODE,
                "file exceeds the bounded independent-verification size",
                ErrorCategory.RESOURCE,
                details={"max_bytes": MAX_STRUCTURAL_FILE_BYTES},
            )
        )
    digest = hashlib.sha256()
    consumed = 0
    try:
        with path.open("rb") as handle:
            while True:
                block = handle.read(_CHUNK_BYTES)
                if not block:
                    break
                consumed += len(block)
                if consumed > MAX_STRUCTURAL_FILE_BYTES:
                    return Result.failure(
                        _error(
                            FILE_TOO_LARGE_ERROR_CODE,
                            "file grew beyond the verification bound while being read",
                            ErrorCategory.RESOURCE,
                            details={"max_bytes": MAX_STRUCTURAL_FILE_BYTES},
                        )
                    )
                digest.update(block)
    except (FileNotFoundError, IsADirectoryError) as exc:
        return Result.failure(
            _error(
                OS_ERROR_CODE,
                "file changed type or disappeared during fingerprinting",
                ErrorCategory.CONFLICT,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )
    except PermissionError as exc:
        return Result.failure(
            _error(
                OS_ERROR_CODE, "file fingerprint read denied", ErrorCategory.PERMISSION, cause=exc
            )
        )
    except OSError as exc:
        return Result.failure(
            _error(
                OS_ERROR_CODE,
                "file fingerprint read failed",
                ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )
    if consumed != info.st_size:
        return Result.failure(
            _error(
                OS_ERROR_CODE,
                "file size changed during fingerprinting",
                ErrorCategory.CONFLICT,
                retryability=Retryability.UNKNOWN,
            )
        )
    return Result.success(_Fingerprint(byte_count=consumed, sha256=digest.hexdigest()))


def _directory_empty(path: Path) -> Result[bool, AgentXError]:
    try:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            return Result.failure(
                _error(
                    UNSUPPORTED_TARGET_ERROR_CODE,
                    "target must be a real directory, not a symlink or other object",
                    ErrorCategory.VALIDATION,
                )
            )
        with os.scandir(path) as entries:
            return Result.success(next(entries, None) is None)
    except FileNotFoundError as exc:
        return Result.failure(
            _error(
                NOT_FOUND_ERROR_CODE, "directory does not exist", ErrorCategory.NOT_FOUND, cause=exc
            )
        )
    except PermissionError as exc:
        return Result.failure(
            _error(
                OS_ERROR_CODE, "directory inspection denied", ErrorCategory.PERMISSION, cause=exc
            )
        )
    except OSError as exc:
        return Result.failure(
            _error(
                OS_ERROR_CODE,
                "directory inspection failed",
                ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            )
        )


def _risk_guard(
    request: FilesystemStructuralRiskRequest, descriptor: CapabilityDescriptor
) -> AgentXError | None:
    result = classify_filesystem_structural_risk(request)
    if (
        result.assessment.effective_level.severity
        > descriptor.risk_assessment.effective_level.severity
    ):
        return _error(
            RISK_INVARIANT_ERROR_CODE,
            "request-sensitive structural risk exceeds the capability governance ceiling",
            ErrorCategory.INTERNAL,
            details={
                "request_risk": result.assessment.effective_level.value,
                "descriptor_risk": descriptor.risk_assessment.effective_level.value,
            },
        )
    return None


def _failed(error: AgentXError, summary: str) -> ExecutionResult:
    return ExecutionResult(
        succeeded=False,
        message=error.message,
        observation=CapabilityObservation(summary=summary, data={"error": error.to_dict()}),
    )


def _cancelled(operation: str) -> ExecutionResult:
    return _failed(
        _error(
            "capabilities.filesystem.structural.cancelled",
            f"{operation} cancelled before filesystem access",
            ErrorCategory.CANCELLED,
            retryability=Retryability.UNKNOWN,
        ),
        f"{operation} cancelled",
    )


def _verify_fail(detail: str) -> VerificationResult:
    return VerificationResult(passed=False, detail=f"{VERIFICATION_ERROR_CODE}: {detail}")


def _move_or_rename_execute(
    *,
    source: Path,
    destination: Path,
    descriptor: CapabilityDescriptor,
    operation: FilesystemStructuralOperationKind,
) -> ExecutionResult:
    parent = _parent_ready(destination)
    if parent.is_failure:
        return _failed(parent.unwrap_error(), "structural destination parent precondition failed")
    destination_exists = _exists(destination)
    if destination_exists.is_failure:
        return _failed(destination_exists.unwrap_error(), "destination inspection failed")
    if destination_exists.unwrap():
        return _failed(
            _error(
                COLLISION_ERROR_CODE,
                "destination already exists; replacement is prohibited",
                ErrorCategory.CONFLICT,
            ),
            "structural destination collision",
        )
    fingerprint = _fingerprint(source)
    if fingerprint.is_failure:
        return _failed(fingerprint.unwrap_error(), "source fingerprint failed")
    before = fingerprint.unwrap()
    risk_error = _risk_guard(
        FilesystemStructuralRiskRequest(
            operation=operation,
            source_path=str(source),
            destination_path=str(destination),
            source_state=TargetState.PRESENT,
            destination_state=TargetState.ABSENT,
        ),
        descriptor,
    )
    if risk_error is not None:
        return _failed(risk_error, "structural risk invariant failed")
    try:
        source.rename(destination)
    except FileExistsError as exc:
        return _failed(
            _error(
                COLLISION_ERROR_CODE,
                "destination appeared before mutation; replacement refused",
                ErrorCategory.CONFLICT,
                cause=exc,
            ),
            "structural destination collision",
        )
    except FileNotFoundError as exc:
        return _failed(
            _error(
                NOT_FOUND_ERROR_CODE,
                "source or destination parent disappeared before mutation",
                ErrorCategory.CONFLICT,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            ),
            "structural source disappeared",
        )
    except PermissionError as exc:
        return _failed(
            _error(
                OS_ERROR_CODE,
                "operating system denied structural mutation",
                ErrorCategory.PERMISSION,
                cause=exc,
            ),
            "structural mutation denied",
        )
    except OSError as exc:
        return _failed(
            _error(
                OS_ERROR_CODE,
                "operating system could not complete structural mutation",
                ErrorCategory.EXECUTION,
                retryability=Retryability.UNKNOWN,
                cause=exc,
            ),
            "structural mutation failed",
        )
    return ExecutionResult(
        succeeded=True,
        message=f"{operation.value} requested; independent verification required",
        observation=CapabilityObservation(
            summary=f"filesystem {operation.value} mutation completed (unverified)",
            data={"byte_count": before.byte_count, "sha256": before.sha256},
        ),
    )


def _move_or_rename_verify(
    *,
    source: Path,
    destination: Path,
    observation: CapabilityObservation,
) -> VerificationResult:
    data = observation.to_dict()["data"]
    if not isinstance(data, dict):
        return _verify_fail("mutation observation data is malformed")
    byte_count = data.get("byte_count")
    digest = data.get("sha256")
    if type(byte_count) is not int or not isinstance(digest, str):
        return _verify_fail("mutation observation lacks typed fingerprint evidence")
    source_exists = _exists(source)
    if source_exists.is_failure or source_exists.unwrap():
        return _verify_fail("source is not independently confirmed absent")
    destination_fingerprint = _fingerprint(destination)
    if destination_fingerprint.is_failure:
        return _verify_fail("destination could not be independently fingerprinted")
    current = destination_fingerprint.unwrap()
    if current.byte_count != byte_count or current.sha256 != digest:
        return _verify_fail("destination fingerprint does not match pre-mutation source")
    return VerificationResult(
        passed=True,
        detail="source absence and destination file identity independently verified",
    )


class FilesystemCreateDirectoryCapability:
    __slots__ = ()

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return _CREATE_DIRECTORY_DESCRIPTOR

    def execute(
        self, request: CapabilityRequest[CreateDirectoryParams], context: ExecutionContext
    ) -> ExecutionResult:
        if not isinstance(request.params, CreateDirectoryParams):
            raise TypeError("create_directory requires CreateDirectoryParams")
        if context.observe_stop().should_stop:
            return _cancelled("create_directory")
        path = _path(request.params.path, field_name="path")
        parent = _parent_ready(path)
        if parent.is_failure:
            return _failed(parent.unwrap_error(), "directory parent precondition failed")
        exists = _exists(path)
        if exists.is_failure:
            return _failed(exists.unwrap_error(), "directory target inspection failed")
        if exists.unwrap():
            return _failed(
                _error(
                    COLLISION_ERROR_CODE, "directory target already exists", ErrorCategory.CONFLICT
                ),
                "directory creation collision",
            )
        risk_error = _risk_guard(
            FilesystemStructuralRiskRequest(
                operation=FilesystemStructuralOperationKind.CREATE_DIRECTORY,
                source_path=None,
                destination_path=str(path),
                destination_state=TargetState.ABSENT,
            ),
            self.descriptor,
        )
        if risk_error is not None:
            return _failed(risk_error, "directory creation risk invariant failed")
        try:
            path.mkdir(parents=False, exist_ok=False)
        except FileExistsError as exc:
            return _failed(
                _error(
                    COLLISION_ERROR_CODE,
                    "directory target appeared before creation",
                    ErrorCategory.CONFLICT,
                    cause=exc,
                ),
                "directory creation collision",
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            return _failed(
                _error(
                    PARENT_MISSING_ERROR_CODE,
                    "directory parent disappeared before creation",
                    ErrorCategory.PRECONDITION,
                    cause=exc,
                ),
                "directory parent disappeared",
            )
        except PermissionError as exc:
            return _failed(
                _error(
                    OS_ERROR_CODE, "directory creation denied", ErrorCategory.PERMISSION, cause=exc
                ),
                "directory creation denied",
            )
        except OSError as exc:
            return _failed(
                _error(
                    OS_ERROR_CODE,
                    "directory creation failed",
                    ErrorCategory.EXECUTION,
                    retryability=Retryability.UNKNOWN,
                    cause=exc,
                ),
                "directory creation failed",
            )
        return ExecutionResult(
            succeeded=True,
            message="directory creation requested; independent verification required",
            observation=CapabilityObservation(
                summary="filesystem directory creation completed (unverified)",
                data={"created": True},
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[CreateDirectoryParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, CreateDirectoryParams):
            raise TypeError("create_directory requires CreateDirectoryParams")
        if context.observe_stop().should_stop:
            return _verify_fail("verification stop condition is active")
        if not isinstance(observation, CapabilityObservation):
            raise TypeError("observation must be CapabilityObservation")
        path = _path(request.params.path, field_name="path")
        empty = _directory_empty(path)
        if empty.is_failure or not empty.unwrap():
            return _verify_fail(
                "created directory is not independently confirmed as an empty directory"
            )
        return VerificationResult(
            passed=True, detail="empty directory existence independently verified"
        )


class FilesystemMoveFileCapability:
    __slots__ = ()

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return _MOVE_FILE_DESCRIPTOR

    def execute(
        self, request: CapabilityRequest[MoveFileParams], context: ExecutionContext
    ) -> ExecutionResult:
        if not isinstance(request.params, MoveFileParams):
            raise TypeError("move_file requires MoveFileParams")
        if context.observe_stop().should_stop:
            return _cancelled("move_file")
        return _move_or_rename_execute(
            source=_path(request.params.source_path, field_name="source_path"),
            destination=_path(request.params.destination_path, field_name="destination_path"),
            descriptor=self.descriptor,
            operation=FilesystemStructuralOperationKind.MOVE,
        )

    def verify(
        self,
        request: CapabilityRequest[MoveFileParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, MoveFileParams):
            raise TypeError("move_file requires MoveFileParams")
        if context.observe_stop().should_stop:
            return _verify_fail("verification stop condition is active")
        if not isinstance(observation, CapabilityObservation):
            raise TypeError("observation must be CapabilityObservation")
        return _move_or_rename_verify(
            source=_path(request.params.source_path, field_name="source_path"),
            destination=_path(request.params.destination_path, field_name="destination_path"),
            observation=observation,
        )


class FilesystemRenameFileCapability:
    __slots__ = ()

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return _RENAME_FILE_DESCRIPTOR

    def execute(
        self, request: CapabilityRequest[RenameFileParams], context: ExecutionContext
    ) -> ExecutionResult:
        if not isinstance(request.params, RenameFileParams):
            raise TypeError("rename_file requires RenameFileParams")
        if context.observe_stop().should_stop:
            return _cancelled("rename_file")
        return _move_or_rename_execute(
            source=_path(request.params.source_path, field_name="source_path"),
            destination=_path(request.params.destination_path, field_name="destination_path"),
            descriptor=self.descriptor,
            operation=FilesystemStructuralOperationKind.RENAME,
        )

    def verify(
        self,
        request: CapabilityRequest[RenameFileParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, RenameFileParams):
            raise TypeError("rename_file requires RenameFileParams")
        if context.observe_stop().should_stop:
            return _verify_fail("verification stop condition is active")
        if not isinstance(observation, CapabilityObservation):
            raise TypeError("observation must be CapabilityObservation")
        return _move_or_rename_verify(
            source=_path(request.params.source_path, field_name="source_path"),
            destination=_path(request.params.destination_path, field_name="destination_path"),
            observation=observation,
        )


class FilesystemDeleteFileCapability:
    __slots__ = ()

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return _DELETE_FILE_DESCRIPTOR

    def execute(
        self, request: CapabilityRequest[DeleteFileParams], context: ExecutionContext
    ) -> ExecutionResult:
        if not isinstance(request.params, DeleteFileParams):
            raise TypeError("delete_file requires DeleteFileParams")
        if context.observe_stop().should_stop:
            return _cancelled("delete_file")
        path = _path(request.params.path, field_name="path")
        fingerprint = _fingerprint(path)
        if fingerprint.is_failure:
            return _failed(fingerprint.unwrap_error(), "delete_file preflight failed")
        before = fingerprint.unwrap()
        risk_error = _risk_guard(
            FilesystemStructuralRiskRequest(
                operation=FilesystemStructuralOperationKind.DELETE,
                source_path=None,
                destination_path=str(path),
                destination_state=TargetState.PRESENT,
                deletion_scope=DeletionScope.NON_EMPTY,
            ),
            self.descriptor,
        )
        if risk_error is not None:
            return _failed(risk_error, "delete_file risk invariant failed")
        try:
            path.unlink()
        except FileNotFoundError as exc:
            return _failed(
                _error(
                    NOT_FOUND_ERROR_CODE,
                    "file disappeared before deletion",
                    ErrorCategory.CONFLICT,
                    retryability=Retryability.UNKNOWN,
                    cause=exc,
                ),
                "delete_file target disappeared",
            )
        except PermissionError as exc:
            return _failed(
                _error(OS_ERROR_CODE, "file deletion denied", ErrorCategory.PERMISSION, cause=exc),
                "delete_file denied",
            )
        except OSError as exc:
            return _failed(
                _error(
                    OS_ERROR_CODE,
                    "file deletion failed",
                    ErrorCategory.EXECUTION,
                    retryability=Retryability.UNKNOWN,
                    cause=exc,
                ),
                "delete_file failed",
            )
        return ExecutionResult(
            succeeded=True,
            message="file deletion requested; independent absence verification required",
            observation=CapabilityObservation(
                summary="filesystem file deletion completed (unverified)",
                data={"deleted_byte_count": before.byte_count, "deleted_sha256": before.sha256},
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[DeleteFileParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, DeleteFileParams):
            raise TypeError("delete_file requires DeleteFileParams")
        if context.observe_stop().should_stop:
            return _verify_fail("verification stop condition is active")
        if not isinstance(observation, CapabilityObservation):
            raise TypeError("observation must be CapabilityObservation")
        exists = _exists(_path(request.params.path, field_name="path"))
        if exists.is_failure or exists.unwrap():
            return _verify_fail("deleted file path is not independently confirmed absent")
        return VerificationResult(
            passed=True, detail="deleted file path independently confirmed absent"
        )


class FilesystemDeleteDirectoryCapability:
    __slots__ = ()

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return _DELETE_DIRECTORY_DESCRIPTOR

    def execute(
        self, request: CapabilityRequest[DeleteDirectoryParams], context: ExecutionContext
    ) -> ExecutionResult:
        if not isinstance(request.params, DeleteDirectoryParams):
            raise TypeError("delete_directory requires DeleteDirectoryParams")
        if context.observe_stop().should_stop:
            return _cancelled("delete_directory")
        path = _path(request.params.path, field_name="path")
        empty = _directory_empty(path)
        if empty.is_failure:
            return _failed(empty.unwrap_error(), "delete_directory preflight failed")
        if not empty.unwrap():
            return _failed(
                _error(
                    DIRECTORY_NOT_EMPTY_ERROR_CODE,
                    "recursive directory deletion is prohibited; target is not empty",
                    ErrorCategory.PRECONDITION,
                ),
                "delete_directory refused non-empty target",
            )
        risk_error = _risk_guard(
            FilesystemStructuralRiskRequest(
                operation=FilesystemStructuralOperationKind.DELETE,
                source_path=None,
                destination_path=str(path),
                destination_state=TargetState.PRESENT,
                deletion_scope=DeletionScope.EMPTY,
            ),
            self.descriptor,
        )
        if risk_error is not None:
            return _failed(risk_error, "delete_directory risk invariant failed")
        try:
            path.rmdir()
        except FileNotFoundError as exc:
            return _failed(
                _error(
                    NOT_FOUND_ERROR_CODE,
                    "directory disappeared before deletion",
                    ErrorCategory.CONFLICT,
                    retryability=Retryability.UNKNOWN,
                    cause=exc,
                ),
                "delete_directory target disappeared",
            )
        except OSError as exc:
            return _failed(
                _error(
                    DIRECTORY_NOT_EMPTY_ERROR_CODE if path.exists() else OS_ERROR_CODE,
                    "directory deletion failed; target may have changed after preflight",
                    ErrorCategory.CONFLICT,
                    retryability=Retryability.UNKNOWN,
                    cause=exc,
                ),
                "delete_directory failed closed",
            )
        return ExecutionResult(
            succeeded=True,
            message="empty directory deletion requested; independent absence verification required",
            observation=CapabilityObservation(
                summary="filesystem empty directory deletion completed (unverified)",
                data={"recursive": False},
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[DeleteDirectoryParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        if not isinstance(request.params, DeleteDirectoryParams):
            raise TypeError("delete_directory requires DeleteDirectoryParams")
        if context.observe_stop().should_stop:
            return _verify_fail("verification stop condition is active")
        if not isinstance(observation, CapabilityObservation):
            raise TypeError("observation must be CapabilityObservation")
        exists = _exists(_path(request.params.path, field_name="path"))
        if exists.is_failure or exists.unwrap():
            return _verify_fail("deleted directory path is not independently confirmed absent")
        return VerificationResult(
            passed=True,
            detail="empty directory deletion independently confirmed by path absence",
        )


def create_directory_request(path: str) -> CapabilityRequest[CreateDirectoryParams]:
    return CapabilityRequest(
        identity=FILESYSTEM_CREATE_DIRECTORY_IDENTITY,
        params=CreateDirectoryParams(path=path),
    )


def move_file_request(source_path: str, destination_path: str) -> CapabilityRequest[MoveFileParams]:
    return CapabilityRequest(
        identity=FILESYSTEM_MOVE_FILE_IDENTITY,
        params=MoveFileParams(source_path=source_path, destination_path=destination_path),
    )


def rename_file_request(
    source_path: str, destination_path: str
) -> CapabilityRequest[RenameFileParams]:
    return CapabilityRequest(
        identity=FILESYSTEM_RENAME_FILE_IDENTITY,
        params=RenameFileParams(source_path=source_path, destination_path=destination_path),
    )


def delete_file_request(path: str) -> CapabilityRequest[DeleteFileParams]:
    return CapabilityRequest(
        identity=FILESYSTEM_DELETE_FILE_IDENTITY,
        params=DeleteFileParams(path=path),
    )


def delete_directory_request(path: str) -> CapabilityRequest[DeleteDirectoryParams]:
    return CapabilityRequest(
        identity=FILESYSTEM_DELETE_DIRECTORY_IDENTITY,
        params=DeleteDirectoryParams(path=path),
    )
