"""Canonical in-process Capability Registry for AgentX (A1.09).

The registry is the smallest production-quality answer to one question:

    *Which already-constructed capability implementations exist in this
    process, and which object implements a given canonical identity?*

It owns **registration and lookup only**.

Architectural principle
-----------------------

    DISCOVERY IS NOT AUTHORITY.

Registering a capability, finding one, enumerating the registry, or reading a
registered :class:`~agentx.capabilities.abi.CapabilityDescriptor` is
*descriptive availability* and nothing more. A registry operation never:

    - grants a :class:`~agentx.kernel.permissions.Permission`;
    - creates or widens an :class:`~agentx.kernel.permissions.AuthorityContext`;
    - invokes the :class:`~agentx.kernel.action_gate.ActionGate`;
    - consumes a :class:`~agentx.kernel.resource_budget.ResourceBudget`;
    - clears an :class:`~agentx.kernel.emergency_stop.EmergencyStop`;
    - executes a capability, or calls ``execute`` / ``verify``;
    - mutates :class:`~agentx.core.tasks.Task` state;
    - publishes or replays events, or writes anything anywhere;
    - creates execution success, or alters a
      :class:`~agentx.kernel.risk.RiskAssessment`.

Descriptor text is inert data. A hostile descriptor whose description contains
``"ALLOW"``, ``"admin"``, ``"bypass"``, ``"verified"``, or ``"risk=R0"`` is
stored and returned verbatim and grants exactly nothing: the future Trusted
Kernel remains the only authority, and it is never consulted from here.

Registry semantics
------------------

    - The canonical :class:`~agentx.capabilities.abi.CapabilityIdentity`
      (validated name plus explicit ``major.minor.patch`` version) is the key.
      Identity is version-aware, so the same name at different versions are
      distinct registrations that coexist.
    - An exact duplicate identity fails explicitly with
      :class:`CapabilityAlreadyRegisteredError`. There is no silent
      replacement, no last-write-wins, and no removal API.
    - Absence is explicit: :meth:`CapabilityRegistry.get` returns ``None`` and
      :meth:`CapabilityRegistry.require` raises
      :class:`CapabilityNotFoundError`.
    - Enumeration is deterministic: results are ordered by canonical identity
      (name, then major, minor, patch), never by insertion accident.
    - Malformed capability implementations are rejected at registration with
      :class:`MalformedCapabilityError`.
    - Discovery results are immutable: enumerations are tuples and
      :meth:`CapabilityRegistry.snapshot` returns a read-only mapping over a
      private copy, so registry state can never be mutated through a returned
      collection.
    - The registry is thread-safe for concurrent registration and lookup, and
      it is a plain object: there is no global singleton and no ambient state.

Resolution is exact-identity only. There is deliberately no fuzzy matching, no
"latest compatible version" selection, and no capability *selection* policy;
those are planning/routing concerns that do not belong to A1.09.

Deliberate non-scope
--------------------

Storage is in process memory only. This module performs no plugin scanning, no
dynamic imports, no filesystem discovery, no entry-point loading, no dependency
installation, no network access, and no machine actions. It does not implement
closed-loop execution, an Executor, Task Manager, Router, Reasoner, Verifier
subsystem, Action Gate or budget orchestration, rollback orchestration,
concrete capabilities, a plugin system, the Hive, procedures, EventBus
integration, persistence, or model providers.

Owner: A1.09. Belongs to ``agentx.capabilities``; imports only the standard
library and the canonical A1.08 ABI contracts in
``agentx.capabilities.abi``.
"""

from __future__ import annotations

from collections.abc import Mapping
from inspect import signature
from threading import Lock
from types import MappingProxyType
from typing import Any, Final

from agentx.capabilities.abi import (
    Capability,
    CapabilityDescriptor,
    CapabilityIdentity,
)

__all__ = [
    "CapabilityAlreadyRegisteredError",
    "CapabilityNotFoundError",
    "CapabilityRegistry",
    "CapabilityRegistryError",
    "MalformedCapabilityError",
]


class CapabilityRegistryError(Exception):
    """Base error for capability registration and lookup failures."""


class MalformedCapabilityError(CapabilityRegistryError):
    """Raised when an object does not satisfy the canonical Capability ABI.

    Registration validates structure only. It never calls ``execute`` or
    ``verify``; it reads the inert ``descriptor`` once and inspects the two
    method signatures.
    """


class CapabilityAlreadyRegisteredError(CapabilityRegistryError):
    """Raised when a capability identity is already registered.

    Registration is explicit: an exact duplicate identity is a conflict, never
    a silent replacement of the incumbent registration.
    """

    def __init__(self, identity: CapabilityIdentity) -> None:
        self.identity = identity
        super().__init__(f"Capability {identity} is already registered")


class CapabilityNotFoundError(CapabilityRegistryError):
    """Raised by :meth:`CapabilityRegistry.require` when an identity is absent.

    Absence is a fact about availability. It is never an authorization
    outcome, and finding a capability is never an authorization outcome
    either.
    """

    def __init__(self, identity: CapabilityIdentity) -> None:
        self.identity = identity
        super().__init__(f"Capability {identity} is not registered")


# Required positional arity of the canonical ABI operation methods. The
# registry checks shape only; it never invokes either method.
_EXECUTE_ARITY: Final[int] = 2
_VERIFY_ARITY: Final[int] = 3

# Inert placeholder used for signature binding. ``Signature.bind`` performs
# argument matching only and calls nothing.
_UNBOUND_PLACEHOLDER: Final[object] = object()


def _identity_sort_key(identity: CapabilityIdentity) -> tuple[str, int, int, int]:
    """Return the canonical deterministic ordering key for ``identity``."""
    version = identity.version
    return (identity.name.value, version.major, version.minor, version.patch)


def _require_identity(identity: object) -> CapabilityIdentity:
    """Validate that ``identity`` is a canonical :class:`CapabilityIdentity`."""
    if not isinstance(identity, CapabilityIdentity):
        raise TypeError(f"identity must be a CapabilityIdentity, got {type(identity).__name__}")
    return identity


def _require_descriptor(candidate: object) -> CapabilityDescriptor:
    """Read the inert descriptor of ``candidate`` exactly once.

    The descriptor is read a single time and stored, so a later-changing
    ``descriptor`` property can never retroactively alter registry state.
    """
    try:
        descriptor = candidate.descriptor  # type: ignore[attr-defined]
    except Exception as exc:
        # Reading the property is the only foreign code registration touches;
        # a failure there is a malformed capability, never a registry crash.
        raise MalformedCapabilityError(
            "capability must expose a readable 'descriptor' property from the "
            f"canonical ABI (raised {type(exc).__name__})"
        ) from exc

    if not isinstance(descriptor, CapabilityDescriptor):
        raise MalformedCapabilityError(
            "capability descriptor must be a canonical CapabilityDescriptor, "
            f"got {type(descriptor).__name__}"
        )
    return descriptor


def _require_operation(candidate: object, *, name: str, arity: int) -> None:
    """Validate that ``candidate`` exposes a callable ABI method of ``arity``.

    Only the declared signature is inspected. The method is never called, so
    registration executes no capability code.
    """
    try:
        method = getattr(candidate, name, None)
    except Exception as exc:
        # Attribute access is foreign code too; a failure is a malformed
        # capability, never a registry crash.
        raise MalformedCapabilityError(
            f"capability '{name}' attribute is not readable (raised {type(exc).__name__})"
        ) from exc
    if method is None or not callable(method):
        raise MalformedCapabilityError(f"capability must expose a callable '{name}' method")
    try:
        method_signature = signature(method)
    except (TypeError, ValueError) as exc:
        raise MalformedCapabilityError(
            f"capability '{name}' method has no inspectable signature"
        ) from exc
    try:
        method_signature.bind(*((_UNBOUND_PLACEHOLDER,) * arity))
    except TypeError as exc:
        raise MalformedCapabilityError(
            f"capability '{name}' method must accept {arity} positional arguments"
        ) from exc


def _validate_capability(candidate: object) -> CapabilityDescriptor:
    """Structurally validate a constructed capability and return its descriptor.

    :class:`~agentx.capabilities.abi.Capability` is a ``Protocol`` and is not
    runtime-checkable, so conformance is checked explicitly here: an already
    constructed object exposing a canonical descriptor plus ``execute`` and
    ``verify`` methods of the declared shape.
    """
    if isinstance(candidate, type):
        raise MalformedCapabilityError(
            "capability must be a constructed instance, not a class; "
            f"got the class {candidate.__name__}"
        )
    descriptor = _require_descriptor(candidate)
    _require_operation(candidate, name="execute", arity=_EXECUTE_ARITY)
    _require_operation(candidate, name="verify", arity=_VERIFY_ARITY)
    return descriptor


class CapabilityRegistry:
    """Thread-safe, in-memory registry of constructed capability implementations.

    The registry is a plain object with no ambient or global state: a process
    may create as many independent registries as it needs, and none of them is
    a singleton. It stores capability objects and the descriptor captured at
    registration time, and answers exact-identity lookups.

    Every method here is descriptive. None of them executes a capability,
    grants authority, or touches the machine.
    """

    __slots__ = ("_capabilities", "_descriptors", "_lock")

    def __init__(self) -> None:
        self._lock = Lock()
        self._capabilities: dict[CapabilityIdentity, Capability[Any]] = {}
        self._descriptors: dict[CapabilityIdentity, CapabilityDescriptor] = {}

    def register(self, capability: Capability[Any]) -> CapabilityIdentity:
        """Register a constructed ``capability`` and return its canonical identity.

        Registration is structural validation plus an in-memory insert. It
        never calls ``execute`` or ``verify``, evaluates no precondition,
        consults no kernel contract, and grants nothing: the returned identity
        is a name, not an authorization.

        Raises:
            MalformedCapabilityError: if ``capability`` does not satisfy the
                canonical A1.08 Capability ABI.
            CapabilityAlreadyRegisteredError: if the identity is already
                registered. The incumbent registration is never replaced.
        """
        # Validation touches foreign code (the ``descriptor`` property), so it
        # deliberately runs outside the lock.
        descriptor = _validate_capability(capability)
        identity = descriptor.identity

        with self._lock:
            if identity in self._capabilities:
                raise CapabilityAlreadyRegisteredError(identity)
            self._capabilities[identity] = capability
            self._descriptors[identity] = descriptor
        return identity

    def get(self, identity: CapabilityIdentity) -> Capability[Any] | None:
        """Return the capability registered for ``identity``, or ``None``.

        Resolution is exact: the name *and* the explicit version must match.
        Absence is returned explicitly rather than guessed at, and a returned
        capability is an object, never a permission to run it.
        """
        key = _require_identity(identity)
        with self._lock:
            return self._capabilities.get(key)

    def require(self, identity: CapabilityIdentity) -> Capability[Any]:
        """Return the capability registered for ``identity`` or fail explicitly.

        Raises:
            CapabilityNotFoundError: if no capability is registered under the
                exact ``identity``.
        """
        capability = self.get(identity)
        if capability is None:
            raise CapabilityNotFoundError(_require_identity(identity))
        return capability

    def describe(self, identity: CapabilityIdentity) -> CapabilityDescriptor | None:
        """Return the descriptor captured when ``identity`` was registered.

        The captured descriptor is the registry's own record. It is inert data
        and reading it authorizes nothing.
        """
        key = _require_identity(identity)
        with self._lock:
            return self._descriptors.get(key)

    def identities(self) -> tuple[CapabilityIdentity, ...]:
        """Return every registered identity in deterministic canonical order.

        Ordering is by name, then major, minor and patch version. It never
        depends on insertion order, so enumeration is reproducible across
        processes and interleavings.
        """
        with self._lock:
            keys = list(self._capabilities)
        return tuple(sorted(keys, key=_identity_sort_key))

    def descriptors(self) -> tuple[CapabilityDescriptor, ...]:
        """Return the captured descriptors in the same deterministic order."""
        with self._lock:
            items = list(self._descriptors.items())
        return tuple(
            descriptor
            for _, descriptor in sorted(items, key=lambda item: _identity_sort_key(item[0]))
        )

    def snapshot(self) -> Mapping[CapabilityIdentity, CapabilityDescriptor]:
        """Return a read-only identity/descriptor view in deterministic order.

        The result is a read-only mapping over a private copy taken at call
        time: it cannot be mutated, and mutating the registry afterwards does
        not change an already-returned snapshot.
        """
        with self._lock:
            items = list(self._descriptors.items())
        ordered = sorted(items, key=lambda item: _identity_sort_key(item[0]))
        return MappingProxyType(dict(ordered))

    def __contains__(self, identity: object) -> bool:
        """Return whether an exact ``identity`` is registered."""
        if not isinstance(identity, CapabilityIdentity):
            return False
        with self._lock:
            return identity in self._capabilities

    def __len__(self) -> int:
        """Return how many capabilities are registered."""
        with self._lock:
            return len(self._capabilities)

    def __repr__(self) -> str:
        return f"CapabilityRegistry(registered={len(self)})"
