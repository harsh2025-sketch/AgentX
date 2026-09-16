"""Bounded missing-capability detection over an inert inventory snapshot (AX-541).

This module answers one descriptive question: which explicitly required
CapabilityIdentity values are absent from an explicitly complete identity snapshot?
Incomplete visibility is unknown; restricted identities are never missing. The
completeness and restriction metadata must come from the inventory owner, not
model or retrieved text. This module does not decide permissions. It
never imports the registry, loads, installs, generates, selects, executes, or
authorizes a capability. A gap is planning evidence only and cannot grant
permission or lower risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.capabilities.abi import CapabilityIdentity

__all__ = [
    "MAX_CAPABILITY_GAP_REQUIREMENTS",
    "MAX_CAPABILITY_INVENTORY",
    "CapabilityAvailability",
    "CapabilityGap",
    "CapabilityGapDetector",
    "CapabilityGapReport",
    "CapabilityGapValidationError",
]

MAX_CAPABILITY_GAP_REQUIREMENTS: Final[int] = 128
MAX_CAPABILITY_INVENTORY: Final[int] = 4096
_MAX_ALTERNATIVES_PER_GAP: Final[int] = 32


class CapabilityGapValidationError(ValueError):
    """Raised when gap-detection input is malformed or unbounded."""


class CapabilityAvailability(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"
    UNKNOWN = "unknown"
    RESTRICTED = "restricted"


def _identity_key(identity: CapabilityIdentity) -> tuple[str, int, int, int]:
    version = identity.version
    return (identity.name.value, version.major, version.minor, version.patch)


def _validate_identity_tuple(
    identities: object,
    *,
    field_name: str,
    maximum: int,
    allow_empty: bool,
) -> tuple[CapabilityIdentity, ...]:
    if not isinstance(identities, tuple):
        raise TypeError(f"{field_name} must be a tuple")
    if not identities and not allow_empty:
        raise CapabilityGapValidationError(f"{field_name} must not be empty")
    if len(identities) > maximum:
        raise CapabilityGapValidationError(f"{field_name} exceeds the bounded limit")
    seen: set[CapabilityIdentity] = set()
    for identity in identities:
        if not isinstance(identity, CapabilityIdentity):
            raise TypeError(f"{field_name} must contain CapabilityIdentity values")
        if identity in seen:
            raise CapabilityGapValidationError(f"{field_name} identities must be unique")
        seen.add(identity)
    return tuple(sorted(identities, key=_identity_key))


@dataclass(frozen=True, slots=True)
class CapabilityGap:
    """One absent exact identity plus inert same-name alternatives."""

    required: CapabilityIdentity
    alternatives: tuple[CapabilityIdentity, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.required, CapabilityIdentity):
            raise TypeError("required must be a CapabilityIdentity")
        if not isinstance(self.alternatives, tuple):
            raise TypeError("alternatives must be a tuple")
        if len(self.alternatives) > _MAX_ALTERNATIVES_PER_GAP:
            raise CapabilityGapValidationError("too many capability alternatives")
        seen: set[CapabilityIdentity] = set()
        for alternative in self.alternatives:
            if not isinstance(alternative, CapabilityIdentity):
                raise TypeError("alternatives must contain CapabilityIdentity values")
            if alternative.name != self.required.name:
                raise CapabilityGapValidationError(
                    "alternatives must have the required capability name"
                )
            if alternative == self.required:
                raise CapabilityGapValidationError(
                    "the required identity cannot be its own alternative"
                )
            if alternative in seen:
                raise CapabilityGapValidationError("duplicate capability alternative")
            seen.add(alternative)
        object.__setattr__(
            self,
            "alternatives",
            tuple(sorted(self.alternatives, key=_identity_key)),
        )


@dataclass(frozen=True, slots=True)
class CapabilityGapReport:
    """Deterministic availability evidence for one bounded requirement set."""

    required: tuple[CapabilityIdentity, ...]
    available: tuple[CapabilityIdentity, ...]
    gaps: tuple[CapabilityGap, ...]
    unknown: tuple[CapabilityIdentity, ...] = ()
    restricted: tuple[CapabilityIdentity, ...] = ()

    @property
    def is_satisfied(self) -> bool:
        """Whether every exact identity was present; this grants no authority."""
        return not (self.gaps or self.unknown or self.restricted)


class CapabilityGapDetector:
    """Read-only exact-identity detector over an injected immutable snapshot."""

    __slots__ = ("_inventory", "_inventory_complete", "_inventory_set", "_restricted")

    def __init__(
        self,
        inventory: tuple[CapabilityIdentity, ...],
        *,
        inventory_complete: bool = False,
        restricted: tuple[CapabilityIdentity, ...] = (),
    ) -> None:
        if type(inventory_complete) is not bool:
            raise TypeError("inventory_complete must be a bool")
        self._inventory_complete = inventory_complete
        self._restricted = frozenset(
            _validate_identity_tuple(
                restricted,
                field_name="restricted",
                maximum=MAX_CAPABILITY_INVENTORY,
                allow_empty=True,
            )
        )
        ordered = _validate_identity_tuple(
            inventory,
            field_name="inventory",
            maximum=MAX_CAPABILITY_INVENTORY,
            allow_empty=True,
        )
        self._inventory = ordered
        self._inventory_set = frozenset(ordered)

    def detect(self, required: tuple[CapabilityIdentity, ...]) -> CapabilityGapReport:
        ordered_required = _validate_identity_tuple(
            required,
            field_name="required",
            maximum=MAX_CAPABILITY_GAP_REQUIREMENTS,
            allow_empty=False,
        )

        available: list[CapabilityIdentity] = []
        gaps: list[CapabilityGap] = []
        unknown: list[CapabilityIdentity] = []
        restricted: list[CapabilityIdentity] = []
        for identity in ordered_required:
            if identity in self._restricted:
                restricted.append(identity)
                continue
            if identity in self._inventory_set:
                available.append(identity)
                continue
            if not self._inventory_complete:
                unknown.append(identity)
                continue
            alternatives = tuple(
                candidate
                for candidate in self._inventory
                if candidate.name == identity.name
                and candidate != identity
                and candidate not in self._restricted
            )[:_MAX_ALTERNATIVES_PER_GAP]
            gaps.append(CapabilityGap(required=identity, alternatives=alternatives))
        return CapabilityGapReport(
            required=ordered_required,
            available=tuple(available),
            gaps=tuple(gaps),
            unknown=tuple(unknown),
            restricted=tuple(restricted),
        )
