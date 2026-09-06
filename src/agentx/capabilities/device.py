"""C8.01 provider-neutral device abstraction/protocol contracts.

This module is the foundation for AgentX's future multi-device fabric. It
describes a *remote or attached device* the way the browser contracts describe a
browser: as inert, immutable, provider-neutral identity/state data. It performs
no transport, no pairing, no discovery, no remote execution, and no authority
decision.

What C8.01 represents
---------------------

A :class:`DeviceDescriptor` is a snapshot of what a peer reported about a
device:

* identity (``DeviceProviderId`` + ``DeviceId``),
* type/form factor (``DeviceKind``) and host platform (``DevicePlatform``),
* the device-protocol version it speaks (``DeviceProtocolVersion``),
* where it sits relative to the runtime (``DeviceScope``),
* the canonical capabilities it claims/references (``DeviceCapabilityRef``),
* observation metadata (``DeviceObservation``) carrying availability,
  connectivity, last-seen, and derived freshness.

Every identifier, version, and state belongs to a validated closed vocabulary.
Malformed values are rejected at construction. Serialization is deterministic
and side-effect free.

Authority invariant
-------------------

**Device metadata is DATA, not authority.** A descriptor is a self-report. A
remote device claiming ``"ADMIN"``, ``"permission": "grant"``, or
``"role": "root"`` in its metadata is merely *claiming* text; nothing in this
module turns a claim into a ``Permission``, an ``AuthorityContext``, a
``RiskAssessment``, a budget, a clearance of an ``EmergencyStop``, a registered
capability, or a routed action. Hostile metadata is preserved verbatim and
authorizes nothing. There is no field on any C8.01 value that can grant
authority.

Deliberate non-scope
--------------------

C8.01 does **not** implement: pairing (C8.02), an Android companion, Android
accessibility, remote capability execution, cross-device routing, a shared
Hive, cross-device verification, or network transport. The only "interface"
here is the data contract itself; there is no socket, process, protocol client,
or live handle. The abstraction is deliberately platform-neutral: Android is one
possible ``DevicePlatform`` value among several, never the model the contract is
built around.

Owner: C8.01. Belongs to ``agentx.capabilities``; imports only the standard
library and the canonical capability ABI identity contract.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from agentx.capabilities.abi import CapabilityIdentity

__all__ = [
    "CANONICAL_DEVICE_AVAILABILITY",
    "CANONICAL_DEVICE_CAPABILITY_AVAILABILITY",
    "CANONICAL_DEVICE_CONNECTIVITY",
    "CANONICAL_DEVICE_ENVIRONMENTS",
    "CANONICAL_DEVICE_FRESHNESS",
    "CANONICAL_DEVICE_KINDS",
    "CANONICAL_DEVICE_PLATFORMS",
    "DEVICE_CAPABILITY_SCHEMA_VERSION",
    "DEVICE_OBSERVATION_SCHEMA_VERSION",
    "DEVICE_PROTOCOL_SCHEMA_VERSION",
    "DeviceAvailability",
    "DeviceCapabilityAvailability",
    "DeviceCapabilityRef",
    "DeviceConnectivity",
    "DeviceDescriptor",
    "DeviceEnvironment",
    "DeviceFreshness",
    "DeviceId",
    "DeviceKind",
    "DeviceObservation",
    "DevicePlatform",
    "DeviceProtocolVersion",
    "DeviceProviderId",
    "DeviceScope",
    "DeviceValidationError",
    "evaluate_device_freshness",
]

DEVICE_PROTOCOL_SCHEMA_VERSION: Final[int] = 1
DEVICE_OBSERVATION_SCHEMA_VERSION: Final[int] = 1
DEVICE_CAPABILITY_SCHEMA_VERSION: Final[int] = 1

_PROVIDER_ID_PATTERN: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")
_MAX_PROVIDER_ID_LENGTH: Final[int] = 128
_MAX_DEVICE_ID_LENGTH: Final[int] = 512
_MAX_DETAIL_LENGTH: Final[int] = 1024
_MAX_COUNTER: Final[int] = (1 << 63) - 1
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class DeviceValidationError(ValueError):
    """Raised when a C8.01 device contract value is malformed."""


def _validate_text(value: object, *, field_name: str, max_length: int) -> str:
    """Validate explicit, non-empty, trimmed, control-character-free text."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise DeviceValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > max_length:
        raise DeviceValidationError(f"{field_name} must not exceed {max_length} characters")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise DeviceValidationError(f"{field_name} must not contain control characters")
    return value


def _validate_opaque_id(value: object, *, field_name: str, max_length: int) -> str:
    """Validate an opaque, non-empty, trimmed identifier value."""
    return _validate_text(value, field_name=field_name, max_length=max_length)


def _validate_counter(value: object, *, field_name: str) -> int:
    """Validate an explicit non-negative integer counter (bools rejected)."""
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise DeviceValidationError(f"{field_name} must not be negative")
    if value > _MAX_COUNTER:
        raise OverflowError(f"{field_name} exceeds the supported counter range")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    """Validate a timezone-aware datetime and normalize to UTC."""
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise DeviceValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    """Return the canonical UTC ISO-8601 ``Z`` form used by AgentX."""
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _freeze_json(value: object, *, path: str) -> object:
    """Validate JSON compatibility and return an immutable defensive copy."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DeviceValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise DeviceValidationError(f"{path} contains a non-string object key")
            frozen[key] = _freeze_json(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(
            _freeze_json(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        )
    raise DeviceValidationError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _freeze_json_object(value: object, *, path: str) -> Mapping[str, object]:
    """Validate a JSON object mapping and return its immutable frozen copy."""
    if not isinstance(value, Mapping):
        raise TypeError(f"{path} must be a mapping, got {type(value).__name__}")
    frozen = _freeze_json(value, path=path)
    if not isinstance(frozen, Mapping):  # pragma: no cover - guarded above
        raise AssertionError("JSON object freezing produced a non-mapping")
    return frozen


def _to_json_value(value: object, *, path: str) -> object:
    """Convert a frozen JSON value back to JSON-compatible primitives."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):  # defensive; construction already rejects this
            raise DeviceValidationError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):  # defensive; construction already rejects this
                raise DeviceValidationError(f"{path} contains a non-string object key")
            result[key] = _to_json_value(item, path=f"{path}.{key}")
        return result
    if isinstance(value, tuple | list):
        return [_to_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    raise DeviceValidationError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _to_json_object(value: Mapping[str, object], *, path: str) -> dict[str, object]:
    """Convert a frozen observation mapping to a plain JSON-compatible dict."""
    converted = _to_json_value(value, path=path)
    if not isinstance(converted, dict):  # pragma: no cover - mapping always converts to dict
        raise AssertionError("JSON object conversion produced a non-dict")
    return converted


def _json_dumps(value: Mapping[str, object]) -> str:
    """Serialize deterministically without dynamic-code or execution hooks."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


# --------------------------------------------------------------------------
# Identity.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class DeviceProviderId:
    """Stable, validated namespace for a device-reporting source.

    This scopes the opaque device identities so two providers never compare
    equal on a colliding opaque string. It is a name, not authority.
    """

    value: str

    def __post_init__(self) -> None:
        _validate_text(
            self.value,
            field_name="device provider id",
            max_length=_MAX_PROVIDER_ID_LENGTH,
        )
        if _PROVIDER_ID_PATTERN.fullmatch(self.value) is None:
            raise DeviceValidationError(
                "device provider id must be lowercase alphanumeric segments separated by "
                f"single '._-' characters, got {self.value!r}"
            )

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class DeviceId:
    """Provider-scoped opaque identity for one device.

    The opaque ``value`` is whatever the reporting provider uses as the device
    identifier (serial, advertisement id, etc.). It is treated as untrusted
    data: identity is never derived from a device's self-reported metadata and
    never grants anything.
    """

    provider_id: DeviceProviderId
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, DeviceProviderId):
            raise TypeError(
                f"provider_id must be a DeviceProviderId, got {type(self.provider_id).__name__}"
            )
        _validate_opaque_id(
            self.value,
            field_name="device id",
            max_length=_MAX_DEVICE_ID_LENGTH,
        )

    def __str__(self) -> str:
        return self.value


# --------------------------------------------------------------------------
# Protocol version.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, order=True)
class DeviceProtocolVersion:
    """Explicit typed device-protocol version as ``major.minor.patch`` integers.

    Versions are structured data, never an opaque string: ``major`` marks
    incompatible protocol changes, ``minor`` marks backwards-compatible
    additions, and ``patch`` marks backwards-compatible fixes. A version is a
    description, not a compatibility guarantee and not an authorization.
    """

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        _validate_counter(self.major, field_name="protocol.version.major")
        _validate_counter(self.minor, field_name="protocol.version.minor")
        _validate_counter(self.patch, field_name="protocol.version.patch")

    def to_str(self) -> str:
        """Return the canonical ``"major.minor.patch"`` form."""
        return f"{self.major}.{self.minor}.{self.patch}"

    def __str__(self) -> str:
        return self.to_str()

    @classmethod
    def from_str(cls, raw: str) -> DeviceProtocolVersion:
        """Parse the canonical ``"major.minor.patch"`` form.

        Raises:
            TypeError: if ``raw`` is not a string (a programming error).
            DeviceValidationError: if ``raw`` is not exactly three dot-separated
                non-negative integer components.
        """
        if not isinstance(raw, str):
            raise TypeError(f"version string must be a string, got {type(raw).__name__}")
        if not raw or raw != raw.strip():
            raise DeviceValidationError("version string must be non-empty and trimmed")
        parts = raw.split(".")
        if len(parts) != 3 or any(not part.isascii() or not part.isdigit() for part in parts):
            raise DeviceValidationError(
                "version string must have the form 'major.minor.patch' with "
                f"non-negative integer components, got {raw!r}"
            )
        major, minor, patch = (int(part) for part in parts)
        return cls(major=major, minor=minor, patch=patch)


# --------------------------------------------------------------------------
# Closed vocabularies.
# --------------------------------------------------------------------------


class DeviceKind(StrEnum):
    """Closed vocabulary of device type / form factor.

    A kind is a category, not an OS. ``OTHER`` preserves an explicitly known
    device without inventing provider-specific classes; ``UNKNOWN`` is the
    fail-closed default for a device whose type was not reported.
    """

    PHONE = "phone"
    TABLET = "tablet"
    DESKTOP = "desktop"
    LAPTOP = "laptop"
    WEARABLE = "wearable"
    EMBEDDED = "embedded"
    SERVER = "server"
    OTHER = "other"
    UNKNOWN = "unknown"


CANONICAL_DEVICE_KINDS: Final[tuple[DeviceKind, ...]] = (
    DeviceKind.PHONE,
    DeviceKind.TABLET,
    DeviceKind.DESKTOP,
    DeviceKind.LAPTOP,
    DeviceKind.WEARABLE,
    DeviceKind.EMBEDDED,
    DeviceKind.SERVER,
    DeviceKind.OTHER,
    DeviceKind.UNKNOWN,
)


class DevicePlatform(StrEnum):
    """Closed vocabulary of host platform for a device.

    This is deliberately a plain, unprivileged vocabulary. Android and iOS are
    ordinary members alongside desktop/server operating systems; none of them
    is the model the contract is built around, and none carries special status.
    ``OTHER``/``UNKNOWN`` preserve a device whose platform was not reported.
    """

    WINDOWS = "windows"
    LINUX = "linux"
    MACOS = "macos"
    ANDROID = "android"
    IOS = "ios"
    OTHER = "other"
    UNKNOWN = "unknown"


CANONICAL_DEVICE_PLATFORMS: Final[tuple[DevicePlatform, ...]] = (
    DevicePlatform.WINDOWS,
    DevicePlatform.LINUX,
    DevicePlatform.MACOS,
    DevicePlatform.ANDROID,
    DevicePlatform.IOS,
    DevicePlatform.OTHER,
    DevicePlatform.UNKNOWN,
)


class DeviceEnvironment(StrEnum):
    """Closed vocabulary of where a device sits relative to the runtime.

    Scope is descriptive and provider-neutral; it routes future selection and
    never grants trust, authority, or a kernel bypass.
    """

    LOCAL = "local"
    TRUSTED_NETWORK = "trusted_network"
    REMOTE = "remote"
    UNKNOWN = "unknown"


CANONICAL_DEVICE_ENVIRONMENTS: Final[tuple[DeviceEnvironment, ...]] = (
    DeviceEnvironment.LOCAL,
    DeviceEnvironment.TRUSTED_NETWORK,
    DeviceEnvironment.REMOTE,
    DeviceEnvironment.UNKNOWN,
)


class DeviceConnectivity(StrEnum):
    """Closed vocabulary of device reachability/connectivity snapshot state."""

    ONLINE = "online"
    OFFLINE = "offline"
    STALE = "stale"
    UNKNOWN = "unknown"


CANONICAL_DEVICE_CONNECTIVITY: Final[tuple[DeviceConnectivity, ...]] = (
    DeviceConnectivity.ONLINE,
    DeviceConnectivity.OFFLINE,
    DeviceConnectivity.STALE,
    DeviceConnectivity.UNKNOWN,
)


class DeviceAvailability(StrEnum):
    """Closed vocabulary of whether a device is usable, independent of reach."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    UNKNOWN = "unknown"


CANONICAL_DEVICE_AVAILABILITY: Final[tuple[DeviceAvailability, ...]] = (
    DeviceAvailability.AVAILABLE,
    DeviceAvailability.UNAVAILABLE,
    DeviceAvailability.STALE,
    DeviceAvailability.UNKNOWN,
)


class DeviceCapabilityAvailability(StrEnum):
    """Closed vocabulary of a device's claimed availability of one capability."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


CANONICAL_DEVICE_CAPABILITY_AVAILABILITY: Final[tuple[DeviceCapabilityAvailability, ...]] = (
    DeviceCapabilityAvailability.AVAILABLE,
    DeviceCapabilityAvailability.UNAVAILABLE,
    DeviceCapabilityAvailability.UNKNOWN,
)


class DeviceFreshness(StrEnum):
    """Closed vocabulary of how fresh a device observation is."""

    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


CANONICAL_DEVICE_FRESHNESS: Final[tuple[DeviceFreshness, ...]] = (
    DeviceFreshness.FRESH,
    DeviceFreshness.STALE,
    DeviceFreshness.UNKNOWN,
)


# --------------------------------------------------------------------------
# Compositional contracts.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DeviceScope:
    """Inert declaration of the environment/device scope of a device."""

    environment: DeviceEnvironment

    def __post_init__(self) -> None:
        if not isinstance(self.environment, DeviceEnvironment):
            raise TypeError(
                f"environment must be a DeviceEnvironment, got {type(self.environment).__name__}"
            )

    def to_dict(self) -> dict[str, str]:
        return {"environment": self.environment.value}


@dataclass(frozen=True, slots=True)
class DeviceCapabilityRef:
    """A device's reference to a canonical capability, with claimed availability.

    The ``capability`` is a re-used canonical :class:`CapabilityIdentity`; the
    reference does **not** register, execute, verify, or authorise that
    capability. Availability here is a device self-report, never a guarantee.
    """

    capability: CapabilityIdentity
    availability: DeviceCapabilityAvailability
    schema_version: int = DEVICE_CAPABILITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.capability, CapabilityIdentity):
            raise TypeError(
                f"capability must be a CapabilityIdentity, got {type(self.capability).__name__}"
            )
        if not isinstance(self.availability, DeviceCapabilityAvailability):
            raise TypeError(
                "availability must be a DeviceCapabilityAvailability, "
                f"got {type(self.availability).__name__}"
            )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != DEVICE_CAPABILITY_SCHEMA_VERSION:
            raise DeviceValidationError(
                f"unsupported device capability schema version {self.schema_version}; "
                f"supported version is {DEVICE_CAPABILITY_SCHEMA_VERSION}"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "capability": str(self.capability),
            "availability": self.availability.value,
        }

    def to_json(self) -> str:
        """Serialize deterministically without dynamic-code or execution hooks."""
        return _json_dumps(self.to_dict())


@dataclass(frozen=True, slots=True)
class DeviceObservation:
    """Immutable observation metadata snapshot for a device.

    ``connectivity`` and ``availability`` are explicit snapshot states supplied
    by a caller; they are facts about a claim, never liveness guarantees and
    never authority. ``last_seen`` preserves the last time the device was
    reported present, so an offline snapshot can still record when it was last
    actually seen. Freshness is never stored on the value: callers derive it
    with :func:`evaluate_device_freshness` so a stale claim cannot masquerade as
    fresh.
    """

    connectivity: DeviceConnectivity
    availability: DeviceAvailability
    observed_at: datetime
    last_seen: datetime
    detail: str | None = None
    schema_version: int = DEVICE_OBSERVATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.connectivity, DeviceConnectivity):
            raise TypeError(
                f"connectivity must be a DeviceConnectivity, got {type(self.connectivity).__name__}"
            )
        if not isinstance(self.availability, DeviceAvailability):
            raise TypeError(
                f"availability must be a DeviceAvailability, got {type(self.availability).__name__}"
            )
        object.__setattr__(
            self,
            "observed_at",
            _validate_timestamp(self.observed_at, field_name="observed_at"),
        )
        object.__setattr__(
            self,
            "last_seen",
            _validate_timestamp(self.last_seen, field_name="last_seen"),
        )
        if self.detail is not None:
            if not isinstance(self.detail, str):
                raise TypeError(
                    f"detail must be a string or None, got {type(self.detail).__name__}"
                )
            if len(self.detail) > _MAX_DETAIL_LENGTH:
                raise DeviceValidationError(
                    f"detail must not exceed {_MAX_DETAIL_LENGTH} characters"
                )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != DEVICE_OBSERVATION_SCHEMA_VERSION:
            raise DeviceValidationError(
                f"unsupported device observation schema version {self.schema_version}; "
                f"supported version is {DEVICE_OBSERVATION_SCHEMA_VERSION}"
            )
        # An available device must be connected. An explicitly offline/known-stale
        # device cannot be reported as available.
        if (
            self.availability is DeviceAvailability.AVAILABLE
            and self.connectivity is not DeviceConnectivity.ONLINE
        ):
            raise DeviceValidationError(
                "an available device requires online connectivity; "
                "offline/stale/unknown connectivity cannot be available"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "connectivity": self.connectivity.value,
            "availability": self.availability.value,
            "observed_at": _format_timestamp(self.observed_at),
            "last_seen": _format_timestamp(self.last_seen),
            "detail": self.detail,
        }

    def to_json(self) -> str:
        return _json_dumps(self.to_dict())


@dataclass(frozen=True, slots=True)
class DeviceDescriptor:
    """Immutable, provider-neutral snapshot of one device.

    This is a *self-report* stored as data. Nothing on it grants a permission,
    lowers a risk level, enlarges a budget, clears an emergency stop, registers
    a capability, or authorises a remote action. ``metadata`` is optional
    untrusted extra data and is preserved verbatim as inert text.
    """

    device_id: DeviceId
    kind: DeviceKind
    platform: DevicePlatform
    protocol_version: DeviceProtocolVersion
    scope: DeviceScope
    observation: DeviceObservation
    capabilities: tuple[DeviceCapabilityRef, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)
    schema_version: int = DEVICE_PROTOCOL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.device_id, DeviceId):
            raise TypeError(f"device_id must be a DeviceId, got {type(self.device_id).__name__}")
        if not isinstance(self.kind, DeviceKind):
            raise TypeError(f"kind must be a DeviceKind, got {type(self.kind).__name__}")
        if not isinstance(self.platform, DevicePlatform):
            raise TypeError(
                f"platform must be a DevicePlatform, got {type(self.platform).__name__}"
            )
        if not isinstance(self.protocol_version, DeviceProtocolVersion):
            raise TypeError(
                "protocol_version must be a DeviceProtocolVersion, "
                f"got {type(self.protocol_version).__name__}"
            )
        if not isinstance(self.scope, DeviceScope):
            raise TypeError(f"scope must be a DeviceScope, got {type(self.scope).__name__}")
        if not isinstance(self.observation, DeviceObservation):
            raise TypeError(
                f"observation must be a DeviceObservation, got {type(self.observation).__name__}"
            )
        if not isinstance(self.capabilities, tuple):
            raise TypeError("capabilities must be a tuple")
        seen: set[CapabilityIdentity] = set()
        for capability_ref in self.capabilities:
            if not isinstance(capability_ref, DeviceCapabilityRef):
                raise TypeError(
                    "capabilities must contain DeviceCapabilityRef values, "
                    f"got {type(capability_ref).__name__}"
                )
            if capability_ref.capability in seen:
                raise DeviceValidationError(
                    "device capabilities must have unique canonical capability identities"
                )
            seen.add(capability_ref.capability)
        object.__setattr__(
            self,
            "metadata",
            _freeze_json_object(self.metadata, path="device.metadata"),
        )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != DEVICE_PROTOCOL_SCHEMA_VERSION:
            raise DeviceValidationError(
                f"unsupported device protocol schema version {self.schema_version}; "
                f"supported version is {DEVICE_PROTOCOL_SCHEMA_VERSION}"
            )

    @property
    def provider_id(self) -> DeviceProviderId:
        """Return the canonical provider identity owning this device."""
        return self.device_id.provider_id

    @property
    def connectivity(self) -> DeviceConnectivity:
        """Return the explicit connectivity snapshot state."""
        return self.observation.connectivity

    @property
    def availability(self) -> DeviceAvailability:
        """Return the explicit availability snapshot state."""
        return self.observation.availability

    @property
    def last_seen(self) -> datetime:
        """Return the last time this device was reported present."""
        return self.observation.last_seen

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "device_id": {
                "provider_id": str(self.provider_id),
                "value": self.device_id.value,
            },
            "kind": self.kind.value,
            "platform": self.platform.value,
            "protocol_version": self.protocol_version.to_str(),
            "scope": self.scope.to_dict(),
            "capabilities": [capability_ref.to_dict() for capability_ref in self.capabilities],
            "observation": self.observation.to_dict(),
            "metadata": _to_json_object(self.metadata, path="device.metadata"),
        }

    def to_json(self) -> str:
        """Serialize deterministically without dynamic-code or execution hooks."""
        return _json_dumps(self.to_dict())


# --------------------------------------------------------------------------
# Pure derivation helpers.
# --------------------------------------------------------------------------


def evaluate_device_freshness(
    observation: DeviceObservation,
    *,
    now: datetime,
    max_age: timedelta,
) -> DeviceFreshness:
    """Derive freshness of a device observation at ``now``.

    Freshness is a pure function of the observable snapshot and the caller's
    clock; it is never stored on the value, so a stale or hostile claim cannot
    fabricate a fresh verdict.

    * An :attr:`DeviceConnectivity.OFFLINE` device is ``UNKNOWN``: offline is an
      explicit "no live evidence" state, never fresh.
    * An :attr:`DeviceConnectivity.UNKNOWN` device is ``UNKNOWN`` for the same
      reason.
    * An :attr:`DeviceConnectivity.STALE` device is ``STALE``.
    * Otherwise (an online device) the verdict is ``FRESH`` when ``now`` is
      within ``max_age`` of ``last_seen`` and ``STALE`` otherwise.
    """
    if not isinstance(observation, DeviceObservation):
        raise TypeError(
            f"observation must be a DeviceObservation, got {type(observation).__name__}"
        )
    actual_now = _validate_timestamp(now, field_name="now")
    if not isinstance(max_age, timedelta):
        raise TypeError(f"max_age must be a timedelta, got {type(max_age).__name__}")

    if observation.connectivity is DeviceConnectivity.OFFLINE:
        return DeviceFreshness.UNKNOWN
    if observation.connectivity is DeviceConnectivity.UNKNOWN:
        return DeviceFreshness.UNKNOWN
    if observation.connectivity is DeviceConnectivity.STALE:
        return DeviceFreshness.STALE

    age = actual_now - observation.last_seen
    if age <= max_age:
        return DeviceFreshness.FRESH
    return DeviceFreshness.STALE
