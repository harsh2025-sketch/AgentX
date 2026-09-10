"""Integration: N2.20 window management through the canonical closed loop.

Each window-management operation is an ordinary canonical ``Capability``, so
it must flow through the full A1.10 governed path — registry resolution,
permission checks, ActionGate authority evaluation, EmergencyStop
observation, budget accounting, execution, verification, Task transition,
and canonical events — exactly like any other capability. These tests
compose that path with the real canonical collaborators and a deterministic
recording fake of the injected native port.

Two governing facts are proven here:

* without an explicit ``WRITE`` plus ``EXTERNAL_EFFECT`` grant the run is
  denied before anything executes;
* with the full grant the ``R3 EXTERNAL_EFFECT`` assessment still requires
  confirmation, so the run stays blocked and native acceptance can never
  become Task success.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, LoopOutcome
from agentx.capabilities.windows.provider import (
    PlatformFacts,
    WindowsProvider,
    WindowsSupport,
    evaluate_windows_support,
)
from agentx.capabilities.windows.window_management_v2 import (
    NativeWindowManagementPort,
    NativeWindowMutationReceipt,
    WindowManagementCapability,
    WindowManagementOperation,
    WindowManagementParams,
    WindowTarget,
    window_management_request,
)
from agentx.core.errors import AgentXError
from agentx.core.events import Event
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.event_bus import EventBus
from agentx.kernel.action_gate import ActionGate, GateDecision, GateRequest
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import (
    BudgetDecision,
    ResourceBudget,
    ResourceDelta,
    ResourceEnvelope,
    ResourceRequest,
    ResourceUsage,
)
from agentx.kernel.risk import RiskLevel

_ALL_OPERATIONS = tuple(WindowManagementOperation)
_FULL_GRANT = AuthorityContext(
    permissions=frozenset({Permission.WRITE, Permission.EXTERNAL_EFFECT})
)


class FakeWindowPort(NativeWindowManagementPort):
    """Recording fake that accepts every governed window mutation."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def _accept(
        self, operation: str, handle: int
    ) -> Result[NativeWindowMutationReceipt, AgentXError]:
        self.calls.append((operation, handle))
        return Result.success(
            NativeWindowMutationReceipt(
                handle=handle,
                operation=operation,
                native_accepted=True,
                native_error_code=None,
            )
        )

    def activate_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        return self._accept("activate", handle)

    def minimize_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        return self._accept("minimize", handle)

    def maximize_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        return self._accept("maximize", handle)

    def restore_window(self, handle: int) -> Result[NativeWindowMutationReceipt, AgentXError]:
        return self._accept("restore", handle)

    def move_resize_window(
        self, handle: int, x: int, y: int, width: int, height: int
    ) -> Result[NativeWindowMutationReceipt, AgentXError]:
        del x, y, width, height
        return self._accept("move_resize", handle)


def _windows_support() -> WindowsSupport:
    return evaluate_windows_support(
        PlatformFacts(system="Windows", release="11", version="10.0", machine="AMD64")
    )


def _request_for(operation: WindowManagementOperation) -> CapabilityRequest[WindowManagementParams]:
    if operation is WindowManagementOperation.MOVE_RESIZE:
        return window_management_request(
            operation, WindowTarget(handle=5150), x=10, y=20, width=640, height=480
        )
    return window_management_request(operation, WindowTarget(handle=5150))


def _envelope(**overrides: Any) -> ResourceEnvelope:
    values: dict[str, Any] = {
        "max_wall_clock": timedelta(seconds=30),
        "max_model_calls": 0,
        "max_model_tokens": 0,
        "max_research_queries": 0,
        "max_machine_actions": 10,
        "max_repair_attempts": 0,
        "max_external_cost": Decimal("0"),
        "max_risk_level": RiskLevel.R3,
    }
    values.update(overrides)
    return ResourceEnvelope(**values)


def _capabilities(fake: FakeWindowPort) -> list[WindowManagementCapability]:
    return [
        WindowManagementCapability(
            operation=operation, support=_windows_support(), native_port=fake
        )
        for operation in _ALL_OPERATIONS
    ]


def _loop(
    authority: AuthorityContext | None,
    *,
    fake: FakeWindowPort,
    events: list[Event],
    envelope: ResourceEnvelope | None = None,
    emergency_stop: EmergencyStop | None = None,
) -> CapabilityExecutionLoop:
    registry = CapabilityRegistry()
    for capability in _capabilities(fake):
        registry.register(capability)
    bus = EventBus()
    bus.subscribe(events.append)
    return CapabilityExecutionLoop(
        registry=registry,
        action_gate=ActionGate(),
        authority=authority,
        emergency_stop=emergency_stop if emergency_stop is not None else EmergencyStop(),
        budget=ResourceBudget(envelope if envelope is not None else _envelope()),
        publish_event=bus.publish,
        publish_audit=lambda record: None,
    )


def _task_context() -> tuple[Task, ExecutionContext]:
    task = Task.create(objective="manage one explicitly identified window")
    context = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=task.task_id,
    )
    return task, context


# --------------------------------------------------------------------------
# Permission denial through the real governed loop.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("operation", _ALL_OPERATIONS)
def test_window_mutation_is_denied_without_any_authority(
    operation: WindowManagementOperation,
) -> None:
    fake = FakeWindowPort()
    events: list[Event] = []
    loop = _loop(None, fake=fake, events=events)
    task, context = _task_context()

    result = loop.run(task, _request_for(operation), context)

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.code == "runtime.permission_denied"
    assert outcome.execution is None
    assert outcome.verification is None
    assert fake.calls == []


@pytest.mark.parametrize(
    "permissions",
    [
        frozenset({Permission.WRITE}),
        frozenset({Permission.EXTERNAL_EFFECT}),
        frozenset({Permission.READ}),
        frozenset({Permission.READ, Permission.WRITE}),
        frozenset({Permission.READ, Permission.EXTERNAL_EFFECT}),
    ],
)
def test_window_mutation_is_denied_without_the_full_grant(
    permissions: frozenset[Permission],
) -> None:
    fake = FakeWindowPort()
    events: list[Event] = []
    loop = _loop(AuthorityContext(permissions=permissions), fake=fake, events=events)
    task, context = _task_context()

    result = loop.run(task, _request_for(WindowManagementOperation.MINIMIZE), context)

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert fake.calls == []


# --------------------------------------------------------------------------
# R3 confirmation behavior: permission is necessary but never sufficient.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("operation", _ALL_OPERATIONS)
def test_r3_window_mutation_requires_confirmation_despite_full_permission(
    operation: WindowManagementOperation,
) -> None:
    fake = FakeWindowPort()
    events: list[Event] = []
    loop = _loop(_FULL_GRANT, fake=fake, events=events)
    task, context = _task_context()

    result = loop.run(task, _request_for(operation), context)

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.error is not None
    assert outcome.error.code == "runtime.gate_denied"
    assert outcome.error.details["decision"] == GateDecision.REQUIRE_CONFIRMATION.value
    assert outcome.execution is None
    assert outcome.verification is None
    assert fake.calls == []
    assert events != []


@pytest.mark.parametrize("operation", _ALL_OPERATIONS)
def test_action_gate_requires_confirmation_for_each_operation(
    operation: WindowManagementOperation,
) -> None:
    capability = WindowManagementCapability(
        operation=operation, support=_windows_support(), native_port=FakeWindowPort()
    )

    for permission in (Permission.WRITE, Permission.EXTERNAL_EFFECT):
        verdict = ActionGate().evaluate(
            GateRequest(
                operation=str(capability.descriptor.identity),
                required_permission=permission,
                risk_assessment=capability.descriptor.risk_assessment,
            ),
            _FULL_GRANT,
        )
        assert verdict.decision is GateDecision.REQUIRE_CONFIRMATION


@pytest.mark.parametrize("operation", _ALL_OPERATIONS)
def test_action_gate_denies_each_operation_without_permission(
    operation: WindowManagementOperation,
) -> None:
    capability = WindowManagementCapability(
        operation=operation, support=_windows_support(), native_port=FakeWindowPort()
    )

    verdict = ActionGate().evaluate(
        GateRequest(
            operation=str(capability.descriptor.identity),
            required_permission=Permission.EXTERNAL_EFFECT,
            risk_assessment=capability.descriptor.risk_assessment,
        ),
        None,
    )

    assert verdict.decision is GateDecision.DENY


# --------------------------------------------------------------------------
# Budget, emergency stop, and cancellation stay fail-closed.
# --------------------------------------------------------------------------


def test_bounded_estimate_supports_budget_denial_at_the_evaluator() -> None:
    capability = WindowManagementCapability(
        operation=WindowManagementOperation.ACTIVATE,
        support=_windows_support(),
        native_port=FakeWindowPort(),
    )
    estimate = capability.descriptor.estimate
    starved = ResourceBudget(_envelope(max_machine_actions=0))

    verdict = starved.evaluate(
        ResourceRequest(
            delta=ResourceDelta(
                wall_clock=estimate.wall_clock,
                model_calls=0,
                model_tokens=0,
                research_queries=0,
                machine_actions=estimate.machine_actions,
                repair_attempts=0,
                external_cost=estimate.external_cost,
            ),
            risk_level=capability.descriptor.risk_assessment.effective_level,
        )
    )

    assert verdict.decision is BudgetDecision.DENY
    assert starved.snapshot() == ResourceUsage.zero()


def test_loop_consumes_no_budget_for_a_denied_window_mutation() -> None:
    fake = FakeWindowPort()
    events: list[Event] = []
    budget = ResourceBudget(_envelope())
    registry = CapabilityRegistry()
    for capability in _capabilities(fake):
        registry.register(capability)
    bus = EventBus()
    bus.subscribe(events.append)
    loop = CapabilityExecutionLoop(
        registry=registry,
        action_gate=ActionGate(),
        authority=_FULL_GRANT,
        emergency_stop=EmergencyStop(),
        budget=budget,
        publish_event=bus.publish,
        publish_audit=lambda record: None,
    )
    task, context = _task_context()

    result = loop.run(task, _request_for(WindowManagementOperation.MAXIMIZE), context)

    assert result.is_success
    assert result.unwrap().kind is LoopOutcome.DENIED
    assert budget.snapshot() == ResourceUsage.zero()
    assert fake.calls == []


def test_emergency_stop_denies_without_execution() -> None:
    fake = FakeWindowPort()
    events: list[Event] = []
    stop = EmergencyStop()
    stop.request_stop()
    loop = _loop(_FULL_GRANT, fake=fake, events=events, emergency_stop=stop)
    task, context = _task_context()

    result = loop.run(task, _request_for(WindowManagementOperation.RESTORE), context)

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert fake.calls == []
    assert stop.stop_requested is True


def test_cancelled_context_denies_without_execution() -> None:
    fake = FakeWindowPort()
    events: list[Event] = []
    loop = _loop(_FULL_GRANT, fake=fake, events=events)
    task = Task.create(objective="manage one explicitly identified window")
    source = CancellationSource()
    assert source.request_cancellation("integration test cancels") is True
    context = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=source.token,
        task_id=task.task_id,
    )

    result = loop.run(task, _request_for(WindowManagementOperation.ACTIVATE), context)

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert fake.calls == []


# --------------------------------------------------------------------------
# Verification truth boundary through the loop.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("operation", _ALL_OPERATIONS)
def test_native_success_cannot_become_task_success_through_the_loop(
    operation: WindowManagementOperation,
) -> None:
    """The fake port would accept, yet the governed run never verifies."""
    fake = FakeWindowPort()
    events: list[Event] = []
    loop = _loop(_FULL_GRANT, fake=fake, events=events)
    task, context = _task_context()

    result = loop.run(task, _request_for(operation), context)

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is not LoopOutcome.VERIFIED
    assert outcome.verified is False
    assert outcome.task.status is not TaskStatus.SUCCEEDED
    assert outcome.verification is None


# --------------------------------------------------------------------------
# Provider composition: five Windows-scoped capabilities, nothing ambient.
# --------------------------------------------------------------------------


def test_all_operations_contribute_through_the_windows_provider() -> None:
    fake = FakeWindowPort()
    provider = WindowsProvider(_windows_support())

    for capability in _capabilities(fake):
        contributed = provider.contribute(capability)
        assert contributed.is_success, contributed.unwrap_error()

    assert len(provider.capabilities()) == 5
    assert fake.calls == []


def test_provider_refuses_contribution_on_an_unsupported_host() -> None:
    fake = FakeWindowPort()
    linux = evaluate_windows_support(
        PlatformFacts(system="Linux", release="6.8", version="#1 SMP", machine="x86_64")
    )
    provider = WindowsProvider(linux)
    capability = WindowManagementCapability(
        operation=WindowManagementOperation.ACTIVATE,
        support=_windows_support(),
        native_port=fake,
    )

    contributed = provider.contribute(capability)

    assert contributed.is_failure
    assert contributed.unwrap_error().code == "capabilities.windows.unsupported_platform"
