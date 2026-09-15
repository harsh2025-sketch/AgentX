"""AX-122 authority-boundary adversarial proof."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge_assurance import KnowledgeAssuranceMetadata, KnowledgeConfidenceState
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import Permission
from agentx.kernel.resource_budget import ResourceEnvelope
from agentx.kernel.risk import RiskAssessment, RiskLevel


def test_verified_evidence_metadata_cannot_grant_machine_authority() -> None:
    metadata = KnowledgeAssuranceMetadata(
        knowledge_id=KnowledgeId.create(),
        verification_count=100,
        failure_count=0,
        environment_valid=True,
        last_verification=datetime.now(UTC),
        confidence_state=KnowledgeConfidenceState.VERIFIED_EVIDENCE,
    )
    request = GateRequest(
        operation="ax122.assurance-is-data",
        required_permission=Permission.WRITE,
        risk_assessment=RiskAssessment(
            level=RiskLevel.R0,
            reason=f"confidence={metadata.confidence_state.value}",
            reversible=False,
            external_effect=True,
        ),
    )
    stop = EmergencyStop()
    stop.request_stop()
    envelope = ResourceEnvelope(
        max_wall_clock=timedelta(seconds=1),
        max_model_calls=1,
        max_model_tokens=1,
        max_research_queries=1,
        max_machine_actions=1,
        max_repair_attempts=1,
        max_external_cost=Decimal("1"),
        max_risk_level=RiskLevel.R2,
    )

    assert ActionGate().evaluate(request, None).decision is GateDecision.DENY
    assert request.risk_assessment.effective_level is RiskLevel.R3
    assert envelope.max_machine_actions == 1
    assert stop.stop_requested is True
