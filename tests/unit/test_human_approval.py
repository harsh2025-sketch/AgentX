"""Unit coverage for A6.08 human-in-the-loop approval data contracts."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, dataclass
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
    HumanApprovalMismatchError,
    HumanApprovalOutcome,
    HumanApprovalRequest,
    HumanApprovalRequestId,
    HumanApprovalValidationError,
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
    options: dict[str, JsonValue]

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "options": self.options,
            "target": self.target,
            "count": self.count,
        }


def _context(task_id: TaskId, *, correlation_id: UUID | None = None) -> ExecutionContext:
    source = CancellationSource()
    return ExecutionContext(
        correlation_id=uuid4() if correlation_id is None else correlation_id,
        cancellation_token=source.token,
        task_id=task_id,
    )


def _capability_request(
    *,
    name: str = "demo.write",
    version: tuple[int, int, int] = (1, 0, 0),
    target: str = "alpha",
    count: int = 1,
    options: dict[str, JsonValue] | None = None,
) -> CapabilityRequest[DemoParams]:
    major, minor, patch = version
    identity = CapabilityIdentity(
        name=CapabilityName(name),
        version=CapabilityVersion(major, minor, patch),
    )
    params = DemoParams(
        target=target,
        count=count,
        options={"z": 3, "a": [2, 1]} if options is None else options,
    )
    return CapabilityRequest(identity=identity, params=params)


def _gate_request(
    *,
    operation: str = "write demo target",
    permission: Permission = Permission.EXTERNAL_EFFECT,
    level: RiskLevel = RiskLevel.R3,
    reason: str = "R3 external effect requires explicit human confirmation.",
    external_effect: bool = True,
    destructive: bool = False,
) -> GateRequest:
    return GateRequest(
        operation=operation,
        required_permission=permission,
        risk_assessment=RiskAssessment(
            level=level,
            reason=reason,
            reversible=False,
            external_effect=external_effect,
            destructive=destructive,
        ),
    )


def _approval_request(
    *,
    task_id: TaskId | None = None,
    context: ExecutionContext | None = None,
    gate_request: GateRequest | None = None,
    capability_request: CapabilityRequest[DemoParams] | None = None,
    request_id: HumanApprovalRequestId | None = None,
) -> HumanApprovalRequest:
    actual_task_id = TaskId.create() if task_id is None else task_id
    actual_context = _context(actual_task_id) if context is None else context
    return HumanApprovalRequest.create(
        task_id=actual_task_id,
        context=actual_context,
        gate_request=_gate_request() if gate_request is None else gate_request,
        capability_request=(
            _capability_request() if capability_request is None else capability_request
        ),
        request_id=request_id,
    )


def test_decision_vocabulary_is_exact_and_closed() -> None:
    assert tuple(HumanApprovalOutcome) == (
        HumanApprovalOutcome.APPROVED,
        HumanApprovalOutcome.DENIED,
    )
    assert {member.name: member.value for member in HumanApprovalOutcome} == {
        "APPROVED": "APPROVED",
        "DENIED": "DENIED",
    }
    assert len(HumanApprovalOutcome.__members__) == 2


def test_explicit_approved_decision_is_representable() -> None:
    request = _approval_request()

    decision = HumanApprovalDecision(
        request=request,
        outcome=HumanApprovalOutcome.APPROVED,
    )

    assert decision.request is request
    assert decision.outcome is HumanApprovalOutcome.APPROVED


def test_explicit_denied_decision_is_representable() -> None:
    request = _approval_request()

    decision = HumanApprovalDecision(
        request=request,
        outcome=HumanApprovalOutcome.DENIED,
    )

    assert decision.request is request
    assert decision.outcome is HumanApprovalOutcome.DENIED


def test_unknown_decision_values_fail_closed() -> None:
    for raw in ("", "approved", "denied", "YES", "ALLOW", "ADMIN", "APPROVE_ALL"):
        with pytest.raises(ValueError):
            HumanApprovalOutcome(raw)


def test_free_form_string_cannot_be_used_as_typed_decision() -> None:
    request = _approval_request()

    with pytest.raises(TypeError, match="outcome must be a HumanApprovalOutcome"):
        HumanApprovalDecision(request=request, outcome="APPROVED")  # type: ignore[arg-type]


def test_request_factory_snapshots_exact_canonical_binding() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    context = _context(task_id, correlation_id=correlation_id)
    gate = _gate_request()
    capability = _capability_request()

    request = HumanApprovalRequest.create(
        task_id=task_id,
        context=context,
        gate_request=gate,
        capability_request=capability,
    )

    assert request.task_id == task_id
    assert request.correlation_id == correlation_id
    assert request.gate_request is gate
    assert request.capability_identity == capability.identity
    assert json.loads(request.parameters_json) == capability.params.to_dict()
    assert request.request_id.value.int != 0


def test_parameter_serialization_is_canonical_and_deterministic() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    capability = _capability_request(options={"z": 3, "a": [2, 1]})
    fixed_id = HumanApprovalRequestId(UUID("11111111-1111-4111-8111-111111111111"))

    left = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=_gate_request(),
        capability_request=capability,
        request_id=fixed_id,
    )
    right = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=_gate_request(),
        capability_request=capability,
        request_id=fixed_id,
    )

    assert left.parameters_json == '{"count":1,"options":{"a":[2,1],"z":3},"target":"alpha"}'
    assert left.to_json() == right.to_json()


def test_request_json_contains_auditable_binding_and_is_stable() -> None:
    task_id = TaskId(UUID("22222222-2222-4222-8222-222222222222"))
    correlation_id = UUID("33333333-3333-4333-8333-333333333333")
    request_id = HumanApprovalRequestId(UUID("44444444-4444-4444-8444-444444444444"))
    request = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=_gate_request(),
        capability_request=_capability_request(),
        request_id=request_id,
    )

    payload = json.loads(request.to_json())

    assert payload["request_id"] == request_id.to_str()
    assert payload["task_id"] == task_id.to_str()
    assert payload["correlation_id"] == str(correlation_id)
    assert payload["capability"] == {"name": "demo.write", "version": "1.0.0"}
    assert payload["gate"]["operation"] == "write demo target"
    assert payload["gate"]["required_permission"] == "EXTERNAL_EFFECT"
    assert payload["gate"]["risk"]["effective_level"] == "R3"
    assert payload["parameters"] == {
        "count": 1,
        "options": {"a": [2, 1], "z": 3},
        "target": "alpha",
    }
    assert request.to_json() == request.to_json()


def test_decision_json_is_deterministic_and_contains_exact_request() -> None:
    request = _approval_request(
        request_id=HumanApprovalRequestId(UUID("55555555-5555-4555-8555-555555555555"))
    )
    decision = HumanApprovalDecision(request=request, outcome=HumanApprovalOutcome.APPROVED)

    first = decision.to_json()
    second = decision.to_json()
    payload = json.loads(first)

    assert first == second
    assert payload["outcome"] == "APPROVED"
    assert payload["request"] == json.loads(request.to_json())


def test_request_and_decision_are_immutable() -> None:
    request = _approval_request()
    decision = HumanApprovalDecision(request=request, outcome=HumanApprovalOutcome.APPROVED)

    with pytest.raises(FrozenInstanceError):
        request.task_id = TaskId.create()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.outcome = HumanApprovalOutcome.DENIED  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        request.request_id.value = uuid4()  # type: ignore[misc]


def test_exact_request_binding_validates() -> None:
    request = _approval_request()
    decision = HumanApprovalDecision(request=request, outcome=HumanApprovalOutcome.APPROVED)

    decision.validate_binding(request)


def test_new_identical_request_instance_cannot_reuse_prior_decision() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    gate = _gate_request()
    capability = _capability_request()
    first = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=gate,
        capability_request=capability,
    )
    second = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=gate,
        capability_request=capability,
    )
    decision = HumanApprovalDecision(request=first, outcome=HumanApprovalOutcome.APPROVED)

    assert first.request_id != second.request_id
    with pytest.raises(HumanApprovalMismatchError):
        decision.validate_binding(second)


def test_task_mismatch_rejected_even_when_request_id_is_reused() -> None:
    first_task = TaskId.create()
    second_task = TaskId.create()
    correlation_id = uuid4()
    request_id = HumanApprovalRequestId.create()
    first = HumanApprovalRequest.create(
        task_id=first_task,
        context=_context(first_task, correlation_id=correlation_id),
        gate_request=_gate_request(),
        capability_request=_capability_request(),
        request_id=request_id,
    )
    second = HumanApprovalRequest.create(
        task_id=second_task,
        context=_context(second_task, correlation_id=correlation_id),
        gate_request=_gate_request(),
        capability_request=_capability_request(),
        request_id=request_id,
    )
    decision = HumanApprovalDecision(request=first, outcome=HumanApprovalOutcome.APPROVED)

    with pytest.raises(HumanApprovalMismatchError):
        decision.validate_binding(second)


def test_capability_mismatch_rejected_even_when_request_id_is_reused() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    request_id = HumanApprovalRequestId.create()
    first = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=_gate_request(),
        capability_request=_capability_request(name="demo.write"),
        request_id=request_id,
    )
    second = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=_gate_request(),
        capability_request=_capability_request(name="demo.delete"),
        request_id=request_id,
    )
    decision = HumanApprovalDecision(request=first, outcome=HumanApprovalOutcome.APPROVED)

    with pytest.raises(HumanApprovalMismatchError):
        decision.validate_binding(second)


def test_parameter_mismatch_rejected_even_when_request_id_is_reused() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    request_id = HumanApprovalRequestId.create()
    first = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=_gate_request(),
        capability_request=_capability_request(target="alpha", count=1),
        request_id=request_id,
    )
    second = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=_gate_request(),
        capability_request=_capability_request(target="alpha", count=2),
        request_id=request_id,
    )
    decision = HumanApprovalDecision(request=first, outcome=HumanApprovalOutcome.APPROVED)

    with pytest.raises(HumanApprovalMismatchError):
        decision.validate_binding(second)


def test_operation_mismatch_rejected_even_when_request_id_is_reused() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    request_id = HumanApprovalRequestId.create()
    first = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=_gate_request(operation="send draft"),
        capability_request=_capability_request(),
        request_id=request_id,
    )
    second = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=_gate_request(operation="delete draft"),
        capability_request=_capability_request(),
        request_id=request_id,
    )
    decision = HumanApprovalDecision(request=first, outcome=HumanApprovalOutcome.APPROVED)

    with pytest.raises(HumanApprovalMismatchError):
        decision.validate_binding(second)


def test_risk_request_mutation_rejected_even_at_same_effective_level() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    request_id = HumanApprovalRequestId.create()
    first = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=_gate_request(reason="First explicit R3 reason."),
        capability_request=_capability_request(),
        request_id=request_id,
    )
    second = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=_gate_request(reason="Materially different R3 reason."),
        capability_request=_capability_request(),
        request_id=request_id,
    )
    decision = HumanApprovalDecision(request=first, outcome=HumanApprovalOutcome.APPROVED)

    assert first.gate_request.risk_assessment.effective_level is RiskLevel.R3
    assert second.gate_request.risk_assessment.effective_level is RiskLevel.R3
    with pytest.raises(HumanApprovalMismatchError):
        decision.validate_binding(second)


def test_permission_mutation_rejected_even_when_request_id_is_reused() -> None:
    task_id = TaskId.create()
    correlation_id = uuid4()
    request_id = HumanApprovalRequestId.create()
    first = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=_gate_request(permission=Permission.EXTERNAL_EFFECT),
        capability_request=_capability_request(),
        request_id=request_id,
    )
    second = HumanApprovalRequest.create(
        task_id=task_id,
        context=_context(task_id, correlation_id=correlation_id),
        gate_request=_gate_request(permission=Permission.DESTRUCTIVE),
        capability_request=_capability_request(),
        request_id=request_id,
    )
    decision = HumanApprovalDecision(request=first, outcome=HumanApprovalOutcome.APPROVED)

    with pytest.raises(HumanApprovalMismatchError):
        decision.validate_binding(second)


def test_factory_requires_execution_context_to_name_exact_task() -> None:
    task_id = TaskId.create()
    other_task = TaskId.create()

    with pytest.raises(HumanApprovalValidationError, match="does not match"):
        HumanApprovalRequest.create(
            task_id=task_id,
            context=_context(other_task),
            gate_request=_gate_request(),
            capability_request=_capability_request(),
        )


def test_factory_rejects_context_without_task_binding() -> None:
    source = CancellationSource()
    context = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=source.token,
        task_id=None,
    )

    with pytest.raises(HumanApprovalValidationError, match="must carry task_id"):
        HumanApprovalRequest.create(
            task_id=TaskId.create(),
            context=context,
            gate_request=_gate_request(),
            capability_request=_capability_request(),
        )


def test_direct_request_construction_rejects_noncanonical_parameter_json() -> None:
    base = _approval_request()

    with pytest.raises(HumanApprovalValidationError, match="canonical JSON"):
        HumanApprovalRequest(
            request_id=base.request_id,
            task_id=base.task_id,
            correlation_id=base.correlation_id,
            gate_request=base.gate_request,
            capability_identity=base.capability_identity,
            parameters_json='{ "count": 1 }',
        )


def test_nil_approval_request_id_is_rejected() -> None:
    with pytest.raises(HumanApprovalValidationError, match="nil UUID"):
        HumanApprovalRequestId(UUID(int=0))


def test_request_has_no_mutable_metadata_or_blanket_authority_fields() -> None:
    request = _approval_request()

    for forbidden in (
        "metadata",
        "approved",
        "authority",
        "permissions",
        "budget",
        "execute",
        "activate",
        "verified",
    ):
        assert not hasattr(request, forbidden)
