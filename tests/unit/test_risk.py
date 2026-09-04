"""Unit tests for the canonical AgentX R0-R4 risk model."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import cast

import pytest

import agentx.kernel.risk as risk_module
from agentx.kernel.risk import RiskAssessment, RiskLevel, assess_risk


def test_exact_risk_vocabulary_and_labels() -> None:
    assert tuple(level.value for level in RiskLevel) == ("R0", "R1", "R2", "R3", "R4")
    assert tuple(level.label for level in RiskLevel) == (
        "READ",
        "REVERSIBLE",
        "MODIFY",
        "EXTERNAL_EFFECT",
        "CRITICAL",
    )


def test_explicit_severity_ordering() -> None:
    assert tuple(level.severity for level in RiskLevel) == (0, 1, 2, 3, 4)
    assert RiskLevel.R4 > RiskLevel.R3 > RiskLevel.R2 > RiskLevel.R1 > RiskLevel.R0
    assert sorted(RiskLevel, reverse=True) == [
        RiskLevel.R4,
        RiskLevel.R3,
        RiskLevel.R2,
        RiskLevel.R1,
        RiskLevel.R0,
    ]


def test_classification_is_deterministic_and_reason_is_inspectable() -> None:
    facts = {
        "read_only": False,
        "modifies_state": True,
        "reversible": False,
        "external_effect": False,
    }

    first = assess_risk(**facts)
    second = assess_risk(**facts)

    assert first == second
    assert first.level is RiskLevel.R2
    assert "R2 MODIFY" in first.reason


def test_read_only_classifies_as_r0() -> None:
    assessment = assess_risk(
        read_only=True,
        modifies_state=False,
        reversible=False,
        external_effect=False,
    )

    assert assessment.level is RiskLevel.R0
    assert assessment.external_effect is False
    assert "read-only" in assessment.reason


def test_reversible_local_modification_classifies_as_r1() -> None:
    assessment = assess_risk(
        read_only=False,
        modifies_state=True,
        reversible=True,
        external_effect=False,
    )

    assert assessment.level is RiskLevel.R1
    assert assessment.reversible is True
    assert assessment.external_effect is False


def test_persistent_modification_classifies_as_r2() -> None:
    assessment = assess_risk(
        read_only=False,
        modifies_state=True,
        reversible=False,
        external_effect=False,
    )

    assert assessment.level is RiskLevel.R2
    assert assessment.reversible is False


def test_external_effect_has_r3_floor() -> None:
    assessment = assess_risk(
        read_only=True,
        modifies_state=False,
        reversible=False,
        external_effect=True,
    )

    assert assessment.level is RiskLevel.R3
    assert assessment.external_effect is True
    assert "higher-risk characteristics dominate" in assessment.reason


@pytest.mark.parametrize(
    ("critical", "destructive"),
    [(True, False), (False, True), (True, True)],
)
def test_critical_or_destructive_always_classifies_as_r4(
    critical: bool,
    destructive: bool,
) -> None:
    assessment = assess_risk(
        read_only=True,
        modifies_state=True,
        reversible=True,
        external_effect=True,
        critical=critical,
        destructive=destructive,
    )

    assert assessment.level is RiskLevel.R4
    assert "strongest risk classification" in assessment.reason


def test_conflicting_read_only_and_state_change_conservatively_escalates() -> None:
    assessment = assess_risk(
        read_only=True,
        modifies_state=True,
        reversible=True,
        external_effect=False,
    )

    assert assessment.level is RiskLevel.R1
    assert "conflicts with higher-risk characteristics" in assessment.reason


def test_reversibility_signal_cannot_silently_downgrade_to_read_only() -> None:
    assessment = assess_risk(
        read_only=True,
        modifies_state=False,
        reversible=True,
        external_effect=False,
    )

    assert assessment.level is RiskLevel.R1
    assert "state-change signal" in assessment.reason
    assert "higher-risk characteristics dominate" in assessment.reason


def test_r4_dominates_all_lower_characteristics() -> None:
    assessment = assess_risk(
        read_only=True,
        modifies_state=True,
        reversible=True,
        external_effect=True,
        critical=True,
        destructive=True,
    )

    assert assessment.level is RiskLevel.R4
    assert assessment.level > RiskLevel.R3


def test_risk_assessment_is_immutable() -> None:
    assessment = RiskAssessment(
        level=RiskLevel.R1,
        reason="Explicit reversible local change.",
        reversible=True,
        external_effect=False,
    )

    with pytest.raises(FrozenInstanceError):
        assessment.__setattr__("level", RiskLevel.R4)


def test_invalid_inputs_fail_clearly() -> None:
    with pytest.raises(TypeError, match="read_only must be bool"):
        assess_risk(
            read_only=cast(bool, 1),
            modifies_state=False,
            reversible=False,
            external_effect=False,
        )

    with pytest.raises(ValueError, match="at least one action characteristic"):
        assess_risk(
            read_only=False,
            modifies_state=False,
            reversible=False,
            external_effect=False,
        )

    with pytest.raises(TypeError, match="level must be a RiskLevel"):
        RiskAssessment(
            level=cast(RiskLevel, "R0"),
            reason="invalid level",
            reversible=False,
            external_effect=False,
        )

    with pytest.raises(ValueError, match="reason must not be empty"):
        RiskAssessment(
            level=RiskLevel.R0,
            reason="",
            reversible=False,
            external_effect=False,
        )


def test_risk_contract_exposes_no_permission_or_authority_operations() -> None:
    assert set(risk_module.__all__) == {"RiskAssessment", "RiskLevel", "assess_risk"}

    forbidden = {
        "is_allowed",
        "authorize",
        "grant",
        "execute",
        "bypass",
        "approve",
    }
    assert forbidden.isdisjoint(vars(RiskLevel))
    assert forbidden.isdisjoint(vars(RiskAssessment))
