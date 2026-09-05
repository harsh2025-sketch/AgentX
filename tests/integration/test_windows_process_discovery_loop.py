"""A5.02 integration: Windows discovery through the canonical closed loop.

The discovery capability is an ordinary canonical ``Capability``, so it must
flow through the full A1.10 governed path — registry resolution, ActionGate
authority check, EmergencyStop observation, budget consumption, execution,
verification, Task transition, canonical events and audit records — exactly
like any other capability. These tests compose that path with the real
canonical collaborators and the deterministic fake native seam, so nothing
depends on a real desktop or a particular application being open.

Two governing facts are proven here:

* with an explicit ``READ`` grant the read-only discovery run is verified;
* without it the loop denies the run before anything executes — discovery
  availability never becomes authority.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, LoopOutcome
from agentx.capabilities.windows.process_discovery import (
    WindowsProcessDiscoveryCapability,
    discovery_request,
)
from agentx.core.events import Event
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.event_bus import EventBus
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel
from tests.support.fake_windows_native import (
    FakeWindowsNative,
    path_denied,
    path_ok,
    raw_process,
    raw_window,
    windows_discovery,
)

_MAX_WALL_CLOCK = timedelta(seconds=30)


def _envelope(**overrides: Any) -> ResourceEnvelope:
    values: dict[str, Any] = {
        "max_wall_clock": _MAX_WALL_CLOCK,
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


def _fake() -> FakeWindowsNative:
    fake = FakeWindowsNative()
    fake.processes = [
        raw_process(4, parent_process_id=None, executable_name="System"),
        raw_process(1200, parent_process_id=4, executable_name="notepad.exe"),
    ]
    fake.windows = [raw_window(3002, process_id=1200, title="Untitled - Notepad")]
    fake.path_queries = {
        4: path_denied(),
        1200: path_ok("C:\\Windows\\system32\\notepad.exe"),
    }
    return fake


def _loop(
    authority: AuthorityContext | None,
    *,
    fake: FakeWindowsNative,
    events: list[Event],
) -> CapabilityExecutionLoop:
    registry = CapabilityRegistry()
    registry.register(WindowsProcessDiscoveryCapability(windows_discovery(fake)))
    bus = EventBus()
    bus.subscribe(events.append)
    return CapabilityExecutionLoop(
        registry=registry,
        action_gate=ActionGate(),
        authority=authority,
        emergency_stop=EmergencyStop(),
        budget=ResourceBudget(_envelope()),
        publish_event=bus.publish,
        publish_audit=lambda record: None,
    )


def _task_context() -> tuple[Task, ExecutionContext]:
    task = Task.create(objective="discover running windows processes")
    context = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=task.task_id,
    )
    return task, context


def test_discovery_is_verified_through_the_governed_closed_loop() -> None:
    fake = _fake()
    events: list[Event] = []
    authority = AuthorityContext(permissions=frozenset({Permission.READ}))
    loop = _loop(authority, fake=fake, events=events)
    task, context = _task_context()
    result = loop.run(task, discovery_request(), context)
    assert result.is_success, result.unwrap_error()
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.verification is not None and outcome.verification.passed is True
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert outcome.observation is not None
    fake.assert_only_read_methods_called()


def test_discovery_is_denied_without_an_explicit_read_grant() -> None:
    fake = _fake()
    events: list[Event] = []
    loop = _loop(None, fake=fake, events=events)
    task, context = _task_context()
    result = loop.run(task, discovery_request(), context)
    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is not TaskStatus.SUCCEEDED
    # Nothing executed: the fake seam was never touched.
    assert fake.calls == []
