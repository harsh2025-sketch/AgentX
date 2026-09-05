"""Canonical human-in-the-loop approval evidence for governed capabilities.

A6.08 represents an explicit human decision about one concrete governed
capability request.  It is inert data only: approval does not grant permission,
authorize execution, replace :class:`agentx.kernel.action_gate.ActionGate`,
reduce risk, widen budget, clear an emergency stop, verify an outcome, or
change Task state.

Binding is deliberately exact.  A request snapshots the canonical Task and
correlation identities, the canonical GateRequest, the selected capability
identity, and a canonical JSON representation of that capability's parameters.
A per-request opaque identifier prevents an approval for one request instance
from becoming a reusable blanket token.  No cryptographic fingerprint is
invented because the canonical fields themselves are retained as auditable
binding evidence.

There is no approval inference here.  Arbitrary strings (including model text,
webpage text, task metadata, procedure metadata, or environment content) are
never parsed as decisions.  Callers must supply the closed typed decision
vocabulary after obtaining an explicit human decision through a trusted human
control boundary owned by later orchestration/UI work.

No persistence, timers, notifications, authentication, execution, audit store,
or background service are implemented by this module.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from agentx.capabilities.abi import CapabilityIdentity, CapabilityRequest
from agentx.core.execution import ExecutionContext
from agentx.core.ids import TaskId
from agentx.kernel.action_gate import GateRequest

__all__ = [
    "HumanApprovalDecision",
    "HumanApprovalMismatchError",
    "HumanApprovalOutcome",
    "HumanApprovalRequest",
    "HumanApprovalRequestId",
    "HumanApprovalValidationError",
]


class HumanApprovalValidationError(ValueError):
    """Raised when approval evidence is malformed or internally inconsistent."""


class HumanApprovalMismatchError(ValueError):
    """Raised when a decision is presented for a different approval request."""


class HumanApprovalOutcome(StrEnum):
    """Closed explicit human-decision vocabulary.

    The values are the deterministic serialized representation.  Neither value
    grants authority: ``APPROVED`` is bounded evidence for one request and
    ``DENIED`` is explicit refusal of that same request.
    """

    APPROVED = "APPROVED"
    DENIED = "DENIED"


@dataclass(frozen=True, slots=True)
class HumanApprovalRequestId:
    """Opaque non-nil identity for one human-approval request instance."""

    value: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID):
            raise TypeError("value must be a UUID")
        if self.value.int == 0:
            raise HumanApprovalValidationError("approval request id must not be the nil UUID")

    @classmethod
    def create(cls) -> HumanApprovalRequestId:
        """Create a fresh opaque approval-request identity."""
        return cls(uuid4())

    def to_str(self) -> str:
        """Return canonical lowercase UUID serialization."""
        return str(self.value)

    def __str__(self) -> str:
        return self.to_str()


def _validate_json_value(value: object, *, path: str) -> None:
    if value is None or isinstance(value, bool | int | str):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HumanApprovalValidationError(f"{path} contains a non-finite float")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_json_value(item, path=f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if not isinstance(key, str):
                raise HumanApprovalValidationError(f"{path} contains a non-string object key")
            _validate_json_value(item, path=f"{path}.{key}")
        return
    raise HumanApprovalValidationError(
        f"{path} contains non-JSON-compatible value of type {type(value).__name__}"
    )


def _canonical_json_object(value: object, *, path: str) -> str:
    if type(value) is not dict:
        raise TypeError(f"{path} must be a dict")
    _validate_json_value(value, path=path)
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _risk_payload(request: GateRequest) -> dict[str, object]:
    assessment = request.risk_assessment
    return {
        "critical": assessment.critical,
        "destructive": assessment.destructive,
        "effective_level": assessment.effective_level.value,
        "external_effect": assessment.external_effect,
        "level": assessment.level.value,
        "modifies_state": assessment.modifies_state,
        "read_only": assessment.read_only,
        "reason": assessment.reason,
        "reversible": assessment.reversible,
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class HumanApprovalRequest:
    """Immutable evidence describing exactly one request needing human decision.

    ``request_id`` identifies this particular approval request instance.
    ``task_id`` and ``correlation_id`` preserve canonical audit linkage without
    retaining the mutable cancellation/deadline state of an ExecutionContext.
    ``gate_request`` binds the operation, required permission, and complete risk
    assessment. ``capability_identity`` and ``parameters_json`` bind the exact
    selected capability and canonical parameter payload.

    The object intentionally has no authority context, mutable metadata bag,
    callback, executable object, expiry timer, global state, or approval flag.
    """

    request_id: HumanApprovalRequestId
    task_id: TaskId
    correlation_id: UUID
    gate_request: GateRequest
    capability_identity: CapabilityIdentity
    parameters_json: str

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, HumanApprovalRequestId):
            raise TypeError("request_id must be a HumanApprovalRequestId")
        if not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be a TaskId")
        if not isinstance(self.correlation_id, UUID):
            raise TypeError("correlation_id must be a UUID")
        if self.correlation_id.int == 0:
            raise HumanApprovalValidationError("correlation_id must not be the nil UUID")
        if not isinstance(self.gate_request, GateRequest):
            raise TypeError("gate_request must be a GateRequest")
        if not isinstance(self.capability_identity, CapabilityIdentity):
            raise TypeError("capability_identity must be a CapabilityIdentity")
        if not isinstance(self.parameters_json, str):
            raise TypeError("parameters_json must be a string")
        if not self.parameters_json:
            raise HumanApprovalValidationError("parameters_json must not be empty")
        try:
            parsed = json.loads(self.parameters_json)
        except json.JSONDecodeError as exc:
            raise HumanApprovalValidationError("parameters_json must contain valid JSON") from exc
        canonical = _canonical_json_object(parsed, path="parameters_json")
        if canonical != self.parameters_json:
            raise HumanApprovalValidationError("parameters_json must use canonical JSON encoding")

    @classmethod
    def create(
        cls,
        *,
        task_id: TaskId,
        context: ExecutionContext,
        gate_request: GateRequest,
        capability_request: CapabilityRequest[Any],
        request_id: HumanApprovalRequestId | None = None,
    ) -> HumanApprovalRequest:
        """Snapshot canonical governed-request data into bounded approval evidence.

        The ExecutionContext must explicitly name the same TaskId.  Only its
        stable task/correlation identities are retained; cancellation and
        deadline state are deliberately not copied into approval evidence.
        Capability parameters are serialized through the canonical typed
        CapabilityParams boundary and then normalized to deterministic JSON.
        """
        if not isinstance(task_id, TaskId):
            raise TypeError("task_id must be a TaskId")
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        if context.task_id is None:
            raise HumanApprovalValidationError(
                "execution context must carry task_id for approval binding"
            )
        if context.task_id != task_id:
            raise HumanApprovalValidationError(
                "execution context task_id does not match approval task_id"
            )
        if not isinstance(gate_request, GateRequest):
            raise TypeError("gate_request must be a GateRequest")
        if not isinstance(capability_request, CapabilityRequest):
            raise TypeError("capability_request must be a CapabilityRequest")
        if request_id is not None and not isinstance(request_id, HumanApprovalRequestId):
            raise TypeError("request_id must be a HumanApprovalRequestId or None")

        raw_parameters = capability_request.params.to_dict()
        parameters_json = _canonical_json_object(raw_parameters, path="capability parameters")
        return cls(
            request_id=HumanApprovalRequestId.create() if request_id is None else request_id,
            task_id=task_id,
            correlation_id=context.correlation_id,
            gate_request=gate_request,
            capability_identity=capability_request.identity,
            parameters_json=parameters_json,
        )

    def to_json(self) -> str:
        """Return deterministic JSON for audit/event transport by future owners."""
        parameters = json.loads(self.parameters_json)
        payload = {
            "capability": {
                "name": self.capability_identity.name.value,
                "version": self.capability_identity.version.to_str(),
            },
            "correlation_id": str(self.correlation_id),
            "gate": {
                "operation": self.gate_request.operation,
                "required_permission": self.gate_request.required_permission.value,
                "risk": _risk_payload(self.gate_request),
            },
            "parameters": parameters,
            "request_id": self.request_id.to_str(),
            "task_id": self.task_id.to_str(),
        }
        return _canonical_json_object(payload, path="approval request")


@dataclass(frozen=True, slots=True, kw_only=True)
class HumanApprovalDecision:
    """One explicit APPROVED/DENIED human decision bound to one exact request.

    Construction requires the typed :class:`HumanApprovalOutcome`; free-form
    text is never interpreted as approval.  ``validate_binding`` checks only
    identity/content binding.  It does not authorize execution or reinterpret
    APPROVED as Permission/ActionGate success.
    """

    request: HumanApprovalRequest
    outcome: HumanApprovalOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.request, HumanApprovalRequest):
            raise TypeError("request must be a HumanApprovalRequest")
        if not isinstance(self.outcome, HumanApprovalOutcome):
            raise TypeError("outcome must be a HumanApprovalOutcome")

    def validate_binding(self, request: HumanApprovalRequest) -> None:
        """Fail closed unless this decision concerns the exact supplied request."""
        if not isinstance(request, HumanApprovalRequest):
            raise TypeError("request must be a HumanApprovalRequest")
        if self.request != request:
            raise HumanApprovalMismatchError(
                "human approval decision is bound to a different approval request"
            )

    def to_json(self) -> str:
        """Return deterministic JSON for this bounded decision evidence."""
        payload = {
            "outcome": self.outcome.value,
            "request": json.loads(self.request.to_json()),
        }
        return _canonical_json_object(payload, path="approval decision")
