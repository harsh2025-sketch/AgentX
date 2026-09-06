"""Unit coverage for the C8.01 device abstraction/protocol boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from agentx.capabilities.abi import CapabilityIdentity, CapabilityName, CapabilityVersion
from agentx.capabilities.device import (
    CANONICAL_DEVICE_AVAILABILITY,
    CANONICAL_DEVICE_CAPABILITY_AVAILABILITY,
    CANONICAL_DEVICE_CONNECTIVITY,
    CANONICAL_DEVICE_ENVIRONMENTS,
    CANONICAL_DEVICE_FRESHNESS,
    CANONICAL_DEVICE_KINDS,
    CANONICAL_DEVICE_PLATFORMS,
    DEVICE_CAPABILITY_SCHEMA_VERSION,
    DEVICE_OBSERVATION_SCHEMA_VERSION,
    DEVICE_PROTOCOL_SCHEMA_VERSION,
    DeviceAvailability,
    DeviceCapabilityAvailability,
    DeviceCapabilityRef,
    DeviceConnectivity,
    DeviceDescriptor,
    DeviceEnvironment,
    DeviceFreshness,
    DeviceId,
    DeviceKind,
    DeviceObservation,
    DevicePlatform,
    DeviceProtocolVersion,
    DeviceProviderId,
    DeviceScope,
    DeviceValidationError,
    evaluate_device_freshness,
)

_T0 = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
_MAX_AGE = timedelta(minutes=30)


def _provider(value: str = "agentx.peer") -> DeviceProviderId:
    return DeviceProviderId(value)


def _device_id(
    value: str = "peer-laptop-99",
    *,
    provider_id: DeviceProviderId | None = None,
) -> DeviceId:
    return DeviceId(provider_id=provider_id or _provider(), value=value)


def _version(raw: str = "1.2.0") -> DeviceProtocolVersion:
    return DeviceProtocolVersion.from_str(raw)


def _scope(environment: DeviceEnvironment = DeviceEnvironment.REMOTE) -> DeviceScope:
    return DeviceScope(environment)


def _observation(
    *,
    connectivity: DeviceConnectivity = DeviceConnectivity.ONLINE,
    availability: DeviceAvailability = DeviceAvailability.AVAILABLE,
    observed_at: datetime = _T0,
    last_seen: datetime = _T0,
    detail: str | None = "peer report.",
) -> DeviceObservation:
    return DeviceObservation(
        connectivity=connectivity,
        availability=availability,
        observed_at=observed_at,
        last_seen=last_seen,
        detail=detail,
    )


def _capability(name: str = "device.file.read", version: str = "1.0.0") -> CapabilityIdentity:
    return CapabilityIdentity(CapabilityName(name), CapabilityVersion.from_str(version))


def _capability_ref(
    capability: CapabilityIdentity,
    availability: DeviceCapabilityAvailability = DeviceCapabilityAvailability.AVAILABLE,
) -> DeviceCapabilityRef:
    return DeviceCapabilityRef(capability=capability, availability=availability)


def _descriptor(
    *,
    device_id: DeviceId | None = None,
    kind: DeviceKind = DeviceKind.LAPTOP,
    platform: DevicePlatform = DevicePlatform.WINDOWS,
    protocol_version: DeviceProtocolVersion | None = None,
    scope: DeviceScope | None = None,
    observation: DeviceObservation | None = None,
    capabilities: tuple[DeviceCapabilityRef, ...] = (),
    metadata: dict[str, object] | None = None,
) -> DeviceDescriptor:
    return DeviceDescriptor(
        device_id=device_id or _device_id(),
        kind=kind,
        platform=platform,
        protocol_version=protocol_version or _version(),
        scope=scope or _scope(),
        observation=observation or _observation(),
        capabilities=capabilities,
        metadata=metadata or {},
    )


# --------------------------------------------------------------------------
# Identity.
# --------------------------------------------------------------------------


def test_device_provider_identity_is_validated_and_deterministic() -> None:
    left = _provider()
    right = _provider()
    assert left == right
    assert hash(left) == hash(right)
    assert str(left) == "agentx.peer"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " Upper",
        "Upper ",
        "upper\u00ff",
        "upper name",
        "UPPER",
        "a..b",
        ".a",
        "a.",
        "-a",
        "a-",
        "a__b",
    ],
)
def test_device_provider_identity_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(DeviceValidationError):
        DeviceProviderId(raw)


def test_device_provider_identity_accepts_canonical_separated_segments() -> None:
    assert str(DeviceProviderId("a.b")) == "a.b"
    assert str(DeviceProviderId("a_b")) == "a_b"
    assert str(DeviceProviderId("a-b")) == "a-b"
    assert str(DeviceProviderId("agentx.peer")) == "agentx.peer"


def test_device_id_is_provider_scoped_and_deterministic() -> None:
    left = _device_id(value="peer-laptop-99")
    right = _device_id(value="peer-laptop-99")
    other_provider = DeviceId(_provider("agentx.other"), "peer-laptop-99")
    other_value = _device_id(value="peer-laptop-100")

    assert left == right
    assert hash(left) == hash(right)
    assert left != other_provider
    assert left != other_value
    assert str(left) == "peer-laptop-99"
    assert left.provider_id == _provider()


def test_device_id_requires_typed_provider_identity() -> None:
    with pytest.raises(TypeError, match="DeviceProviderId"):
        DeviceId("agentx.peer", "value")  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", ["", " value", "value ", "value\n1", "value\x001"])
def test_device_id_rejects_malformed_opaque_values(raw: str) -> None:
    with pytest.raises(DeviceValidationError):
        DeviceId(_provider(), raw)


# --------------------------------------------------------------------------
# Vocabularies are exact and closed.
# --------------------------------------------------------------------------


def test_device_kind_vocabulary_is_exact_and_closed() -> None:
    assert tuple(DeviceKind) == CANONICAL_DEVICE_KINDS
    assert CANONICAL_DEVICE_KINDS == (
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


def test_device_platform_vocabulary_is_exact_closed_and_platform_neutral() -> None:
    assert tuple(DevicePlatform) == CANONICAL_DEVICE_PLATFORMS
    assert CANONICAL_DEVICE_PLATFORMS == (
        DevicePlatform.WINDOWS,
        DevicePlatform.LINUX,
        DevicePlatform.MACOS,
        DevicePlatform.ANDROID,
        DevicePlatform.IOS,
        DevicePlatform.OTHER,
        DevicePlatform.UNKNOWN,
    )
    # Android is an ordinary member, never the privileged abstraction.
    assert DevicePlatform.ANDROID in CANONICAL_DEVICE_PLATFORMS
    assert DevicePlatform.UNKNOWN in CANONICAL_DEVICE_PLATFORMS


def test_device_environment_vocabulary_is_exact_and_closed() -> None:
    assert tuple(DeviceEnvironment) == CANONICAL_DEVICE_ENVIRONMENTS
    assert CANONICAL_DEVICE_ENVIRONMENTS == (
        DeviceEnvironment.LOCAL,
        DeviceEnvironment.TRUSTED_NETWORK,
        DeviceEnvironment.REMOTE,
        DeviceEnvironment.UNKNOWN,
    )


def test_device_connectivity_vocabulary_is_exact_and_closed() -> None:
    assert tuple(DeviceConnectivity) == CANONICAL_DEVICE_CONNECTIVITY
    assert CANONICAL_DEVICE_CONNECTIVITY == (
        DeviceConnectivity.ONLINE,
        DeviceConnectivity.OFFLINE,
        DeviceConnectivity.STALE,
        DeviceConnectivity.UNKNOWN,
    )


def test_device_availability_vocabulary_is_exact_and_closed() -> None:
    assert tuple(DeviceAvailability) == CANONICAL_DEVICE_AVAILABILITY
    assert CANONICAL_DEVICE_AVAILABILITY == (
        DeviceAvailability.AVAILABLE,
        DeviceAvailability.UNAVAILABLE,
        DeviceAvailability.STALE,
        DeviceAvailability.UNKNOWN,
    )


def test_device_capability_availability_vocabulary_is_exact_and_closed() -> None:
    assert tuple(DeviceCapabilityAvailability) == CANONICAL_DEVICE_CAPABILITY_AVAILABILITY
    assert CANONICAL_DEVICE_CAPABILITY_AVAILABILITY == (
        DeviceCapabilityAvailability.AVAILABLE,
        DeviceCapabilityAvailability.UNAVAILABLE,
        DeviceCapabilityAvailability.UNKNOWN,
    )


def test_device_freshness_vocabulary_is_exact_and_closed() -> None:
    assert tuple(DeviceFreshness) == CANONICAL_DEVICE_FRESHNESS
    assert CANONICAL_DEVICE_FRESHNESS == (
        DeviceFreshness.FRESH,
        DeviceFreshness.STALE,
        DeviceFreshness.UNKNOWN,
    )


@pytest.mark.parametrize("state", list(DeviceConnectivity))
def test_all_connectivity_states_are_representable(state: DeviceConnectivity) -> None:
    availability = (
        DeviceAvailability.AVAILABLE
        if state is DeviceConnectivity.ONLINE
        else DeviceAvailability.UNAVAILABLE
    )
    observation = _observation(connectivity=state, availability=availability)
    assert observation.connectivity is state
    assert observation.availability is availability


# --------------------------------------------------------------------------
# Protocol version.
# --------------------------------------------------------------------------


def test_protocol_version_is_typed_and_deterministic() -> None:
    left = _version("1.2.0")
    right = _version("1.2.0")
    assert left == right
    assert hash(left) == hash(right)
    assert left.major == 1
    assert left.minor == 2
    assert left.patch == 0
    assert left.to_str() == "1.2.0"
    assert str(left) == "1.2.0"


@pytest.mark.parametrize(
    "raw",
    ["", "1", "1.2", "1.2.0.3", "1.2.x", "1.-2.0", "1.2.-1", "1.2.0 ", " 1.2.0"],
)
def test_protocol_version_rejects_malformed_strings(raw: str) -> None:
    with pytest.raises(DeviceValidationError):
        DeviceProtocolVersion.from_str(raw)


def test_protocol_version_rejects_non_positive_or_overflow_components() -> None:
    with pytest.raises(DeviceValidationError):
        DeviceProtocolVersion(major=-1, minor=0, patch=0)
    with pytest.raises(TypeError):
        DeviceProtocolVersion(major=True, minor=0, patch=0)


# --------------------------------------------------------------------------
# Capability references / descriptors.
# --------------------------------------------------------------------------


def test_capability_ref_reuses_canonical_capability_identity() -> None:
    ref = _capability_ref(_capability())
    assert isinstance(ref.capability, CapabilityIdentity)
    assert ref.capability.name.value == "device.file.read"
    assert ref.capability.version.major == 1


def test_capability_ref_requires_typed_identity_and_availability() -> None:
    with pytest.raises(TypeError, match="CapabilityIdentity"):
        DeviceCapabilityRef("device.file.read", DeviceCapabilityAvailability.AVAILABLE)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="DeviceCapabilityAvailability"):
        DeviceCapabilityRef(_capability(), "available")  # type: ignore[arg-type]


def test_capability_ref_schema_version_is_explicit_and_fail_closed() -> None:
    assert DEVICE_CAPABILITY_SCHEMA_VERSION == 1
    with pytest.raises(DeviceValidationError, match="capability schema version"):
        DeviceCapabilityRef(
            _capability(), DeviceCapabilityAvailability.AVAILABLE, schema_version=99
        )


def test_descriptor_rejects_duplicate_capability_identities() -> None:
    capability = _capability()
    duplicates = (
        _capability_ref(capability),
        _capability_ref(
            CapabilityIdentity(CapabilityName("device.file.read"), CapabilityVersion(1, 0, 0)),
            DeviceCapabilityAvailability.UNAVAILABLE,
        ),
    )
    with pytest.raises(DeviceValidationError, match="unique canonical capability"):
        _descriptor(capabilities=duplicates)


def test_capability_ref_version_is_data_not_authority() -> None:
    # A peer may advertise any capability version; it is recorded as data and
    # never changes authority or grants anything.
    ref = _capability_ref(_capability(version="9.9.9"))
    assert str(ref.capability) == "device.file.read@9.9.9"
    assert ref.availability is DeviceCapabilityAvailability.AVAILABLE
    assert not hasattr(ref, "permission")


# --------------------------------------------------------------------------
# Online/offline transitions and availability state.
# --------------------------------------------------------------------------


def test_available_device_requires_online_connectivity() -> None:
    with pytest.raises(DeviceValidationError, match="requires online connectivity"):
        _observation(
            connectivity=DeviceConnectivity.OFFLINE,
            availability=DeviceAvailability.AVAILABLE,
        )
    with pytest.raises(DeviceValidationError, match="requires online connectivity"):
        _observation(
            connectivity=DeviceConnectivity.STALE,
            availability=DeviceAvailability.AVAILABLE,
        )


def test_offline_device_is_explicitly_unavailable() -> None:
    offline = _observation(
        connectivity=DeviceConnectivity.OFFLINE,
        availability=DeviceAvailability.UNAVAILABLE,
    )
    assert offline.connectivity is DeviceConnectivity.OFFLINE
    assert offline.availability is DeviceAvailability.UNAVAILABLE
    # Last seen is preserved as data even when offline.
    assert offline.last_seen == _T0


@pytest.mark.parametrize(
    "connectivity",
    [DeviceConnectivity.OFFLINE, DeviceConnectivity.STALE, DeviceConnectivity.UNKNOWN],
)
def test_non_online_connectivity_cannot_be_available(connectivity: DeviceConnectivity) -> None:
    with pytest.raises(DeviceValidationError):
        DeviceObservation(
            connectivity=connectivity,
            availability=DeviceAvailability.AVAILABLE,
            observed_at=_T0,
            last_seen=_T0,
        )


def test_online_observation_repr_captures_state_snapshot() -> None:
    observation = _observation()
    assert observation.to_dict() == {
        "schema_version": 1,
        "connectivity": "online",
        "availability": "available",
        "observed_at": "2026-09-06T12:00:00.000000Z",
        "last_seen": "2026-09-06T12:00:00.000000Z",
        "detail": "peer report.",
    }


# --------------------------------------------------------------------------
# Freshness.
# --------------------------------------------------------------------------


def test_freshness_is_fresh_within_max_age() -> None:
    observation = _observation(last_seen=_T0)
    assert (
        evaluate_device_freshness(observation, now=_T0 + timedelta(minutes=10), max_age=_MAX_AGE)
        is DeviceFreshness.FRESH
    )


def test_freshness_is_stale_beyond_max_age() -> None:
    observation = _observation(last_seen=_T0)
    assert (
        evaluate_device_freshness(observation, now=_T0 + timedelta(hours=2), max_age=_MAX_AGE)
        is DeviceFreshness.STALE
    )


def test_offline_device_is_never_fresh() -> None:
    offline = _observation(
        connectivity=DeviceConnectivity.OFFLINE,
        availability=DeviceAvailability.UNAVAILABLE,
        observed_at=_T0,
        last_seen=_T0 - timedelta(minutes=1),
    )
    assert evaluate_device_freshness(offline, now=_T0, max_age=_MAX_AGE) is DeviceFreshness.UNKNOWN


def test_unknown_and_stale_connectivity_freshness() -> None:
    unknown = _observation(
        connectivity=DeviceConnectivity.UNKNOWN,
        availability=DeviceAvailability.UNKNOWN,
        observed_at=_T0,
        last_seen=_T0 - timedelta(seconds=1),
    )
    stale = _observation(
        connectivity=DeviceConnectivity.STALE,
        availability=DeviceAvailability.STALE,
        observed_at=_T0,
        last_seen=_T0 - timedelta(minutes=1),
    )
    assert evaluate_device_freshness(unknown, now=_T0, max_age=_MAX_AGE) is DeviceFreshness.UNKNOWN
    assert evaluate_device_freshness(stale, now=_T0, max_age=_MAX_AGE) is DeviceFreshness.STALE


def test_freshness_function_rejects_bad_arguments() -> None:
    with pytest.raises(TypeError, match="DeviceObservation"):
        evaluate_device_freshness({}, now=_T0, max_age=timedelta())  # type: ignore[arg-type]
    observation = _observation()
    with pytest.raises(TypeError, match="timedelta"):
        evaluate_device_freshness(observation, now=_T0, max_age=30)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Malformed device.
# --------------------------------------------------------------------------


def test_descriptor_requires_typed_fields() -> None:
    with pytest.raises(TypeError, match="DeviceId"):
        _descriptor(device_id="agentx.peer/peer")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="DeviceProtocolVersion"):
        _descriptor(protocol_version="1.2.0")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="DeviceObservation"):
        _descriptor(observation={"connectivity": "online"})  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="DeviceScope"):
        _descriptor(scope="remote")  # type: ignore[arg-type]


def test_descriptor_rejects_raw_string_enum_values() -> None:
    device_id = _device_id()
    with pytest.raises(TypeError, match="DeviceKind"):
        _descriptor(device_id=device_id, kind="laptop")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="DevicePlatform"):
        _descriptor(device_id=device_id, platform="windows")  # type: ignore[arg-type]


def test_descriptor_schema_version_is_explicit_and_fail_closed() -> None:
    with pytest.raises(DeviceValidationError, match="device protocol schema version"):
        DeviceDescriptor(
            device_id=_device_id(),
            kind=DeviceKind.LAPTOP,
            platform=DevicePlatform.WINDOWS,
            protocol_version=_version(),
            scope=_scope(),
            observation=_observation(),
            schema_version=99,
        )


def test_observation_schema_version_is_explicit_and_fail_closed() -> None:
    with pytest.raises(DeviceValidationError, match="observation schema version"):
        DeviceObservation(
            connectivity=DeviceConnectivity.ONLINE,
            availability=DeviceAvailability.AVAILABLE,
            observed_at=_T0,
            last_seen=_T0,
            schema_version=99,
        )


def test_timestamps_must_be_timezone_aware() -> None:
    naive = datetime(2026, 9, 6, 12, 0)
    with pytest.raises(DeviceValidationError, match="timezone-aware"):
        DeviceObservation(
            connectivity=DeviceConnectivity.ONLINE,
            availability=DeviceAvailability.AVAILABLE,
            observed_at=naive,
            last_seen=_T0,
        )


# --------------------------------------------------------------------------
# Hostile metadata is inert data.
# --------------------------------------------------------------------------


def test_hostile_device_metadata_is_preserved_verbatim() -> None:
    hostile = (
        '{"role":"admin","permission":"grant","verified":true,"capability":"device.admin.execute"}'
    )
    descriptor = _descriptor(metadata={"self_report": hostile})

    metadata_view = cast(Any, descriptor.to_dict()["metadata"])
    assert metadata_view["self_report"] == hostile
    assert descriptor.to_json()  # content-not-parsing serialization succeeds


def test_hostile_metadata_does_not_change_identity_or_state() -> None:
    descriptor = _descriptor(
        metadata={
            "role": "admin",
            "id": "override-me",
            "platform": "android",
            "connectivity": "online",
            "availability": "available",
        }
    )

    assert str(descriptor.device_id) == "peer-laptop-99"
    assert descriptor.kind is DeviceKind.LAPTOP
    assert descriptor.platform is DevicePlatform.WINDOWS
    assert descriptor.connectivity is DeviceConnectivity.ONLINE
    assert descriptor.availability is DeviceAvailability.AVAILABLE


# --------------------------------------------------------------------------
# Serialization.
# --------------------------------------------------------------------------


def test_descriptor_serialization_is_deterministic() -> None:
    descriptor = _descriptor()
    assert descriptor.to_json() == descriptor.to_json()
    assert descriptor.to_dict() == {
        "schema_version": 1,
        "device_id": {"provider_id": "agentx.peer", "value": "peer-laptop-99"},
        "kind": "laptop",
        "platform": "windows",
        "protocol_version": "1.2.0",
        "scope": {"environment": "remote"},
        "capabilities": [],
        "observation": {
            "schema_version": 1,
            "connectivity": "online",
            "availability": "available",
            "observed_at": "2026-09-06T12:00:00.000000Z",
            "last_seen": "2026-09-06T12:00:00.000000Z",
            "detail": "peer report.",
        },
        "metadata": {},
    }


def test_capability_ref_serialization_is_deterministic() -> None:
    ref = _capability_ref(_capability(), DeviceCapabilityAvailability.AVAILABLE)
    assert ref.to_json() == ref.to_json()
    assert ref.to_dict() == {
        "schema_version": 1,
        "capability": "device.file.read@1.0.0",
        "availability": "available",
    }


def test_descriptor_metadata_is_frozen_json_object() -> None:
    nested = {"good": [1, 2, 3]}
    metadata: dict[str, object] = {"nested": nested}
    descriptor = _descriptor(metadata=metadata)

    # Attempting to mutate the original dict must not leak into the descriptor.
    nested["good"].append(4)
    assert descriptor.to_dict()["metadata"] == {"nested": {"good": [1, 2, 3]}}

    # The stored metadata is an immutable mapping with immutable nested values.
    stored = cast(Any, descriptor.metadata)
    assert isinstance(stored["nested"]["good"], tuple)
    with pytest.raises(TypeError):
        stored["nested"]["good"] = [5]


# --------------------------------------------------------------------------
# Immutability and no live surface.
# --------------------------------------------------------------------------


def test_descriptor_and_observation_are_immutable_snapshots() -> None:
    descriptor = _descriptor()
    observation = descriptor.observation
    descriptor_dynamic: Any = descriptor
    observation_dynamic: Any = observation

    with pytest.raises(FrozenInstanceError):
        descriptor_dynamic.kind = DeviceKind.PHONE
    with pytest.raises(FrozenInstanceError):
        observation_dynamic.connectivity = DeviceConnectivity.OFFLINE


def test_descriptor_has_no_authority_execution_or_transport_surface() -> None:
    descriptor = _descriptor()
    for forbidden in (
        "permission",
        "permissions",
        "authority",
        "authority_context",
        "risk",
        "risk_level",
        "budget",
        "resource_budget",
        "emergency_stop",
        "verified",
        "verification",
        "task_state",
        "pair",
        "pair_and_connect",
        "connect",
        "disconnect",
        "launch",
        "execute",
        "verify",
        "register",
        "route",
        "socket",
        "process",
        "handle",
        "client",
    ):
        assert not hasattr(descriptor, forbidden)


def test_descriptor_exposes_only_inert_snapshot_accessors() -> None:
    descriptor = _descriptor()
    assert descriptor.provider_id == _provider()
    assert descriptor.connectivity is DeviceConnectivity.ONLINE
    assert descriptor.availability is DeviceAvailability.AVAILABLE
    assert descriptor.last_seen == _T0
    assert descriptor.kind is DeviceKind.LAPTOP
    assert descriptor.platform is DevicePlatform.WINDOWS
    assert descriptor.protocol_version == _version()
    assert descriptor.scope.environment is DeviceEnvironment.REMOTE


def test_observation_schema_version_constant_is_exact() -> None:
    assert DEVICE_OBSERVATION_SCHEMA_VERSION == 1
    assert DEVICE_PROTOCOL_SCHEMA_VERSION == 1
