"""Governed-path integration tests for M7.01 browser actions.

These tests use the real canonical collaborators:

* CapabilityRegistry
* PermissionEngine / ActionGate (via CapabilityExecutionLoop)
* ResourceBudget
* EmergencyStop
* CapabilityExecutionLoop

A deterministic fake implements the baseline BrowserProvider protocol plus the
M7.01 driver port. No real browser is launched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from agentx.capabilities.abi import Capability
from agentx.capabilities.browser_actions import (
    BrowserActionOperation,
    BrowserActionOutcome,
    BrowserActionsCapability,
    BrowserClickPostcondition,
    click_selected_request,
    fill_selected_request,
    navigate_request,
)
from agentx.capabilities.browser_connection import (
    BrowserConnectionRef,
    BrowserConnectionState,
    BrowserSessionId,
    BrowserTargetId,
    BrowserTargetKind,
    BrowserTargetRef,
    BrowserTargetState,
)
from agentx.capabilities.browser_dom import (
    BrowserDomAttribute,
    BrowserDomNodeId,
    BrowserDomNodeRef,
    BrowserDomNodeSnapshot,
    BrowserDomNodeState,
    BrowserDomObservation,
    BrowserDomObservationState,
    BrowserDomReadRequest,
)
from agentx.capabilities.browser_provider import (
    BrowserProviderAvailability,
    BrowserProviderDescriptor,
    BrowserProviderId,
    BrowserProviderStatus,
)
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, LoopOutcome
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.events import DecisionPayload, Event, EventType
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.event_bus import EventBus
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.audit import SecurityAuditRecord
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel

_T0 = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)
_MAX_WAIT = timedelta(seconds=30)


def _provider_id() -> BrowserProviderId:
    return BrowserProviderId("browser.test")


def _session() -> BrowserSessionId:
    return BrowserSessionId(provider_id=_provider_id(), value="session-001")


def _target(url: str = "https://example.invalid/start") -> BrowserTargetRef:
    connection = BrowserConnectionRef(
        session_id=_session(),
        state=BrowserConnectionState.CONNECTED,
        detail="integration snapshot",
    )
    return BrowserTargetRef(
        connection=connection,
        target_id=BrowserTargetId(session_id=connection.session_id, value="target-001"),
        kind=BrowserTargetKind.PAGE,
        state=BrowserTargetState.AVAILABLE,
        title="Example",
        url=url,
    )


def _node(target: BrowserTargetRef, value: str = "node-001") -> BrowserDomNodeRef:
    return BrowserDomNodeRef(
        target=target,
        node_id=BrowserDomNodeId(target_id=target.target_id, value=value),
        state=BrowserDomNodeState.AVAILABLE,
    )


class FakeGovernedBrowser:
    """Baseline BrowserProvider + action driver for governed-path proofs."""

    def __init__(self) -> None:
        self.availability = BrowserProviderAvailability.AVAILABLE
        self.document_url = "https://example.invalid/start"
        self.node_values: dict[str, str] = {}
        self.calls: list[str] = []

    @property
    def descriptor(self) -> BrowserProviderDescriptor:
        return BrowserProviderDescriptor(
            provider_id=_provider_id(),
            description="Deterministic governed-path fake browser provider.",
        )

    @property
    def status(self) -> BrowserProviderStatus:
        return BrowserProviderStatus(availability=self.availability, detail="integration fake")

    @property
    def capabilities(self) -> tuple[Capability[Any], ...]:
        return ()

    def navigate(
        self, target: BrowserTargetRef, url: str
    ) -> Result[BrowserActionOutcome, AgentXError]:
        self.calls.append("navigate")
        self.document_url = url
        return Result.success(BrowserActionOutcome(succeeded=True, message="navigated"))

    def click_selected(self, node: BrowserDomNodeRef) -> Result[BrowserActionOutcome, AgentXError]:
        self.calls.append("click_selected")
        return Result.success(BrowserActionOutcome(succeeded=True, message="clicked"))

    def fill_selected(
        self, node: BrowserDomNodeRef, text: str
    ) -> Result[BrowserActionOutcome, AgentXError]:
        self.calls.append("fill_selected")
        self.node_values[node.node_id.value] = text
        return Result.success(BrowserActionOutcome(succeeded=True, message="filled"))

    def observe_dom(
        self, request: BrowserDomReadRequest
    ) -> Result[BrowserDomObservation, AgentXError]:
        self.calls.append("observe_dom")
        target = BrowserTargetRef(
            connection=request.target.connection,
            target_id=request.target.target_id,
            kind=request.target.kind,
            state=request.target.state,
            title=request.target.title,
            url=self.document_url,
        )
        nodes = []
        for node_id, value in self.node_values.items():
            node_ref = _node(target, node_id)
            nodes.append(
                BrowserDomNodeSnapshot(
                    node=node_ref,
                    tag_name="input",
                    text=value,
                    attributes=(BrowserDomAttribute("value", value),),
                )
            )
        return Result.success(
            BrowserDomObservation(
                target=target,
                state=BrowserDomObservationState.OBSERVED,
                observed_at=_T0,
                nodes=tuple(nodes),
                root_id=nodes[0].node.node_id if nodes else None,
                document_version="doc-1",
            )
        )


def make_envelope(**overrides: Any) -> ResourceEnvelope:
    values: dict[str, Any] = {
        "max_wall_clock": _MAX_WAIT,
        "max_model_calls": 0,
        "max_model_tokens": 0,
        "max_research_queries": 0,
        "max_machine_actions": 10,
        "max_repair_attempts": 0,
        "max_external_cost": Decimal("0"),
        "max_risk_level": RiskLevel.R2,
    }
    values.update(overrides)
    return ResourceEnvelope(**values)


class Harness:
    def __init__(
        self,
        *,
        authority: frozenset[Permission] | None = frozenset({Permission.WRITE}),
        envelope: ResourceEnvelope | None = None,
    ) -> None:
        self.registry = CapabilityRegistry()
        self.bus = EventBus()
        self.events: list[Event] = []
        self.audit_records: list[SecurityAuditRecord] = []
        self.bus.subscribe(self.events.append)
        self.budget = ResourceBudget(envelope if envelope is not None else make_envelope())
        self.emergency_stop = EmergencyStop()
        self.authority = None if authority is None else AuthorityContext(authority)
        self.loop = CapabilityExecutionLoop(
            registry=self.registry,
            action_gate=ActionGate(),
            authority=self.authority,
            emergency_stop=self.emergency_stop,
            budget=self.budget,
            publish_event=self.bus.publish,
            publish_audit=self.audit_records.append,
        )
        self.surface = FakeGovernedBrowser()

    def register(self, operation: BrowserActionOperation) -> BrowserActionsCapability:
        capability = BrowserActionsCapability(
            operation=operation,
            provider=self.surface,
            driver=self.surface,
        )
        self.registry.register(capability)
        return capability

    def run(self, request: Any) -> Any:
        task = Task.create(objective="perform one governed browser action")
        context = ExecutionContext(
            correlation_id=uuid4(),
            cancellation_token=CancellationSource().token,
            task_id=task.task_id,
        )
        return self.loop.run(task, request, context)


def test_authorized_navigation_uses_the_real_governed_path() -> None:
    harness = Harness(authority=frozenset({Permission.WRITE}))
    harness.register(BrowserActionOperation.NAVIGATE)
    result = harness.run(navigate_request(_target(), "https://example.invalid/next"))
    outcome = result.unwrap()

    assert result.is_success
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.verified is True
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert outcome.verification is not None and outcome.verification.passed is True
    assert "navigate" in harness.surface.calls
    assert "observe_dom" in harness.surface.calls
    assert EventType.TASK_COMPLETED in {event.event_type for event in harness.events}
    assert EventType.VERIFICATION_COMPLETED in {event.event_type for event in harness.events}


def test_denied_action_never_reaches_provider() -> None:
    harness = Harness(authority=None)
    harness.register(BrowserActionOperation.NAVIGATE)
    result = harness.run(navigate_request(_target(), "https://example.invalid/next"))
    outcome = result.unwrap()

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.category is ErrorCategory.PERMISSION
    assert harness.surface.calls == []
    decisions = [
        event
        for event in harness.events
        if event.event_type is EventType.POLICY_DECISION
        and isinstance(event.payload, DecisionPayload)
        and event.payload.decision == "DENY"
    ]
    assert decisions


def test_fill_verification_on_governed_path() -> None:
    harness = Harness(authority=frozenset({Permission.WRITE}))
    harness.register(BrowserActionOperation.FILL_SELECTED)
    target = _target()
    result = harness.run(fill_selected_request(target, _node(target), "typed"))
    outcome = result.unwrap()

    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert harness.surface.node_values["node-001"] == "typed"
    assert "fill_selected" in harness.surface.calls
    assert "observe_dom" in harness.surface.calls
    assert "click_selected" not in harness.surface.calls


def test_click_verification_failure_prevents_task_success() -> None:
    harness = Harness(
        authority=frozenset({Permission.WRITE, Permission.EXTERNAL_EFFECT}),
    )
    harness.register(BrowserActionOperation.CLICK_SELECTED)
    target = _target()
    result = harness.run(click_selected_request(target, _node(target)))
    outcome = result.unwrap()

    assert "click_selected" in harness.surface.calls
    assert outcome.execution is not None and outcome.execution.succeeded is True
    assert outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.verification is not None
    assert outcome.verification.passed is False
    assert EventType.TASK_COMPLETED not in {event.event_type for event in harness.events}
    assert EventType.TASK_FAILED in {event.event_type for event in harness.events}


def test_click_with_unmet_postcondition_prevents_task_success() -> None:
    harness = Harness(
        authority=frozenset({Permission.WRITE, Permission.EXTERNAL_EFFECT}),
    )
    harness.register(BrowserActionOperation.CLICK_SELECTED)
    target = _target()
    result = harness.run(
        click_selected_request(
            target,
            _node(target),
            postcondition=BrowserClickPostcondition(
                expected_url="https://example.invalid/never-reached"
            ),
        )
    )
    outcome = result.unwrap()

    assert outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert outcome.task.status is TaskStatus.FAILED


def test_resource_denial_never_reaches_provider() -> None:
    harness = Harness(
        authority=frozenset({Permission.WRITE}),
        envelope=make_envelope(max_machine_actions=0),
    )
    harness.register(BrowserActionOperation.NAVIGATE)
    before = harness.budget.snapshot()
    result = harness.run(navigate_request(_target(), "https://example.invalid/next"))
    outcome = result.unwrap()

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.error is not None
    assert outcome.error.category is ErrorCategory.RESOURCE
    assert harness.surface.calls == []
    assert harness.budget.snapshot() == before


def test_emergency_stop_never_reaches_provider() -> None:
    harness = Harness(authority=frozenset({Permission.WRITE}))
    harness.register(BrowserActionOperation.NAVIGATE)
    harness.emergency_stop.request_stop()
    result = harness.run(navigate_request(_target(), "https://example.invalid/next"))
    outcome = result.unwrap()

    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.CANCELLED
    assert harness.surface.calls == []


def test_canonical_event_and_outcome_behavior_for_verified_fill() -> None:
    harness = Harness(authority=frozenset({Permission.WRITE}))
    harness.register(BrowserActionOperation.FILL_SELECTED)
    target = _target()
    result = harness.run(fill_selected_request(target, _node(target), "alpha"))
    outcome = result.unwrap()

    assert outcome.kind is LoopOutcome.VERIFIED
    types = tuple(event.event_type for event in harness.events)
    assert types == (
        EventType.TASK_STARTED,
        EventType.CAPABILITY_SELECTED,
        EventType.POLICY_DECISION,
        EventType.POLICY_DECISION,
        EventType.ACTION_REQUESTED,
        EventType.ACTION_COMPLETED,
        EventType.OBSERVATION_RECORDED,
        EventType.VERIFICATION_COMPLETED,
        EventType.TASK_COMPLETED,
    )
    assert outcome.budget_usage.machine_actions == 1
    assert outcome.budget_usage.model_calls == 0


def test_click_without_external_effect_permission_is_denied() -> None:
    harness = Harness(authority=frozenset({Permission.WRITE}))
    harness.register(BrowserActionOperation.CLICK_SELECTED)
    target = _target()
    result = harness.run(click_selected_request(target, _node(target)))
    outcome = result.unwrap()

    assert outcome.kind is LoopOutcome.DENIED
    assert harness.surface.calls == []
