"""Android device provider and ADB-backed discovery for AgentX M13."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from agentx.capabilities.abi import Capability
from agentx.capabilities.android.runtime import (
    ANDROID_CAPABILITY_IDENTITIES,
    AndroidOperation,
    build_android_capabilities,
)
from agentx.capabilities.android.transport import AdbDeviceRecord, AdbDeviceState, AdbTransport
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
)
from agentx.core.errors import AgentXError, ErrorCategory, Retryability
from agentx.core.execution import ExecutionContext
from agentx.core.result import Result

__all__ = [
    "ANDROID_ADB_PROVIDER_ID",
    "AndroidProvider",
]

ANDROID_ADB_PROVIDER_ID: Final[DeviceProviderId] = DeviceProviderId("android.adb")
_ANDROID_PROTOCOL_VERSION: Final[DeviceProtocolVersion] = DeviceProtocolVersion(1, 0, 0)
_MAX_PROPERTY_TEXT: Final[int] = 256


class AndroidProvider:
    """Provider-neutral Android contribution/discovery boundary.

    Device/provider metadata remains evidence only. Capabilities returned by
    capabilities() still require registration and canonical Executor/ActionGate
    execution before any operation can occur.
    """

    def __init__(self, transport: AdbTransport) -> None:
        if not isinstance(transport, AdbTransport):
            raise TypeError("transport must be AdbTransport")
        self._transport = transport
        self._capabilities = build_android_capabilities(transport)

    @property
    def provider_id(self) -> DeviceProviderId:
        return ANDROID_ADB_PROVIDER_ID

    @property
    def transport(self) -> AdbTransport:
        return self._transport

    def capabilities(self) -> tuple[Capability[Any], ...]:
        return self._capabilities

    def discover(
        self,
        *,
        context: ExecutionContext,
        observed_at: datetime,
    ) -> Result[tuple[DeviceDescriptor, ...], AgentXError]:
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be ExecutionContext")
        if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
            raise TypeError("observed_at must be timezone-aware datetime")
        moment = observed_at.astimezone(UTC)
        enumerated = self._transport.devices(context=context)
        if enumerated.is_failure:
            return Result.failure(enumerated.unwrap_error())
        descriptors: list[DeviceDescriptor] = []
        for record in enumerated.unwrap():
            try:
                descriptors.append(self._descriptor(record, context=context, observed_at=moment))
            except RuntimeError as exc:
                return Result.failure(
                    AgentXError(
                        code="android.discovery.metadata_failed",
                        message="Android device metadata discovery failed",
                        category=ErrorCategory.DEPENDENCY,
                        retryability=Retryability.RETRYABLE,
                        details={"state": record.state.value, "reason": str(exc)[:256]},
                    )
                )
        return Result.success(tuple(sorted(descriptors, key=lambda item: item.device_id.value)))

    def _descriptor(
        self,
        record: AdbDeviceRecord,
        *,
        context: ExecutionContext,
        observed_at: datetime,
    ) -> DeviceDescriptor:
        online = record.state is AdbDeviceState.DEVICE
        metadata: dict[str, object] = {
            "adb_state": record.state.value,
            "transport_metadata": {key: value for key, value in record.metadata},
        }
        last_seen = observed_at
        if online:
            metadata["android_version"] = self._property(
                record.serial,
                "ro.build.version.release",
                context=context,
            )
            metadata["model"] = self._property(
                record.serial,
                "ro.product.model",
                context=context,
            )
            metadata["sdk"] = self._property(
                record.serial,
                "ro.build.version.sdk",
                context=context,
            )
        capability_state = (
            DeviceCapabilityAvailability.AVAILABLE
            if online
            else DeviceCapabilityAvailability.UNAVAILABLE
        )
        capabilities = tuple(
            DeviceCapabilityRef(
                capability=ANDROID_CAPABILITY_IDENTITIES[operation],
                availability=capability_state,
            )
            for operation in AndroidOperation
        )
        return DeviceDescriptor(
            device_id=DeviceId(self.provider_id, record.serial),
            kind=DeviceKind.PHONE,
            platform=DevicePlatform.ANDROID,
            protocol_version=_ANDROID_PROTOCOL_VERSION,
            scope=DeviceScope(environment=DeviceEnvironment.LOCAL),
            observation=DeviceObservation(
                connectivity=(
                    DeviceConnectivity.ONLINE if online else DeviceConnectivity.OFFLINE
                ),
                availability=(
                    DeviceAvailability.AVAILABLE
                    if online
                    else DeviceAvailability.UNAVAILABLE
                ),
                observed_at=observed_at,
                last_seen=last_seen,
                detail=None if online else f"ADB state is {record.state.value}",
            ),
            capabilities=capabilities,
            metadata=metadata,
        )

    def _property(
        self,
        serial: str,
        name: str,
        *,
        context: ExecutionContext,
    ) -> str:
        result = self._transport.shell(
            serial,
            ("getprop", name),
            context=context,
            max_output_bytes=4096,
        )
        if result.is_failure:
            raise RuntimeError(result.unwrap_error().code)
        command = result.unwrap()
        if not command.succeeded:
            raise RuntimeError("android.discovery.getprop_failed")
        try:
            value = command.stdout.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise RuntimeError("android.discovery.getprop_non_utf8") from exc
        if len(value) > _MAX_PROPERTY_TEXT:
            raise RuntimeError("android.discovery.getprop_oversized")
        return value
