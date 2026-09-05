"""Unit coverage for the C5.01 browser-provider boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from typing import Any

import pytest

from agentx.capabilities.abi import (
    Capability,
    CapabilityDescriptor,
    CapabilityIdentity,
    CapabilityName,
    CapabilityObservation,
    CapabilityParams,
    CapabilityPlatform,
    CapabilityRequest,
    CapabilityScope,
    CapabilityVersion,
    ExecutionResult,
    ResourceEstimate,
    RollbackDeclaration,
    RollbackSupport,
    VerificationResult,
)
from agentx.capabilities.browser_provider import (
    CANONICAL_BROWSER_PROVIDER_AVAILABILITY,
    BrowserProviderAvailability,
    BrowserProviderDescriptor,
    BrowserProviderId,
    BrowserProviderStatus,
    BrowserProviderValidationError,
)
from agentx.capabilities.registry import CapabilityRegistry
from agentx.core.execution import ExecutionContext
from agentx.core.tasks import JsonValue
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import assess_risk


@dataclass(frozen=True, slots=True)
class _NoopParams(CapabilityParams):
    def to_dict(self) -> dict[str, JsonValue]:
        return {}


class _BrowserCapability:
    def __init__(self) -> None:
        self._descriptor = CapabilityDescriptor(
            identity=CapabilityIdentity(
                name=CapabilityName("browser.test.read"),
                version=CapabilityVersion(1, 0, 0),
            ),
            description="Read-only synthetic browser capability.",
            scope=CapabilityScope(CapabilityPlatform.ANY),
            required_permissions=frozenset({Permission.READ}),
            risk_assessment=assess_risk(
                read_only=True,
                modifies_state=False,
                reversible=False,
                external_effect=False,
            ),
            preconditions=(),
            rollback=RollbackDeclaration(
                support=RollbackSupport.NOT_APPLICABLE,
                detail="Read-only capability has no rollback operation.",
            ),
            estimate=ResourceEstimate.zero(),
        )

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._descriptor

    def execute(
        self,
        request: CapabilityRequest[_NoopParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        del request, context
        return ExecutionResult(
            succeeded=True,
            message="Synthetic execution only.",
            observation=CapabilityObservation(summary="Synthetic observation."),
        )

    def verify(
        self,
        request: CapabilityRequest[_NoopParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        del request, observation, context
        return VerificationResult(passed=False, detail="C5.01 fabric test does not verify.")


@dataclass(frozen=True, slots=True)
class _Provider:
    descriptor: BrowserProviderDescriptor
    status: BrowserProviderStatus
    capabilities: tuple[Capability[Any], ...]


def _provider(
    availability: BrowserProviderAvailability = BrowserProviderAvailability.NOT_CONNECTED,
) -> _Provider:
    capability: Capability[Any] = _BrowserCapability()
    return _Provider(
        descriptor=BrowserProviderDescriptor(
            provider_id=BrowserProviderId("browser.test"),
            description="Synthetic browser provider.",
        ),
        status=BrowserProviderStatus(
            availability=availability,
            detail="Explicit test status.",
        ),
        capabilities=(capability,),
    )


def test_provider_identity_is_deterministic_value_identity() -> None:
    left = BrowserProviderId("browser.chromium")
    right = BrowserProviderId("browser.chromium")
    other = BrowserProviderId("browser.firefox")

    assert left == right
    assert hash(left) == hash(right)
    assert str(left) == "browser.chromium"
    assert sorted((other, left)) == [left, other]


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " browser.chromium",
        "browser.chromium ",
        "Browser.Chromium",
        "browser..chromium",
        "browser/chromium",
        "browser chromium",
        "browser\nchromium",
        "browser\tchromium",
    ],
)
def test_provider_identity_rejects_malformed_values(raw: str) -> None:
    with pytest.raises(BrowserProviderValidationError):
        BrowserProviderId(raw)


def test_provider_identity_rejects_non_string() -> None:
    with pytest.raises(TypeError, match="browser provider id must be a string"):
        BrowserProviderId(7)  # type: ignore[arg-type]


def test_availability_vocabulary_is_exact_and_deterministic() -> None:
    assert CANONICAL_BROWSER_PROVIDER_AVAILABILITY == (
        BrowserProviderAvailability.AVAILABLE,
        BrowserProviderAvailability.NOT_CONNECTED,
        BrowserProviderAvailability.UNSUPPORTED,
    )
    assert tuple(BrowserProviderAvailability) == CANONICAL_BROWSER_PROVIDER_AVAILABILITY


@pytest.mark.parametrize(
    "availability",
    list(BrowserProviderAvailability),
)
def test_all_availability_states_are_explicit(availability: BrowserProviderAvailability) -> None:
    status = BrowserProviderStatus(availability=availability, detail="Explicit status.")
    assert status.availability is availability


def test_not_connected_is_distinct_from_unsupported_and_available() -> None:
    assert {
        BrowserProviderAvailability.NOT_CONNECTED.value,
        BrowserProviderAvailability.UNSUPPORTED.value,
        BrowserProviderAvailability.AVAILABLE.value,
    } == {"not_connected", "unsupported", "available"}


def test_status_rejects_raw_string_availability() -> None:
    with pytest.raises(TypeError, match="availability must be a BrowserProviderAvailability"):
        BrowserProviderStatus(availability="available", detail="Raw text is not typed state.")  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["description", "detail"])
def test_provider_text_is_bounded_and_control_character_free(field: str) -> None:
    if field == "description":
        with pytest.raises(BrowserProviderValidationError):
            BrowserProviderDescriptor(
                provider_id=BrowserProviderId("browser.test"),
                description="bad\nmetadata",
            )
    else:
        with pytest.raises(BrowserProviderValidationError):
            BrowserProviderStatus(
                availability=BrowserProviderAvailability.NOT_CONNECTED,
                detail="bad\nstatus",
            )


def test_descriptor_and_status_are_immutable_snapshots() -> None:
    descriptor = BrowserProviderDescriptor(
        provider_id=BrowserProviderId("browser.test"),
        description="Inert provider metadata.",
    )
    status = BrowserProviderStatus(
        availability=BrowserProviderAvailability.NOT_CONNECTED,
        detail="No connection has been established.",
    )

    descriptor_dynamic: Any = descriptor
    status_dynamic: Any = status
    with pytest.raises(FrozenInstanceError):
        descriptor_dynamic.description = "changed"
    with pytest.raises(FrozenInstanceError):
        status_dynamic.availability = BrowserProviderAvailability.AVAILABLE


def test_provider_construction_does_not_register_capabilities() -> None:
    registry = CapabilityRegistry()
    provider = _provider()

    assert provider.capabilities
    assert registry.identities() == ()
    assert len(registry) == 0


def test_exposed_browser_capability_uses_canonical_registry_and_abi() -> None:
    registry = CapabilityRegistry()
    provider = _provider(BrowserProviderAvailability.AVAILABLE)
    capability = provider.capabilities[0]

    identity = registry.register(capability)

    assert identity == capability.descriptor.identity
    assert registry.require(identity) is capability
    assert registry.identities() == (identity,)
    assert registry.describe(identity) == capability.descriptor


def test_provider_availability_does_not_change_registry_state() -> None:
    registry = CapabilityRegistry()

    for availability in BrowserProviderAvailability:
        provider = _provider(availability)
        assert provider.status.availability is availability
        assert registry.identities() == ()


def test_provider_metadata_carries_no_authority_or_verification_fields() -> None:
    provider = _provider(BrowserProviderAvailability.AVAILABLE)

    for candidate in (provider.descriptor, provider.status):
        for forbidden in (
            "permission",
            "permissions",
            "authority",
            "risk",
            "budget",
            "emergency_stop",
            "verified",
            "verification",
            "execute",
            "verify",
            "register",
        ):
            assert not hasattr(candidate, forbidden)


def test_c5_01_defines_no_target_or_session_reference() -> None:
    provider = _provider()
    for forbidden in (
        "target",
        "target_id",
        "session",
        "session_id",
        "connect",
        "disconnect",
        "navigate",
        "dom",
        "cdp",
    ):
        assert not hasattr(provider, forbidden)
