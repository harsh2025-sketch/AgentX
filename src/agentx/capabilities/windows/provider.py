"""Windows provider boundary for AgentX (A5.01).

This module is the smallest production-quality provider/adapter boundary that
subsequent Windows tasks (A5.02+) need in order to exist at all. It answers
three questions and nothing else:

    1. *Who is the Windows provider?* — a deterministic, stable provider
       identity (:data:`WINDOWS_PROVIDER_IDENTITY`).
    2. *Can it operate here?* — an explicit, testable platform-facts value
       (:class:`PlatformFacts`) evaluated by a pure function
       (:func:`evaluate_windows_support`) into an explicit verdict
       (:class:`WindowsSupport`).
    3. *How do Windows capabilities reach the canonical execution path?* — a
       provider object (:class:`WindowsProvider`) that holds
       already-constructed capabilities and can contribute them to a
       caller-owned :class:`~agentx.capabilities.registry.CapabilityRegistry`.

Deliberate non-scope
--------------------

A5.01 implements **no Windows automation**. There is no Win32 call, no COM, no
UI Automation tree walking, no ``pywinauto``, no keyboard/mouse injection, no
clipboard access, no screenshot, no OCR, no visual grounding, no window
selection, no process enumeration, no app launching, no file dialog, and no
browser automation. Those are owned by A5.02-A5.10. This module imports only
the standard library plus canonical ``agentx`` contracts, and it adds no
runtime dependency (no ``pywin32``).

Capability ABI relationship
---------------------------

There is no "Windows Capability V2". Windows capabilities are ordinary
:class:`~agentx.capabilities.abi.Capability` implementations described by
ordinary :class:`~agentx.capabilities.abi.CapabilityDescriptor` values, keyed
by the canonical :class:`~agentx.capabilities.abi.CapabilityIdentity`, and
registered in the canonical A1.09 registry. This module composes those
contracts; it never redefines, wraps, or shadows them. The only new concepts
here are *provider identity* and *provider support*, which the canonical ABI
does not express.

Platform detection semantics
----------------------------

Detection is explicit and never implicit:

    - importing this module reads no platform state, registers nothing, and
      imports nothing native;
    - :func:`detect_platform_facts` is the only function that reads the host,
      and it reads only :mod:`platform`/:mod:`sys` strings — never a native
      API;
    - :func:`evaluate_windows_support` is a pure function of
      :class:`PlatformFacts`, so unit tests can assert both the supported and
      the unsupported branch deterministically on any host;
    - :class:`WindowsProvider` requires facts to be supplied explicitly, so a
      test never depends on the machine it runs on.

An unsupported environment fails predictably as an explicit canonical
:class:`~agentx.core.errors.AgentXError` carried in a
:class:`~agentx.core.result.Result` — never as an ``ImportError`` from an eager
native import, and never as a silent no-op.

Authority
---------

    AVAILABILITY IS NOT AUTHORITY.

Constructing the provider, reporting support, listing contributions, or
registering them grants nothing. This module never creates or widens an
``AuthorityContext``, never grants a :class:`~agentx.kernel.permissions.Permission`,
never invokes the ``ActionGate``, never alters a
:class:`~agentx.kernel.risk.RiskAssessment`, never enlarges a
``ResourceEnvelope``, never clears an ``EmergencyStop``, never calls
``execute`` or ``verify``, never mutates Task state, and never produces a
verification verdict. Provider-supplied text (names, descriptions) is inert
data: hostile metadata is stored and returned verbatim and authorizes nothing.
The Trusted Kernel remains the authority boundary.

Owner: A5.01. Belongs to ``agentx.capabilities``; imports only the standard
library, ``agentx.core`` contracts, and the canonical capability ABI/registry.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from agentx.capabilities.abi import (
    Capability,
    CapabilityDescriptor,
    CapabilityIdentity,
    CapabilityPlatform,
    CapabilityVersion,
)
from agentx.capabilities.registry import CapabilityRegistry
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.result import Result

__all__ = [
    "WINDOWS_PROVIDER_IDENTITY",
    "WINDOWS_PROVIDER_NAME",
    "WINDOWS_PROVIDER_VERSION",
    "WINDOWS_UNSUPPORTED_ERROR_CODE",
    "PlatformFacts",
    "WindowsProvider",
    "WindowsProviderIdentity",
    "WindowsSupport",
    "WindowsSupportStatus",
    "detect_platform_facts",
    "evaluate_windows_support",
    "unsupported_platform_error",
]

_MAX_FACT_LENGTH: Final[int] = 256
_WINDOWS_SYSTEM: Final[str] = "Windows"

#: Canonical error code used when the Windows provider cannot operate here.
#: It is an ordinary canonical :class:`~agentx.core.errors.AgentXError` code;
#: A5.01 introduces no Windows-specific error *type* because the canonical
#: taxonomy already expresses "precondition not met, do not retry".
WINDOWS_UNSUPPORTED_ERROR_CODE: Final[str] = "capabilities.windows.unsupported_platform"


def _validate_fact(value: object, *, field_name: str) -> str:
    """Validate one short, trimmed, control-character-free platform fact."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if value != value.strip():
        raise ValueError(f"{field_name} must be trimmed")
    if len(value) > _MAX_FACT_LENGTH:
        raise ValueError(f"{field_name} must not exceed {_MAX_FACT_LENGTH} characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value


# --------------------------------------------------------------------------
# Provider identity.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WindowsProviderIdentity:
    """Deterministic identity of a capability provider.

    Identity is descriptive naming only. It is deliberately *not* a
    :class:`~agentx.capabilities.abi.CapabilityIdentity`: a provider is not a
    capability and must never be registered as one. The explicit
    :class:`~agentx.capabilities.abi.CapabilityVersion` is reused rather than
    re-invented, and :class:`~agentx.capabilities.abi.CapabilityPlatform`
    supplies the controlled platform vocabulary.
    """

    name: str
    version: CapabilityVersion
    platform: CapabilityPlatform

    def __post_init__(self) -> None:
        name = _validate_fact(self.name, field_name="provider identity name")
        if not name:
            raise ValueError("provider identity name must be non-empty")
        if not isinstance(self.version, CapabilityVersion):
            raise TypeError(
                f"version must be a CapabilityVersion, got {type(self.version).__name__}"
            )
        if not isinstance(self.platform, CapabilityPlatform):
            raise TypeError(
                f"platform must be a CapabilityPlatform, got {type(self.platform).__name__}"
            )

    def __str__(self) -> str:
        return f"{self.name}@{self.version.to_str()}"


#: Stable provider name. A5.02+ must reuse it rather than re-spelling it.
WINDOWS_PROVIDER_NAME: Final[str] = "windows"

#: Explicit provider contract version (``major.minor.patch``).
WINDOWS_PROVIDER_VERSION: Final[CapabilityVersion] = CapabilityVersion(1, 0, 0)

#: The single canonical Windows provider identity.
WINDOWS_PROVIDER_IDENTITY: Final[WindowsProviderIdentity] = WindowsProviderIdentity(
    name=WINDOWS_PROVIDER_NAME,
    version=WINDOWS_PROVIDER_VERSION,
    platform=CapabilityPlatform.WINDOWS,
)


# --------------------------------------------------------------------------
# Platform facts and support verdict.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlatformFacts:
    """Explicit, inert description of the host the provider might operate on.

    Facts are plain strings gathered from :mod:`platform`/:mod:`sys`. They are
    a *value*, so support evaluation is a pure function and every test can
    construct both a Windows and a non-Windows host without touching the real
    machine.
    """

    system: str
    release: str
    version: str
    machine: str

    def __post_init__(self) -> None:
        _validate_fact(self.system, field_name="facts.system")
        _validate_fact(self.release, field_name="facts.release")
        _validate_fact(self.version, field_name="facts.version")
        _validate_fact(self.machine, field_name="facts.machine")

    @property
    def is_windows(self) -> bool:
        """Whether these facts describe a Windows host (case-insensitive)."""
        return self.system.casefold() == _WINDOWS_SYSTEM.casefold()


def detect_platform_facts() -> PlatformFacts:
    """Read the current host's platform facts.

    This is the only host-reading function in the module and it is never
    called at import time. It reads standard-library strings only: it loads no
    native module, opens no handle, and touches no Windows API. Calling it on
    Linux or macOS is safe and simply reports a non-Windows host.
    """
    return PlatformFacts(
        system=platform.system().strip(),
        release=platform.release().strip(),
        version=platform.version().strip(),
        machine=(platform.machine().strip() or sys.platform.strip()),
    )


class WindowsSupportStatus(StrEnum):
    """Explicit support verdict vocabulary for the Windows provider."""

    SUPPORTED = "supported"
    UNSUPPORTED_PLATFORM = "unsupported_platform"


@dataclass(frozen=True, slots=True)
class WindowsSupport:
    """The provider's explicit verdict about one set of platform facts.

    A verdict is descriptive availability. ``SUPPORTED`` means only "this host
    is a Windows host, so future Windows adapters could operate here"; it is
    never permission to act.
    """

    status: WindowsSupportStatus
    facts: PlatformFacts
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, WindowsSupportStatus):
            raise TypeError(
                f"status must be a WindowsSupportStatus, got {type(self.status).__name__}"
            )
        if not isinstance(self.facts, PlatformFacts):
            raise TypeError(f"facts must be PlatformFacts, got {type(self.facts).__name__}")
        reason = _validate_fact(self.reason, field_name="support.reason")
        if not reason:
            raise ValueError("support.reason must be non-empty")

    @property
    def is_supported(self) -> bool:
        """Whether the provider could operate on the described host."""
        return self.status is WindowsSupportStatus.SUPPORTED


def evaluate_windows_support(facts: PlatformFacts) -> WindowsSupport:
    """Evaluate ``facts`` into an explicit support verdict.

    Pure and deterministic: same facts in, same verdict out, on every host and
    in every process. It performs no detection of its own — pass
    :func:`detect_platform_facts` explicitly when the real host is meant.
    """
    if not isinstance(facts, PlatformFacts):
        raise TypeError(f"facts must be PlatformFacts, got {type(facts).__name__}")
    if facts.is_windows:
        return WindowsSupport(
            status=WindowsSupportStatus.SUPPORTED,
            facts=facts,
            reason="host reports a Windows platform",
        )
    return WindowsSupport(
        status=WindowsSupportStatus.UNSUPPORTED_PLATFORM,
        facts=facts,
        reason=f"host platform {facts.system!r} is not Windows",
    )


def unsupported_platform_error(support: WindowsSupport) -> AgentXError:
    """Build the canonical error describing an unsupported Windows host.

    The canonical taxonomy already expresses this failure, so A5.01 adds no
    new exception type: it is a ``PRECONDITION`` / ``NON_RETRYABLE``
    :class:`~agentx.core.errors.AgentXError`. The error is inert data and
    grants nothing.
    """
    if not isinstance(support, WindowsSupport):
        raise TypeError(f"support must be a WindowsSupport, got {type(support).__name__}")
    if support.is_supported:
        raise ValueError("unsupported_platform_error requires an unsupported verdict")
    return AgentXError(
        code=WINDOWS_UNSUPPORTED_ERROR_CODE,
        message=f"Windows provider is unavailable: {support.reason}",
        category=ErrorCategory.PRECONDITION,
        retryability=Retryability.NON_RETRYABLE,
        details={
            "provider": str(WINDOWS_PROVIDER_IDENTITY),
            "status": support.status.value,
            "system": support.facts.system,
        },
    )


# --------------------------------------------------------------------------
# Provider boundary.
# --------------------------------------------------------------------------


class WindowsProvider:
    """The Windows provider/adapter boundary used by A5.02+.

    The provider is a *holder and contributor* of already-constructed Windows
    capabilities, plus the deterministic identity and support verdict that
    describe it. It deliberately owns no registry of its own: registration
    targets a caller-supplied A1.09
    :class:`~agentx.capabilities.registry.CapabilityRegistry`, so there is no
    singleton, no ambient state, and no import-time registration.

    Nothing here executes. ``contribute`` and ``register_into`` never call
    ``execute`` or ``verify``, never consult the kernel, and never turn
    availability into authority.
    """

    __slots__ = ("_capabilities", "_support")

    _support: WindowsSupport
    _capabilities: dict[CapabilityIdentity, Capability[Any]]

    def __init__(self, support: WindowsSupport) -> None:
        """Create a provider for an explicitly evaluated ``support`` verdict.

        Support must be supplied by the caller so construction never depends
        on the host it happens to run on. Constructing a provider for an
        unsupported host is legal and inert: it simply refuses to contribute.
        """
        if not isinstance(support, WindowsSupport):
            raise TypeError(f"support must be a WindowsSupport, got {type(support).__name__}")
        object.__setattr__(self, "_support", support)
        object.__setattr__(self, "_capabilities", {})

    def __setattr__(self, name: str, value: object) -> None:
        """Refuse post-construction mutation of the provider's own state.

        The support verdict in particular is decided once, by the caller. A
        provider must never be able to promote itself to "supported".
        """
        raise AttributeError(f"WindowsProvider is immutable; cannot set {name!r}")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"WindowsProvider is immutable; cannot delete {name!r}")

    @classmethod
    def for_current_host(cls) -> WindowsProvider:
        """Create a provider for the *current* host, detecting facts explicitly.

        This is a convenience for application wiring. It is never called at
        import time and it still reads only standard-library platform strings.
        """
        return cls(evaluate_windows_support(detect_platform_facts()))

    @property
    def identity(self) -> WindowsProviderIdentity:
        """The deterministic canonical Windows provider identity."""
        return WINDOWS_PROVIDER_IDENTITY

    @property
    def platform(self) -> CapabilityPlatform:
        """The canonical platform this provider serves."""
        return CapabilityPlatform.WINDOWS

    @property
    def support(self) -> WindowsSupport:
        """The explicit support verdict this provider was constructed with."""
        return self._support

    @property
    def is_supported(self) -> bool:
        """Whether this provider could operate on its described host."""
        return self._support.is_supported

    def contribute(self, capability: Capability[Any]) -> Result[CapabilityIdentity, AgentXError]:
        """Offer an already-constructed Windows capability to this provider.

        The capability must describe itself with the canonical A1.08
        descriptor and must be scoped to ``windows`` (``any`` is not a Windows
        provider concern). Contribution stores an object; it grants nothing,
        runs nothing, and is not registration.

        Returns a canonical failure when the host is unsupported, when the
        descriptor is malformed or wrongly scoped, or when the identity has
        already been contributed.
        """
        if not self._support.is_supported:
            return Result.failure(unsupported_platform_error(self._support))

        descriptor = getattr(capability, "descriptor", None)
        if not isinstance(descriptor, CapabilityDescriptor):
            return Result.failure(
                AgentXError(
                    code="capabilities.windows.malformed_capability",
                    message="capability does not expose a canonical CapabilityDescriptor",
                    category=ErrorCategory.VALIDATION,
                    retryability=Retryability.NON_RETRYABLE,
                )
            )
        if descriptor.scope.platform is not CapabilityPlatform.WINDOWS:
            return Result.failure(
                AgentXError(
                    code="capabilities.windows.scope_mismatch",
                    message=(
                        "Windows provider only accepts capabilities scoped to the "
                        f"windows platform, got {descriptor.scope.platform.value!r}"
                    ),
                    category=ErrorCategory.VALIDATION,
                    retryability=Retryability.NON_RETRYABLE,
                    details={"identity": str(descriptor.identity)},
                )
            )
        identity = descriptor.identity
        if identity in self._capabilities:
            return Result.failure(
                AgentXError(
                    code="capabilities.windows.duplicate_capability",
                    message=f"capability {identity} was already contributed to this provider",
                    category=ErrorCategory.CONFLICT,
                    retryability=Retryability.NON_RETRYABLE,
                    details={"identity": str(identity)},
                )
            )
        self._capabilities[identity] = capability
        return Result.success(identity)

    def contributions(self) -> tuple[CapabilityDescriptor, ...]:
        """Return contributed descriptors in deterministic canonical order.

        Descriptors are inert data captured from the capabilities themselves.
        Reading them authorizes nothing.
        """
        items = [
            capability.descriptor
            for _identity, capability in sorted(
                self._capabilities.items(), key=lambda item: _identity_sort_key(item[0])
            )
        ]
        return tuple(items)

    def register_into(
        self, registry: CapabilityRegistry
    ) -> Result[tuple[CapabilityIdentity, ...], AgentXError]:
        """Register every contributed capability into a caller-owned registry.

        This is the provider's only interaction with the capability fabric: it
        hands existing objects to the canonical A1.09 registry so that future
        Windows capabilities can participate in the ordinary execution path.
        It performs no discovery, no dynamic import, no plugin scan, and no
        execution, and it is never triggered by importing anything.

        On an unsupported host it fails explicitly and registers nothing.
        """
        if not isinstance(registry, CapabilityRegistry):
            raise TypeError(f"registry must be a CapabilityRegistry, got {type(registry).__name__}")
        if not self._support.is_supported:
            return Result.failure(unsupported_platform_error(self._support))

        registered: list[CapabilityIdentity] = []
        for identity in sorted(self._capabilities, key=_identity_sort_key):
            registered.append(registry.register(self._capabilities[identity]))
        return Result.success(tuple(registered))

    def __len__(self) -> int:
        """Return how many capabilities have been contributed."""
        return len(self._capabilities)

    def __repr__(self) -> str:
        return (
            f"WindowsProvider(identity={self.identity!s}, "
            f"status={self._support.status.value!r}, contributions={len(self._capabilities)})"
        )


def _identity_sort_key(identity: CapabilityIdentity) -> tuple[str, int, int, int]:
    """Order identities by name then explicit version, never by insertion."""
    return (
        identity.name.value,
        identity.version.major,
        identity.version.minor,
        identity.version.patch,
    )
