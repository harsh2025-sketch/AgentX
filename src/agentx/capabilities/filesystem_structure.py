"""Governed filesystem structural operations for AgentX (M7.05).

This module is the smallest production-quality **structural** foundation for
deterministic, bounded, governed filesystem operations needed by real desktop
workflows: reading structured metadata about a path, listing a directory,
creating a single directory, and moving a path. It deliberately does **not**
read or write file *contents* (that is Worker-01's separate
``agentx.capabilities.filesystem`` surface, on which this module never
depends).

Contract inventory
------------------

- :class:`PathKind` — the closed vocabulary for what a path *is* as detected
  by ``lstat``: file, directory, symbolic link, or other.
- :class:`PathStat` — bounded, deterministic metadata for one path.
- :class:`DirectoryEntry` — minimal structured metadata for one directory
  entry (an untrusted name, never an authority).
- :class:`DirectoryListing` — a bounded, deterministically ordered result of
  one non-recursive directory read.
- :class:`CreateDirectoryResult` / :class:`MoveResult` — explicit evidence of
  one bounded mutation.
- module-level operations :func:`stat_path`, :func:`list_directory`,
  :func:`create_directory` and :func:`move_path` — side-effecting functions
  that return ``Result`` values and never raise for expected operational
  outcomes.
- :class:`StatPathCapability`, :class:`ListDirectoryCapability`,
  :class:`CreateDirectoryCapability` and :class:`MovePathCapability` — each
  operation wrapped as an ordinary canonical
  :class:`~agentx.capabilities.abi.Capability` so it can reach the governed
  A1.10 closed-loop execution path through the A1.09 registry like any other
  capability. Read operations (``stat``/``list``) declare ``READ`` permission
  and a read-only R0 risk assessment; mutations (``mkdir``/``move``) declare
  ``WRITE`` permission and a conservative R2 (state-modifying) risk
  assessment.

Scope and non-goals
-------------------

Structural operations only. There is no file-content read or write, no
delete, no recursive directory copy, no recursive traversal, no watch, no
index, no search, no backup, no rollback orchestration, no shell, no
``subprocess``, and no dynamic code. Directory trees are never traversed: a
listing reads one directory and returns at most a bounded number of its
immediate entries.

Path rules
----------

Every operation requires an explicit **absolute** path. Relative paths,
embedded NUL characters, empty paths, and unbounded path strings are rejected
with a ``validation`` ``AgentXError``. Paths are not shell-expanded, do not
contain environment-variable expansion, wildcards or globs, and ``~`` is not
reinterpreted. ``..`` and ``.`` components are handled only by the documented
``os.path`` semantics of the host platform; this module never hand-collapses
or reinterprets path components. Deterministic ordering of a listing uses the
entry name only.

Symbolic-link / reparse-point handling
--------------------------------------

- **Reads** use ``lstat`` (never following the final symlink target), so
  ``stat``/``list`` report the link itself as ``kind=SYMLINK`` and never
  blindly resolve a link to an unintended location.
- **Mutations** never write *through* a final symlink: creating a directory
  refuses when the target already exists (including when it is a symlink),
  and moving refuses an overwrite through a symlink and refuses to merge into
  an existing directory. Ancestor directories are resolved by the OS according
  to documented platform path semantics; this module does not expand them and
  does not grant any permission based on what an ancestor resolves to.

Mutation conflict / cross-volume behaviour
------------------------------------------

``create_directory`` creates exactly one directory (no ``parents=True``) and
fails closed if the target already exists. ``move_path`` takes an explicit
absolute source and destination, never a pattern, and fails closed on any
destination conflict unless the caller explicitly sets ``overwrite=True`` and
the overwrite is a supported file-over-file replacement. Moves never silently
fall back to copy+delete: a cross-volume move is reported as an explicit
unsupported/``resource`` error rather than degrading to an unverified copy.

Verification
------------

``execute`` performs exactly one operation and returns observation evidence.
``verify`` independently confirms the postcondition against the real
filesystem (fresh ``lstat`` reads) for mutations, and checks observation
evidence for structural consistency plus a fresh ``lstat`` cross-check for
``stat``. A mutation is never reported verified on the strength of the
execution result alone; success requires independent filesystem evidence.

Owner: M7.05. Belongs to ``agentx.capabilities``; imports only the standard
library and the canonical ``agentx.core`` / ``agentx.kernel`` contracts plus
the canonical capability ABI. It performs no registry wiring, no shell, no
``subprocess``, no dynamic imports, and no Worker-01 dependency.
"""

from __future__ import annotations

import os
import stat as stat_module
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

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
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskAssessment, assess_risk

__all__ = [
    "CREATE_DIRECTORY_IDENTITY",
    "DEFAULT_MAX_ENTRIES",
    "HARD_MAX_ENTRIES",
    "LIST_DIRECTORY_IDENTITY",
    "MAX_PATH_CHARS",
    "MOVE_PATH_IDENTITY",
    "STAT_PATH_IDENTITY",
    "CreateDirectoryCapability",
    "CreateDirectoryParams",
    "CreateDirectoryResult",
    "DirectoryEntry",
    "DirectoryListing",
    "ListDirectoryCapability",
    "ListDirectoryParams",
    "MovePathCapability",
    "MovePathParams",
    "MoveResult",
    "PathKind",
    "PathStat",
    "StatPathCapability",
    "StatPathParams",
    "create_directory",
    "create_directory_request",
    "list_directory",
    "list_directory_request",
    "move_path",
    "move_path_request",
    "stat_path",
    "stat_path_request",
]

# --------------------------------------------------------------------------
# Validation / resource bounds.
# --------------------------------------------------------------------------

#: Maximum accepted length of an explicit absolute path string.
MAX_PATH_CHARS: Final[int] = 4096
#: Default maximum number of entries returned by one bounded listing.
DEFAULT_MAX_ENTRIES: Final[int] = 100
#: Hard upper ceiling for ``max_entries`` on a single listing.
HARD_MAX_ENTRIES: Final[int] = 1000

_PREFIX: Final[str] = "capabilities.filesystem_structure"

# --------------------------------------------------------------------------
# Canonical capability identities.
# --------------------------------------------------------------------------

STAT_PATH_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("filesystem.structure.stat"),
    version=CapabilityVersion(1, 0, 0),
)
LIST_DIRECTORY_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("filesystem.structure.list"),
    version=CapabilityVersion(1, 0, 0),
)
CREATE_DIRECTORY_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("filesystem.structure.create_directory"),
    version=CapabilityVersion(1, 0, 0),
)
MOVE_PATH_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("filesystem.structure.move"),
    version=CapabilityVersion(1, 0, 0),
)


def _error(
    *,
    code: str,
    message: str,
    category: ErrorCategory,
    details: dict[str, JsonValue] | None = None,
    retryability: Retryability = Retryability.NON_RETRYABLE,
    cause: BaseException | None = None,
) -> AgentXError:
    """Construct a canonical immutable error value for this capability."""
    return AgentXError(
        code=code,
        message=message,
        category=category,
        retryability=retryability,
        details=details,
        cause=cause,
    )


def _invalid_path(reason: str, *, path: str) -> AgentXError:
    return _error(
        code=f"{_PREFIX}.invalid_path",
        message=reason,
        category=ErrorCategory.VALIDATION,
        details={"path": path},
    )


# --------------------------------------------------------------------------
# Path validation.
# --------------------------------------------------------------------------


def _validate_absolute_path(path: object, *, field_name: str) -> Result[str, AgentXError]:
    """Validate an explicit absolute path and return it, or a validation error.

    Rejects: non-``str``, empty, embedded NUL, unbounded length, and paths
    that are not absolute under the host platform's documented ``os.path``
    semantics. No shell expansion, environment expansion, wildcard handling or
    ``~`` reinterpretation ever occurs.
    """
    if not isinstance(path, str):
        return Result.failure(
            _error(
                code=f"{_PREFIX}.invalid_path",
                message=f"{field_name} must be a string, got {type(path).__name__}",
                category=ErrorCategory.VALIDATION,
            )
        )
    if not path:
        return Result.failure(_invalid_path(f"{field_name} must not be empty", path=path))
    if "\x00" in path:
        return Result.failure(_invalid_path(f"{field_name} must not contain NUL", path=path))
    if len(path) > MAX_PATH_CHARS:
        return Result.failure(
            _invalid_path(f"{field_name} exceeds the {MAX_PATH_CHARS}-character bound", path=path)
        )
    if not Path(path).is_absolute():
        return Result.failure(_invalid_path(f"{field_name} must be an absolute path", path=path))
    return Result.success(path)


def _require_entries_bound(max_entries: int) -> Result[int, AgentXError]:
    if type(max_entries) is not int:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.invalid_max_entries",
                message=f"max_entries must be an int, got {type(max_entries).__name__}",
                category=ErrorCategory.VALIDATION,
            )
        )
    if max_entries < 1:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.invalid_max_entries",
                message="max_entries must be at least 1",
                category=ErrorCategory.VALIDATION,
            )
        )
    if max_entries > HARD_MAX_ENTRIES:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.invalid_max_entries",
                message=f"max_entries must not exceed {HARD_MAX_ENTRIES}",
                category=ErrorCategory.VALIDATION,
                details={"hard_max_entries": HARD_MAX_ENTRIES},
            )
        )
    return Result.success(max_entries)


# --------------------------------------------------------------------------
# Kind classification.
# --------------------------------------------------------------------------


class PathKind(StrEnum):
    """Closed vocabulary for what a path is, as detected by ``lstat``.

    ``SYMLINK`` means the final path component is itself a symbolic link (its
    target is not resolved). ``OTHER`` is every remaining safe-detected kind
    (socket, fifo, device, and so on).
    """

    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"


def _kind_from_mode(mode: int) -> PathKind:
    if stat_module.S_ISLNK(mode):
        return PathKind.SYMLINK
    if stat_module.S_ISDIR(mode):
        return PathKind.DIRECTORY
    if stat_module.S_ISREG(mode):
        return PathKind.FILE
    return PathKind.OTHER


def _lstat_kind(path: str) -> PathKind | None:
    """Return the ``lstat`` kind of ``path`` or ``None`` when it does not exist.

    Never follows a final symlink and never reads file content. Returns
    ``None`` only for a genuinely absent path; other failures propagate to the
    caller through explicit checks rather than being guessed at here.
    """
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return None
    return _kind_from_mode(mode)


# --------------------------------------------------------------------------
# PathStat / stat.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PathStat:
    """Bounded structured metadata for one path (``stat_path`` result).

    ``size_bytes`` is reported for regular files only. Existence is always
    explicit via ``exists``; a missing path yields ``exists=False`` with
    ``kind=OTHER`` and no size rather than a raised error, so callers can tell
    "absent" apart from "unreadable".
    """

    path: str
    exists: bool
    kind: PathKind
    is_symlink: bool
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "path": self.path,
            "exists": self.exists,
            "kind": self.kind.value,
            "is_symlink": self.is_symlink,
            "size_bytes": self.size_bytes,
        }


def stat_path(path: str) -> Result[PathStat, AgentXError]:
    """Read bounded ``lstat`` metadata for one explicit absolute ``path``.

    This is a read-only observation. It never follows a final symlink target,
    never reads file content, and returns ``PathStat(exists=False)`` for an
    absent path rather than failing, so ``not found`` is reported as data.
    """
    validated = _validate_absolute_path(path, field_name="path")
    if validated.is_failure:
        return Result.failure(validated.unwrap_error())
    target = validated.unwrap()
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return Result.success(
            PathStat(path=target, exists=False, kind=PathKind.OTHER, is_symlink=False)
        )
    except PermissionError as exc:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.stat_permission_denied",
                message=f"permission denied reading metadata for {target!r}",
                category=ErrorCategory.PERMISSION,
                cause=exc,
            )
        )
    except OSError as exc:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.stat_io_failed",
                message=f"could not read metadata for {target!r}",
                category=ErrorCategory.EXECUTION,
                cause=exc,
            )
        )
    kind = _kind_from_mode(info.st_mode)
    is_symlink = kind is PathKind.SYMLINK
    size_bytes = info.st_size if kind is PathKind.FILE else None
    return Result.success(
        PathStat(path=target, exists=True, kind=kind, is_symlink=is_symlink, size_bytes=size_bytes)
    )


# --------------------------------------------------------------------------
# Directory listing.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """Minimal structured metadata for one untrusted directory entry.

    ``name`` is stored verbatim (it is data and authorizes nothing); ordering
    of a listing is by ``name`` only and is deterministic.
    """

    name: str
    kind: PathKind
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "kind": self.kind.value,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class DirectoryListing:
    """Result of one bounded, non-recursive directory read.

    ``total_entries`` is the number of immediate entries actually present in
    the directory; ``entries`` never exceeds ``limit``. When the directory
    contains more than ``limit`` entries, ``truncated`` is ``True`` and only
    the first ``limit`` in deterministic name order are returned.
    """

    path: str
    limit: int
    total_entries: int
    truncated: bool
    entries: tuple[DirectoryEntry, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "path": self.path,
            "limit": self.limit,
            "total_entries": self.total_entries,
            "truncated": self.truncated,
            "entries": [entry.to_dict() for entry in self.entries],
        }


def list_directory(
    path: str, *, max_entries: int = DEFAULT_MAX_ENTRIES
) -> Result[DirectoryListing, AgentXError]:
    """Read at most ``max_entries`` immediate entries of one directory.

    The directory must exist and be a directory (otherwise a ``not_found`` or
    ``wrong_path_kind`` error is returned). Entries are read with
    ``follow_symlinks=False``, deterministically ordered by name, and never
    recursed. Directory entry names are treated as untrusted data.
    """
    validated = _validate_absolute_path(path, field_name="path")
    if validated.is_failure:
        return Result.failure(validated.unwrap_error())
    target = validated.unwrap()
    bounded = _require_entries_bound(max_entries)
    if bounded.is_failure:
        return Result.failure(bounded.unwrap_error())
    limit = bounded.unwrap()

    kind = _lstat_kind(target)
    if kind is None:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.not_found",
                message=f"directory {target!r} does not exist",
                category=ErrorCategory.NOT_FOUND,
                details={"path": target},
            )
        )
    if kind is not PathKind.DIRECTORY:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.wrong_path_kind",
                message=f"{target!r} is a {kind.value}, not a directory",
                category=ErrorCategory.PRECONDITION,
                details={"path": target, "kind": kind.value},
            )
        )

    collected: list[tuple[str, PathKind, int | None]] = []
    try:
        with os.scandir(target) as iterator:
            for entry in iterator:
                entry_kind: PathKind = PathKind.OTHER
                size: int | None = None
                try:
                    entry_kind = _kind_from_mode(entry.stat(follow_symlinks=False).st_mode)
                except OSError:
                    # The entry could not be stat'ed (e.g. vanished mid-read);
                    # keep the untrusted name with an 'other' kind rather than
                    # failing the whole listing.
                    entry_kind = PathKind.OTHER
                if entry_kind is PathKind.FILE:
                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        size = None
                collected.append((entry.name, entry_kind, size))
    except FileNotFoundError as exc:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.not_found",
                message=f"directory {target!r} does not exist",
                category=ErrorCategory.NOT_FOUND,
                details={"path": target},
                cause=exc,
            )
        )
    except PermissionError as exc:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.list_permission_denied",
                message=f"permission denied reading directory {target!r}",
                category=ErrorCategory.PERMISSION,
                cause=exc,
            )
        )
    except OSError as exc:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.list_io_failed",
                message=f"could not read directory {target!r}",
                category=ErrorCategory.EXECUTION,
                cause=exc,
            )
        )

    # Deterministic ordering by name only (never by OS enumeration order).
    collected.sort(key=lambda item: item[0])
    total = len(collected)
    truncated = total > limit
    entries = tuple(
        DirectoryEntry(name=name, kind=entry_kind, size_bytes=size)
        for name, entry_kind, size in collected[:limit]
    )
    return Result.success(
        DirectoryListing(
            path=target,
            limit=limit,
            total_entries=total,
            truncated=truncated,
            entries=entries,
        )
    )


# --------------------------------------------------------------------------
# Create directory (single, non-recursive).
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateDirectoryResult:
    """Evidence that one directory was created."""

    path: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {"path": self.path, "kind": PathKind.DIRECTORY.value}


def create_directory(path: str) -> Result[CreateDirectoryResult, AgentXError]:
    """Create exactly one directory at explicit absolute ``path``.

    Single-directory semantics (no ``parents=True`` equivalent): the parent
    directory must already exist. The target must not already exist; an
    existing target (file, directory or symlink) is an explicit ``conflict``
    and is never overwritten. After ``mkdir`` an independent ``lstat``
    confirms the directory actually exists before success is returned.
    """
    validated = _validate_absolute_path(path, field_name="path")
    if validated.is_failure:
        return Result.failure(validated.unwrap_error())
    target = validated.unwrap()

    existing = _lstat_kind(target)
    if existing is not None:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.destination_exists",
                message=f"destination {target!r} already exists ({existing.value})",
                category=ErrorCategory.CONFLICT,
                details={"path": target, "kind": existing.value},
            )
        )

    parent = str(Path(target).parent)
    parent_kind = _lstat_kind(parent)
    if parent_kind is None:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.parent_not_found",
                message=f"parent directory {parent!r} does not exist",
                category=ErrorCategory.NOT_FOUND,
                details={"parent": parent},
            )
        )
    if parent_kind is not PathKind.DIRECTORY:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.parent_wrong_kind",
                message=f"parent {parent!r} is a {parent_kind.value}, not a directory",
                category=ErrorCategory.PRECONDITION,
                details={"parent": parent, "kind": parent_kind.value},
            )
        )

    try:
        Path(target).mkdir()
    except FileExistsError as exc:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.destination_exists",
                message=f"destination {target!r} already exists",
                category=ErrorCategory.CONFLICT,
                details={"path": target},
                cause=exc,
            )
        )
    except FileNotFoundError as exc:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.not_found",
                message=f"parent directory for {target!r} does not exist",
                category=ErrorCategory.NOT_FOUND,
                details={"path": target},
                cause=exc,
            )
        )
    except PermissionError as exc:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.mkdir_permission_denied",
                message=f"permission denied creating directory {target!r}",
                category=ErrorCategory.PERMISSION,
                cause=exc,
            )
        )
    except OSError as exc:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.mkdir_io_failed",
                message=f"could not create directory {target!r}",
                category=ErrorCategory.EXECUTION,
                cause=exc,
            )
        )

    confirmed = _lstat_kind(target)
    if confirmed is not PathKind.DIRECTORY:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.verification_failed",
                message=f"directory {target!r} was not confirmed after creation",
                category=ErrorCategory.VERIFICATION,
                details={"path": target},
            )
        )
    return Result.success(CreateDirectoryResult(path=target))


# --------------------------------------------------------------------------
# Move.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MoveResult:
    """Evidence that one path was moved from ``source_path`` to ``destination_path``."""

    source_path: str
    destination_path: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {"source_path": self.source_path, "destination_path": self.destination_path}


def _paths_alias(source: str, destination: str) -> bool:
    """Report whether ``source`` and ``destination`` refer to the same path string.

    Compares both the raw and host-normalized (``abspath``) forms. Symlink
    alias resolution is intentionally *not* performed here: a symlink alias is
    surfaced as a destination-conflict rather than guessed at.
    """
    if source == destination:
        return True
    try:
        if Path(source).resolve() == Path(destination).resolve():
            return True
    except OSError:
        pass
    return False


def move_path(
    source_path: str, destination_path: str, *, overwrite: bool = False
) -> Result[MoveResult, AgentXError]:
    """Move one explicit absolute ``source_path`` to ``destination_path``.

    There is no wildcard or pattern move. By default any existing destination
    is an explicit conflict and the move fails closed. ``overwrite=True``
    permits a single supported replacement: moving a regular file over an
    existing regular file. No directory merge, no overwrite through a symlink,
    and no silent copy+delete fallback: a cross-volume move is reported as an
    explicit unsupported error rather than degrading to a copy.
    """
    source_v = _validate_absolute_path(source_path, field_name="source_path")
    if source_v.is_failure:
        return Result.failure(source_v.unwrap_error())
    source = source_v.unwrap()
    dest_v = _validate_absolute_path(destination_path, field_name="destination_path")
    if dest_v.is_failure:
        return Result.failure(dest_v.unwrap_error())
    destination = dest_v.unwrap()

    if _paths_alias(source, destination):
        return Result.failure(
            _error(
                code=f"{_PREFIX}.source_destination_alias",
                message="source and destination refer to the same path",
                category=ErrorCategory.VALIDATION,
                details={"source_path": source, "destination_path": destination},
            )
        )

    source_kind = _lstat_kind(source)
    if source_kind is None:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.not_found",
                message=f"source {source!r} does not exist",
                category=ErrorCategory.NOT_FOUND,
                details={"path": source},
            )
        )
    if source_kind is PathKind.SYMLINK:
        # Moving a symlink moves the link itself, never its target. This is a
        # deliberate, documented behaviour and is not an arbitrary overwrite.
        pass

    destination_kind = _lstat_kind(destination)
    if destination_kind is not None:
        if not overwrite:
            return Result.failure(
                _error(
                    code=f"{_PREFIX}.destination_exists",
                    message=(
                        f"destination {destination!r} already exists "
                        f"({destination_kind.value}); refusing to overwrite"
                    ),
                    category=ErrorCategory.CONFLICT,
                    details={
                        "destination_path": destination,
                        "destination_kind": destination_kind.value,
                    },
                )
            )
        # With overwrite requested we only support replacing a regular file
        # with a regular file. Anything else (a directory destination, a
        # symlink destination, or a non-file source) is refused.
        if source_kind is not PathKind.FILE or destination_kind is not PathKind.FILE:
            return Result.failure(
                _error(
                    code=f"{_PREFIX}.destination_exists",
                    message=(
                        f"overwrite of {destination!r} is unsupported "
                        f"(source={source_kind.value}, destination={destination_kind.value}); "
                        "only file-over-file replacement is allowed"
                    ),
                    category=ErrorCategory.CONFLICT,
                    details={
                        "source_kind": source_kind.value,
                        "destination_kind": destination_kind.value,
                        "destination_path": destination,
                    },
                )
            )

    try:
        if overwrite:
            Path(source).replace(destination)
        else:
            Path(source).rename(destination)
    except OSError as exc:
        if isinstance(exc, PermissionError):
            return Result.failure(
                _error(
                    code=f"{_PREFIX}.move_permission_denied",
                    message=f"permission denied moving {source!r} to {destination!r}",
                    category=ErrorCategory.PERMISSION,
                    cause=exc,
                )
            )
        if exc.errno == getattr(os, "EXDEV", None):
            return Result.failure(
                _error(
                    code=f"{_PREFIX}.cross_device_move_unsupported",
                    message=(
                        f"cannot move {source!r} across filesystems; cross-volume moves are "
                        "unsupported and never degrade to an unverified copy+delete"
                    ),
                    category=ErrorCategory.RESOURCE,
                    details={"source_path": source, "destination_path": destination},
                    cause=exc,
                )
            )
        if exc.errno in (17, 20, 39, 40):  # EEXIST/ENOTDIR/ENOTEMPTY/ELOOP
            return Result.failure(
                _error(
                    code=f"{_PREFIX}.destination_exists",
                    message=f"destination conflict while moving to {destination!r}",
                    category=ErrorCategory.CONFLICT,
                    details={"source_path": source, "destination_path": destination},
                    cause=exc,
                )
            )
        return Result.failure(
            _error(
                code=f"{_PREFIX}.move_io_failed",
                message=f"could not move {source!r} to {destination!r}",
                category=ErrorCategory.EXECUTION,
                details={"source_path": source, "destination_path": destination},
                cause=exc,
            )
        )
    except FileNotFoundError as exc:  # pragma: no cover - defensive
        return Result.failure(
            _error(
                code=f"{_PREFIX}.not_found",
                message=f"source {source!r} does not exist",
                category=ErrorCategory.NOT_FOUND,
                details={"path": source},
                cause=exc,
            )
        )

    # Independent confirmation: source absent (where semantics require) and
    # destination present with the expected kind.
    source_after = _lstat_kind(source)
    destination_after = _lstat_kind(destination)
    if source_after is not None or destination_after is None:
        return Result.failure(
            _error(
                code=f"{_PREFIX}.verification_failed",
                message=(f"move of {source!r} to {destination!r} was not independently confirmed"),
                category=ErrorCategory.VERIFICATION,
                details={"source_path": source, "destination_path": destination},
            )
        )
    return Result.success(MoveResult(source_path=source, destination_path=destination))


# --------------------------------------------------------------------------
# Shared capability plumbing.
# --------------------------------------------------------------------------


def _execution_failure(message: str, error: AgentXError) -> ExecutionResult:
    return ExecutionResult(
        succeeded=False,
        message=message,
        observation=CapabilityObservation(
            summary="operation failed",
            data={"error": _to_json_object(error.to_dict())},
        ),
    )


def _to_json_object(mapping: dict[str, Any]) -> dict[str, JsonValue]:
    return {key: value for key, value in mapping.items()}


def _scope() -> CapabilityScope:
    return CapabilityScope(platform=CapabilityPlatform.ANY)


def _read_estimate() -> ResourceEstimate:
    return ResourceEstimate(
        wall_clock=timedelta(milliseconds=5),
        machine_actions=1,
        external_cost=Decimal("0"),
    )


def _mutation_estimate() -> ResourceEstimate:
    return ResourceEstimate(
        wall_clock=timedelta(milliseconds=10),
        machine_actions=1,
        external_cost=Decimal("0"),
    )


def _rollback_not_applicable() -> RollbackDeclaration:
    return RollbackDeclaration(
        support=RollbackSupport.NOT_APPLICABLE,
        detail=(
            "Rollback orchestration is owned by future kernel work; this "
            "capability performs no rollback."
        ),
    )


# --------------------------------------------------------------------------
# Stat capability.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StatPathParams(CapabilityParams):
    """Typed parameters for the ``stat_path`` read operation."""

    path: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {"path": self.path}


class StatPathCapability:
    """Canonical read-only ``stat`` capability (READ / R0).

    ``execute`` performs exactly one bounded ``lstat`` read. ``verify``
    independently re-reads the path with ``lstat`` and cross-checks the
    observed existence/kind so an execution result never becomes Task success
    on its own word alone.
    """

    __slots__ = ("_descriptor",)

    _descriptor: CapabilityDescriptor

    def __init__(self) -> None:
        object.__setattr__(
            self,
            "_descriptor",
            CapabilityDescriptor(
                identity=STAT_PATH_IDENTITY,
                description=(
                    "Read bounded lstat metadata (existence, kind, size for files) "
                    "for one explicit absolute path without reading file content."
                ),
                scope=_scope(),
                required_permissions=frozenset({Permission.READ}),
                risk_assessment=_read_risk(),
                preconditions=(_path_precondition("stat reads one explicit absolute path"),),
                rollback=_rollback_not_applicable(),
                estimate=_read_estimate(),
            ),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"StatPathCapability is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("StatPathCapability is immutable; cannot delete attributes")

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self, request: CapabilityRequest[StatPathParams], context: ExecutionContext
    ) -> ExecutionResult:
        if not isinstance(request.params, StatPathParams):
            raise TypeError(f"stat requires StatPathParams, got {type(request.params).__name__}")
        if context.observe_stop().should_stop:
            return ExecutionResult(
                succeeded=False,
                message="stat was cancelled before it started",
                observation=CapabilityObservation(
                    summary="stat cancelled before any filesystem read",
                    data={"cancelled_before_start": True},
                ),
            )
        result = stat_path(request.params.path)
        if result.is_failure:
            error = result.unwrap_error()
            return _execution_failure(f"stat failed: {error.message}", error)
        info = result.unwrap()
        return ExecutionResult(
            succeeded=True,
            message=f"stat reported {info.kind.value} exists={info.exists}",
            observation=CapabilityObservation(
                summary="bounded filesystem stat observation",
                data=_to_json_object(info.to_dict()),
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[StatPathParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        data = observation.to_dict()["data"]
        if not isinstance(data, dict):
            return VerificationResult(passed=False, detail="stat observation carries no data")
        observed_path = data.get("path")
        if observed_path != request.params.path:
            return VerificationResult(
                passed=False, detail="stat observation path does not match the request"
            )
        # Independent fresh lstat cross-check: never trust the execution result.
        fresh = stat_path(request.params.path)
        if fresh.is_failure:
            return VerificationResult(
                passed=False, detail="independent stat re-read failed during verification"
            )
        current = fresh.unwrap()
        if data.get("exists") is not True:
            # The observation claimed absence; confirm it is still absent.
            if current.exists:
                return VerificationResult(
                    passed=False, detail="stat claimed absence but the path now exists"
                )
            return VerificationResult(
                passed=True, detail="stat correctly observed the path is absent"
            )
        if not current.exists:
            return VerificationResult(
                passed=False, detail="stat claimed existence but the path is now absent"
            )
        observed_kind = data.get("kind")
        if observed_kind != current.kind.value:
            return VerificationResult(
                passed=False, detail="stat observed kind disagrees with the filesystem"
            )
        if current.kind is PathKind.FILE:
            observed_size = data.get("size_bytes")
            if not isinstance(observed_size, int) or observed_size != current.size_bytes:
                return VerificationResult(
                    passed=False, detail="stat observed size disagrees with the filesystem"
                )
        return VerificationResult(
            passed=True,
            detail=f"stat observation independently confirmed ({current.kind.value}, exists=True)",
        )


def _read_risk() -> RiskAssessment:
    return assess_risk(
        read_only=True, modifies_state=False, reversible=False, external_effect=False
    )


def _mutation_risk() -> RiskAssessment:
    # A local filesystem mutation is a persistent state change that is not
    # reversible by this capability (no delete/rollback in scope). It is
    # classified R2 MODIFY under canonical C1.07 policy: it always requires
    # explicit WRITE authority but does not itself claim a cross-machine
    # external effect, which would force a separate R3 confirmation flow that
    # the governed loop does not perform.
    return assess_risk(
        read_only=False,
        modifies_state=True,
        reversible=False,
        external_effect=False,
    )


def _path_precondition(description: str) -> CapabilityPrecondition:
    return CapabilityPrecondition(name="explicit_absolute_path", description=description)


# --------------------------------------------------------------------------
# List capability.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListDirectoryParams(CapabilityParams):
    """Typed parameters for the bounded ``list_directory`` read operation."""

    path: str
    max_entries: int = DEFAULT_MAX_ENTRIES

    def to_dict(self) -> dict[str, JsonValue]:
        return {"path": self.path, "max_entries": self.max_entries}


class ListDirectoryCapability:
    """Canonical read-only, bounded directory listing capability (READ / R0)."""

    __slots__ = ("_descriptor",)

    _descriptor: CapabilityDescriptor

    def __init__(self) -> None:
        object.__setattr__(
            self,
            "_descriptor",
            CapabilityDescriptor(
                identity=LIST_DIRECTORY_IDENTITY,
                description=(
                    "List at most a bounded number of immediate directory entries in "
                    "deterministic order, without recursing or reading file contents."
                ),
                scope=_scope(),
                required_permissions=frozenset({Permission.READ}),
                risk_assessment=_read_risk(),
                preconditions=(
                    _path_precondition("list reads one explicit absolute directory path"),
                ),
                rollback=_rollback_not_applicable(),
                estimate=_read_estimate(),
            ),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"ListDirectoryCapability is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("ListDirectoryCapability is immutable; cannot delete attributes")

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self, request: CapabilityRequest[ListDirectoryParams], context: ExecutionContext
    ) -> ExecutionResult:
        if not isinstance(request.params, ListDirectoryParams):
            raise TypeError(
                f"list requires ListDirectoryParams, got {type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return ExecutionResult(
                succeeded=False,
                message="list was cancelled before it started",
                observation=CapabilityObservation(
                    summary="list cancelled before any filesystem read",
                    data={"cancelled_before_start": True},
                ),
            )
        result = list_directory(request.params.path, max_entries=request.params.max_entries)
        if result.is_failure:
            error = result.unwrap_error()
            return _execution_failure(f"list failed: {error.message}", error)
        listing = result.unwrap()
        return ExecutionResult(
            succeeded=True,
            message=(
                f"listed {len(listing.entries)} of {listing.total_entries} entries"
                + (" (truncated)" if listing.truncated else "")
            ),
            observation=CapabilityObservation(
                summary="bounded directory listing observation",
                data=_to_json_object(listing.to_dict()),
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[ListDirectoryParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        data = observation.to_dict()["data"]
        if not isinstance(data, dict):
            return VerificationResult(passed=False, detail="list observation carries no data")
        if data.get("path") != request.params.path:
            return VerificationResult(
                passed=False, detail="list observation path does not match the request"
            )
        limit = data.get("limit")
        if type(limit) is not int or limit != request.params.max_entries:
            return VerificationResult(
                passed=False, detail="list observation limit does not match the request"
            )
        entries = data.get("entries")
        total = data.get("total_entries")
        truncated = data.get("truncated")
        if not isinstance(entries, list) or type(total) is not int or type(truncated) is not bool:
            return VerificationResult(
                passed=False, detail="list observation is structurally malformed"
            )
        if total < 0 or len(entries) > limit:
            return VerificationResult(passed=False, detail="list observation exceeds its bound")
        if truncated != (total > limit):
            return VerificationResult(
                passed=False, detail="list observation truncation flag is inconsistent"
            )
        names: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                return VerificationResult(passed=False, detail="list entry is malformed")
            name = entry.get("name")
            if not isinstance(name, str):
                return VerificationResult(passed=False, detail="list entry has no name")
            kind = entry.get("kind")
            if kind not in {kind.value for kind in PathKind}:
                return VerificationResult(passed=False, detail="list entry has an invalid kind")
            names.append(name)
        if names != sorted(names):
            return VerificationResult(
                passed=False, detail="list entries are not in deterministic sorted order"
            )
        return VerificationResult(
            passed=True,
            detail=(
                f"list observation is bounded and deterministically ordered "
                f"({len(names)} of {total})"
            ),
        )


# --------------------------------------------------------------------------
# Create-directory capability.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreateDirectoryParams(CapabilityParams):
    """Typed parameters for the single-directory creation mutation."""

    path: str

    def to_dict(self) -> dict[str, JsonValue]:
        return {"path": self.path}


class CreateDirectoryCapability:
    """Canonical governed single-directory creation (WRITE / R2)."""

    __slots__ = ("_descriptor",)

    _descriptor: CapabilityDescriptor

    def __init__(self) -> None:
        object.__setattr__(
            self,
            "_descriptor",
            CapabilityDescriptor(
                identity=CREATE_DIRECTORY_IDENTITY,
                description=(
                    "Create exactly one directory at an explicit absolute path with an "
                    "existing parent, refusing any existing target and never overwriting."
                ),
                scope=_scope(),
                required_permissions=frozenset({Permission.WRITE}),
                risk_assessment=_mutation_risk(),
                preconditions=(
                    _path_precondition(
                        "mkdir targets one explicit absolute path with an existing parent"
                    ),
                ),
                rollback=_rollback_not_applicable(),
                estimate=_mutation_estimate(),
            ),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"CreateDirectoryCapability is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("CreateDirectoryCapability is immutable; cannot delete attributes")

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self, request: CapabilityRequest[CreateDirectoryParams], context: ExecutionContext
    ) -> ExecutionResult:
        if not isinstance(request.params, CreateDirectoryParams):
            raise TypeError(
                "create_directory requires CreateDirectoryParams, got "
                f"{type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return ExecutionResult(
                succeeded=False,
                message="create_directory was cancelled before it started",
                observation=CapabilityObservation(
                    summary="create_directory cancelled before any mutation",
                    data={"cancelled_before_start": True},
                ),
            )
        result = create_directory(request.params.path)
        if result.is_failure:
            error = result.unwrap_error()
            return _execution_failure(f"create_directory failed: {error.message}", error)
        evidence = result.unwrap()
        return ExecutionResult(
            succeeded=True,
            message=f"created directory {evidence.path}",
            observation=CapabilityObservation(
                summary="directory created",
                data=_to_json_object(evidence.to_dict()),
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[CreateDirectoryParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        # Independent filesystem confirmation: the directory truly exists.
        fresh = stat_path(request.params.path)
        if fresh.is_failure:
            return VerificationResult(
                passed=False, detail="independent stat during verification failed"
            )
        current = fresh.unwrap()
        if not current.exists or current.kind is not PathKind.DIRECTORY:
            return VerificationResult(
                passed=False,
                detail="directory was not independently confirmed to exist after creation",
            )
        return VerificationResult(
            passed=True,
            detail=f"directory independently confirmed at {current.path}",
        )


# --------------------------------------------------------------------------
# Move capability.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MovePathParams(CapabilityParams):
    """Typed parameters for the bounded move mutation."""

    source_path: str
    destination_path: str
    overwrite: bool = False

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "source_path": self.source_path,
            "destination_path": self.destination_path,
            "overwrite": self.overwrite,
        }


class MovePathCapability:
    """Canonical governed move of one path (WRITE / R2)."""

    __slots__ = ("_descriptor",)

    _descriptor: CapabilityDescriptor

    def __init__(self) -> None:
        object.__setattr__(
            self,
            "_descriptor",
            CapabilityDescriptor(
                identity=MOVE_PATH_IDENTITY,
                description=(
                    "Move one explicit absolute path to another, failing closed on "
                    "destination conflict and never degrading to a copy+delete fallback."
                ),
                scope=_scope(),
                required_permissions=frozenset({Permission.WRITE}),
                risk_assessment=_mutation_risk(),
                preconditions=(
                    _path_precondition(
                        "move targets explicit absolute source and destination paths"
                    ),
                ),
                rollback=_rollback_not_applicable(),
                estimate=_mutation_estimate(),
            ),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"MovePathCapability is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("MovePathCapability is immutable; cannot delete attributes")

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self, request: CapabilityRequest[MovePathParams], context: ExecutionContext
    ) -> ExecutionResult:
        if not isinstance(request.params, MovePathParams):
            raise TypeError(f"move requires MovePathParams, got {type(request.params).__name__}")
        if context.observe_stop().should_stop:
            return ExecutionResult(
                succeeded=False,
                message="move was cancelled before it started",
                observation=CapabilityObservation(
                    summary="move cancelled before any mutation",
                    data={"cancelled_before_start": True},
                ),
            )
        params = request.params
        result = move_path(
            params.source_path,
            params.destination_path,
            overwrite=params.overwrite,
        )
        if result.is_failure:
            error = result.unwrap_error()
            return _execution_failure(f"move failed: {error.message}", error)
        evidence = result.unwrap()
        return ExecutionResult(
            succeeded=True,
            message=f"moved {evidence.source_path} to {evidence.destination_path}",
            observation=CapabilityObservation(
                summary="path moved",
                data=_to_json_object(evidence.to_dict()),
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[MovePathParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        params = request.params
        # Independent confirmation: source absent and destination present.
        source_after = stat_path(params.source_path)
        if source_after.is_failure:
            return VerificationResult(
                passed=False, detail="independent stat of source during verification failed"
            )
        if source_after.unwrap().exists:
            return VerificationResult(
                passed=False, detail="source path still exists after the move"
            )
        dest_after = stat_path(params.destination_path)
        if dest_after.is_failure:
            return VerificationResult(
                passed=False, detail="independent stat of destination during verification failed"
            )
        if not dest_after.unwrap().exists:
            return VerificationResult(
                passed=False, detail="destination path does not exist after the move"
            )
        return VerificationResult(
            passed=True,
            detail=(
                f"move independently confirmed: source absent and destination present at "
                f"{params.destination_path}"
            ),
        )


# --------------------------------------------------------------------------
# Request builders (convenience).
# --------------------------------------------------------------------------


def stat_path_request(path: str) -> CapabilityRequest[StatPathParams]:
    return CapabilityRequest(identity=STAT_PATH_IDENTITY, params=StatPathParams(path=path))


def list_directory_request(
    path: str, *, max_entries: int = DEFAULT_MAX_ENTRIES
) -> CapabilityRequest[ListDirectoryParams]:
    return CapabilityRequest(
        identity=LIST_DIRECTORY_IDENTITY,
        params=ListDirectoryParams(path=path, max_entries=max_entries),
    )


def create_directory_request(path: str) -> CapabilityRequest[CreateDirectoryParams]:
    return CapabilityRequest(
        identity=CREATE_DIRECTORY_IDENTITY, params=CreateDirectoryParams(path=path)
    )


def move_path_request(
    source_path: str, destination_path: str, *, overwrite: bool = False
) -> CapabilityRequest[MovePathParams]:
    return CapabilityRequest(
        identity=MOVE_PATH_IDENTITY,
        params=MovePathParams(
            source_path=source_path,
            destination_path=destination_path,
            overwrite=overwrite,
        ),
    )
