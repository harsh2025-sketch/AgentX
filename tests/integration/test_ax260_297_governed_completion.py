"""Governed integration acceptance for AX-260–265 and AX-296–297."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from agentx.capabilities.abi import Capability
from agentx.capabilities.filesystem_structural import (
    FilesystemCreateDirectoryCapability,
    FilesystemDeleteFileCapability,
    FilesystemMoveFileCapability,
    create_directory_request,
    delete_file_request,
    move_file_request,
)
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, LoopOutcome
from agentx.capabilities.windows.input_verification import (
    IndependentVerificationEvidence,
    VerifiedWindowsClipboardWriteTextCapability,
    VerifiedWindowsSendTextCapability,
)
from agentx.capabilities.windows.keyboard_text_clipboard import (
    KeyChord,
    RawClipboardClear,
    RawClipboardText,
    RawClipboardWrite,
    RawKeySend,
    RawTextSend,
    clipboard_write_request,
    send_text_request,
)
from agentx.core.errors import AgentXError
from agentx.core.events import Event
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import Task, TaskStatus
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.audit import SecurityAuditRecord
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel
from tests.support.fake_windows_native import windows_support


def _envelope() -> ResourceEnvelope:
    return ResourceEnvelope(
        max_wall_clock=timedelta(seconds=30),
        max_model_calls=0,
        max_model_tokens=0,
        max_research_queries=0,
        max_machine_actions=16,
        max_repair_attempts=0,
        max_external_cost=Decimal("0"),
        max_risk_level=RiskLevel.R4,
    )


def _task_context(objective: str) -> tuple[Task, ExecutionContext]:
    task = Task.create(objective=objective)
    return task, ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=CancellationSource().token,
        task_id=task.task_id,
    )


def _loop(
    capability: Capability[Any],
    permissions: frozenset[Permission],
) -> tuple[CapabilityExecutionLoop, list[Event], list[SecurityAuditRecord]]:
    registry = CapabilityRegistry()
    registry.register(capability)
    events: list[Event] = []
    audit: list[SecurityAuditRecord] = []
    loop = CapabilityExecutionLoop(
        registry=registry,
        action_gate=ActionGate(),
        authority=AuthorityContext(permissions=permissions),
        emergency_stop=EmergencyStop(),
        budget=ResourceBudget(_envelope()),
        publish_event=events.append,
        publish_audit=audit.append,
    )
    return loop, events, audit


def test_create_directory_runs_through_canonical_loop_and_reaches_verified_success(
    tmp_path: Path,
) -> None:
    target = tmp_path / "created"
    loop, events, audit = _loop(
        FilesystemCreateDirectoryCapability(),
        frozenset({Permission.WRITE}),
    )
    task, context = _task_context("create a governed directory")

    result = loop.run(task, create_directory_request(str(target.resolve())), context)

    assert result.is_success, result.unwrap_error()
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert outcome.verification is not None and outcome.verification.passed
    assert target.is_dir()
    assert events
    assert audit


def test_move_file_runs_through_canonical_loop_with_independent_identity_verification(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source-α.txt"
    destination = tmp_path / "destination-β.txt"
    payload = b"governed-move-exact-bytes"
    source.write_bytes(payload)
    loop, _, _ = _loop(FilesystemMoveFileCapability(), frozenset({Permission.WRITE}))
    task, context = _task_context("move a governed file")

    result = loop.run(
        task,
        move_file_request(str(source.resolve()), str(destination.resolve())),
        context,
    )

    assert result.is_success, result.unwrap_error()
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert destination.read_bytes() == payload
    assert not source.exists()


def test_destructive_file_delete_is_not_silently_executed_even_with_destructive_authority(
    tmp_path: Path,
) -> None:
    target = tmp_path / "must-remain.txt"
    target.write_text("preserve until confirmed", encoding="utf-8")
    loop, _, audit = _loop(
        FilesystemDeleteFileCapability(),
        frozenset({Permission.DESTRUCTIVE}),
    )
    task, context = _task_context("delete a file requiring confirmation")

    result = loop.run(task, delete_file_request(str(target.resolve())), context)

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is TaskStatus.FAILED
    assert outcome.execution is None
    assert target.read_text(encoding="utf-8") == "preserve until confirmed"
    assert any("REQUIRE_CONFIRMATION" in record.reason for record in audit)


@dataclass(slots=True)
class _ClipboardState:
    text: str | None = None
    writes: int = 0


class _ClipboardExecutionPort:
    def __init__(self, state: _ClipboardState) -> None:
        self.state = state

    def read_text(self) -> Result[RawClipboardText, AgentXError]:
        return Result.success(RawClipboardText(text=self.state.text))

    def write_text(self, text: str) -> Result[RawClipboardWrite, AgentXError]:
        self.state.writes += 1
        self.state.text = text
        return Result.success(RawClipboardWrite(character_count=len(text)))

    def clear(self) -> Result[RawClipboardClear, AgentXError]:
        previous_was_empty = self.state.text is None
        self.state.text = None
        return Result.success(RawClipboardClear(previous_was_empty=previous_was_empty))


class _ClipboardReadbackPort:
    def __init__(self, state: _ClipboardState) -> None:
        self.state = state

    @property
    def source(self) -> str:
        return "integration.clipboard.readback"

    def read_text(self) -> Result[RawClipboardText, AgentXError]:
        return Result.success(RawClipboardText(text=self.state.text))


@dataclass(slots=True)
class _KeyboardState:
    text: str | None = None
    sends: int = 0


class _KeyboardExecutionPort:
    def __init__(self, state: _KeyboardState) -> None:
        self.state = state

    def send_text(self, text: str) -> Result[RawTextSend, AgentXError]:
        self.state.sends += 1
        self.state.text = text
        return Result.success(RawTextSend(character_count=len(text)))

    def send_keys(self, chords: tuple[KeyChord, ...]) -> Result[RawKeySend, AgentXError]:
        return Result.success(RawKeySend(chord_count=len(chords)))


class _KeyboardVerifier:
    def __init__(self, state: _KeyboardState) -> None:
        self.state = state

    def verify_text_entry(
        self,
        expected_text: str,
    ) -> Result[IndependentVerificationEvidence, AgentXError]:
        return Result.success(
            IndependentVerificationEvidence(
                passed=self.state.text == expected_text,
                source="integration.keyboard.independent_state",
                evidence_reference=UUID(int=101),
            )
        )

    def verify_key_sequence(
        self,
        expected_chords: tuple[KeyChord, ...],
    ) -> Result[IndependentVerificationEvidence, AgentXError]:
        return Result.success(
            IndependentVerificationEvidence(
                passed=False,
                source="integration.keyboard.independent_state",
                evidence_reference=UUID(int=102),
            )
        )


def test_r3_clipboard_action_is_stopped_by_kernel_before_native_execution() -> None:
    state = _ClipboardState()
    capability = VerifiedWindowsClipboardWriteTextCapability(
        windows_support(),
        execution_port=_ClipboardExecutionPort(state),
        verification_port=_ClipboardReadbackPort(state),
    )
    loop, _, audit = _loop(capability, frozenset({Permission.EXTERNAL_EFFECT}))
    task, context = _task_context("write clipboard after trusted confirmation")

    result = loop.run(task, clipboard_write_request("sensitive value"), context)

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.execution is None
    assert state.writes == 0
    assert state.text is None
    assert any("REQUIRE_CONFIRMATION" in record.reason for record in audit)


def test_r3_keyboard_action_is_stopped_by_kernel_before_native_execution() -> None:
    state = _KeyboardState()
    capability = VerifiedWindowsSendTextCapability(
        windows_support(),
        execution_port=_KeyboardExecutionPort(state),
        verification_port=_KeyboardVerifier(state),
    )
    loop, _, audit = _loop(capability, frozenset({Permission.EXTERNAL_EFFECT}))
    task, context = _task_context("send text after trusted confirmation")

    result = loop.run(task, send_text_request("typed only after confirmation"), context)

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.execution is None
    assert state.sends == 0
    assert state.text is None
    assert any("REQUIRE_CONFIRMATION" in record.reason for record in audit)
