"""Windows process/application discovery for AgentX (A5.02).

This module is the smallest production-quality **read-only** discovery
foundation for identifying running Windows applications and processes. It
answers one question per operation:

    *Which processes are running on this Windows host right now, which
    top-level windows (applications) do they own, and how reliable is each
    piece of metadata?*

Contract inventory
------------------

    - :class:`MetadataStatus` — the explicit vocabulary explaining why an
      optional metadata field (image name, image path, window title, class
      name) is absent: unsupported, access-denied, vanished, empty or invalid.
    - :class:`WindowsProcessIdentity` — the typed identity of one discovered
      process: PID, parent PID, executable name, best-effort full executable
      path, its associated top-level window handles, and a derived
      ``is_application`` signal.
    - :class:`WindowsWindowIdentity` — the typed identity of one discovered
      top-level window: handle, owning PID, title, class name, visibility.
    - :class:`WindowsProcessSnapshot` — the immutable, deterministically
      ordered result of one discovery operation, plus explicit counters for
      OS entries dropped as invalid or merged as duplicates.
    - :class:`WindowsProcessDiscovery` — the deterministic read-only discovery
      operation. Construction is side-effect free; every OS read happens only
      inside :meth:`WindowsProcessDiscovery.discover`.
    - :class:`WindowsProcessDiscoveryCapability` — the discovery operation
      wrapped as an ordinary canonical
      :class:`~agentx.capabilities.abi.Capability` so it can reach the
      governed execution path through the A5.01 provider and the A1.09
      registry like any other capability.

Deliberate non-scope
--------------------

A5.02 is discovery only, and discovery is a **read**. There is no UI
Automation traversal, no control discovery, no clicking, no text entry, no
keyboard/mouse input, no window manipulation of any kind, no focus change, no
capture, no OCR, no vision, no shell, no process lifecycle operation, and no
code execution. Those belong to later tasks. The native reads themselves live
entirely in :mod:`agentx.capabilities.windows._native`; this module never
imports :mod:`ctypes`.

Determinism
-----------

The OS may enumerate in any order and may hand back duplicates or garbage.
Every discovery result is therefore normalized: processes are deduplicated by
PID (the deterministic survivor is chosen by a total key, never by arrival
order), windows are deduplicated by handle, invalid OS entries are dropped and
counted, and the snapshot is ordered by (PID, executable name, executable
path) for processes and by handle for windows. No hidden global cache, no
background watcher, no polling loop: every
:meth:`~WindowsProcessDiscovery.discover` call performs a fresh, explicit
read and nothing persists between calls.

Security
--------

Process and window metadata is **untrusted data**. Executable names, paths,
window titles and class names are stored verbatim (up to validation bounds)
and authorize exactly nothing: they cannot grant a
:class:`~agentx.kernel.permissions.Permission`, become capability names,
bypass the ActionGate, reduce a
:class:`~agentx.kernel.risk.RiskAssessment`, widen a ``ResourceEnvelope``,
clear an :class:`~agentx.kernel.emergency_stop.EmergencyStop`, or fabricate a
verification verdict. Availability is not authority; the Trusted Kernel
remains the only authority boundary.

Owner: A5.02. Belongs to ``agentx.capabilities.windows``; imports only the
standard library, canonical ``agentx.core``/``agentx.kernel`` contracts, the
canonical capability ABI, and the A5.01 provider boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Final, Protocol

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
from agentx.capabilities.windows import _native
from agentx.capabilities.windows.provider import (
    WINDOWS_PROVIDER_IDENTITY,
    WindowsProviderIdentity,
    WindowsSupport,
    unsupported_platform_error,
)
from agentx.core.errors import AgentXError
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk

__all__ = [
    "PATH_QUERY_FAILED_ERROR_CODE",
    "WINDOWS_PROCESS_DISCOVERY_DESCRIPTION",
    "WINDOWS_PROCESS_DISCOVERY_IDENTITY",
    "MetadataStatus",
    "NativeWindowsSurface",
    "ToolhelpNativeSurface",
    "WindowsProcessDiscovery",
    "WindowsProcessDiscoveryCapability",
    "WindowsProcessDiscoveryParams",
    "WindowsProcessIdentity",
    "WindowsProcessSnapshot",
    "WindowsWindowIdentity",
    "discovery_request",
]

#: Canonical error code used when a per-process image-path query fails at the
#: operation level (as opposed to a per-record OS error, which is data).
PATH_QUERY_FAILED_ERROR_CODE: Final[str] = (
    "capabilities.windows.process_discovery.path_query_failed"
)

# Validation bounds for untrusted OS-provided strings. These are defensive
# upper bounds, not precision claims: an OS string beyond a bound is kept out
# of the value and reported as INVALID metadata.
_MAX_EXECUTABLE_NAME_CHARS: Final[int] = 512
_MAX_EXECUTABLE_PATH_CHARS: Final[int] = 2048
_MAX_WINDOW_TITLE_CHARS: Final[int] = 512
_MAX_WINDOW_CLASS_CHARS: Final[int] = 256

_WIN32_ERROR_VANISHED: Final[frozenset[int]] = frozenset(
    {
        _native.WIN32_ERROR_FILE_NOT_FOUND,
        _native.WIN32_ERROR_PATH_NOT_FOUND,
        _native.WIN32_ERROR_INVALID_PARAMETER,
    }
)


def _require_int(value: object, *, field_name: str, minimum: int) -> int:
    """Validate an explicit integer with a lower bound (bools rejected)."""
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}, got {value}")
    return value


def _has_control_characters(value: str) -> bool:
    """Report whether ``value`` contains ASCII control characters or DEL."""
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _validate_optional_text(
    *,
    value: str | None,
    status: MetadataStatus,
    field_name: str,
    max_chars: int,
    allow_control_characters: bool,
) -> None:
    """Enforce the value/status invariant for one optional text field.

    A field is non-``None`` exactly when its status is ``AVAILABLE``. Values
    marked available must be non-empty and within the defensive bound; OS
    identifier fields must additionally be control-character free. Free text
    (window titles) may contain anything — hostile content stays verbatim and
    inert.
    """
    if not isinstance(status, MetadataStatus):
        raise TypeError(f"{field_name}_status must be a MetadataStatus")
    if status is MetadataStatus.AVAILABLE:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a str when status is available")
        if not value:
            raise ValueError(f"{field_name} must be non-empty when status is available")
        if len(value) > max_chars:
            raise ValueError(f"{field_name} must not exceed {max_chars} characters")
        if not allow_control_characters and _has_control_characters(value):
            raise ValueError(f"{field_name} must not contain control characters")
    elif value is not None:
        raise ValueError(f"{field_name} must be None when status is {status.value}")


# --------------------------------------------------------------------------
# Metadata availability semantics.
# --------------------------------------------------------------------------


class MetadataStatus(StrEnum):
    """Explicit explanation of why an optional metadata field is absent.

    The vocabulary is deliberately small and shared by every optional field
    of the discovery model, so callers never have to guess what ``None``
    means.
    """

    AVAILABLE = "available"
    #: The mechanism does not provide this metadata on this host/API level.
    UNSUPPORTED = "unsupported"
    #: The OS refused the read (for example an elevated process's image path).
    ACCESS_DENIED = "access_denied"
    #: The target disappeared mid-discovery (PID reuse / process exit race).
    VANISHED = "vanished"
    #: The OS answered, but the value itself was empty.
    EMPTY = "empty"
    #: The OS returned data that failed validation; it is not trusted.
    INVALID = "invalid"


# --------------------------------------------------------------------------
# Typed discovered identities.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowsWindowIdentity:
    """Typed identity of one discovered top-level window.

    A window is identified by its handle **as of the discovery snapshot**;
    handles are recycled by the OS and are not stable across time. The owning
    process is recorded as a plain PID reference; that process may or may not
    appear in the same snapshot (it can exit between the two reads).

    ``title`` is free application text: any content is legal and it is stored
    verbatim (it is untrusted data and authorizes nothing). ``class_name`` is
    an OS identifier and therefore validated like one. A field is non-``None``
    exactly when its status is :attr:`MetadataStatus.AVAILABLE`.
    """

    handle: int
    process_id: int
    title: str | None
    title_status: MetadataStatus
    class_name: str | None
    class_name_status: MetadataStatus
    is_visible: bool

    def __post_init__(self) -> None:
        _require_int(self.handle, field_name="window.handle", minimum=1)
        _require_int(self.process_id, field_name="window.process_id", minimum=0)
        _validate_optional_text(
            value=self.title,
            status=self.title_status,
            field_name="window.title",
            max_chars=_MAX_WINDOW_TITLE_CHARS,
            allow_control_characters=True,
        )
        _validate_optional_text(
            value=self.class_name,
            status=self.class_name_status,
            field_name="window.class_name",
            max_chars=_MAX_WINDOW_CLASS_CHARS,
            allow_control_characters=False,
        )
        if type(self.is_visible) is not bool:
            raise TypeError(f"window.is_visible must be bool, got {type(self.is_visible).__name__}")


@dataclass(frozen=True, slots=True)
class WindowsProcessIdentity:
    """Typed identity of one discovered running process.

    Fields:

    - ``process_id`` / ``parent_process_id``: the OS process identifiers.
      ``parent_process_id`` is ``None`` only when the mechanism reported no
      parent; note that a parent may have exited before discovery (PID reuse)
      so it is a *reference*, never a guarantee the parent still exists.
    - ``executable_name`` / ``executable_path``: the image basename always
      comes from the process snapshot; the full path is a best-effort
      enrichment whose status explains any absence (typically
      ``ACCESS_DENIED`` for elevated processes). Both are OS identifiers and
      are validated like one; both are untrusted data.
    - ``window_handles``: the sorted handles of the top-level windows in this
      snapshot that are owned by this process; ``visible_window_count`` is how
      many of them are visible. ``is_application`` is the derived signal that
      this process currently presents at least one visible window — the
      closest thing to "a running application" the OS can tell us read-only.
    """

    process_id: int
    parent_process_id: int | None
    executable_name: str | None
    executable_name_status: MetadataStatus
    executable_path: str | None
    executable_path_status: MetadataStatus
    window_handles: tuple[int, ...]
    visible_window_count: int

    def __post_init__(self) -> None:
        _require_int(self.process_id, field_name="process.process_id", minimum=0)
        if self.parent_process_id is not None:
            _require_int(self.parent_process_id, field_name="process.parent_process_id", minimum=0)
        _validate_optional_text(
            value=self.executable_name,
            status=self.executable_name_status,
            field_name="process.executable_name",
            max_chars=_MAX_EXECUTABLE_NAME_CHARS,
            allow_control_characters=False,
        )
        _validate_optional_text(
            value=self.executable_path,
            status=self.executable_path_status,
            field_name="process.executable_path",
            max_chars=_MAX_EXECUTABLE_PATH_CHARS,
            allow_control_characters=False,
        )
        if not isinstance(self.window_handles, tuple):
            raise TypeError("process.window_handles must be a tuple of ints")
        previous = 0
        for handle in self.window_handles:
            _require_int(handle, field_name="process.window_handles entry", minimum=1)
            if handle <= previous:
                raise ValueError("process.window_handles must be strictly increasing")
            previous = handle
        _require_int(
            self.visible_window_count, field_name="process.visible_window_count", minimum=0
        )
        if self.visible_window_count > len(self.window_handles):
            raise ValueError("process.visible_window_count must not exceed its window count")

    @property
    def is_application(self) -> bool:
        """Whether this process currently owns at least one visible window."""
        return self.visible_window_count > 0

    @property
    def sort_key(self) -> tuple[int, str, str]:
        """Total deterministic ordering key (PID, then name, then path)."""
        return (
            self.process_id,
            self.executable_name if self.executable_name is not None else "",
            self.executable_path if self.executable_path is not None else "",
        )


@dataclass(frozen=True, slots=True)
class WindowsProcessSnapshot:
    """Immutable, deterministically ordered result of one discovery operation.

    Ordering invariants are enforced at construction: processes strictly
    ascending by :attr:`WindowsProcessIdentity.sort_key`, windows strictly
    ascending by handle. ``dropped_invalid_entries`` counts raw OS entries
    that failed validation and were excluded; ``merged_duplicate_entries``
    counts raw OS entries merged away as duplicates of a surviving entry.
    Both are diagnostic counters — inert data that authorize nothing.
    """

    processes: tuple[WindowsProcessIdentity, ...]
    windows: tuple[WindowsWindowIdentity, ...]
    dropped_invalid_entries: int
    merged_duplicate_entries: int

    def __post_init__(self) -> None:
        if not isinstance(self.processes, tuple):
            raise TypeError("snapshot.processes must be a tuple")
        if not isinstance(self.windows, tuple):
            raise TypeError("snapshot.windows must be a tuple")
        for process in self.processes:
            if not isinstance(process, WindowsProcessIdentity):
                raise TypeError("snapshot.processes must contain WindowsProcessIdentity")
        for window in self.windows:
            if not isinstance(window, WindowsWindowIdentity):
                raise TypeError("snapshot.windows must contain WindowsWindowIdentity")
        previous: tuple[int, str, str] | None = None
        for process in self.processes:
            if previous is not None and process.sort_key <= previous:
                raise ValueError("snapshot.processes must be strictly ascending")
            previous = process.sort_key
        previous_handle = 0
        for window in self.windows:
            if window.handle <= previous_handle:
                raise ValueError("snapshot.windows must be strictly ascending by handle")
            previous_handle = window.handle
        handles = {window.handle for window in self.windows}
        for process in self.processes:
            for handle in process.window_handles:
                if handle not in handles:
                    raise ValueError(
                        "snapshot processes must not reference handles absent from windows"
                    )
        _require_int(
            self.dropped_invalid_entries,
            field_name="snapshot.dropped_invalid_entries",
            minimum=0,
        )
        _require_int(
            self.merged_duplicate_entries,
            field_name="snapshot.merged_duplicate_entries",
            minimum=0,
        )

    @property
    def process_count(self) -> int:
        """How many processes the snapshot holds."""
        return len(self.processes)

    @property
    def window_count(self) -> int:
        """How many top-level windows the snapshot holds."""
        return len(self.windows)

    @property
    def application_count(self) -> int:
        """How many processes currently present at least one visible window."""
        return sum(1 for process in self.processes if process.is_application)

    def to_dict(self) -> dict[str, JsonValue]:
        """Return the canonical JSON-compatible representation (evidence)."""
        return {
            "process_count": self.process_count,
            "window_count": self.window_count,
            "application_count": self.application_count,
            "dropped_invalid_entries": self.dropped_invalid_entries,
            "merged_duplicate_entries": self.merged_duplicate_entries,
            "processes": [self._process_to_dict(process) for process in self.processes],
            "windows": [self._window_to_dict(window) for window in self.windows],
        }

    @staticmethod
    def _process_to_dict(process: WindowsProcessIdentity) -> dict[str, JsonValue]:
        return {
            "process_id": process.process_id,
            "parent_process_id": process.parent_process_id,
            "executable_name": process.executable_name,
            "executable_name_status": process.executable_name_status.value,
            "executable_path": process.executable_path,
            "executable_path_status": process.executable_path_status.value,
            "window_handles": list(process.window_handles),
            "visible_window_count": process.visible_window_count,
            "is_application": process.is_application,
        }

    @staticmethod
    def _window_to_dict(window: WindowsWindowIdentity) -> dict[str, JsonValue]:
        return {
            "handle": window.handle,
            "process_id": window.process_id,
            "title": window.title,
            "title_status": window.title_status.value,
            "class_name": window.class_name,
            "class_name_status": window.class_name_status.value,
            "is_visible": window.is_visible,
        }


# --------------------------------------------------------------------------
# Native surface seam.
# --------------------------------------------------------------------------


class NativeWindowsSurface(Protocol):
    """The read-only native seam used by discovery.

    The real implementation is :class:`ToolhelpNativeSurface`, a thin adapter
    over :mod:`agentx.capabilities.windows._native`. Tests substitute fakes;
    nothing else ever needs to. Every method is a read.
    """

    def enumerate_processes(self) -> Result[tuple[_native.RawProcessEntry, ...], AgentXError]:
        """Take one read-only process snapshot, in OS order."""
        ...

    def query_executable_path(self, process_id: int) -> Result[_native.RawPathQuery, AgentXError]:
        """Query one process's full image path, best effort."""
        ...

    def enumerate_windows(self) -> Result[tuple[_native.RawWindowEntry, ...], AgentXError]:
        """Walk the top-level windows once, read-only."""
        ...


class ToolhelpNativeSurface:
    """Adapter over the isolated Win32 module; the default native seam.

    Constructing it is side-effect free. Each method delegates directly to
    :mod:`agentx.capabilities.windows._native`, which imports ``ctypes``
    lazily and only inside the call, so even constructing this adapter loads
    nothing native on any host.
    """

    __slots__ = ()

    def enumerate_processes(self) -> Result[tuple[_native.RawProcessEntry, ...], AgentXError]:
        return _native.enumerate_processes_raw()

    def query_executable_path(self, process_id: int) -> Result[_native.RawPathQuery, AgentXError]:
        return _native.query_executable_path_raw(process_id)

    def enumerate_windows(self) -> Result[tuple[_native.RawWindowEntry, ...], AgentXError]:
        return _native.enumerate_windows_raw()


# --------------------------------------------------------------------------
# Normalization helpers (OS data -> typed model).
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _NormalizedProcess:
    """Validated process identity before path enrichment and window join."""

    process_id: int
    parent_process_id: int | None
    executable_name: str | None
    executable_name_status: MetadataStatus


@dataclass(frozen=True, slots=True)
class _NormalizedWindow:
    """Validated window identity before the window join."""

    handle: int
    process_id: int
    title: str | None
    title_status: MetadataStatus
    class_name: str | None
    class_name_status: MetadataStatus
    is_visible: bool


def _normalize_os_identifier(value: object, *, max_chars: int) -> MetadataStatus:
    """Classify an OS identifier string that must be non-empty and clean.

    Returns the status; ``AVAILABLE`` means the value is trusted enough to
    store (the caller then stores it verbatim), anything else means the value
    must be dropped and only the status kept.
    """
    if not isinstance(value, str):
        return MetadataStatus.INVALID
    if len(value) > max_chars:
        return MetadataStatus.INVALID
    if _has_control_characters(value):
        return MetadataStatus.INVALID
    if not value:
        return MetadataStatus.EMPTY
    return MetadataStatus.AVAILABLE


def _normalize_free_text(value: object, *, max_chars: int) -> MetadataStatus:
    """Classify free application text (for example a window title).

    Free text may contain any characters — hostile content is stored verbatim
    and stays inert — so only the type and a defensive length bound are
    checked. An empty string is ``EMPTY``, not stored.
    """
    if not isinstance(value, str):
        return MetadataStatus.INVALID
    if len(value) > max_chars:
        return MetadataStatus.INVALID
    if not value:
        return MetadataStatus.EMPTY
    return MetadataStatus.AVAILABLE


def _map_win32_error(error_code: int) -> MetadataStatus:
    """Translate a Win32 error code from a per-item read into a status."""
    if error_code == _native.WIN32_ERROR_ACCESS_DENIED:
        return MetadataStatus.ACCESS_DENIED
    if error_code in _WIN32_ERROR_VANISHED:
        return MetadataStatus.VANISHED
    return MetadataStatus.INVALID


def _normalize_processes(
    raw_entries: tuple[_native.RawProcessEntry, ...],
) -> tuple[list[_NormalizedProcess], int, int]:
    """Validate, deduplicate and deterministically order raw process entries.

    Entries with an invalid PID are dropped and counted. Duplicate PIDs are
    merged: the deterministic survivor is the entry with the smallest
    ``(executable name, parent PID)`` key — never the one that happened to
    arrive first — and each merge is counted.
    """
    dropped = 0
    grouped: dict[int, list[_native.RawProcessEntry]] = {}
    for entry in raw_entries:
        pid = entry.process_id
        if type(pid) is not int or pid < 0:
            dropped += 1
            continue
        grouped.setdefault(pid, []).append(entry)

    merged = 0
    normalized: list[_NormalizedProcess] = []
    for pid in sorted(grouped):
        candidates = grouped[pid]
        if len(candidates) > 1:
            merged += len(candidates) - 1
        survivor = min(
            candidates,
            key=lambda item: (
                item.executable_name if isinstance(item.executable_name, str) else "",
                item.parent_process_id
                if type(item.parent_process_id) is int and item.parent_process_id >= 0
                else -1,
            ),
        )
        name_status = _normalize_os_identifier(
            survivor.executable_name, max_chars=_MAX_EXECUTABLE_NAME_CHARS
        )
        parent = survivor.parent_process_id
        normalized.append(
            _NormalizedProcess(
                process_id=pid,
                parent_process_id=parent if type(parent) is int and parent >= 0 else None,
                executable_name=(
                    survivor.executable_name if name_status is MetadataStatus.AVAILABLE else None
                ),
                executable_name_status=name_status,
            )
        )
    return normalized, dropped, merged


def _normalize_windows(
    raw_entries: tuple[_native.RawWindowEntry, ...],
) -> tuple[list[_NormalizedWindow], int, int]:
    """Validate, deduplicate and order raw window entries by handle.

    A raw window with an invalid handle or PID is dropped and counted. A
    ``None`` text read keeps its Win32 error semantics (access denied vs
    invalid); a present string is classified as free text (title) or an OS
    identifier (class name).
    """
    dropped = 0
    merged = 0
    by_handle: dict[int, _native.RawWindowEntry] = {}
    for entry in raw_entries:
        handle = entry.handle
        if type(handle) is not int or handle <= 0:
            dropped += 1
            continue
        if handle in by_handle:
            merged += 1
            continue
        by_handle[handle] = entry

    normalized: list[_NormalizedWindow] = []
    for handle in sorted(by_handle):
        entry = by_handle[handle]
        if type(entry.process_id) is not int or entry.process_id < 0:
            dropped += 1
            continue
        if entry.title is None:
            title_status = _map_raw_text_error(entry.title_error_code)
        else:
            title_status = _normalize_free_text(entry.title, max_chars=_MAX_WINDOW_TITLE_CHARS)
        if entry.class_name is None:
            class_status = _map_raw_text_error(entry.class_name_error_code)
        else:
            class_status = _normalize_os_identifier(
                entry.class_name, max_chars=_MAX_WINDOW_CLASS_CHARS
            )
        normalized.append(
            _NormalizedWindow(
                handle=handle,
                process_id=entry.process_id,
                title=entry.title if title_status is MetadataStatus.AVAILABLE else None,
                title_status=title_status,
                class_name=entry.class_name if class_status is MetadataStatus.AVAILABLE else None,
                class_name_status=class_status,
                is_visible=entry.is_visible is True,
            )
        )
    return normalized, dropped, merged


def _map_raw_text_error(error_code: object) -> MetadataStatus:
    """Translate a Win32 error code from a per-window text read."""
    if error_code == _native.WIN32_ERROR_ACCESS_DENIED:
        return MetadataStatus.ACCESS_DENIED
    return MetadataStatus.INVALID


# --------------------------------------------------------------------------
# The discovery operation.
# --------------------------------------------------------------------------


class WindowsProcessDiscovery:
    """Deterministic read-only Windows process/application discovery.

    Construction is side-effect free and stores only the caller-supplied
    A5.01 support verdict plus the native seam. Every OS read happens inside
    :meth:`discover`, once per call: there is no cache, no watcher, no
    polling, and no state carried between calls. On an unsupported host the
    operation refuses explicitly with the canonical A5.01 unsupported-platform
    error and touches no native surface at all.
    """

    __slots__ = ("_native_surface", "_support")

    _support: WindowsSupport
    _native_surface: NativeWindowsSurface

    def __init__(
        self,
        support: WindowsSupport,
        *,
        native_surface: NativeWindowsSurface | None = None,
    ) -> None:
        """Create a discovery operation for an explicit support verdict.

        ``native_surface`` defaults to the real Toolhelp-backed seam; passing
        a fake is how tests keep every behaviour deterministic and
        platform-independent.
        """
        if not isinstance(support, WindowsSupport):
            raise TypeError(f"support must be a WindowsSupport, got {type(support).__name__}")
        object.__setattr__(
            self,
            "_native_surface",
            native_surface if native_surface is not None else ToolhelpNativeSurface(),
        )
        object.__setattr__(self, "_support", support)

    def __setattr__(self, name: str, value: object) -> None:
        """Refuse post-construction mutation (the support verdict is final)."""
        raise AttributeError(f"WindowsProcessDiscovery is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"WindowsProcessDiscovery is immutable; cannot delete {name!r}")

    @property
    def identity(self) -> WindowsProviderIdentity:
        """The canonical A5.01 Windows provider identity this operation serves."""
        return WINDOWS_PROVIDER_IDENTITY

    @property
    def support(self) -> WindowsSupport:
        """The explicit support verdict supplied at construction."""
        return self._support

    @property
    def is_supported(self) -> bool:
        """Whether the described host is a supported Windows host."""
        return self._support.is_supported

    def discover(self) -> Result[WindowsProcessSnapshot, AgentXError]:
        """Take one read-only, deterministically normalized snapshot.

        Failure semantics: an unsupported host, an unavailable native
        surface, or an OS-level enumeration failure returns an explicit
        canonical :class:`~agentx.core.errors.AgentXError`. Per-item metadata
        failures (access denied, vanished mid-read, invalid OS data) are
        **data**: they appear as :class:`MetadataStatus` values on the
        affected record and never fail the operation. A process whose image
        path vanished between snapshot and query still appears in the
        snapshot — the race is recorded, not hidden.
        """
        if not self._support.is_supported:
            return Result.failure(unsupported_platform_error(self._support))

        raw_processes = self._native_surface.enumerate_processes()
        if raw_processes.is_failure:
            return Result.failure(raw_processes.unwrap_error())
        raw_windows = self._native_surface.enumerate_windows()
        if raw_windows.is_failure:
            return Result.failure(raw_windows.unwrap_error())

        normalized_processes, dropped_processes, merged_processes = _normalize_processes(
            raw_processes.unwrap()
        )
        normalized_windows, dropped_windows, merged_windows = _normalize_windows(
            raw_windows.unwrap()
        )

        windows_by_pid: dict[int, list[_NormalizedWindow]] = {}
        for window in normalized_windows:
            windows_by_pid.setdefault(window.process_id, []).append(window)

        processes: list[WindowsProcessIdentity] = []
        for candidate in normalized_processes:
            path_query = self._native_surface.query_executable_path(candidate.process_id)
            if path_query.is_failure:
                error = path_query.unwrap_error()
                if error.code == _native.NATIVE_UNAVAILABLE_ERROR_CODE:
                    return Result.failure(error)
                return Result.failure(
                    AgentXError(
                        code=PATH_QUERY_FAILED_ERROR_CODE,
                        message=(
                            f"Image path query failed for process "
                            f"{candidate.process_id}: {error.message}"
                        ),
                        category=error.category,
                        retryability=error.retryability,
                        details={
                            "process_id": candidate.process_id,
                            "cause_code": error.code,
                        },
                    )
                )
            outcome = path_query.unwrap()
            path_status = _map_win32_error(outcome.error_code)
            if path_status is MetadataStatus.AVAILABLE:
                path_status = _normalize_os_identifier(
                    outcome.value, max_chars=_MAX_EXECUTABLE_PATH_CHARS
                )
            path_value = (
                outcome.value
                if path_status is MetadataStatus.AVAILABLE and isinstance(outcome.value, str)
                else None
            )
            owned = windows_by_pid.get(candidate.process_id, [])
            processes.append(
                WindowsProcessIdentity(
                    process_id=candidate.process_id,
                    parent_process_id=candidate.parent_process_id,
                    executable_name=candidate.executable_name,
                    executable_name_status=candidate.executable_name_status,
                    executable_path=path_value,
                    executable_path_status=path_status,
                    window_handles=tuple(window.handle for window in owned),
                    visible_window_count=sum(1 for window in owned if window.is_visible),
                )
            )

        windows = tuple(
            WindowsWindowIdentity(
                handle=window.handle,
                process_id=window.process_id,
                title=window.title,
                title_status=window.title_status,
                class_name=window.class_name,
                class_name_status=window.class_name_status,
                is_visible=window.is_visible,
            )
            for window in normalized_windows
        )
        processes.sort(key=lambda process: process.sort_key)
        return Result.success(
            WindowsProcessSnapshot(
                processes=tuple(processes),
                windows=windows,
                dropped_invalid_entries=dropped_processes + dropped_windows,
                merged_duplicate_entries=merged_processes + merged_windows,
            )
        )

    def __repr__(self) -> str:
        return (
            f"WindowsProcessDiscovery(identity={self.identity!s}, "
            f"status={self._support.status.value!r})"
        )


# --------------------------------------------------------------------------
# Canonical capability wrapper.
# --------------------------------------------------------------------------

#: Canonical identity of the discovery capability.
WINDOWS_PROCESS_DISCOVERY_IDENTITY: Final[CapabilityIdentity] = CapabilityIdentity(
    name=CapabilityName("windows.processes.discover"),
    version=CapabilityVersion(1, 0, 0),
)

WINDOWS_PROCESS_DISCOVERY_DESCRIPTION: Final[str] = (
    "Read-only discovery of running Windows processes and their top-level "
    "application windows, normalized into a deterministic snapshot."
)


@dataclass(frozen=True, slots=True)
class WindowsProcessDiscoveryParams(CapabilityParams):
    """Typed parameters for the discovery capability.

    The operation takes none: one call returns one full normalized snapshot.
    Adding filters is a future, explicitly-versioned contract change.
    """

    def to_dict(self) -> dict[str, JsonValue]:
        return {}


class WindowsProcessDiscoveryCapability:
    """The discovery operation as an ordinary canonical Capability.

    This is how A5.02 discovery reaches the governed execution path: the
    composition root contributes the instance to the A5.01
    :class:`~agentx.capabilities.windows.provider.WindowsProvider` and
    registers it in the A1.09 registry it owns. The descriptor declares
    ``READ`` permission only and a read-only R0 risk assessment; hostile
    process/window metadata discovered at run time can never change either.

    ``execute`` performs exactly one :meth:`WindowsProcessDiscovery.discover`
    (after honouring cooperative cancellation) and returns the snapshot as
    observation evidence. ``verify`` checks that evidence for structural
    consistency — it never re-reads the OS and never manufactures success.
    """

    __slots__ = ("_descriptor", "_discovery")

    _descriptor: CapabilityDescriptor
    _discovery: WindowsProcessDiscovery

    def __init__(self, discovery: WindowsProcessDiscovery) -> None:
        """Bind the capability to one discovery operation object."""
        if not isinstance(discovery, WindowsProcessDiscovery):
            raise TypeError(
                f"discovery must be a WindowsProcessDiscovery, got {type(discovery).__name__}"
            )
        object.__setattr__(self, "_discovery", discovery)
        object.__setattr__(
            self,
            "_descriptor",
            CapabilityDescriptor(
                identity=WINDOWS_PROCESS_DISCOVERY_IDENTITY,
                description=WINDOWS_PROCESS_DISCOVERY_DESCRIPTION,
                scope=CapabilityScope(platform=CapabilityPlatform.WINDOWS),
                required_permissions=frozenset({Permission.READ}),
                risk_assessment=assess_risk(
                    read_only=True,
                    modifies_state=False,
                    reversible=False,
                    external_effect=False,
                ),
                preconditions=(
                    CapabilityPrecondition(
                        name="windows.supported_host",
                        description=(
                            "The host must evaluate as a supported Windows host through "
                            "the A5.01 provider boundary before discovery can run."
                        ),
                    ),
                ),
                rollback=RollbackDeclaration(
                    support=RollbackSupport.NOT_APPLICABLE,
                    detail="Discovery is a read-only snapshot; there is nothing to undo.",
                ),
                estimate=ResourceEstimate(
                    wall_clock=timedelta(milliseconds=250),
                    machine_actions=1,
                    external_cost=Decimal("0"),
                ),
            ),
        )

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"WindowsProcessDiscoveryCapability is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"WindowsProcessDiscoveryCapability is immutable; cannot delete {name!r}"
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        """The inert canonical descriptor governing this capability."""
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[WindowsProcessDiscoveryParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        """Perform one read-only discovery and return it as evidence."""
        if not isinstance(request.params, WindowsProcessDiscoveryParams):
            raise TypeError(
                "discovery requires WindowsProcessDiscoveryParams, got "
                f"{type(request.params).__name__}"
            )
        if context.observe_stop().should_stop:
            return ExecutionResult(
                succeeded=False,
                message="discovery was cooperatively cancelled before it started",
                observation=CapabilityObservation(
                    summary="discovery cancelled before any OS read",
                    data={"cancelled_before_start": True},
                ),
            )
        result = self._discovery.discover()
        if result.is_failure:
            error = result.unwrap_error()
            return ExecutionResult(
                succeeded=False,
                message=f"process discovery failed: {error.message}",
                observation=CapabilityObservation(
                    summary="process discovery failed",
                    data={"error": error.to_dict()},
                ),
            )
        snapshot = result.unwrap()
        return ExecutionResult(
            succeeded=True,
            message=(
                f"discovered {snapshot.process_count} processes and "
                f"{snapshot.window_count} top-level windows "
                f"({snapshot.application_count} presenting applications)"
            ),
            observation=CapabilityObservation(
                summary="read-only Windows process/application discovery snapshot",
                data=snapshot.to_dict(),
            ),
        )

    def verify(
        self,
        request: CapabilityRequest[WindowsProcessDiscoveryParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        """Check the discovery evidence for structural consistency.

        Verification of a read means verifying the *evidence*: the observation
        must be a well-formed, deterministically ordered snapshot whose
        counters agree with its contents. It never re-reads the OS (a second
        read would race with process churn and prove nothing) and a passing
        verdict grants nothing beyond this one check.
        """
        if not isinstance(observation, CapabilityObservation):
            raise TypeError(
                f"observation must be a CapabilityObservation, got {type(observation).__name__}"
            )
        data = observation.to_dict()["data"]
        if not isinstance(data, dict):
            return VerificationResult(
                passed=False, detail="discovery observation carries no data object"
            )
        if "processes" not in data or "windows" not in data:
            return VerificationResult(
                passed=False, detail="discovery observation is missing snapshot sections"
            )
        if "error" in data:
            error = data["error"]
            code = error.get("code") if isinstance(error, dict) else None
            return VerificationResult(
                passed=False,
                detail=f"discovery execution failed ({code}); nothing to verify",
            )
        processes = data["processes"]
        windows = data["windows"]
        if not isinstance(processes, list) or not isinstance(windows, list):
            return VerificationResult(
                passed=False, detail="discovery snapshot sections are malformed"
            )
        if data.get("process_count") != len(processes):
            return VerificationResult(
                passed=False, detail="process_count disagrees with the process list"
            )
        if data.get("window_count") != len(windows):
            return VerificationResult(
                passed=False, detail="window_count disagrees with the window list"
            )
        previous_pid = -1
        for entry in processes:
            if not isinstance(entry, dict):
                return VerificationResult(passed=False, detail="process entry is malformed")
            pid = entry.get("process_id")
            if type(pid) is not int or pid < 0:
                return VerificationResult(
                    passed=False, detail="process entry has an invalid process_id"
                )
            if pid <= previous_pid:
                return VerificationResult(
                    passed=False, detail="process entries are not in ascending order"
                )
            previous_pid = pid
        previous_handle = 0
        for entry in windows:
            if not isinstance(entry, dict):
                return VerificationResult(passed=False, detail="window entry is malformed")
            handle = entry.get("handle")
            if type(handle) is not int or handle <= previous_handle:
                return VerificationResult(
                    passed=False, detail="window entries are not in ascending handle order"
                )
            previous_handle = handle
        return VerificationResult(
            passed=True,
            detail=(
                f"observation is a consistent snapshot: {len(processes)} processes and "
                f"{len(windows)} windows in canonical order"
            ),
        )


def discovery_request() -> CapabilityRequest[WindowsProcessDiscoveryParams]:
    """Build a canonical request for the discovery capability (convenience)."""
    return CapabilityRequest(
        identity=WINDOWS_PROCESS_DISCOVERY_IDENTITY,
        params=WindowsProcessDiscoveryParams(),
    )
