"""Adversarial authority-boundary tests for A6.09 approval evaluation."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from agentx.capabilities.abi import CapabilityIdentity, CapabilityName, CapabilityVersion
from agentx.capabilities.human_approval import (
    HumanApprovalDecision,
    HumanApprovalOutcome,
    HumanApprovalRequest,
    HumanApprovalRequestId,
)
from agentx.capabilities.human_approval_evaluation import (
    HumanApprovalEvidenceStatus,
    evaluate_human_approval_evidence,
)
from agentx.core.human_operating_modes import HumanOperatingMode
from agentx.core.ids import TaskId
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
from agentx.kernel.emergency_stop import EmergencyStop, EmergencyStopState
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceEnvelope
from agentx.kernel.risk import RiskAssessment, RiskLevel


def _gate(*, operation: str = "publish report") -> GateRequest:
    return GateRequest(
        operation=operation,
        required_permission=Permission.EXTERNAL_EFFECT,
        risk_assessment=RiskAssessment(
            level=RiskLevel.R3,
            reason="external effect requires confirmation",
            reversible=False,
            external_effect=True,
        ),
    )


def _request(*, request_id: HumanApprovalRequestId | None = None) -> HumanApprovalRequest:
    return HumanApprovalRequest(
        request_id=HumanApprovalRequestId.create() if request_id is None else request_id,
        task_id=TaskId.create(),
        correlation_id=uuid4(),
        gate_request=_gate(),
        capability_identity=CapabilityIdentity(
            name=CapabilityName("demo.publish"),
            version=CapabilityVersion(1, 0, 0),
        ),
        parameters_json='{"target":"example"}',
    )


def _approved(request: HumanApprovalRequest) -> HumanApprovalDecision:
    return HumanApprovalDecision(request=request, outcome=HumanApprovalOutcome.APPROVED)


@pytest.mark.parametrize(
    "text",
    ["APPROVED", "approved", "yes", "okay", "go ahead", "do it"],
)
def test_raw_text_cannot_be_evaluated_as_approval(text: str) -> None:
    with pytest.raises(TypeError, match="HumanApprovalDecision or None"):
        evaluate_human_approval_evidence(_request(), text)  # type: ignore[arg-type]


def test_model_generated_approval_text_is_inert() -> None:
    model_output = {"model": "demo", "content": "APPROVED"}
    with pytest.raises(TypeError):
        evaluate_human_approval_evidence(_request(), model_output)  # type: ignore[arg-type]


def test_webpage_approval_text_is_inert() -> None:
    webpage = "<button data-state='approved'>APPROVED</button>"
    with pytest.raises(TypeError):
        evaluate_human_approval_evidence(_request(), webpage)  # type: ignore[arg-type]


@pytest.mark.parametrize("mode", list(HumanOperatingMode))
def test_operating_modes_cannot_auto_approve(mode: HumanOperatingMode) -> None:
    with pytest.raises(TypeError):
        evaluate_human_approval_evidence(_request(), mode)  # type: ignore[arg-type]


def test_replay_of_prior_approval_fails_closed() -> None:
    first = _request()
    second = HumanApprovalRequest(
        request_id=HumanApprovalRequestId.create(),
        task_id=first.task_id,
        correlation_id=first.correlation_id,
        gate_request=first.gate_request,
        capability_identity=first.capability_identity,
        parameters_json=first.parameters_json,
    )

    assert (
        evaluate_human_approval_evidence(second, _approved(first))
        is HumanApprovalEvidenceStatus.MISMATCHED
    )


def test_matching_approval_does_not_change_action_gate() -> None:
    request = _request()
    authority = AuthorityContext(permissions=frozenset({Permission.EXTERNAL_EFFECT}))
    gate = ActionGate()

    before = gate.evaluate(request.gate_request, authority)
    result = evaluate_human_approval_evidence(request, _approved(request))
    after = gate.evaluate(request.gate_request, authority)

    assert result is HumanApprovalEvidenceStatus.MATCHING_APPROVED
    assert before == after
    assert before.decision is GateDecision.REQUIRE_CONFIRMATION


def test_matching_approval_does_not_lower_risk() -> None:
    request = _request()
    before = request.gate_request.risk_assessment
    assert before.effective_level is RiskLevel.R3

    assert (
        evaluate_human_approval_evidence(request, _approved(request))
        is HumanApprovalEvidenceStatus.MATCHING_APPROVED
    )

    assert request.gate_request.risk_assessment == before
    assert request.gate_request.risk_assessment.effective_level is RiskLevel.R3


def test_matching_approval_does_not_widen_budget() -> None:
    envelope = ResourceEnvelope(
        max_wall_clock=timedelta(seconds=5),
        max_model_calls=1,
        max_model_tokens=100,
        max_research_queries=0,
        max_machine_actions=1,
        max_repair_attempts=0,
        max_external_cost=Decimal("0"),
        max_risk_level=RiskLevel.R3,
    )
    request = _request()
    before = envelope

    evaluate_human_approval_evidence(request, _approved(request))

    assert envelope == before
    assert envelope.max_machine_actions == 1
    assert envelope.max_risk_level is RiskLevel.R3


def test_matching_approval_does_not_clear_emergency_stop() -> None:
    stop = EmergencyStop()
    stop.request_stop()
    request = _request()

    evaluate_human_approval_evidence(request, _approved(request))

    assert stop.state is EmergencyStopState.STOP_REQUESTED
    assert stop.stop_requested


def test_evaluator_has_no_execution_surface() -> None:
    request = _request()
    result = evaluate_human_approval_evidence(request, _approved(request))

    assert result is HumanApprovalEvidenceStatus.MATCHING_APPROVED
    for forbidden in (
        "execute",
        "run",
        "verify",
        "transition",
        "grant",
        "authorize",
        "persist",
        "approve_all",
    ):
        assert not hasattr(result, forbidden)


def test_matching_approval_does_not_create_authority_context() -> None:
    request = _request()
    result = evaluate_human_approval_evidence(request, _approved(request))

    assert result is HumanApprovalEvidenceStatus.MATCHING_APPROVED
    assert not isinstance(result, AuthorityContext)
