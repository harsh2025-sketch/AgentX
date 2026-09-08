from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentx.capabilities.abi import (
    Capability,
    CapabilityDescriptor,
    CapabilityObservation,
    CapabilityRequest,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.filesystem import (
    FilesystemReadTextCapability,
    FilesystemWriteTextCapability,
    WriteTextParams,
    read_text_request,
    write_text_request,
)
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, LoopOutcome
from agentx.core.events import Event
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.audit import SecurityAuditRecord
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel


def _envelope(**overrides: Any) -> ResourceEnvelope:
    values: dict[str, Any] = {
        "max_wall_clock": timedelta(seconds=30),
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


def _task_context(objective: str) -> tuple[Task, ExecutionContext]:
    task = Task.create(objective=objective)
    context = ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=task.task_id,
    )
    return task, context


def _loop(
    capability: Capability[Any],
    *,
    permissions: frozenset[Permission] | None,
    envelope: ResourceEnvelope | None = None,
    emergency_stop: EmergencyStop | None = None,
) -> tuple[CapabilityExecutionLoop, list[Event], list[SecurityAuditRecord]]:
    registry = CapabilityRegistry()
    registry.register(capability)
    events: list[Event] = []
    audit: list[SecurityAuditRecord] = []
    authority = None if permissions is None else AuthorityContext(permissions=permissions)
    loop = CapabilityExecutionLoop(
        registry=registry,
        action_gate=ActionGate(),
        authority=authority,
        emergency_stop=emergency_stop if emergency_stop is not None else EmergencyStop(),
        budget=ResourceBudget(envelope if envelope is not None else _envelope()),
        publish_event=events.append,
        publish_audit=audit.append,
    )
    return loop, events, audit


def test_authorized_write_runs_through_real_governed_loop(tmp_path: Path) -> None:
    target = tmp_path / "governed-write.txt"
    loop, events, audit = _loop(
        FilesystemWriteTextCapability(),
        permissions=frozenset({Permission.WRITE}),
    )
    task, context = _task_context("create governed text file")

    result = loop.run(
        task,
        write_text_request(str(target), content="governed", overwrite=False),
        context,
    )

    assert result.is_success, result.unwrap_error()
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert outcome.verification is not None and outcome.verification.passed is True
    assert target.read_bytes() == b"governed"
    assert events
    assert audit


def test_authorized_read_runs_through_real_governed_loop(tmp_path: Path) -> None:
    target = tmp_path / "governed-read.txt"
    target.write_bytes("नमस्ते".encode("utf-8"))
    loop, _, _ = _loop(
        FilesystemReadTextCapability(),
        permissions=frozenset({Permission.READ}),
    )
    task, context = _task_context("read governed text file")

    result = loop.run(task, read_text_request(str(target), max_bytes=64), context)

    assert result.is_success, result.unwrap_error()
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert outcome.observation is not None
    data = outcome.observation.to_dict()["data"]
    assert isinstance(data, dict)
    assert data["text"] == "नमस्ते"


def test_write_is_denied_without_explicit_write_authority(tmp_path: Path) -> None:
    target = tmp_path / "denied.txt"
    loop, _, _ = _loop(
        FilesystemWriteTextCapability(),
        permissions=frozenset({Permission.READ}),
    )
    task, context = _task_context("attempt denied write")

    result = loop.run(
        task,
        write_text_request(str(target), content="must not exist", overwrite=False),
        context,
    )

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.execution is None
    assert not target.exists()


class _MutatingWriteCapability:
    """Test seam that changes state between real execution and real verification."""

    __slots__ = ("_delegate", "_path")

    def __init__(self, path: Path) -> None:
        self._delegate = FilesystemWriteTextCapability()
        self._path = path

    @property
    def descriptor(self) -> CapabilityDescriptor:
        return self._delegate.descriptor

    def execute(
        self,
        request: CapabilityRequest[WriteTextParams],
        context: ExecutionContext,
    ) -> ExecutionResult:
        execution = self._delegate.execute(request, context)
        if execution.succeeded:
            self._path.write_bytes(b"mutated-between-execute-and-verify")
        return execution

    def verify(
        self,
        request: CapabilityRequest[WriteTextParams],
        observation: CapabilityObservation,
        context: ExecutionContext,
    ) -> VerificationResult:
        return self._delegate.verify(request, observation, context)


def test_verification_failure_never_becomes_task_success(tmp_path: Path) -> None:
    target = tmp_path / "verification-failure.txt"
    loop, _, _ = _loop(
        _MutatingWriteCapability(target),
        permissions=frozenset({Permission.WRITE}),
    )
    task, context = _task_context("prove post-action verification boundary")

    result = loop.run(
        task,
        write_text_request(str(target), content="intended", overwrite=False),
        context,
    )

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFICATION_FAILED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.verification is not None
    assert outcome.verification.passed is False
    assert outcome.verified is False


def test_budget_denial_happens_before_write_execution(tmp_path: Path) -> None:
    target = tmp_path / "budget-denied.txt"
    loop, _, _ = _loop(
        FilesystemWriteTextCapability(),
        permissions=frozenset({Permission.WRITE}),
        envelope=_envelope(max_machine_actions=1),
    )
    task, context = _task_context("write over machine-action budget")

    result = loop.run(
        task,
        write_text_request(str(target), content="blocked", overwrite=False),
        context,
    )

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.execution is None
    assert not target.exists()


def test_emergency_stop_halts_before_write_execution(tmp_path: Path) -> None:
    target = tmp_path / "stopped.txt"
    stop = EmergencyStop()
    stop.request_stop()
    loop, _, _ = _loop(
        FilesystemWriteTextCapability(),
        permissions=frozenset({Permission.WRITE}),
        emergency_stop=stop,
    )
    task, context = _task_context("write while emergency stop is active")

    result = loop.run(
        task,
        write_text_request(str(target), content="blocked", overwrite=False),
        context,
    )

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.CANCELLED
    assert outcome.execution is None
    assert not target.exists()
