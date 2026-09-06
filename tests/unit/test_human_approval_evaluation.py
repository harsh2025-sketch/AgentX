"""Unit coverage for A6.09 human approval evidence evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from agentx.capabilities.abi import (
    CapabilityIdentity,
    CapabilityName,
    CapabilityParams,
    CapabilityRequest,
    CapabilityVersion,
)
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
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.ids import TaskId
from agentx.core.tasks import JsonValue
from agentx.kernel.action_gate import GateRequest
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskAssessment, RiskLevel


@dataclass(frozen=True, slots=True)
class DemoParams(CapabilityParams):
    target: str
    count: int

    def to_dict(self) -> dict[str, JsonValue]:
        return {"target": self.target, "count": self.count}


def _context(task_id: TaskId, *, correlation_id: UUID | None = None) -> ExecutionContext:
    source = CancellationSource()
    return ExecutionContext(
        correlation_id=uuid4() if correlation_id is None else correlation_id,
        cancellation_token=source.token,
        task_id=task_id,
    )


def _capability(
    *,
    name: str = "demo.write",
    version: tuple[int, int, int] = (1, 0, 0),
    target: str = "alpha",
    count: int = 1,
) -> CapabilityRequest[DemoParams]:
    major, minor, patch = version
    return CapabilityRequest(
        identity=CapabilityIdentity(
            name=CapabilityName(name),
            version=CapabilityVersion(major, minor, patch),
        ),
        params=DemoParams(target=target, count=count),
    )


def _gate(
    *,
    operation: str = "write demo target",
    permission: Permission = Permission.EXTERNAL_EFFECT,
    level: RiskLevel = RiskLevel.R3,
    reason: str = "explicit confirmation required",
) -> GateRequest:
    return GateRequest(
        operation=operation,
        required_permission=permission,
        risk_assessment=RiskAssessment(
            level=level,
            reason=reason,
            reversible=False,
            external_effect=True,
            destructive=False,
        ),
    )


def _request(
    *,
    task_id: TaskId | None = None,
    correlation_id: UUID | None = None,
    request_id: HumanApprovalRequestId | None = None,
    capability: CapabilityRequest[DemoParams] | None = None,
    gate: GateRequest | None = None,
) -> HumanApprovalRequest:
    actual_task = TaskId.create() if task_id is None else task_id
    return HumanApprovalRequest.create(
        task_id=actual_task,
        context=_context(actual_task, correlation_id=correlation_id),
        gate_request=_gate() if gate is None else gate,
        capability_request=_capability() if capability is None else capability,
        request_id=request_id,
    )


def _decision(
    request: HumanApprovalRequest,
    outcome: HumanApprovalOutcome,
) -> HumanApprovalDecision:
    return HumanApprovalDecision(request=request, outcome=outcome)


def test_result_vocabulary_is_exact_and_closed() -> None:
    assert tuple(HumanApprovalEvidenceStatus) == (
        HumanApprovalEvidenceStatus.MATCHING_APPROVED,
        HumanApprovalEvidenceStatus.MATCHING_DENIED,
        HumanApprovalEvidenceStatus.MISSING,
        HumanApprovalEvidenceStatus.MISMATCHED,
    )
    assert {member.value for member in HumanApprovalEvidenceStatus} == {
        "MATCHING_APPROVED",
        "MATCHING_DENIED",
        "MISSING",
        "MISMATCHED",
    }


def test_matching_approved() -> None:
    request = _request()
    assert (
        evaluate_human_approval_evidence(request, _decision(request, HumanApprovalOutcome.APPROVED))
        is HumanApprovalEvidenceStatus.MATCHING_APPROVED
    )


def test_matching_denied() -> None:
    request = _request()
    assert (
        evaluate_human_approval_evidence(request, _decision(request, HumanApprovalOutcome.DENIED))
        is HumanApprovalEvidenceStatus.MATCHING_DENIED
    )


def test_missing_evidence() -> None:
    assert evaluate_human_approval_evidence(_request(), None) is HumanApprovalEvidenceStatus.MISSING


def test_mismatched_evidence() -> None:
    first = _request()
    second = _request()
    assert (
        evaluate_human_approval_evidence(second, _decision(first, HumanApprovalOutcome.APPROVED))
        is HumanApprovalEvidenceStatus.MISMATCHED
    )


def test_new_otherwise_identical_request_is_replay_mismatch() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    capability = _capability()
    gate = _gate()
    first = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        capability=capability,
        gate=gate,
    )
    second = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        capability=capability,
        gate=gate,
    )

    assert first.request_id != second.request_id
    assert (
        evaluate_human_approval_evidence(second, _decision(first, HumanApprovalOutcome.APPROVED))
        is HumanApprovalEvidenceStatus.MISMATCHED
    )


def test_task_mismatch_fails_closed_even_with_reused_request_id() -> None:
    request_id = HumanApprovalRequestId.create()
    correlation_id = uuid4()
    first = _request(task_id=TaskId.create(), correlation_id=correlation_id, request_id=request_id)
    second = _request(task_id=TaskId.create(), correlation_id=correlation_id, request_id=request_id)
    assert (
        evaluate_human_approval_evidence(second, _decision(first, HumanApprovalOutcome.APPROVED))
        is HumanApprovalEvidenceStatus.MISMATCHED
    )


def test_correlation_mismatch_fails_closed() -> None:
    task_id = TaskId.create()
    request_id = HumanApprovalRequestId.create()
    first = _request(task_id=task_id, correlation_id=uuid4(), request_id=request_id)
    second = _request(task_id=task_id, correlation_id=uuid4(), request_id=request_id)
    assert (
        evaluate_human_approval_evidence(second, _decision(first, HumanApprovalOutcome.APPROVED))
        is HumanApprovalEvidenceStatus.MISMATCHED
    )


def test_capability_name_mismatch_fails_closed() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    request_id = HumanApprovalRequestId.create()
    first = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        request_id=request_id,
        capability=_capability(name="demo.write"),
    )
    second = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        request_id=request_id,
        capability=_capability(name="demo.delete"),
    )
    assert (
        evaluate_human_approval_evidence(second, _decision(first, HumanApprovalOutcome.APPROVED))
        is HumanApprovalEvidenceStatus.MISMATCHED
    )


def test_capability_version_mismatch_fails_closed() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    request_id = HumanApprovalRequestId.create()
    first = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        request_id=request_id,
        capability=_capability(version=(1, 0, 0)),
    )
    second = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        request_id=request_id,
        capability=_capability(version=(1, 0, 1)),
    )
    assert (
        evaluate_human_approval_evidence(second, _decision(first, HumanApprovalOutcome.APPROVED))
        is HumanApprovalEvidenceStatus.MISMATCHED
    )


def test_parameter_mismatch_fails_closed() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    request_id = HumanApprovalRequestId.create()
    first = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        request_id=request_id,
        capability=_capability(count=1),
    )
    second = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        request_id=request_id,
        capability=_capability(count=2),
    )
    assert (
        evaluate_human_approval_evidence(second, _decision(first, HumanApprovalOutcome.APPROVED))
        is HumanApprovalEvidenceStatus.MISMATCHED
    )


def test_operation_mismatch_fails_closed() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    request_id = HumanApprovalRequestId.create()
    first = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        request_id=request_id,
        gate=_gate(operation="write alpha"),
    )
    second = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        request_id=request_id,
        gate=_gate(operation="delete alpha"),
    )
    assert (
        evaluate_human_approval_evidence(second, _decision(first, HumanApprovalOutcome.APPROVED))
        is HumanApprovalEvidenceStatus.MISMATCHED
    )


def test_permission_mismatch_fails_closed() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    request_id = HumanApprovalRequestId.create()
    first = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        request_id=request_id,
        gate=_gate(permission=Permission.EXTERNAL_EFFECT),
    )
    second = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        request_id=request_id,
        gate=_gate(permission=Permission.DESTRUCTIVE),
    )
    assert (
        evaluate_human_approval_evidence(second, _decision(first, HumanApprovalOutcome.APPROVED))
        is HumanApprovalEvidenceStatus.MISMATCHED
    )


def test_risk_mismatch_fails_closed_even_at_same_level() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    request_id = HumanApprovalRequestId.create()
    first = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        request_id=request_id,
        gate=_gate(reason="first R3 reason"),
    )
    second = _request(
        task_id=task_id,
        correlation_id=correlation_id,
        request_id=request_id,
        gate=_gate(reason="materially different R3 reason"),
    )
    assert (
        evaluate_human_approval_evidence(second, _decision(first, HumanApprovalOutcome.APPROVED))
        is HumanApprovalEvidenceStatus.MISMATCHED
    )


def test_request_identity_mismatch_fails_closed() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    first = _request(task_id=task_id, correlation_id=correlation_id)
    second = _request(task_id=task_id, correlation_id=correlation_id)
    assert (
        evaluate_human_approval_evidence(second, _decision(first, HumanApprovalOutcome.DENIED))
        is HumanApprovalEvidenceStatus.MISMATCHED
    )


def test_raw_string_is_rejected_not_parsed() -> None:
    with pytest.raises(TypeError, match="HumanApprovalDecision or None"):
        evaluate_human_approval_evidence(_request(), "APPROVED")  # type: ignore[arg-type]


def test_noncanonical_request_is_rejected() -> None:
    with pytest.raises(TypeError, match="HumanApprovalRequest"):
        evaluate_human_approval_evidence("request", None)  # type: ignore[arg-type]


def test_evaluation_is_deterministic() -> None:
    request = _request(
        request_id=HumanApprovalRequestId(UUID("11111111-1111-4111-8111-111111111111")),
        correlation_id=UUID("22222222-2222-4222-8222-222222222222"),
    )
    decision = _decision(request, HumanApprovalOutcome.APPROVED)
    assert evaluate_human_approval_evidence(request, decision) is evaluate_human_approval_evidence(
        request, decision
    )


def test_evaluation_does_not_mutate_immutable_request_or_decision() -> None:
    request = _request()
    decision = _decision(request, HumanApprovalOutcome.APPROVED)
    before_request = request.to_json()
    before_decision = decision.to_json()

    result = evaluate_human_approval_evidence(request, decision)

    assert result is HumanApprovalEvidenceStatus.MATCHING_APPROVED
    assert request.to_json() == before_request
    assert decision.to_json() == before_decision
