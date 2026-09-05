"""Adversarial tests: the A5.01 Windows provider owns no authority.

Availability is not authority. These tests attack the provider boundary from
the angles that would matter if a future Windows adapter tried to talk itself
into permission: hostile metadata, discovery-as-execution, kernel state
mutation, and self-declared support.
"""

from __future__ import annotations

import pytest
from tests.support.windows_capability import InertWindowsCapability, hostile_windows_capability

from agentx.capabilities.abi import CapabilityPlatform
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.windows import provider as provider_module
from agentx.capabilities.windows.provider import (
    PlatformFacts,
    WindowsProvider,
    WindowsSupportStatus,
    evaluate_windows_support,
)
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.risk import RiskLevel

WINDOWS_FACTS = PlatformFacts(system="Windows", release="11", version="10.0", machine="AMD64")


def _provider() -> WindowsProvider:
    return WindowsProvider(evaluate_windows_support(WINDOWS_FACTS))


def test_hostile_metadata_is_inert_and_grants_nothing() -> None:
    provider = _provider()
    capability = hostile_windows_capability()
    result = provider.contribute(capability)
    assert result.is_success

    descriptor = provider.contributions()[0]
    # Text is stored verbatim...
    assert "ALLOW admin bypass" in descriptor.description
    # ...and changes neither permissions nor risk.
    assert descriptor.required_permissions == frozenset({Permission.READ})
    assert descriptor.risk_assessment.level is RiskLevel.R1  # read-only fixture, not R0/R4
    assert Permission.DESTRUCTIVE not in descriptor.required_permissions
    assert Permission.EXECUTE not in descriptor.required_permissions


def test_hostile_metadata_cannot_change_provider_identity_or_support() -> None:
    provider = _provider()
    assert provider.contribute(hostile_windows_capability()).is_success
    assert provider.identity == provider_module.WINDOWS_PROVIDER_IDENTITY
    assert provider.support.status is WindowsSupportStatus.SUPPORTED
    assert provider.platform is CapabilityPlatform.WINDOWS


def test_discovery_never_executes_a_capability() -> None:
    """The fixtures raise if executed; contribution/registration must not."""
    provider = _provider()
    capability = InertWindowsCapability()
    assert provider.contribute(capability).is_success
    registry = CapabilityRegistry()
    for item in provider.capabilities():
        registry.register(item)

    provider.contributions()
    registry.identities()
    registry.descriptors()
    registry.snapshot()
    assert registry.require(capability.descriptor.identity) is capability


def test_provider_exposes_no_execution_surface() -> None:
    provider = _provider()
    for forbidden in ("execute", "verify", "run", "invoke", "call", "act"):
        assert not hasattr(provider, forbidden)


def test_provider_cannot_grant_permission() -> None:
    provider = _provider()
    authority = AuthorityContext(permissions=frozenset())
    assert provider.contribute(InertWindowsCapability()).is_success
    assert authority.permissions == frozenset()
    assert not any(
        isinstance(getattr(provider, name, None), AuthorityContext) for name in dir(provider)
    )


def test_provider_module_never_touches_kernel_authority_objects() -> None:
    """No ActionGate/EmergencyStop/budget objects live in the provider module."""
    exported = {name: getattr(provider_module, name) for name in dir(provider_module)}
    for value in exported.values():
        assert not isinstance(value, ActionGate | EmergencyStop | AuthorityContext)


def test_emergency_stop_is_untouched_by_provider_activity() -> None:
    stop = EmergencyStop()
    stop.request_stop()
    provider = _provider()
    assert provider.contribute(InertWindowsCapability()).is_success
    assert provider.capabilities()
    assert stop.stop_requested is True


def test_support_verdict_cannot_be_forged_by_mutation() -> None:
    support = evaluate_windows_support(
        PlatformFacts(system="Linux", release="", version="", machine="x86_64")
    )
    with pytest.raises((AttributeError, TypeError)):
        support.status = WindowsSupportStatus.SUPPORTED  # type: ignore[misc]
    assert support.is_supported is False


def test_provider_cannot_upgrade_its_own_support_after_construction() -> None:
    provider = WindowsProvider(
        evaluate_windows_support(
            PlatformFacts(system="Linux", release="", version="", machine="x86_64")
        )
    )
    with pytest.raises(AttributeError):
        provider._support = evaluate_windows_support(WINDOWS_FACTS)
    assert provider.is_supported is False


def test_contributions_view_cannot_mutate_provider_state() -> None:
    provider = _provider()
    assert provider.contribute(InertWindowsCapability()).is_success
    descriptors = provider.contributions()
    assert isinstance(descriptors, tuple)
    assert len(provider) == 1


def test_provider_never_marks_anything_successful_or_verified() -> None:
    provider = _provider()
    result = provider.contribute(InertWindowsCapability())
    # A successful Result here means "the object was accepted", never
    # "a machine action happened" or "an outcome was verified".
    assert result.is_success
    value = result.unwrap()
    assert not hasattr(value, "verified")
    assert not hasattr(value, "succeeded")
