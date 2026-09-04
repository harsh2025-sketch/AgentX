"""Opaque secret-reference and value boundaries for the AgentX Trusted Kernel.

Ordinary components should pass SecretRef values. Trusted composition code may
resolve a reference through a SecretResolver and receive a SecretValue whose
material is intentionally difficult to expose accidentally through logs,
representation, or common serialization paths.
"""

from __future__ import annotations

import re
from typing import NoReturn, Protocol, SupportsIndex

_SECRET_REF_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")


class SecretRef:
    """Immutable, printable identifier for secret material, never the material itself."""

    __slots__ = ("_identifier",)

    _identifier: str

    def __init__(self, identifier: str) -> None:
        if not isinstance(identifier, str):
            raise TypeError("secret reference identifier must be a string")
        if not _SECRET_REF_PATTERN.fullmatch(identifier):
            raise ValueError(
                "secret reference identifier must be 1-128 safe characters "
                "using letters, digits, '.', '_', ':', '/', or '-'"
            )
        object.__setattr__(self, "_identifier", identifier)

    @property
    def identifier(self) -> str:
        """Return the opaque reference identifier."""

        return self._identifier

    def __repr__(self) -> str:
        return f"SecretRef({self._identifier!r})"

    def __str__(self) -> str:
        return self._identifier

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretRef):
            return NotImplemented
        return self._identifier == other._identifier

    def __hash__(self) -> int:
        return hash((SecretRef, self._identifier))

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("SecretRef instances are immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("SecretRef instances are immutable")


class SecretValue:
    """Narrow in-memory wrapper for resolved secret string or byte material.

    ``reveal()`` is deliberately explicit and is intended only for trusted code
    at the point where a provider/API must consume the material. Python cannot
    guarantee secure memory zeroization, so this wrapper makes no such claim.
    """

    __slots__ = ("__material",)

    __material: str | bytes

    def __init__(self, material: str | bytes) -> None:
        if type(material) not in (str, bytes):
            raise TypeError("secret material must be exactly str or bytes")
        if len(material) == 0:
            raise ValueError("secret material must not be empty")
        object.__setattr__(self, "_SecretValue__material", material)

    def reveal(self) -> str | bytes:
        """Explicitly expose secret material to trusted composition/provider code."""

        return self.__material

    def __repr__(self) -> str:
        return "<SecretValue redacted>"

    def __str__(self) -> str:
        return "<SecretValue redacted>"

    def __format__(self, format_spec: str) -> str:
        return format(str(self), format_spec)

    def __eq__(self, other: object) -> bool:
        return self is other

    def __reduce_ex__(self, protocol: SupportsIndex) -> NoReturn:
        raise TypeError("SecretValue serialization is intentionally unsupported")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("SecretValue instances are immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("SecretValue instances are immutable")


class SecretResolver(Protocol):
    """Trusted boundary for resolving an opaque SecretRef into SecretValue material."""

    def resolve(self, reference: SecretRef, /) -> SecretValue:
        """Resolve one reference without changing authority or permission state."""
        ...


__all__ = ["SecretRef", "SecretResolver", "SecretValue"]
