"""Bounded missing-capability detection over the canonical registry (AX-541).

This module answers one descriptive question: which explicitly required
CapabilityIdentity values are absent from a registry snapshot?  It never loads,
installs, generates, imports, selects, executes, or authorizes a capability.
A gap is planning evidence only and cannot grant permission or lower risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.capabilities.abi import CapabilityIdentity
from agentx.capabilities.registry import CapabilityRegistry

__all__ = [
    "MAX_CAPABILITY_GAP_REQUIREMENTS",
    "CapabilityAvailability",
    "CapabilityGap",
    "CapabilityGapDetector",
    "CapabilityGapReport",
    "CapabilityGapValidationError",
]

MAX_CAPABILITY_GAP_REQUIREMENTS: Final[int] = 128
_MAX_ALTERNATIVES_PER_GAP: Final[int] = 32


class CapabilityGapValidationError(ValueError):
    """Raised when a gap-detection request is malformed or unbounded."""


class CapabilityAvailability(StrEnum):
    AVAILABLE = "available"
    MISSING = "missing"


def _identity_key(identity: CapabilityIdentity) -> tuple[str, int, int, int]:
    version = identity.version
    return (identity.name.value, version.major, version.minor, version.patch)


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
                raise CapabilityGapValidationError("alternatives must have the required capability name")
            if alternative == self.required:
                raise CapabilityGapValidationError("the required identity cannot be its own alternative")
            if alternative in seen:
                raise CapabilityGapValidationError("duplicate capability alternative")
            seen.add(alternative)
        object.__setattr__(self, "alternatives", tuple(sorted(self.alternatives, key=_identity_key)))


@dataclass(frozen=True, slots=True)
class CapabilityGapReport:
    """Deterministic availability evidence for one bounded requirement set."""

    required: tuple[CapabilityIdentity, ...]
    available: tuple[CapabilityIdentity, ...]
    gaps: tuple[CapabilityGap, ...]

    @property
    def is_satisfied(self) -> bool:
        """Whether every exact identity was present; this grants no authority."""
        return not self.gaps


class CapabilityGapDetector:
    """Read-only exact-identity detector backed by CapabilityRegistry snapshots."""

    __slots__ = ("_registry",)

    def __init__(self, registry: CapabilityRegistry) -> None:
        if not isinstance(registry, CapabilityRegistry):
            raise TypeError("registry must be a CapabilityRegistry")
        self._registry = registry

    def detect(self, required: tuple[CapabilityIdentity, ...]) -> CapabilityGapReport:
        if not isinstance(required, tuple):
            raise TypeError("required must be a tuple")
        if not required:
            raise CapabilityGapValidationError("required must not be empty")
        if len(required) > MAX_CAPABILITY_GAP_REQUIREMENTS:
            raise CapabilityGapValidationError("required exceeds the bounded requirement limit")

        seen: set[CapabilityIdentity] = set()
        for identity in required:
            if not isinstance(identity, CapabilityIdentity):
                raise TypeError("required must contain CapabilityIdentity values")
            if identity in seen:
                raise CapabilityGapValidationError("required identities must be unique")
            seen.add(identity)

        ordered_required = tuple(sorted(required, key=_identity_key))
        snapshot = self._registry.snapshot()
        registered = tuple(snapshot)
        available: list[CapabilityIdentity] = []
        gaps: list[CapabilityGap] = []
        for identity in ordered_required:
            if identity in snapshot:
                available.append(identity)
                continue
            alternatives = tuple(
                candidate
                for candidate in registered
                if candidate.name == identity.name and candidate != identity
            )
            if len(alternatives) > _MAX_ALTERNATIVES_PER_GAP:
                alternatives = alternatives[:_MAX_ALTERNATIVES_PER_GAP]
            gaps.append(CapabilityGap(required=identity, alternatives=alternatives))
        return CapabilityGapReport(
            required=ordered_required,
            available=tuple(available),
            gaps=tuple(gaps),
        )
