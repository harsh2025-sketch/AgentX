"""C5.02 inert browser connection/session and target references.

This module represents already-established browser connection/session facts and
browser target facts for future governed browser capabilities. It does not
establish, probe, refresh, or close connections and it does not perform browser
automation.

All values are immutable snapshots. ``CONNECTED`` and ``AVAILABLE`` are
historical/current-state claims supplied by a caller, not authority decisions or
liveness guarantees. External target metadata is untrusted inert data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.capabilities.browser_provider import BrowserProviderId

__all__ = [
    "BROWSER_CONNECTION_SCHEMA_VERSION",
    "BROWSER_TARGET_SCHEMA_VERSION",
    "CANONICAL_BROWSER_CONNECTION_STATES",
    "CANONICAL_BROWSER_TARGET_KINDS",
    "CANONICAL_BROWSER_TARGET_STATES",
    "BrowserConnectionRef",
    "BrowserConnectionState",
    "BrowserConnectionValidationError",
    "BrowserSessionId",
    "BrowserTargetId",
    "BrowserTargetKind",
    "BrowserTargetRef",
    "BrowserTargetState",
]

BROWSER_CONNECTION_SCHEMA_VERSION: Final[int] = 1
BROWSER_TARGET_SCHEMA_VERSION: Final[int] = 1
_MAX_OPAQUE_ID_LENGTH: Final[int] = 512
_MAX_DETAIL_LENGTH: Final[int] = 1024
_MAX_TARGET_TITLE_LENGTH: Final[int] = 2048
_MAX_TARGET_URL_LENGTH: Final[int] = 8192
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class BrowserConnectionValidationError(ValueError):
    """Raised when a C5.02 connection/session or target reference is malformed."""


def _validate_opaque_id(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise BrowserConnectionValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > _MAX_OPAQUE_ID_LENGTH:
        raise BrowserConnectionValidationError(
            f"{field_name} must not exceed {_MAX_OPAQUE_ID_LENGTH} characters"
        )
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise BrowserConnectionValidationError(f"{field_name} must not contain control characters")
    return value


def _validate_detail(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"detail must be a string or None, got {type(value).__name__}")
    if len(value) > _MAX_DETAIL_LENGTH:
        raise BrowserConnectionValidationError(
            f"detail must not exceed {_MAX_DETAIL_LENGTH} characters"
        )
    return value


def _validate_untrusted_metadata(
    value: object,
    *,
    field_name: str,
    max_length: int,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None, got {type(value).__name__}")
    if len(value) > max_length:
        raise BrowserConnectionValidationError(
            f"{field_name} must not exceed {max_length} characters"
        )
    return value


@dataclass(frozen=True, slots=True, order=True)
class BrowserSessionId:
    """Provider-scoped deterministic identity for one browser connection/session."""

    provider_id: BrowserProviderId
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, BrowserProviderId):
            raise TypeError(
                f"provider_id must be a BrowserProviderId, got {type(self.provider_id).__name__}"
            )
        _validate_opaque_id(self.value, field_name="browser session id")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class BrowserTargetId:
    """Session-scoped deterministic identity for one browser target."""

    session_id: BrowserSessionId
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, BrowserSessionId):
            raise TypeError(
                f"session_id must be a BrowserSessionId, got {type(self.session_id).__name__}"
            )
        _validate_opaque_id(self.value, field_name="browser target id")

    @property
    def provider_id(self) -> BrowserProviderId:
        """Return the canonical C5.01 provider identity for this target identity."""
        return self.session_id.provider_id

    def __str__(self) -> str:
        return self.value


class BrowserConnectionState(StrEnum):
    """Explicit connection/session snapshot state; never an authority decision."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


CANONICAL_BROWSER_CONNECTION_STATES: Final[tuple[BrowserConnectionState, ...]] = (
    BrowserConnectionState.CONNECTED,
    BrowserConnectionState.DISCONNECTED,
    BrowserConnectionState.STALE,
    BrowserConnectionState.UNAVAILABLE,
)


class BrowserTargetKind(StrEnum):
    """Minimal provider-neutral target vocabulary.

    ``PAGE`` covers page/tab-like top-level browsing surfaces. ``WORKER``
    covers non-page execution targets. ``OTHER`` preserves an explicitly known
    target without inventing provider-specific target classes.
    """

    PAGE = "page"
    WORKER = "worker"
    OTHER = "other"


CANONICAL_BROWSER_TARGET_KINDS: Final[tuple[BrowserTargetKind, ...]] = (
    BrowserTargetKind.PAGE,
    BrowserTargetKind.WORKER,
    BrowserTargetKind.OTHER,
)


class BrowserTargetState(StrEnum):
    """Explicit target snapshot state; never permission or executability."""

    AVAILABLE = "available"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


CANONICAL_BROWSER_TARGET_STATES: Final[tuple[BrowserTargetState, ...]] = (
    BrowserTargetState.AVAILABLE,
    BrowserTargetState.STALE,
    BrowserTargetState.UNAVAILABLE,
)


@dataclass(frozen=True, slots=True)
class BrowserConnectionRef:
    """Immutable reference snapshot for one established-or-known browser session.

    This object contains no socket, process, protocol client, callback, or live
    browser handle. ``CONNECTED`` only records caller-supplied state at the
    boundary; it does not prove current reachability and does not authorize use.
    """

    session_id: BrowserSessionId
    state: BrowserConnectionState
    detail: str | None = None
    schema_version: int = BROWSER_CONNECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, BrowserSessionId):
            raise TypeError(
                f"session_id must be a BrowserSessionId, got {type(self.session_id).__name__}"
            )
        if not isinstance(self.state, BrowserConnectionState):
            raise TypeError(
                f"state must be a BrowserConnectionState, got {type(self.state).__name__}"
            )
        _validate_detail(self.detail)
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != BROWSER_CONNECTION_SCHEMA_VERSION:
            raise BrowserConnectionValidationError(
                f"unsupported browser connection schema version {self.schema_version}; "
                f"supported version is {BROWSER_CONNECTION_SCHEMA_VERSION}"
            )

    @property
    def provider_id(self) -> BrowserProviderId:
        """Return the exact canonical C5.01 provider identity."""
        return self.session_id.provider_id

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible inert snapshot."""
        return {
            "schema_version": self.schema_version,
            "provider_id": str(self.provider_id),
            "session_id": self.session_id.value,
            "state": self.state.value,
            "detail": self.detail,
        }

    def to_json(self) -> str:
        """Serialize deterministically without reconstruction or execution hooks."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class BrowserTargetRef:
    """Immutable target reference/snapshot associated with one connection session.

    ``title`` and ``url`` are optional untrusted metadata snapshots. They are
    preserved as data only and are never parsed, navigated to, executed, or
    promoted into capability or authority state by this contract.
    """

    connection: BrowserConnectionRef
    target_id: BrowserTargetId
    kind: BrowserTargetKind
    state: BrowserTargetState
    title: str | None = None
    url: str | None = None
    schema_version: int = BROWSER_TARGET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.connection, BrowserConnectionRef):
            raise TypeError(
                f"connection must be a BrowserConnectionRef, got {type(self.connection).__name__}"
            )
        if not isinstance(self.target_id, BrowserTargetId):
            raise TypeError(
                f"target_id must be a BrowserTargetId, got {type(self.target_id).__name__}"
            )
        if self.target_id.session_id != self.connection.session_id:
            raise BrowserConnectionValidationError(
                "target_id must belong to the exact provider/session in connection"
            )
        if not isinstance(self.kind, BrowserTargetKind):
            raise TypeError(f"kind must be a BrowserTargetKind, got {type(self.kind).__name__}")
        if not isinstance(self.state, BrowserTargetState):
            raise TypeError(f"state must be a BrowserTargetState, got {type(self.state).__name__}")
        if (
            self.state is BrowserTargetState.AVAILABLE
            and self.connection.state is not BrowserConnectionState.CONNECTED
        ):
            raise BrowserConnectionValidationError(
                "an available target requires a connected connection snapshot"
            )
        _validate_untrusted_metadata(
            self.title,
            field_name="target title",
            max_length=_MAX_TARGET_TITLE_LENGTH,
        )
        _validate_untrusted_metadata(
            self.url,
            field_name="target url",
            max_length=_MAX_TARGET_URL_LENGTH,
        )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != BROWSER_TARGET_SCHEMA_VERSION:
            raise BrowserConnectionValidationError(
                f"unsupported browser target schema version {self.schema_version}; "
                f"supported version is {BROWSER_TARGET_SCHEMA_VERSION}"
            )

    @property
    def provider_id(self) -> BrowserProviderId:
        """Return the exact canonical C5.01 provider identity."""
        return self.connection.provider_id

    @property
    def session_id(self) -> BrowserSessionId:
        """Return the exact session identity owning this target."""
        return self.connection.session_id

    def to_dict(self) -> dict[str, object]:
        """Return deterministic provider/session/target provenance and snapshot data."""
        return {
            "schema_version": self.schema_version,
            "provider_id": str(self.provider_id),
            "session_id": self.session_id.value,
            "target_id": self.target_id.value,
            "kind": self.kind.value,
            "state": self.state.value,
            "title": self.title,
            "url": self.url,
            "connection_state": self.connection.state.value,
        }

    def to_json(self) -> str:
        """Serialize deterministically without browser or dynamic-code behavior."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
