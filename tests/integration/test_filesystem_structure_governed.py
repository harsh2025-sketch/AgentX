"""M7.05 integration: governed filesystem structural operations through the loop.

These tests prove the four structural operations flow through the full A1.10
governed closed loop against a real temporary filesystem: registry resolution,
ActionGate authority check, EmergencyStop observation, budget consumption,
execution, independent verification, Task transition, and canonical events.
Nothing depends on Worker-01's text read/write surface.

Governing facts proven here:

* a governed ``mkdir`` and a governed ``move`` reach ``VERIFIED`` only with an
  explicit ``WRITE`` grant and real independent postcondition evidence;
* a denied write performs nothing (no directory is created, nothing moves);
* budget denial and an active EmergencyStop each refuse the run before any
  mutation;
* a verification failure prevents Task success even when the underlying
  operation executed;
* directory listing through the loop stays bounded and deterministic.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from agentx.capabilities import filesystem_structure as fs
from agentx.capabilities.abi import VerificationResult
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, LoopOutcome
from agentx.core.events import Event
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.event_bus import EventBus
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel

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
        # Mutations declare R2 (state-modifying) risk; allow up to R4 here so
        # the risk ceiling never shadows the denial tests under test.
        "max_risk_level": RiskLevel.R4,
    }
    values.update(overrides)
    return ResourceEnvelope(**values)


def _loop(
    registry: CapabilityRegistry,
    authority: AuthorityContext | None,
    *,
    events: list[Event],
    emergency_stop: EmergencyStop | None = None,
    envelope: ResourceEnvelope | None = None,
) -> CapabilityExecutionLoop:
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


def _register(*capabilities: Any) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for capability in capabilities:
        registry.register(capability)
    return registry


def _task_context() -> tuple[Task, ExecutionContext]:
    task = Task.create(objective="exercise governed filesystem structure operations")
    context = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=task.task_id,
    )
    return task, context


def _authority(*permissions: Permission) -> AuthorityContext:
    return AuthorityContext(permissions=frozenset(permissions))


# --------------------------------------------------------------------------
# Governed mutations are verified.
# --------------------------------------------------------------------------


def test_governed_create_directory_is_verified_through_the_closed_loop(tmp_path: Any) -> None:
    target = tmp_path / "governed_dir"
    registry = _register(fs.CreateDirectoryCapability())
    events: list[Event] = []
    loop = _loop(registry, _authority(Permission.WRITE), events=events)
    task, context = _task_context()
    result = loop.run(task, fs.create_directory_request(str(target)), context)
    assert result.is_success, result.unwrap_error()
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.task.status is TaskStatus.SUCCEEDED
    # Independent postcondition: the directory really exists on disk.
    assert target.is_dir()


def test_governed_move_is_verified_through_the_closed_loop(tmp_path: Any) -> None:
    source = tmp_path / "governed_src.txt"
    destination = tmp_path / "governed_dst.txt"
    source.write_text("payload", encoding="utf-8")
    registry = _register(fs.MovePathCapability())
    loop = _loop(registry, _authority(Permission.WRITE), events=[])
    task, context = _task_context()
    result = loop.run(
        task,
        fs.move_path_request(str(source), str(destination)),
        context,
    )
    assert result.is_success, result.unwrap_error()
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert not source.exists() and destination.exists()
    assert destination.read_text(encoding="utf-8") == "payload"


def test_governed_stat_is_verified_through_the_closed_loop(tmp_path: Any) -> None:
    target = tmp_path / "observed.txt"
    target.write_text("abc", encoding="utf-8")
    registry = _register(fs.StatPathCapability())
    loop = _loop(registry, _authority(Permission.READ), events=[])
    task, context = _task_context()
    result = loop.run(task, fs.stat_path_request(str(target)), context)
    assert result.is_success, result.unwrap_error()
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.task.status is TaskStatus.SUCCEEDED
    data = outcome.observation.to_dict()["data"] if outcome.observation else {}
    assert data.get("kind") == fs.PathKind.FILE.value


def test_governed_list_is_bounded_through_the_closed_loop(tmp_path: Any) -> None:
    for index in range(250):
        (tmp_path / f"entry_{index:04d}").write_text("x", encoding="utf-8")
    registry = _register(fs.ListDirectoryCapability())
    loop = _loop(registry, _authority(Permission.READ), events=[])
    task, context = _task_context()
    request = fs.list_directory_request(str(tmp_path), max_entries=100)
    result = loop.run(task, request, context)
    assert result.is_success, result.unwrap_error()
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.task.status is TaskStatus.SUCCEEDED
    data = outcome.observation.to_dict()["data"] if outcome.observation else {}
    entries = data.get("entries")
    assert isinstance(entries, list)
    assert len(entries) == 100
    assert data.get("truncated") is True
    names = [entry["name"] for entry in entries]
    assert names == sorted(names)


# --------------------------------------------------------------------------
# Denial: no authority, wrong authority, budget, emergency stop.
# --------------------------------------------------------------------------


def test_denied_write_performs_nothing(tmp_path: Any) -> None:
    target = tmp_path / "should_not_exist"
    registry = _register(fs.CreateDirectoryCapability(), fs.MovePathCapability())
    # No grant at all.
    loop = _loop(registry, None, events=[])
    task, context = _task_context()
    outcome = loop.run(task, fs.create_directory_request(str(target)), context).unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is not TaskStatus.SUCCEEDED
    assert not target.exists()


def test_write_with_only_read_grant_is_denied_and_performs_nothing(tmp_path: Any) -> None:
    target = tmp_path / "read_only_target"
    registry = _register(fs.CreateDirectoryCapability())
    loop = _loop(registry, _authority(Permission.READ), events=[])
    task, context = _task_context()
    outcome = loop.run(task, fs.create_directory_request(str(target)), context).unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert not target.exists()


def test_read_without_grant_is_denied(tmp_path: Any) -> None:
    registry = _register(fs.ListDirectoryCapability())
    loop = _loop(registry, None, events=[])
    task, context = _task_context()
    outcome = loop.run(task, fs.list_directory_request(str(tmp_path)), context).unwrap()
    assert outcome.kind is LoopOutcome.DENIED


def test_budget_denial_prevents_mutation(tmp_path: Any) -> None:
    target = tmp_path / "budget_blocked"
    registry = _register(fs.CreateDirectoryCapability())
    loop = _loop(
        registry,
        _authority(Permission.WRITE),
        events=[],
        envelope=_envelope(max_machine_actions=0),
    )
    task, context = _task_context()
    outcome = loop.run(task, fs.create_directory_request(str(target)), context).unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.error is not None and outcome.error.code == "runtime.budget_denied"
    assert not target.exists()


def test_emergency_stop_prevents_mutation(tmp_path: Any) -> None:
    target = tmp_path / "stopped"
    emergency_stop = EmergencyStop()
    emergency_stop.request_stop()
    registry = _register(fs.CreateDirectoryCapability())
    loop = _loop(
        registry,
        _authority(Permission.WRITE),
        events=[],
        emergency_stop=emergency_stop,
    )
    task, context = _task_context()
    outcome = loop.run(task, fs.create_directory_request(str(target)), context).unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.error is not None and outcome.error.code == "runtime.emergency_stop_active"
    assert not target.exists()


# --------------------------------------------------------------------------
# Verification failure prevents Task success.
# --------------------------------------------------------------------------


class _FailingVerifyCreate:
    """Wraps the real governed mkdir but always fails verification.

    This proves that even when the underlying mutation executes, Task success
    requires an independently verified postcondition and never trusts the
    execution result alone.
    """

    __slots__ = ("_inner",)

    def __init__(self) -> None:
        object.__setattr__(self, "_inner", fs.CreateDirectoryCapability())

    @property
    def descriptor(self) -> Any:
        return self._inner.descriptor

    def execute(self, request: Any, context: ExecutionContext) -> Any:
        return self._inner.execute(request, context)

    def verify(
        self, request: Any, observation: Any, context: ExecutionContext
    ) -> VerificationResult:
        return VerificationResult(
            passed=False,
            detail="deliberate verification failure injected for the test",
        )


def test_verification_failure_prevents_task_success(tmp_path: Any) -> None:
    target = tmp_path / "dir_with_failed_verification"
    registry = _register(_FailingVerifyCreate())
    loop = _loop(registry, _authority(Permission.WRITE), events=[])
    task, context = _task_context()
    result = loop.run(task, fs.create_directory_request(str(target)), context)
    assert result.is_success, result.unwrap_error()
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert outcome.task.status is not TaskStatus.SUCCEEDED
    assert outcome.verification is not None and outcome.verification.passed is False
    # Whatever the underlying effect, the governed run never reported success.
    assert outcome.verified is False
