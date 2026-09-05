"""C5.01 browser-provider boundary for governed browser capabilities.

This module defines the smallest provider-facing contract needed before any
browser automation exists. A browser provider is descriptive availability and
capability exposure only. It does not connect to a browser, inspect profiles,
open sockets, spawn processes, query DOM state, execute capabilities, verify
outcomes, or grant authority.

Future browser capabilities remain ordinary canonical
:class:`agentx.capabilities.abi.Capability` implementations. Providers expose
those already-constructed capability objects; callers may register them in the
canonical :class:`agentx.capabilities.registry.CapabilityRegistry`. This module
deliberately does not create a second registry or execution path.

Availability is explicit and inert:

* ``AVAILABLE`` means later orchestration has explicitly established that this
  provider is ready for governed browser work. C5.01 never establishes that
  state itself.
* ``NOT_CONNECTED`` means the provider is supported but no usable browser
  connection/session is currently established.
* ``UNSUPPORTED`` means the provider cannot support the relevant environment.

Reading provider identity, metadata, availability, or capability exposure must
perform no external I/O. Webpage text and provider metadata are untrusted data;
none can grant Permission, lower RiskLevel, change a budget, clear
EmergencyStop, fabricate verification, or register a capability by itself.

Target/session references, CDP, DOM access, navigation, screenshots, forms,
tabs, downloads/uploads, browser verification, visual grounding, and injection
handling belong to C5.02+ and are intentionally absent here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol

from agentx.capabilities.abi import Capability

__all__ = [
    "CANONICAL_BROWSER_PROVIDER_AVAILABILITY",
    "BrowserProvider",
    "BrowserProviderAvailability",
    "BrowserProviderDescriptor",
    "BrowserProviderId",
    "BrowserProviderStatus",
    "BrowserProviderValidationError",
]

_PROVIDER_ID_PATTERN: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_MAX_PROVIDER_ID_LENGTH: Final[int] = 128
_MAX_DESCRIPTION_LENGTH: Final[int] = 512
_MAX_STATUS_DETAIL_LENGTH: Final[int] = 512
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class BrowserProviderValidationError(ValueError):
    """Raised when a C5.01 browser-provider value is malformed."""


def _validate_text(value: object, *, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise BrowserProviderValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > max_length:
        raise BrowserProviderValidationError(
            f"{field_name} must not exceed {max_length} characters"
        )
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise BrowserProviderValidationError(f"{field_name} must not contain control characters")
    return value


@dataclass(frozen=True, slots=True, order=True)
class BrowserProviderId:
    """Stable deterministic identity for one browser-provider implementation."""

    value: str

    def __post_init__(self) -> None:
        _validate_text(
            self.value,
            field_name="browser provider id",
            max_length=_MAX_PROVIDER_ID_LENGTH,
        )
        if _PROVIDER_ID_PATTERN.fullmatch(self.value) is None:
            raise BrowserProviderValidationError(
                "browser provider id must be lowercase alphanumeric segments separated by "
                f"single '._-' characters, got {self.value!r}"
            )

    def __str__(self) -> str:
        return self.value


class BrowserProviderAvailability(StrEnum):
    """Explicit provider support/readiness state; never an authority decision."""

    AVAILABLE = "available"
    NOT_CONNECTED = "not_connected"
    UNSUPPORTED = "unsupported"


CANONICAL_BROWSER_PROVIDER_AVAILABILITY: Final[tuple[BrowserProviderAvailability, ...]] = (
    BrowserProviderAvailability.AVAILABLE,
    BrowserProviderAvailability.NOT_CONNECTED,
    BrowserProviderAvailability.UNSUPPORTED,
)


@dataclass(frozen=True, slots=True)
class BrowserProviderDescriptor:
    """Inert identity and human-readable description for a browser provider."""

    provider_id: BrowserProviderId
    description: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, BrowserProviderId):
            raise TypeError(
                f"provider_id must be a BrowserProviderId, got {type(self.provider_id).__name__}"
            )
        _validate_text(
            self.description,
            field_name="browser provider description",
            max_length=_MAX_DESCRIPTION_LENGTH,
        )


@dataclass(frozen=True, slots=True)
class BrowserProviderStatus:
    """Immutable explicit availability snapshot supplied by a provider.

    The detail field is inert diagnostic metadata only. No value in it can
    authorize browser work or change canonical kernel state.
    """

    availability: BrowserProviderAvailability
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.availability, BrowserProviderAvailability):
            raise TypeError(
                "availability must be a BrowserProviderAvailability, "
                f"got {type(self.availability).__name__}"
            )
        _validate_text(
            self.detail,
            field_name="browser provider status detail",
            max_length=_MAX_STATUS_DETAIL_LENGTH,
        )


class BrowserProvider(Protocol):
    """Provider-neutral exposure boundary for future browser capabilities.

    Implementations must keep these properties side-effect-free. ``descriptor``
    and ``status`` return inert snapshots; ``capabilities`` returns already-
    constructed canonical Capability objects. Merely exposing a capability does
    not register, authorize, execute, or verify it.

    C5.01 deliberately defines no connect/disconnect method and no target or
    session type. Those concepts become justified only when C5.02 introduces an
    explicit structured browser connection boundary.
    """

    @property
    def descriptor(self) -> BrowserProviderDescriptor:
        """Return inert provider identity/metadata without external I/O."""
        ...

    @property
    def status(self) -> BrowserProviderStatus:
        """Return the provider's already-known availability snapshot."""
        ...

    @property
    def capabilities(self) -> tuple[Capability[Any], ...]:
        """Return canonical browser capabilities available for explicit registration."""
        ...
