"""Tests for deterministic missing-capability detection (A9.01)."""

from __future__ import annotations

import pytest

from agentx.capabilities.abi import CapabilityName, CapabilityPlatform, CapabilityVersion
from agentx.capabilities.capability_gap import (
    CAPABILITY_GAP_SCHEMA_VERSION,
    CapabilityAvailability,
    CapabilityGapAssessment,
    CapabilityGapAssessmentRequest,
    CapabilityGapDetector,
    CapabilityGapReason,
    CapabilityGapStatus,
    CapabilityGapValidationError,
    CapabilityIoContract,
    CapabilityRequirement,
    CapabilityRequirementAssessment,
    CapabilityRestriction,
    CapabilityRestrictionKind,
    EnvironmentProfile,
    GovernanceSignals,
    MissingCapabilityRepresentation,
)
from agentx.core.procedures import ProcedureStatus
from agentx.kernel.action_gate import GateDecision
from agentx.kernel.emergency_stop import EmergencyStopState
from agentx.kernel.resource_budget import BudgetDecision
from agentx.kernel.risk import RiskLevel
from tests.support.capability_gap_fixtures import (
    available,
    procedure_alternative,
    requirement,
)

_WINDOWS = EnvironmentProfile(platform=CapabilityPlatform.WINDOWS, environment_id="workstation-1")
_NO_GOVERNANCE = GovernanceSignals()


def _assess(
    *,
    requirements: tuple[CapabilityRequirement, ...] | None = None,
    capabilities: tuple[object, ...] = (),
    alternatives: tuple[object, ...] = (),
    environment: EnvironmentProfile = _WINDOWS,
    governance: GovernanceSignals = _NO_GOVERNANCE,
) -> CapabilityGapAssessment:
    request = CapabilityGapAssessmentRequest(
        requirements=requirements if requirements is not None else (requirement(),),
        available_capabilities=capabilities,  # type: ignore[arg-type]
        procedure_alternatives=alternatives,  # type: ignore[arg-type]
        environment=environment,
        governance=governance,
    )
    return CapabilityGapDetector().assess(request)


def _only(assessment: CapabilityGapAssessment) -> CapabilityRequirementAssessment:
    assert len(assessment.requirement_assessments) == 1
    return assessment.requirement_assessments[0]


# --------------------------------------------------------------------------
# Core outcome vocabulary.
# --------------------------------------------------------------------------


def test_available_when_a_supplied_capability_matches() -> None:
    result = _only(_assess(capabilities=(available(),)))

    assert result.status is CapabilityGapStatus.AVAILABLE
    assert result.reason is CapabilityGapReason.REGISTERED_CAPABILITY_MATCHES
    assert result.matched_identity is not None
    assert result.matched_identity.name == CapabilityName("demo.note.write")
    assert result.missing_representation is None


def test_available_prefers_the_highest_matching_version() -> None:
    result = _only(
        _assess(
            capabilities=(
                available(version=(1, 0, 0)),
                available(version=(2, 3, 1)),
            )
        )
    )

    assert result.status is CapabilityGapStatus.AVAILABLE
    assert result.matched_identity is not None
    assert result.matched_identity.version == CapabilityVersion(2, 3, 1)


def test_actually_missing_capability_is_reported_with_representation() -> None:
    result = _only(_assess(capabilities=(available(name="other.capability"),)))

    assert result.status is CapabilityGapStatus.MISSING
    assert result.reason is CapabilityGapReason.NO_PROVIDER_FOR_OPERATION
    assert result.is_missing
    representation = result.missing_representation
    assert representation is not None
    assert representation.operation == CapabilityName("demo.note.write")
    assert representation.category == "note"
    assert representation.platform is CapabilityPlatform.WINDOWS
    assert representation.environment_id == "workstation-1"
    assert representation.evidence


def test_missing_representation_records_known_io_characteristics() -> None:
    result = _only(
        _assess(requirements=(requirement(input_kind="file_path", output_kind="note_id"),))
    )

    representation = result.missing_representation
    assert representation is not None
    assert representation.io_contract == CapabilityIoContract(
        input_kind="file_path", output_kind="note_id"
    )


def test_missing_representation_scope_is_narrow_and_explicit() -> None:
    representation = _only(_assess()).missing_representation
    assert representation is not None

    assert representation.scope == (
        "operation=demo.note.write;platform=windows;environment=workstation-1"
    )


def test_wrong_platform_is_incompatible_not_missing() -> None:
    result = _only(_assess(capabilities=(available(platform=CapabilityPlatform.LINUX),)))

    assert result.status is CapabilityGapStatus.INCOMPATIBLE
    assert result.reason is CapabilityGapReason.PLATFORM_INCOMPATIBLE
    assert result.missing_representation is None


def test_platform_any_matches_every_environment() -> None:
    result = _only(_assess(capabilities=(available(platform=CapabilityPlatform.ANY),)))

    assert result.status is CapabilityGapStatus.AVAILABLE


def test_version_below_minimum_is_incompatible() -> None:
    result = _only(
        _assess(
            requirements=(requirement(minimum_version=(2, 0, 0)),),
            capabilities=(available(version=(1, 9, 9)),),
        )
    )

    assert result.status is CapabilityGapStatus.INCOMPATIBLE
    assert result.reason is CapabilityGapReason.VERSION_INCOMPATIBLE


def test_mismatched_io_contract_is_incompatible() -> None:
    result = _only(
        _assess(
            requirements=(requirement(output_kind="note_id"),),
            capabilities=(available(output_kind="process_list"),),
        )
    )

    assert result.status is CapabilityGapStatus.INCOMPATIBLE
    assert result.reason is CapabilityGapReason.IO_CONTRACT_INCOMPATIBLE


def test_temporarily_unavailable_capability_is_unavailable_not_missing() -> None:
    result = _only(
        _assess(
            capabilities=(available(availability=CapabilityAvailability.TEMPORARILY_UNAVAILABLE),)
        )
    )

    assert result.status is CapabilityGapStatus.UNAVAILABLE
    assert result.reason is CapabilityGapReason.TEMPORARILY_UNAVAILABLE
    assert result.missing_representation is None


def test_an_operational_sibling_outranks_a_temporarily_unavailable_one() -> None:
    result = _only(
        _assess(
            capabilities=(
                available(
                    version=(3, 0, 0),
                    availability=CapabilityAvailability.TEMPORARILY_UNAVAILABLE,
                ),
                available(version=(1, 0, 0)),
            )
        )
    )

    assert result.status is CapabilityGapStatus.AVAILABLE
    assert result.matched_identity is not None
    assert result.matched_identity.version == CapabilityVersion(1, 0, 0)


def test_unknown_platform_is_insufficient_information() -> None:
    result = _only(_assess(environment=EnvironmentProfile()))

    assert result.status is CapabilityGapStatus.INSUFFICIENT_INFORMATION
    assert result.reason is CapabilityGapReason.UNKNOWN_ENVIRONMENT
    assert result.missing_representation is None


def test_undeclared_io_contract_is_insufficient_information() -> None:
    result = _only(
        _assess(
            requirements=(requirement(input_kind="file_path"),),
            capabilities=(available(),),
        )
    )

    assert result.status is CapabilityGapStatus.INSUFFICIENT_INFORMATION
    assert result.reason is CapabilityGapReason.UNDECLARED_IO_CONTRACT


# --------------------------------------------------------------------------
# Procedure alternatives.
# --------------------------------------------------------------------------


def test_active_procedure_alternative_satisfies_the_requirement() -> None:
    result = _only(_assess(alternatives=(procedure_alternative(),)))

    assert result.status is CapabilityGapStatus.AVAILABLE
    assert result.reason is CapabilityGapReason.PROCEDURE_ALTERNATIVE_MATCHES
    assert result.matched_identity is None


def test_candidate_and_retired_procedures_satisfy_nothing() -> None:
    for status in (ProcedureStatus.CANDIDATE, ProcedureStatus.RETIRED):
        result = _only(_assess(alternatives=(procedure_alternative(status=status),)))
        assert result.status is CapabilityGapStatus.MISSING


def test_procedure_alternative_scoped_to_another_os_does_not_match() -> None:
    result = _only(_assess(alternatives=(procedure_alternative(operating_system="linux"),)))

    assert result.status is CapabilityGapStatus.MISSING


def test_unscoped_active_procedure_alternative_matches() -> None:
    result = _only(_assess(alternatives=(procedure_alternative(operating_system=None),)))

    assert result.status is CapabilityGapStatus.AVAILABLE


def test_procedure_alternative_for_another_operation_does_not_match() -> None:
    result = _only(_assess(alternatives=(procedure_alternative(operation="other.operation"),)))

    assert result.status is CapabilityGapStatus.MISSING


# --------------------------------------------------------------------------
# DENIED != MISSING.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("governance", "kind"),
    [
        (
            GovernanceSignals(gate_decision=GateDecision.DENY),
            CapabilityRestrictionKind.PERMISSION_DENIED,
        ),
        (
            GovernanceSignals(gate_decision=GateDecision.REQUIRE_CONFIRMATION),
            CapabilityRestrictionKind.HUMAN_APPROVAL_REQUIRED,
        ),
        (
            GovernanceSignals(restricted_risk_level=RiskLevel.R4),
            CapabilityRestrictionKind.RISK_RESTRICTED,
        ),
        (
            GovernanceSignals(emergency_stop_state=EmergencyStopState.STOP_REQUESTED),
            CapabilityRestrictionKind.EMERGENCY_STOP,
        ),
        (
            GovernanceSignals(budget_decision=BudgetDecision.DENY),
            CapabilityRestrictionKind.BUDGET_EXHAUSTED,
        ),
    ],
)
def test_restrictions_never_produce_a_missing_capability(
    governance: GovernanceSignals,
    kind: CapabilityRestrictionKind,
) -> None:
    result = _only(_assess(governance=governance))

    assert result.status is CapabilityGapStatus.INSUFFICIENT_INFORMATION
    assert result.reason is CapabilityGapReason.GOVERNANCE_RESTRICTED
    assert result.missing_representation is None
    assert kind in {restriction.kind for restriction in result.restrictions}


def test_restricted_but_present_capability_stays_available_with_restrictions() -> None:
    result = _only(
        _assess(
            capabilities=(available(),),
            governance=GovernanceSignals(gate_decision=GateDecision.DENY),
        )
    )

    assert result.status is CapabilityGapStatus.AVAILABLE
    assert result.is_restricted
    assert result.restrictions[0].kind is CapabilityRestrictionKind.PERMISSION_DENIED


def test_allowed_gate_and_budget_produce_no_restrictions() -> None:
    result = _only(
        _assess(
            governance=GovernanceSignals(
                gate_decision=GateDecision.ALLOW,
                budget_decision=BudgetDecision.ALLOW,
                emergency_stop_state=EmergencyStopState.RUNNING,
            )
        )
    )

    assert result.restrictions == ()
    assert result.status is CapabilityGapStatus.MISSING


def test_a_missing_assessment_with_restrictions_cannot_be_constructed() -> None:
    req = requirement()
    representation = MissingCapabilityRepresentation(
        operation=req.operation,
        category=req.category,
        io_contract=req.io_contract,
        platform=CapabilityPlatform.WINDOWS,
        environment_id="workstation-1",
        reason=CapabilityGapReason.NO_PROVIDER_FOR_OPERATION,
        evidence=("none",),
    )

    with pytest.raises(CapabilityGapValidationError, match="never be reported MISSING"):
        CapabilityRequirementAssessment(
            requirement=req,
            status=CapabilityGapStatus.MISSING,
            reason=CapabilityGapReason.NO_PROVIDER_FOR_OPERATION,
            restrictions=(
                CapabilityRestriction(
                    kind=CapabilityRestrictionKind.PERMISSION_DENIED,
                    detail="ActionGate returned DENY.",
                ),
            ),
            missing_representation=representation,
        )


def test_missing_status_requires_a_representation() -> None:
    with pytest.raises(CapabilityGapValidationError, match="must carry"):
        CapabilityRequirementAssessment(
            requirement=requirement(),
            status=CapabilityGapStatus.MISSING,
            reason=CapabilityGapReason.NO_PROVIDER_FOR_OPERATION,
        )


def test_only_missing_status_may_carry_a_representation() -> None:
    req = requirement()
    representation = MissingCapabilityRepresentation(
        operation=req.operation,
        category=req.category,
        io_contract=req.io_contract,
        platform=CapabilityPlatform.WINDOWS,
        environment_id=None,
        reason=CapabilityGapReason.NO_PROVIDER_FOR_OPERATION,
        evidence=(),
    )
    with pytest.raises(CapabilityGapValidationError, match="only a MISSING assessment"):
        CapabilityRequirementAssessment(
            requirement=req,
            status=CapabilityGapStatus.AVAILABLE,
            reason=CapabilityGapReason.REGISTERED_CAPABILITY_MATCHES,
            missing_representation=representation,
        )


def test_representation_must_agree_with_its_assessment() -> None:
    req = requirement()
    representation = MissingCapabilityRepresentation(
        operation=CapabilityName("some.other.operation"),
        category=None,
        io_contract=CapabilityIoContract(),
        platform=CapabilityPlatform.WINDOWS,
        environment_id=None,
        reason=CapabilityGapReason.NO_PROVIDER_FOR_OPERATION,
        evidence=(),
    )
    with pytest.raises(CapabilityGapValidationError, match="must match the requirement operation"):
        CapabilityRequirementAssessment(
            requirement=req,
            status=CapabilityGapStatus.MISSING,
            reason=CapabilityGapReason.NO_PROVIDER_FOR_OPERATION,
            missing_representation=representation,
        )


# --------------------------------------------------------------------------
# Malformed requirements.
# --------------------------------------------------------------------------


def test_malformed_requirement_id_is_rejected() -> None:
    for bad in ("", "  padded  ", "with\nnewline", "x" * 300):
        with pytest.raises(CapabilityGapValidationError):
            requirement(requirement_id=bad)


def test_requirement_operation_must_be_a_canonical_capability_name() -> None:
    with pytest.raises(TypeError):
        CapabilityRequirement(requirement_id="req-1", operation="demo.note.write")  # type: ignore[arg-type]


def test_requirements_must_be_unique_and_non_empty() -> None:
    with pytest.raises(CapabilityGapValidationError, match="must not be empty"):
        CapabilityGapAssessmentRequest(requirements=())
    with pytest.raises(CapabilityGapValidationError, match="unique requirement_id"):
        CapabilityGapAssessmentRequest(
            requirements=(requirement(), requirement(operation="other.thing"))
        )


def test_duplicate_capability_declarations_are_rejected() -> None:
    with pytest.raises(CapabilityGapValidationError, match="must be unique"):
        CapabilityGapAssessmentRequest(
            requirements=(requirement(),),
            available_capabilities=(available(), available()),
        )


def test_detector_rejects_non_canonical_requests() -> None:
    with pytest.raises(TypeError):
        CapabilityGapDetector().assess({"requirements": []})  # type: ignore[arg-type]


def test_unsupported_schema_version_is_rejected() -> None:
    result = _only(_assess())
    with pytest.raises(CapabilityGapValidationError, match="unsupported capability gap schema"):
        CapabilityGapAssessment(schema_version=2, requirement_assessments=(result,))


# --------------------------------------------------------------------------
# Determinism and aggregation.
# --------------------------------------------------------------------------


def test_assessment_is_deterministic_and_order_independent() -> None:
    first = requirement(requirement_id="a-req", operation="demo.note.write")
    second = requirement(requirement_id="b-req", operation="demo.note.read")
    caps = (available(name="demo.note.read"), available(name="demo.note.write"))

    forward = CapabilityGapDetector().assess(
        CapabilityGapAssessmentRequest(
            requirements=(first, second),
            available_capabilities=caps,
            environment=_WINDOWS,
        )
    )
    reverse = CapabilityGapDetector().assess(
        CapabilityGapAssessmentRequest(
            requirements=(second, first),
            available_capabilities=tuple(reversed(caps)),
            environment=_WINDOWS,
        )
    )

    assert forward == reverse
    assert [item.requirement.requirement_id for item in forward.requirement_assessments] == [
        "a-req",
        "b-req",
    ]


def test_assessment_aggregates_missing_and_restricted_requirements() -> None:
    assessment = _assess(
        requirements=(
            requirement(requirement_id="present"),
            requirement(requirement_id="absent", operation="demo.note.read"),
        ),
        capabilities=(available(),),
    )

    assert assessment.schema_version == CAPABILITY_GAP_SCHEMA_VERSION
    assert assessment.has_missing_capability
    assert [item.requirement.requirement_id for item in assessment.missing_requirements] == [
        "absent"
    ]
    assert assessment.restricted_requirements == ()


def test_restricted_assessment_reports_no_missing_capability() -> None:
    assessment = _assess(governance=GovernanceSignals(budget_decision=BudgetDecision.DENY))

    assert not assessment.has_missing_capability
    assert assessment.missing_requirements == ()
    assert len(assessment.restricted_requirements) == 1
