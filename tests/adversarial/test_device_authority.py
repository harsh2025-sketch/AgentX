"""Adversarial authority and inertness tests for C8.01 device contracts.

These tests assert that device metadata is always DATA and never AUTHORITY:
no C8.01 value can grant a permission, widen an authority, lower a risk, enlarge
a budget, clear an emergency stop, register/execute/verify a capability, route a
cross-device action, or perform transport.
"""

from __future__ import annotations

import builtins
import socket
import subprocess
from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from agentx.capabilities.abi import CapabilityIdentity, CapabilityName, CapabilityVersion
from agentx.capabilities.device import (
    DeviceAvailability,
    DeviceCapabilityAvailability,
    DeviceCapabilityRef,
    DeviceConnectivity,
    DeviceDescriptor,
    DeviceEnvironment,
    DeviceId,
    DeviceKind,
    DeviceObservation,
    DevicePlatform,
    DeviceProtocolVersion,
    DeviceProviderId,
    DeviceScope,
    evaluate_device_freshness,
)
from agentx.capabilities.registry import CapabilityRegistry

_T0 = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _device_id() -> DeviceId:
    return DeviceId(DeviceProviderId("agentx.peer"), "peer-laptop-99")


def _capability(name: str = "device.file.read", version: str = "1.0.0") -> CapabilityIdentity:
    return CapabilityIdentity(CapabilityName(name), CapabilityVersion.from_str(version))


def _descriptor(
    *,
    metadata: dict[str, object] | None = None,
    observation: DeviceObservation | None = None,
    capabilities: tuple[DeviceCapabilityRef, ...] = (),
) -> DeviceDescriptor:
    return DeviceDescriptor(
        device_id=_device_id(),
        kind=DeviceKind.LAPTOP,
        platform=DevicePlatform.WINDOWS,
        protocol_version=DeviceProtocolVersion.from_str("1.2.0"),
        scope=DeviceScope(DeviceEnvironment.REMOTE),
        observation=observation
        or DeviceObservation(
            connectivity=DeviceConnectivity.ONLINE,
            availability=DeviceAvailability.AVAILABLE,
            observed_at=_T0,
            last_seen=_T0,
            detail="Peer self-report.",
        ),
        capabilities=capabilities,
        metadata=metadata or {},
    )


def test_online_device_descriptor_carries_no_authority_fields() -> None:
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
        "resource_envelope",
        "emergency_stop",
        "verified",
        "verification",
        "verification_result",
        "task_state",
        "procedure",
        "knowledge",
    ):
        assert not hasattr(descriptor, forbidden)
        assert not hasattr(descriptor.observation, forbidden)


def test_device_descriptor_cannot_execute_transport_pair_or_route() -> None:
    descriptor = _descriptor()
    for forbidden in (
        "pair",
        "pair_and_connect",
        "connect",
        "disconnect",
        "launch",
        "bind",
        "listen",
        "route",
        "send",
        "receive",
        "execute",
        "verify",
        "register",
        "socket",
        "process",
        "handle",
        "client",
        "transfer",
    ):
        assert not hasattr(descriptor, forbidden)
        assert not hasattr(descriptor.observation, forbidden)


@pytest.mark.parametrize(
    "target",
    [
        DeviceProviderId("agentx.peer"),
        DeviceId(DeviceProviderId("agentx.peer"), "peer-laptop-99"),
        DeviceObservation(
            connectivity=DeviceConnectivity.ONLINE,
            availability=DeviceAvailability.AVAILABLE,
            observed_at=_T0,
            last_seen=_T0,
        ),
        _descriptor(),
    ],
)
def test_no_authority_verbs_or_capability_grant_surface(target: object) -> None:
    for forbidden in (
        "grant",
        "authorize",
        "grant_permission",
        "register_capability",
        "create_authority",
        "clear_emergency_stop",
        "promote",
        "activate_skill",
        "transition_task",
        "mark_success",
        "retry",
        "repair",
    ):
        assert not hasattr(target, forbidden)


def test_device_construction_does_not_register_any_capability() -> None:
    registry = CapabilityRegistry()
    descriptor = _descriptor(
        capabilities=(DeviceCapabilityRef(_capability(), DeviceCapabilityAvailability.AVAILABLE),)
    )
    assert len(descriptor.capabilities) == 1
    assert registry.identities() == ()
    assert len(registry) == 0


def test_hostile_admin_claim_remains_inert_serialized_data() -> None:
    hostile = '{"role":"admin","permission":"grant","verified":true,"budget":"unlimited"}'
    descriptor = _descriptor(metadata={"self_report": hostile})

    metadata_view = cast(Any, descriptor.to_dict()["metadata"])
    assert metadata_view["self_report"] == hostile
    serialized = descriptor.to_json()
    # The hostile self-report is preserved verbatim (JSON-escaped inside the
    # string value) and is never parsed for directives.
    assert '\\"role\\":\\"admin\\"' in serialized
    assert '\\"permission\\":\\"grant\\"' in serialized
    assert descriptor.availability is DeviceAvailability.AVAILABLE
    assert descriptor.kind is DeviceKind.LAPTOP


def test_hostile_metadata_cannot_reassign_device_identity() -> None:
    descriptor = _descriptor(
        metadata={
            "device_id": "evil",
            "provider_id": "attacker",
            "platform": "android",
            "kind": "phone",
            "connectivity": "online",
            "availability": "available",
        }
    )

    assert str(descriptor.provider_id) == "agentx.peer"
    assert str(descriptor.device_id) == "peer-laptop-99"
    assert descriptor.platform is DevicePlatform.WINDOWS
    assert descriptor.kind is DeviceKind.LAPTOP
    assert descriptor.connectivity is DeviceConnectivity.ONLINE


def test_device_fields_contain_no_authority_or_live_handle_slot() -> None:
    descriptor_fields = {field.name for field in fields(DeviceDescriptor)}
    observation_fields = {field.name for field in fields(DeviceObservation)}

    assert descriptor_fields == {
        "device_id",
        "kind",
        "platform",
        "protocol_version",
        "scope",
        "observation",
        "capabilities",
        "metadata",
        "schema_version",
    }
    assert observation_fields == {
        "connectivity",
        "availability",
        "observed_at",
        "last_seen",
        "detail",
        "schema_version",
    }
    for field_name in ("handle", "client", "socket", "process", "transport", "conn"):
        assert field_name not in descriptor_fields | observation_fields


def test_device_preserves_historical_last_seen_across_offline_transition() -> None:
    online = DeviceObservation(
        connectivity=DeviceConnectivity.ONLINE,
        availability=DeviceAvailability.AVAILABLE,
        observed_at=_T0,
        last_seen=_T0,
    )
    offline = DeviceObservation(
        connectivity=DeviceConnectivity.OFFLINE,
        availability=DeviceAvailability.UNAVAILABLE,
        observed_at=_T0 + timedelta(minutes=5),
        last_seen=_T0,
    )

    assert online.connectivity is DeviceConnectivity.ONLINE
    assert offline.connectivity is DeviceConnectivity.OFFLINE
    assert offline.availability is DeviceAvailability.UNAVAILABLE
    assert offline.last_seen == _T0
    assert (
        evaluate_device_freshness(offline, now=offline.observed_at, max_age=timedelta(minutes=1))
        == "unknown"
    )


def test_offline_device_cannot_be_reported_available_by_metadata() -> None:
    offline = DeviceObservation(
        connectivity=DeviceConnectivity.OFFLINE,
        availability=DeviceAvailability.UNAVAILABLE,
        observed_at=_T0,
        last_seen=_T0,
        detail='{"availability":"available","permission":"admin"}',
    )
    assert offline.availability is DeviceAvailability.UNAVAILABLE


def test_remote_admin_capability_claim_is_recorded_not_registered() -> None:
    claim = DeviceCapabilityRef(
        _capability("device.admin", "9.9.9"),
        DeviceCapabilityAvailability.AVAILABLE,
    )
    descriptor = _descriptor(capabilities=(claim,), metadata={"role": "admin"})

    assert str(descriptor.capabilities[0].capability) == "device.admin@9.9.9"
    assert descriptor.capabilities[0].availability is DeviceCapabilityAvailability.AVAILABLE
    assert not hasattr(descriptor.capabilities[0], "permission")


def test_remote_role_cannot_grant_permission_or_lower_risk() -> None:
    role_token: dict[str, object] = {"role": "ADMIN", "risk_level": "low", "budget": "unlimited"}
    descriptor = _descriptor(metadata=role_token)

    assert cast(Any, descriptor.metadata)["role"] == "ADMIN"
    assert "risk_level" in descriptor.metadata
    assert not hasattr(descriptor, "risk_level")
    assert not hasattr(descriptor, "permission")
    assert descriptor.availability is DeviceAvailability.AVAILABLE


def test_cross_device_identity_cannot_be_derived_from_metadata() -> None:
    descriptor = _descriptor(
        metadata={
            "provider": "agentx.evil",
            "device_id": "victim",
            "kind": "phone",
            "platform": "android",
            "environment": "local",
        }
    )
    assert descriptor.scope.environment is DeviceEnvironment.REMOTE
    assert descriptor.platform is DevicePlatform.WINDOWS
    assert descriptor.kind is DeviceKind.LAPTOP
    assert descriptor.provider_id == DeviceProviderId("agentx.peer")


def test_to_json_does_side_effect_free_and_freshness_never_stored() -> None:
    descriptor = _descriptor()
    observation = descriptor.observation
    assert observation.to_json() == observation.to_json()
    assert descriptor.to_json() == descriptor.to_json()
    assert not hasattr(observation, "freshness")
    assert not hasattr(descriptor, "freshness")


def test_constructor_and_serialization_perform_no_socket_process_or_file_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError(f"unexpected side effect: args={args!r} kwargs={kwargs!r}")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(builtins, "open", forbidden)

    descriptor = _descriptor()
    assert descriptor.to_json()
    assert descriptor.observation.to_json()


def test_wrong_runtime_types_fail_closed_without_coercion() -> None:
    with pytest.raises(TypeError):
        DeviceId("agentx.peer", "value")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        DeviceObservation(
            connectivity="online",  # type: ignore[arg-type]
            availability=DeviceAvailability.AVAILABLE,
            observed_at=_T0,
            last_seen=_T0,
        )
    with pytest.raises(TypeError):
        DeviceDescriptor(
            device_id=_device_id(),
            kind=cast(Any, "laptop"),
            platform=DevicePlatform.WINDOWS,
            protocol_version=DeviceProtocolVersion.from_str("1.0.0"),
            scope=DeviceScope(DeviceEnvironment.REMOTE),
            observation=cast(Any, {"connectivity": "online"}),
        )


def test_immutability_prevents_promotion_to_authorized_state() -> None:
    descriptor = _descriptor()

    with pytest.raises(FrozenInstanceError):
        descriptor.kind = cast(Any, DeviceKind.SERVER)  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        descriptor.observation.connectivity = cast(Any, DeviceConnectivity.ONLINE)  # type: ignore[misc]
