"""Unit tests for the A5.01 Windows provider boundary."""

from __future__ import annotations

import pytest

from agentx.capabilities.abi import (
    CapabilityDescriptor,
    CapabilityPlatform,
    CapabilityVersion,
)
from agentx.capabilities.registry import CapabilityAlreadyRegisteredError, CapabilityRegistry
from agentx.capabilities.windows.provider import (
    WINDOWS_PROVIDER_IDENTITY,
    WINDOWS_PROVIDER_NAME,
    WINDOWS_PROVIDER_VERSION,
    WINDOWS_UNSUPPORTED_ERROR_CODE,
    PlatformFacts,
    WindowsProvider,
    WindowsProviderIdentity,
    WindowsSupport,
    WindowsSupportStatus,
    detect_platform_facts,
    evaluate_windows_support,
    unsupported_platform_error,
)
from agentx.core.errors import AgentXError, AgentXException, ErrorCategory, Retryability
from tests.support.windows_capability import InertWindowsCapability

WINDOWS_FACTS = PlatformFacts(
    system="Windows",
    release="11",
    version="10.0.26100",
    machine="AMD64",
)

LINUX_FACTS = PlatformFacts(
    system="Linux",
    release="6.8.0",
    version="#1 SMP",
    machine="x86_64",
)


def _supported_provider() -> WindowsProvider:
    return WindowsProvider(evaluate_windows_support(WINDOWS_FACTS))


def _unsupported_provider() -> WindowsProvider:
    return WindowsProvider(evaluate_windows_support(LINUX_FACTS))


# --------------------------------------------------------------------------
# Identity.
# --------------------------------------------------------------------------


def test_provider_identity_is_deterministic_and_stable() -> None:
    assert WINDOWS_PROVIDER_IDENTITY.name == WINDOWS_PROVIDER_NAME == "windows"
    assert CapabilityVersion(1, 0, 0) == WINDOWS_PROVIDER_VERSION
    assert WINDOWS_PROVIDER_IDENTITY.platform is CapabilityPlatform.WINDOWS
    assert str(WINDOWS_PROVIDER_IDENTITY) == "windows@1.0.0"


def test_provider_identity_is_a_value_and_equal_across_instances() -> None:
    rebuilt = WindowsProviderIdentity(
        name="windows",
        version=CapabilityVersion(1, 0, 0),
        platform=CapabilityPlatform.WINDOWS,
    )
    assert rebuilt == WINDOWS_PROVIDER_IDENTITY
    assert hash(rebuilt) == hash(WINDOWS_PROVIDER_IDENTITY)


def test_provider_identity_is_immutable() -> None:
    with pytest.raises((AttributeError, TypeError)):
        WINDOWS_PROVIDER_IDENTITY.name = "linux"  # type: ignore[misc]


def test_every_provider_instance_reports_the_same_identity() -> None:
    assert _supported_provider().identity == _unsupported_provider().identity
    assert _supported_provider().platform is CapabilityPlatform.WINDOWS


@pytest.mark.parametrize("bad_name", ["", " windows", "windows\n"])
def test_provider_identity_rejects_malformed_names(bad_name: str) -> None:
    with pytest.raises(ValueError):
        WindowsProviderIdentity(
            name=bad_name,
            version=CapabilityVersion(1, 0, 0),
            platform=CapabilityPlatform.WINDOWS,
        )


def test_provider_identity_rejects_non_canonical_version_and_platform() -> None:
    with pytest.raises(TypeError):
        WindowsProviderIdentity(
            name="windows",
            version="1.0.0",  # type: ignore[arg-type]
            platform=CapabilityPlatform.WINDOWS,
        )
    with pytest.raises(TypeError):
        WindowsProviderIdentity(
            name="windows",
            version=CapabilityVersion(1, 0, 0),
            platform="windows",  # type: ignore[arg-type]
        )


# --------------------------------------------------------------------------
# Platform facts and support.
# --------------------------------------------------------------------------


def test_platform_facts_validate_their_inputs() -> None:
    with pytest.raises(TypeError):
        PlatformFacts(system=1, release="", version="", machine="")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        PlatformFacts(system=" Windows", release="", version="", machine="")
    with pytest.raises(ValueError):
        PlatformFacts(system="Win\x00dows", release="", version="", machine="")


def test_windows_facts_are_supported() -> None:
    support = evaluate_windows_support(WINDOWS_FACTS)
    assert support.status is WindowsSupportStatus.SUPPORTED
    assert support.is_supported is True
    assert support.facts is WINDOWS_FACTS


@pytest.mark.parametrize("system", ["Linux", "Darwin", "Java", "", "windows-like"])
def test_non_windows_facts_are_unsupported(system: str) -> None:
    facts = PlatformFacts(system=system, release="", version="", machine="x86_64")
    support = evaluate_windows_support(facts)
    assert support.status is WindowsSupportStatus.UNSUPPORTED_PLATFORM
    assert support.is_supported is False


@pytest.mark.parametrize("system", ["windows", "WINDOWS", "Windows"])
def test_platform_detection_is_case_insensitive(system: str) -> None:
    facts = PlatformFacts(system=system, release="", version="", machine="AMD64")
    assert facts.is_windows is True
    assert evaluate_windows_support(facts).is_supported is True


def test_support_evaluation_is_pure_and_deterministic() -> None:
    first = evaluate_windows_support(WINDOWS_FACTS)
    second = evaluate_windows_support(WINDOWS_FACTS)
    assert first == second


def test_support_evaluation_rejects_untyped_facts() -> None:
    with pytest.raises(TypeError):
        evaluate_windows_support({"system": "Windows"})  # type: ignore[arg-type]


def test_support_value_validates_its_fields() -> None:
    with pytest.raises(TypeError):
        WindowsSupport(status="supported", facts=WINDOWS_FACTS, reason="x")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        WindowsSupport(
            status=WindowsSupportStatus.SUPPORTED,
            facts=WINDOWS_FACTS,
            reason="",
        )


def test_detect_platform_facts_reads_only_stdlib_strings() -> None:
    facts = detect_platform_facts()
    assert isinstance(facts, PlatformFacts)
    # Deterministic in the sense that repeated detection agrees with itself.
    assert facts == detect_platform_facts()


def test_for_current_host_never_raises_on_any_platform() -> None:
    provider = WindowsProvider.for_current_host()
    assert provider.identity == WINDOWS_PROVIDER_IDENTITY
    assert isinstance(provider.is_supported, bool)


# --------------------------------------------------------------------------
# Unsupported behaviour is explicit and predictable.
# --------------------------------------------------------------------------


def test_unsupported_error_is_a_canonical_agentx_error() -> None:
    support = evaluate_windows_support(LINUX_FACTS)
    error = unsupported_platform_error(support)
    assert isinstance(error, AgentXError)
    assert error.code == WINDOWS_UNSUPPORTED_ERROR_CODE
    assert error.category is ErrorCategory.PRECONDITION
    assert error.retryability is Retryability.NON_RETRYABLE
    assert error.details["provider"] == "windows@1.0.0"


def test_unsupported_error_requires_an_unsupported_verdict() -> None:
    with pytest.raises(ValueError):
        unsupported_platform_error(evaluate_windows_support(WINDOWS_FACTS))
    with pytest.raises(TypeError):
        unsupported_platform_error("unsupported")  # type: ignore[arg-type]


def test_unsupported_provider_refuses_contribution_predictably() -> None:
    provider = _unsupported_provider()
    result = provider.contribute(InertWindowsCapability())
    assert result.is_failure
    assert result.unwrap_error().code == WINDOWS_UNSUPPORTED_ERROR_CODE
    assert len(provider) == 0


def test_unsupported_provider_hands_out_no_capabilities() -> None:
    provider = _unsupported_provider()
    with pytest.raises(AgentXException) as excinfo:
        provider.capabilities()
    assert excinfo.value.error.code == WINDOWS_UNSUPPORTED_ERROR_CODE
    assert len(CapabilityRegistry()) == 0


def test_unsupported_provider_still_constructs_and_reports() -> None:
    provider = _unsupported_provider()
    assert provider.is_supported is False
    assert provider.support.status is WindowsSupportStatus.UNSUPPORTED_PLATFORM
    assert provider.contributions() == ()
    assert "unsupported_platform" in repr(provider)


# --------------------------------------------------------------------------
# Contribution / registry compatibility.
# --------------------------------------------------------------------------


def test_provider_requires_an_explicit_support_verdict() -> None:
    with pytest.raises(TypeError):
        WindowsProvider("supported")  # type: ignore[arg-type]


def test_contribution_returns_the_canonical_identity() -> None:
    provider = _supported_provider()
    capability = InertWindowsCapability()
    result = provider.contribute(capability)
    assert result.is_success
    assert result.unwrap() == capability.descriptor.identity
    assert len(provider) == 1


def test_contribution_rejects_non_windows_scope() -> None:
    provider = _supported_provider()
    result = provider.contribute(InertWindowsCapability(platform=CapabilityPlatform.ANY))
    assert result.is_failure
    assert result.unwrap_error().code == "capabilities.windows.scope_mismatch"


def test_contribution_rejects_objects_without_a_canonical_descriptor() -> None:
    provider = _supported_provider()
    result = provider.contribute(object())  # type: ignore[arg-type]
    assert result.is_failure
    assert result.unwrap_error().code == "capabilities.windows.malformed_capability"


def test_duplicate_contribution_fails_explicitly() -> None:
    provider = _supported_provider()
    assert provider.contribute(InertWindowsCapability()).is_success
    duplicate = provider.contribute(InertWindowsCapability())
    assert duplicate.is_failure
    assert duplicate.unwrap_error().code == "capabilities.windows.duplicate_capability"
    assert len(provider) == 1


def test_contributions_are_deterministically_ordered_descriptors() -> None:
    provider = _supported_provider()
    for name in ("windows.zeta", "windows.alpha", "windows.mid"):
        assert provider.contribute(InertWindowsCapability(name=name)).is_success
    descriptors = provider.contributions()
    assert all(isinstance(item, CapabilityDescriptor) for item in descriptors)
    assert [str(item.identity.name) for item in descriptors] == [
        "windows.alpha",
        "windows.mid",
        "windows.zeta",
    ]


def test_versioned_capabilities_coexist_and_sort_by_version() -> None:
    provider = _supported_provider()
    assert provider.contribute(
        InertWindowsCapability(name="windows.probe", version=CapabilityVersion(2, 0, 0))
    ).is_success
    assert provider.contribute(
        InertWindowsCapability(name="windows.probe", version=CapabilityVersion(1, 3, 0))
    ).is_success
    versions = [item.identity.version.to_str() for item in provider.contributions()]
    assert versions == ["1.3.0", "2.0.0"]


def test_capabilities_are_registrable_by_the_composition_root() -> None:
    """The provider hands objects over; the caller owns the registry."""
    provider = _supported_provider()
    capability = InertWindowsCapability()
    assert provider.contribute(capability).is_success

    registry = CapabilityRegistry()
    identities = [registry.register(item) for item in provider.capabilities()]

    assert identities == [capability.descriptor.identity]
    assert registry.require(capability.descriptor.identity) is capability
    assert registry.describe(capability.descriptor.identity) == capability.descriptor


def test_provider_exposes_no_registration_surface() -> None:
    """A1.09 reserves registry wiring; the provider must not take it over."""
    provider = _supported_provider()
    for forbidden in ("register_into", "register", "registry"):
        assert not hasattr(provider, forbidden)


def test_capabilities_are_returned_in_deterministic_order() -> None:
    provider = _supported_provider()
    for name in ("windows.zeta", "windows.alpha"):
        assert provider.contribute(InertWindowsCapability(name=name)).is_success
    names = [str(item.descriptor.identity.name) for item in provider.capabilities()]
    assert names == ["windows.alpha", "windows.zeta"]


def test_registering_the_same_capability_twice_conflicts_canonically() -> None:
    provider = _supported_provider()
    assert provider.contribute(InertWindowsCapability()).is_success
    registry = CapabilityRegistry()
    for item in provider.capabilities():
        registry.register(item)
    with pytest.raises(CapabilityAlreadyRegisteredError):
        for item in provider.capabilities():
            registry.register(item)
