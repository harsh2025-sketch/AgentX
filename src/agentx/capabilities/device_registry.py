"""Provider-neutral multi-device registry, discovery, health and environment identity.

M13 extends the immutable C8.01 device protocol with runtime inventory semantics.
The registry stores descriptive device evidence only. Discovery never grants
permissions, lowers risk, registers capabilities, or executes machine actions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from types import MappingProxyType
from typing import Final, Protocol

from agentx.capabilities.abi import CapabilityIdentity
from agentx.capabilities.device import (
    DeviceAvailability,
    DeviceCapabilityAvailability,
    DeviceConnectivity,
    DeviceDescriptor,
    DeviceEnvironment,
    DeviceFreshness,
    DeviceId,
    DeviceObservation,
    DevicePlatform,
    DeviceProviderId,
    evaluate_device_freshness,
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result

__all__ = [
    "DeviceDiscovery",
    "DeviceDiscoveryProvider",
    "DeviceDiscoveryReport",
    "DeviceEnvironmentIdentity",
    "DeviceHealth",
    "DeviceRegistry",
    "DeviceRegistryConflictError",
    "DeviceRegistryError",
    "DeviceRegistrySnapshot",
    "DeviceUnavailableError",
    "evaluate_device_health",
]

_MAX_DEVICES: Final[int] = 256


class DeviceRegistryError(ValueError):
    """Base error for provider-neutral device inventory failures."""


class DeviceRegistryConflictError(DeviceRegistryError):
    """Raised when equally fresh evidence conflicts or a registry is full."""


class DeviceUnavailableError(DeviceRegistryError):
    """Raised when a requested device is absent or not safely usable."""


class DeviceHealth(StrEnum):
    """Derived health state. A provider cannot directly assert this verdict."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True, order=True)
class DeviceEnvironmentIdentity:
    """Stable environment applicability identity derived from typed device facts."""

    provider_id: DeviceProviderId
    device_id: DeviceId
    platform: DevicePlatform
    environment: DeviceEnvironment

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, DeviceProviderId):
            raise TypeError("provider_id must be DeviceProviderId")
        if not isinstance(self.device_id, DeviceId):
            raise TypeError("device_id must be DeviceId")
        if self.device_id.provider_id != self.provider_id:
            raise DeviceRegistryError("device identity provider does not match provider_id")
        if not isinstance(self.platform, DevicePlatform):
            raise TypeError("platform must be DevicePlatform")
        if not isinstance(self.environment, DeviceEnvironment):
            raise TypeError("environment must be DeviceEnvironment")

    @classmethod
    def from_descriptor(cls, descriptor: DeviceDescriptor) -> DeviceEnvironmentIdentity:
        if not isinstance(descriptor, DeviceDescriptor):
            raise TypeError("descriptor must be DeviceDescriptor")
        return cls(
            provider_id=descriptor.provider_id,
            device_id=descriptor.device_id,
            platform=descriptor.platform,
            environment=descriptor.scope.environment,
        )

    def to_key(self) -> str:
        """Return an opaque canonical applicability token; never authority."""
        return (
            f"device:{self.provider_id.value}:{self.device_id.value}:"
            f"{self.platform.value}:{self.environment.value}"
        )


@dataclass(frozen=True, slots=True)
class DeviceRegistrySnapshot:
    """Restart-safe descriptive snapshot.

    Restoring this snapshot deliberately does not restore live connectivity.
    Callers must rediscover devices before they become usable again.
    """

    descriptors: tuple[DeviceDescriptor, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.descriptors, tuple):
            raise TypeError("descriptors must be a tuple")
        if len(self.descriptors) > _MAX_DEVICES:
            raise DeviceRegistryError("device snapshot exceeds bounded registry size")
        seen: set[DeviceId] = set()
        for descriptor in self.descriptors:
            if not isinstance(descriptor, DeviceDescriptor):
                raise TypeError("snapshot entries must be DeviceDescriptor values")
            if descriptor.device_id in seen:
                raise DeviceRegistryError("snapshot contains duplicate device identity")
            seen.add(descriptor.device_id)


class DeviceRegistry:
    """Thread-safe bounded registry of the freshest evidence for each device."""

    def __init__(self, *, max_devices: int = _MAX_DEVICES) -> None:
        if type(max_devices) is not int or max_devices < 1:
            raise DeviceRegistryError("max_devices must be a positive int")
        self._max_devices = max_devices
        self._devices: dict[DeviceId, DeviceDescriptor] = {}
        self._lock = RLock()

    @property
    def max_devices(self) -> int:
        return self._max_devices

    def __len__(self) -> int:
        with self._lock:
            return len(self._devices)

    def observe(self, descriptor: DeviceDescriptor) -> DeviceDescriptor:
        """Insert or update a device using monotonic observation evidence.

        Older/replayed evidence is rejected. Equally timed but different
        evidence is ambiguous and fails closed rather than last-write-wins.
        """
        if not isinstance(descriptor, DeviceDescriptor):
            raise TypeError("descriptor must be DeviceDescriptor")
        with self._lock:
            existing = self._devices.get(descriptor.device_id)
            if existing is None:
                if len(self._devices) >= self._max_devices:
                    raise DeviceRegistryConflictError("device registry is full")
                self._devices[descriptor.device_id] = descriptor
                return descriptor
            incoming_at = descriptor.observation.observed_at
            existing_at = existing.observation.observed_at
            if incoming_at < existing_at:
                raise DeviceRegistryConflictError("stale/replayed device observation rejected")
            if incoming_at == existing_at:
                if descriptor == existing:
                    return existing
                raise DeviceRegistryConflictError(
                    "equally fresh conflicting device observations are ambiguous"
                )
            self._devices[descriptor.device_id] = descriptor
            return descriptor

    def get(self, device_id: DeviceId) -> DeviceDescriptor | None:
        if not isinstance(device_id, DeviceId):
            raise TypeError("device_id must be DeviceId")
        with self._lock:
            return self._devices.get(device_id)

    def require(self, device_id: DeviceId) -> DeviceDescriptor:
        found = self.get(device_id)
        if found is None:
            raise DeviceUnavailableError(f"device {device_id} is not registered")
        return found

    def descriptors(self) -> tuple[DeviceDescriptor, ...]:
        with self._lock:
            return tuple(
                self._devices[key]
                for key in sorted(
                    self._devices,
                    key=lambda item: (item.provider_id.value, item.value),
                )
            )

    def snapshot(self) -> DeviceRegistrySnapshot:
        return DeviceRegistrySnapshot(descriptors=self.descriptors())

    def reconcile_provider(
        self,
        provider_id: DeviceProviderId,
        *,
        descriptors: tuple[DeviceDescriptor, ...],
        observed_at: datetime,
    ) -> tuple[DeviceDescriptor, ...]:
        """Atomically validate and apply one provider discovery batch.

        No registry mutation occurs unless the entire provider batch is
        structurally valid and monotonic relative to current evidence.
        """
        if not isinstance(provider_id, DeviceProviderId):
            raise TypeError("provider_id must be DeviceProviderId")
        if not isinstance(descriptors, tuple):
            raise TypeError("descriptors must be a tuple")
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
            raise TypeError("observed_at must be timezone-aware datetime")
        moment = observed_at.astimezone(UTC)

        seen: set[DeviceId] = set()
        for descriptor in descriptors:
            if not isinstance(descriptor, DeviceDescriptor):
                raise DeviceRegistryConflictError(
                    "provider discovery returned a non-DeviceDescriptor value"
                )
            if descriptor.provider_id != provider_id:
                raise DeviceRegistryConflictError(
                    "provider returned a descriptor from a different provider namespace"
                )
            if descriptor.device_id in seen:
                raise DeviceRegistryConflictError(
                    "provider discovery returned duplicate device identity"
                )
            seen.add(descriptor.device_id)

        with self._lock:
            new_ids = seen - set(self._devices)
            if len(self._devices) + len(new_ids) > self._max_devices:
                raise DeviceRegistryConflictError("device registry is full")

            for descriptor in descriptors:
                existing = self._devices.get(descriptor.device_id)
                if existing is None:
                    continue
                incoming_at = descriptor.observation.observed_at
                existing_at = existing.observation.observed_at
                if incoming_at < existing_at:
                    raise DeviceRegistryConflictError("stale/replayed device observation rejected")
                if incoming_at == existing_at and descriptor != existing:
                    raise DeviceRegistryConflictError(
                        "equally fresh conflicting device observations are ambiguous"
                    )

            # Only after the complete batch passes validation is state changed.
            for descriptor in descriptors:
                self._devices[descriptor.device_id] = descriptor

            for device_id, descriptor in tuple(self._devices.items()):
                if device_id.provider_id != provider_id or device_id in seen:
                    continue
                if moment <= descriptor.observation.observed_at:
                    continue
                self._devices[device_id] = replace(
                    descriptor,
                    observation=DeviceObservation(
                        connectivity=DeviceConnectivity.OFFLINE,
                        availability=DeviceAvailability.UNAVAILABLE,
                        observed_at=moment,
                        last_seen=descriptor.observation.last_seen,
                        detail="device absent from latest provider discovery",
                    ),
                )

        return tuple(
            sorted(
                descriptors,
                key=lambda item: (item.provider_id.value, item.device_id.value),
            )
        )

    @classmethod
    def restore(cls, snapshot: DeviceRegistrySnapshot) -> DeviceRegistry:
        """Restore durable identity/history while invalidating live availability."""
        if not isinstance(snapshot, DeviceRegistrySnapshot):
            raise TypeError("snapshot must be DeviceRegistrySnapshot")
        registry = cls(max_devices=max(_MAX_DEVICES, len(snapshot.descriptors)))
        for descriptor in snapshot.descriptors:
            stale = replace(
                descriptor,
                observation=DeviceObservation(
                    connectivity=DeviceConnectivity.UNKNOWN,
                    availability=DeviceAvailability.UNKNOWN,
                    observed_at=descriptor.observation.observed_at,
                    last_seen=descriptor.observation.last_seen,
                    detail="restored registry entry requires live rediscovery",
                ),
            )
            registry._devices[stale.device_id] = stale
        return registry

    def available(
        self,
        *,
        now: datetime,
        max_age: timedelta,
        platform: DevicePlatform | None = None,
    ) -> tuple[DeviceDescriptor, ...]:
        if platform is not None and not isinstance(platform, DevicePlatform):
            raise TypeError("platform must be DevicePlatform or None")
        candidates: list[DeviceDescriptor] = []
        for descriptor in self.descriptors():
            if platform is not None and descriptor.platform is not platform:
                continue
            if descriptor.connectivity is not DeviceConnectivity.ONLINE:
                continue
            if descriptor.availability is not DeviceAvailability.AVAILABLE:
                continue
            if (
                evaluate_device_freshness(descriptor.observation, now=now, max_age=max_age)
                is not DeviceFreshness.FRESH
            ):
                continue
            candidates.append(descriptor)
        return tuple(candidates)

    def capability_advertisements(
        self,
        device_id: DeviceId,
        *,
        now: datetime,
        max_age: timedelta,
    ) -> tuple[CapabilityIdentity, ...]:
        descriptor = self.require(device_id)
        if descriptor not in self.available(now=now, max_age=max_age):
            raise DeviceUnavailableError("device is not fresh, online and available")
        return tuple(
            item.capability
            for item in descriptor.capabilities
            if item.availability is DeviceCapabilityAvailability.AVAILABLE
        )

    def mark_provider_missing(
        self,
        provider_id: DeviceProviderId,
        *,
        present: frozenset[DeviceId],
        observed_at: datetime,
    ) -> tuple[DeviceId, ...]:
        """Mark previously known provider devices absent from fresh discovery offline."""
        if not isinstance(provider_id, DeviceProviderId):
            raise TypeError("provider_id must be DeviceProviderId")
        if not isinstance(present, frozenset):
            raise TypeError("present must be a frozenset")
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
            raise TypeError("observed_at must be timezone-aware datetime")
        moment = observed_at.astimezone(UTC)
        changed: list[DeviceId] = []
        with self._lock:
            for device_id, descriptor in tuple(self._devices.items()):
                if device_id.provider_id != provider_id or device_id in present:
                    continue
                if moment <= descriptor.observation.observed_at:
                    continue
                self._devices[device_id] = replace(
                    descriptor,
                    observation=DeviceObservation(
                        connectivity=DeviceConnectivity.OFFLINE,
                        availability=DeviceAvailability.UNAVAILABLE,
                        observed_at=moment,
                        last_seen=descriptor.observation.last_seen,
                        detail="device absent from latest provider discovery",
                    ),
                )
                changed.append(device_id)
        return tuple(sorted(changed, key=lambda item: item.value))


def evaluate_device_health(
    descriptor: DeviceDescriptor,
    *,
    now: datetime,
    max_age: timedelta,
) -> DeviceHealth:
    """Derive health from trusted shape/freshness plus descriptive provider evidence."""
    if not isinstance(descriptor, DeviceDescriptor):
        raise TypeError("descriptor must be DeviceDescriptor")
    freshness = evaluate_device_freshness(descriptor.observation, now=now, max_age=max_age)
    if descriptor.connectivity in {DeviceConnectivity.OFFLINE, DeviceConnectivity.UNKNOWN}:
        return DeviceHealth.UNAVAILABLE
    if descriptor.availability is DeviceAvailability.UNAVAILABLE:
        return DeviceHealth.UNAVAILABLE
    if freshness is DeviceFreshness.STALE:
        return DeviceHealth.STALE
    if freshness is DeviceFreshness.UNKNOWN:
        return DeviceHealth.UNKNOWN
    advertised = descriptor.capabilities
    if any(item.availability is DeviceCapabilityAvailability.UNAVAILABLE for item in advertised):
        return DeviceHealth.DEGRADED
    if descriptor.availability is DeviceAvailability.AVAILABLE:
        return DeviceHealth.HEALTHY
    return DeviceHealth.UNKNOWN


class DeviceDiscoveryProvider(Protocol):
    """Narrow discovery port. Returned descriptors remain untrusted evidence."""

    @property
    def provider_id(self) -> DeviceProviderId: ...

    def discover(
        self,
        *,
        context: ExecutionContext,
        observed_at: datetime,
    ) -> Result[tuple[DeviceDescriptor, ...], AgentXError]: ...


@dataclass(frozen=True, slots=True)
class DeviceDiscoveryReport:
    """Outcome of one bounded fan-out discovery pass."""

    discovered: tuple[DeviceDescriptor, ...]
    provider_errors: MappingProxyType[DeviceProviderId, AgentXError]

    @property
    def succeeded_provider_count(self) -> int:
        return len({item.provider_id for item in self.discovered})


class DeviceDiscovery:
    """Run explicitly supplied providers and reconcile a DeviceRegistry."""

    def __init__(
        self,
        *,
        registry: DeviceRegistry,
        providers: tuple[DeviceDiscoveryProvider, ...],
    ) -> None:
        if not isinstance(registry, DeviceRegistry):
            raise TypeError("registry must be DeviceRegistry")
        if not isinstance(providers, tuple):
            raise TypeError("providers must be a tuple")
        ids: set[DeviceProviderId] = set()
        for provider in providers:
            provider_id = provider.provider_id
            if not isinstance(provider_id, DeviceProviderId):
                raise TypeError("provider_id must be DeviceProviderId")
            if provider_id in ids:
                raise DeviceRegistryConflictError("duplicate device discovery provider")
            ids.add(provider_id)
        self._registry = registry
        self._providers = providers

    @property
    def registry(self) -> DeviceRegistry:
        return self._registry

    def discover(
        self,
        *,
        context: ExecutionContext,
        observed_at: datetime,
    ) -> DeviceDiscoveryReport:
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be ExecutionContext")
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
            raise TypeError("observed_at must be timezone-aware datetime")
        if context.observe_stop().should_stop:
            error = AgentXError(
                code="device.discovery.cancelled",
                message="device discovery rejected because execution context is stopped",
                category=ErrorCategory.CANCELLED,
                retryability=Retryability.NON_RETRYABLE,
            )
            return DeviceDiscoveryReport(
                (), MappingProxyType({provider.provider_id: error for provider in self._providers})
            )
        all_found: list[DeviceDescriptor] = []
        errors: dict[DeviceProviderId, AgentXError] = {}
        for provider in self._providers:
            result = provider.discover(context=context, observed_at=observed_at)
            if result.is_failure:
                errors[provider.provider_id] = result.unwrap_error()
                continue
            descriptors = result.unwrap()
            try:
                accepted = self._registry.reconcile_provider(
                    provider.provider_id,
                    descriptors=descriptors,
                    observed_at=observed_at,
                )
            except (DeviceRegistryError, TypeError) as exc:
                errors[provider.provider_id] = AgentXError(
                    code="device.discovery.invalid_provider_batch",
                    message="provider discovery batch was rejected atomically",
                    category=ErrorCategory.VALIDATION,
                    retryability=Retryability.NON_RETRYABLE,
                    details={"reason": str(exc)[:512]},
                )
                continue
            all_found.extend(accepted)
        return DeviceDiscoveryReport(
            discovered=tuple(
                sorted(
                    all_found,
                    key=lambda item: (item.provider_id.value, item.device_id.value),
                )
            ),
            provider_errors=MappingProxyType(errors),
        )
