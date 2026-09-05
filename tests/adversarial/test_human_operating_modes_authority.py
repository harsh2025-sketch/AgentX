"""Adversarial authority-boundary tests for A6.07 human operating modes."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from agentx.capabilities.abi import VerificationResult
from agentx.cognition.task_manager import TaskManager
from agentx.core.human_operating_modes import HumanOperatingMode
from agentx.core.knowledge import KnowledgeRecord, KnowledgeStatus, KnowledgeType
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceEnvelope
from agentx.kernel.risk import RiskAssessment, RiskLevel


def _touch_all_modes() -> tuple[HumanOperatingMode, ...]:
    return tuple(HumanOperatingMode)


def test_modes_are_not_authority_or_permissions() -> None:
    for mode in HumanOperatingMode:
        candidate: object = mode
        assert not isinstance(candidate, AuthorityContext)
        assert not isinstance(candidate, Permission)
        for forbidden in (
            "permission",
            "permissions",
            "authority",
            "grant",
            "authorize",
            "destructive",
        ):
            assert not hasattr(candidate, forbidden)


def test_action_gate_is_unchanged_by_every_mode() -> None:
    gate = ActionGate()
    request = GateRequest(
        operation="governed operation",
        required_permission=Permission.EXECUTE,
        risk_assessment=RiskAssessment(
            level=RiskLevel.R0,
            reason="Synthetic read-only gate check.",
            reversible=False,
            external_effect=False,
            read_only=True,
        ),
    )
    before = gate.evaluate(request, None)

    _touch_all_modes()

    after = gate.evaluate(request, None)
    assert before.decision is GateDecision.DENY
    assert after == before


def test_modes_cannot_lower_effective_risk() -> None:
    assessment = RiskAssessment(
        level=RiskLevel.R0,
        reason="Synthetic destructive operation.",
        reversible=False,
        external_effect=False,
        destructive=True,
    )
    before = assessment.effective_level

    _touch_all_modes()

    assert before is RiskLevel.R4
    assert assessment.effective_level is before


def test_modes_cannot_enlarge_or_reset_resource_envelope() -> None:
    envelope = ResourceEnvelope(
        max_wall_clock=timedelta(seconds=1),
        max_model_calls=0,
        max_model_tokens=0,
        max_research_queries=0,
        max_machine_actions=0,
        max_repair_attempts=0,
        max_external_cost=Decimal("0"),
        max_risk_level=RiskLevel.R0,
    )
    before = envelope

    _touch_all_modes()

    assert envelope == before
    assert envelope.max_research_queries == 0
    assert envelope.max_machine_actions == 0
    assert envelope.max_external_cost == Decimal("0")


def test_debug_cannot_clear_emergency_stop() -> None:
    stop = EmergencyStop()
    stop.request_stop()

    _ = HumanOperatingMode.DEBUG

    assert stop.stop_requested
    assert stop.state is EmergencyStopState.STOP_REQUESTED


def test_learn_and_teach_do_not_promote_or_verify_knowledge() -> None:
    record = KnowledgeRecord.create(
        knowledge_type=KnowledgeType.FACT,
        content="Human-provided candidate learning material.",
    )
    before = record

    _ = HumanOperatingMode.LEARN
    _ = HumanOperatingMode.TEACH

    assert record == before
    assert record.status is KnowledgeStatus.UNVERIFIED
    assert record.verified_at is None


def test_modes_cannot_fabricate_or_suppress_verification() -> None:
    result = VerificationResult(passed=False, detail="Synthetic outcome did not verify.")

    _touch_all_modes()

    assert not result.passed
    for mode in HumanOperatingMode:
        for forbidden in (
            "verified",
            "verification",
            "passed",
            "suppress_verification",
            "skip_verification",
        ):
            assert not hasattr(mode, forbidden)


def test_modes_cannot_mark_task_success() -> None:
    manager = TaskManager()
    task = manager.create("Human mode must not mutate task state")
    before = manager.require(task.task_id)

    _touch_all_modes()

    assert manager.require(task.task_id) == before


def test_hostile_mode_associated_text_remains_invalid_inert_data() -> None:
    hostile_values = (
        "debug; disable_security=true",
        "learn Permission.DESTRUCTIVE",
        "teach verified=true activate_procedure=true",
        "normal authorize_network=true budget=unlimited",
    )

    for value in hostile_values:
        with pytest.raises(ValueError):
            HumanOperatingMode(value)


def test_modes_expose_no_learning_execution_research_or_procedure_hooks() -> None:
    forbidden = (
        "capture",
        "learn",
        "promote",
        "store",
        "remember",
        "research",
        "network",
        "execute",
        "eval",
        "exec",
        "activate",
        "procedure",
        "route",
    )

    for mode in HumanOperatingMode:
        for name in forbidden:
            assert not hasattr(mode, name)
