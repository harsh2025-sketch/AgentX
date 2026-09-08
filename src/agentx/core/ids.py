"""Canonical opaque domain identifiers for AgentX subsystems.

This module provides strongly-typed, UUID-backed identifier types that prevent
accidental cross-domain equality while supporting round-trip string
serialization, hashing, and deterministic comparison.

Design:
    - Each domain ID type wraps a UUIDv4 value and carries a unique domain tag.
    - Equality and hashing include the domain tag, so IDs from different domains
      never compare equal even if their underlying UUID bytes are identical.
    - IDs are immutable and frozen after construction.
    - String serialization uses canonical lowercase UUID format (no domain prefix).
    - Malformed or nil UUIDs are rejected at parse time.

This module belongs to ``agentx.core`` and imports nothing from other AgentX
subsystems.
"""

from __future__ import annotations

import uuid as _uuid_mod
from typing import Self
from uuid import UUID

__all__ = [
    "ArtifactId",
    "AudioStreamId",
    "CapabilityId",
    "DecompositionId",
    "DomainId",
    "EpisodeId",
    "KnowledgeId",
    "NegativeExperienceId",
    "ProcedureId",
    "TaskId",
]


class _IdConstructionError(ValueError):
    """Raised when a domain ID cannot be constructed from the given input."""


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    """Validate that *value* is a non-nil UUID."""
    if not isinstance(value, UUID):
        raise _IdConstructionError(f"{field_name} must be a UUID instance")
    if value.int == 0:
        raise _IdConstructionError(f"{field_name} must not be the nil UUID")
    return value


class DomainId:
    """Base class for canonical opaque domain identifiers.

    Subclasses are strongly distinguished at the type level: two IDs with
    identical UUID bytes but different subclass types are never equal.

    Subclasses must not override ``__eq__``, ``__hash__``, or ``__repr__``.
    They are not intended to be subclassed outside this module.
    """

    __slots__ = ("_value",)

    #: Populated by ``__init_subclass__`` for each concrete subclass.
    _domain: str

    #: Underlying UUID value — declared for type checkers, stored in __slots__.
    _value: UUID

    def __init_subclass__(cls, *, domain: str = "", **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if domain:
            cls._domain = domain

    def __init__(self, value: UUID) -> None:
        _validate_uuid(value, field_name="value")
        object.__setattr__(self, "_value", value)

    @classmethod
    def create(cls) -> Self:
        """Generate a new globally unique identifier for this domain."""
        return cls(_uuid_mod.uuid4())

    @classmethod
    def parse(cls, raw: str) -> Self:
        """Parse a canonical UUID string into an identifier for this domain.

        Raises ``_IdConstructionError`` if *raw* is malformed or the nil UUID.
        """
        if not isinstance(raw, str):
            raise _IdConstructionError(
                f"{cls.__name__}.parse requires a string, got {type(raw).__name__}"
            )
        stripped = raw.strip()
        if not stripped:
            raise _IdConstructionError(f"{cls.__name__} string must not be empty")
        try:
            parsed = UUID(stripped)
        except ValueError as exc:
            raise _IdConstructionError(
                f"{cls.__name__} string is not a valid UUID: {stripped!r}"
            ) from exc
        if parsed.int == 0:
            raise _IdConstructionError(f"{cls.__name__} must not be the nil UUID")
        return cls(parsed)

    @property
    def value(self) -> UUID:
        """The underlying UUID value."""
        return self._value

    @property
    def domain(self) -> str:
        """The domain tag that distinguishes this ID type."""
        return self._domain

    def to_str(self) -> str:
        """Return the canonical string representation (lowercase UUID)."""
        return str(self._value)

    # -- Identity and hashing ------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DomainId):
            return NotImplemented
        # Both type and UUID must match. Different domain subclasses with
        # identical UUID bytes are deliberately NOT equal.
        if type(self) is not type(other):
            return False
        return self._value == other._value

    def __hash__(self) -> int:
        return hash((type(self), self._value))

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._value})"

    def __str__(self) -> str:
        return self.to_str()

    # -- Immutability --------------------------------------------------------

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(f"{type(self).__name__} instances are immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} instances are immutable")


# --------------------------------------------------------------------------
# Concrete domain ID types.
# --------------------------------------------------------------------------


class TaskId(DomainId, domain="task"):
    """Identifier for a task within the AgentX runtime."""


class CapabilityId(DomainId, domain="capability"):
    """Identifier for a capability registered in the AgentX runtime."""


class ProcedureId(DomainId, domain="procedure"):
    """Identifier for a procedure graph definition."""


class DecompositionId(DomainId, domain="decomposition"):
    """Identifier for one hierarchical task decomposition (A6.01)."""


class EpisodeId(DomainId, domain="episode"):
    """Identifier for a cognitive episode or reasoning session."""


class KnowledgeId(DomainId, domain="knowledge"):
    """Identifier for one persistent semantic/knowledge record (C2.02)."""


class NegativeExperienceId(DomainId, domain="negative_experience"):
    """Identifier for one recorded negative (failed-approach) experience (C2.06)."""


class ArtifactId(DomainId, domain="artifact"):
    """Identifier for an artifact produced or consumed by the runtime."""


class AudioStreamId(DomainId, domain="audio_stream"):
    """Identifier for one audio stream (A7.01).

    AgentX owns stream identity: a provider may restart, rename, or reuse its own
    session handle, but the stream a caller opened keeps this id for its whole
    life, which is what makes sequence numbers and timestamps comparable.
    """
