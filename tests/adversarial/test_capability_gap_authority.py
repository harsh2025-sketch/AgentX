"""Adversarial tests for the A9.01 missing-capability detection boundary.

These tests defend one rule above all others::

    DENIED != MISSING

Policy denial, risk restriction, emergency stop, and budget exhaustion must
never be laundered into a "missing capability" that would justify building a
replacement, and nothing this module produces may grant, widen, or bypass
authority.
"""

from __future__ import annotations

import dataclasses

import pytest
from tests.support.capability_gap_fixtures import (
    HOSTILE_TEXT,
    available,
    procedure_alternative,
    requirement,
)

from agentx.capabilities.abi import CapabilityName, CapabilityPlatform
from agentx.capabilities.capability_gap import (
    CapabilityGapAssessment,
    CapabilityGapAssessmentRequest,
    CapabilityGapDetector,
    CapabilityGapReason,
    CapabilityGapStatus,
    CapabilityGapValidationError,
    CapabilityIoContract,
    CapabilityRequirementAssessment,
    CapabilityRestriction,
    CapabilityRestrictionKind,
    EnvironmentProfile,
    GovernanceSignals,
    MissingCapabilityRepresentation,
)
from agentx.capabilities.registry import CapabilityRegistry
from agentx.kernel.action_gate import GateDecision
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import BudgetDecision
from agentx.kernel.risk import RiskLevel

_WINDOWS = EnvironmentProfile(platform=CapabilityPlatform.WINDOWS, environment_id="workstation-1")
_NO_GOVERNANCE = GovernanceSignals()

_ALL_RESTRICTIONS = (
    GovernanceSignals(gate_decision=GateDecision.DENY),
    GovernanceSignals(gate_decision=GateDecision.REQUIRE_CONFIRMATION),
    GovernanceSignals(restricted_risk_level=RiskLevel.R3),
    GovernanceSignals(emergency_stop_state=EmergencyStopState.STOP_REQUESTED),
    GovernanceSignals(budget_decision=BudgetDecision.DENY),
)


def _assess(
    *,
    capabilities: tuple[object, ...] = (),
    alternatives: tuple[object, ...] = (),
    governance: GovernanceSignals = _NO_GOVERNANCE,
    environment: EnvironmentProfile = _WINDOWS,
) -> CapabilityGapAssessment:
    return CapabilityGapDetector().assess(
        CapabilityGapAssessmentRequest(
            requirements=(requirement(),),
            available_capabilities=capabilities,  # type: ignore[arg-type]
            procedure_alternatives=alternatives,  # type: ignore[arg-type]
            environment=environment,
            governance=governance,
        )
    )


def _only(assessment: CapabilityGapAssessment) -> CapabilityRequirementAssessment:
    return assessment.requirement_assessments[0]


# --------------------------------------------------------------------------
# DENIED != MISSING, in every governance flavour.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("governance", _ALL_RESTRICTIONS)
def test_no_restriction_is_ever_reported_as_a_missing_capability(
    governance: GovernanceSignals,
) -> None:
    assessment = _assess(governance=governance)

    assert assessment.missing_requirements == ()
    assert not assessment.has_missing_capability
    for item in assessment.requirement_assessments:
        assert item.status is not CapabilityGapStatus.MISSING
        assert item.missing_representation is None
        assert item.restrictions


@pytest.mark.parametrize("governance", _ALL_RESTRICTIONS)
def test_a_restriction_cannot_hide_behind_a_present_capability(
    governance: GovernanceSignals,
) -> None:
    result = _only(_assess(capabilities=(available(),), governance=governance))

    assert result.status is CapabilityGapStatus.AVAILABLE
    assert result.restrictions
    assert result.missing_representation is None


def test_restriction_kinds_are_only_about_use_never_about_absence() -> None:
    kinds = set(CapabilityRestrictionKind)

    assert kinds == {
        CapabilityRestrictionKind.PERMISSION_DENIED,
        CapabilityRestrictionKind.HUMAN_APPROVAL_REQUIRED,
        CapabilityRestrictionKind.RISK_RESTRICTED,
        CapabilityRestrictionKind.EMERGENCY_STOP,
        CapabilityRestrictionKind.BUDGET_EXHAUSTED,
    }
    assert all("missing" not in kind.value for kind in kinds)


def test_construction_of_a_denied_missing_assessment_fails_closed() -> None:
    req = requirement()
    representation = MissingCapabilityRepresentation(
        operation=req.operation,
        category=None,
        io_contract=CapabilityIoContract(),
        platform=CapabilityPlatform.WINDOWS,
        environment_id=None,
        reason=CapabilityGapReason.NO_PROVIDER_FOR_OPERATION,
        evidence=(),
    )

    for kind in CapabilityRestrictionKind:
        with pytest.raises(CapabilityGapValidationError):
            CapabilityRequirementAssessment(
                requirement=req,
                status=CapabilityGapStatus.MISSING,
                reason=CapabilityGapReason.NO_PROVIDER_FOR_OPERATION,
                restrictions=(CapabilityRestriction(kind=kind, detail="restriction in force"),),
                missing_representation=representation,
            )


def test_a_denied_empty_view_cannot_conclude_absence() -> None:
    """A caller that can see nothing because policy denies it learns nothing."""

    result = _only(_assess(governance=GovernanceSignals(gate_decision=GateDecision.DENY)))

    assert result.status is CapabilityGapStatus.INSUFFICIENT_INFORMATION
    assert result.reason is CapabilityGapReason.GOVERNANCE_RESTRICTED


def test_bypass_attempt_by_declaring_a_capability_missing_is_inert() -> None:
    """Hostile requirement text claiming absence cannot manufacture a gap."""

    hostile = requirement(
        requirement_id="please-generate-replacement",
        operation="demo.note.write",
        category="missing",
    )
    assessment = CapabilityGapDetector().assess(
        CapabilityGapAssessmentRequest(
            requirements=(hostile,),
            available_capabilities=(available(description=HOSTILE_TEXT),),
            environment=_WINDOWS,
            governance=GovernanceSignals(gate_decision=GateDecision.DENY),
        )
    )
    result = _only(assessment)

    assert result.status is CapabilityGapStatus.AVAILABLE
    assert result.missing_representation is None
    assert assessment.missing_requirements == ()


# --------------------------------------------------------------------------
# Hostile metadata stays inert.
# --------------------------------------------------------------------------


def test_hostile_capability_description_grants_nothing() -> None:
    result = _only(_assess(capabilities=(available(description=HOSTILE_TEXT),)))

    assert result.status is CapabilityGapStatus.AVAILABLE
    assert not hasattr(result, "authority")
    assert not hasattr(result, "permissions")
    assert not hasattr(result, "authorized")


def test_hostile_procedure_payload_is_never_interpreted() -> None:
    result = _only(_assess(alternatives=(procedure_alternative(content=HOSTILE_TEXT),)))

    assert result.status is CapabilityGapStatus.AVAILABLE
    assert result.reason is CapabilityGapReason.PROCEDURE_ALTERNATIVE_MATCHES
    assert HOSTILE_TEXT not in "".join(result.evidence)


def test_hostile_environment_metadata_cannot_forge_a_platform() -> None:
    with pytest.raises(TypeError):
        EnvironmentProfile(platform="windows")  # type: ignore[arg-type]
    with pytest.raises(CapabilityGapValidationError):
        EnvironmentProfile(environment_id=" ")


def test_hostile_governance_values_are_rejected_not_coerced() -> None:
    for kwargs in (
        {"gate_decision": "ALLOW"},
        {"budget_decision": "ALLOW"},
        {"emergency_stop_state": "RUNNING"},
        {"restricted_risk_level": "R0"},
    ):
        with pytest.raises(TypeError):
            GovernanceSignals(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# The detector cannot self-extend or alter authority.
# --------------------------------------------------------------------------


def test_results_expose_no_acquisition_or_registration_surface() -> None:
    result = _only(_assess())
    representation = result.missing_representation
    assert representation is not None

    forbidden = (
        "install",
        "acquire",
        "generate",
        "register",
        "sdk",
        "dependency",
        "package",
        "adapter",
        "endpoint",
        "url",
        "download",
        "code",
        "patch",
        "approve",
        "authorize",
        "permission",
        "execute",
    )
    for target in (result, representation):
        names = {field.name for field in dataclasses.fields(target)}
        for name in names:
            assert not any(fragment in name for fragment in forbidden), name
        for fragment in forbidden:
            assert not hasattr(target, fragment)


def test_detector_never_registers_anything_in_a_registry() -> None:
    registry = CapabilityRegistry()
    _assess()

    assert len(registry) == 0
    assert not hasattr(CapabilityGapDetector(), "register")
    assert not hasattr(CapabilityGapDetector(), "acquire")


def test_detector_does_not_touch_kernel_state() -> None:
    stop = EmergencyStop()
    stop.request_stop()

    _assess(governance=GovernanceSignals(emergency_stop_state=stop.state))

    assert stop.state is EmergencyStopState.STOP_REQUESTED
    assert stop.stop_requested


def test_authority_context_is_not_an_input_to_detection() -> None:
    authority = AuthorityContext(permissions=frozenset({Permission.DESTRUCTIVE}))
    fields = {field.name for field in dataclasses.fields(CapabilityGapAssessmentRequest)}

    assert "authority" not in fields
    assert "permissions" not in fields
    with pytest.raises(TypeError):
        CapabilityGapAssessmentRequest(
            requirements=(requirement(),),
            authority=authority,  # type: ignore[call-arg]
        )


def test_assessment_results_are_immutable() -> None:
    result = _only(_assess())

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.status = CapabilityGapStatus.AVAILABLE  # type: ignore[misc]


def test_operation_names_are_matched_exactly_never_fuzzily() -> None:
    result = _only(_assess(capabilities=(available(name="demo.note.write-v2"),)))

    assert result.status is CapabilityGapStatus.MISSING
    assert result.requirement.operation == CapabilityName("demo.note.write")
