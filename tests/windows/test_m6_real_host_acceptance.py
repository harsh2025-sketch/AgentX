"""Real-host M6 acceptance: governed clipboard mutation with independent readback.

This test runs automatically on canonical Windows CI and only runs on another
Windows machine when AGENTX_M6_REAL_HOST=1 is explicitly set. It uses only the
ephemeral system clipboard and restores/clears the text state during cleanup.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.executor import Executor, ExecutorRequest
from agentx.capabilities.human_approval import HumanApprovalDecision, HumanApprovalOutcome
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, ClosedLoopOutcome, LoopOutcome
from agentx.capabilities.windows.clipboard_native import Win32ClipboardReadbackPort
from agentx.capabilities.windows.clipboard_native_adapter import CanonicalNativeClipboardPort
from agentx.capabilities.windows.input_verification import (
    VerifiedWindowsClipboardClearCapability,
    VerifiedWindowsClipboardReadTextCapability,
    VerifiedWindowsClipboardWriteTextCapability,
)
from agentx.capabilities.windows.keyboard_text_clipboard import (
    clipboard_clear_request,
    clipboard_read_request,
    clipboard_write_request,
)
from agentx.capabilities.windows.native_mutation import WindowsNativeMutationAdapter
from agentx.capabilities.windows.provider import detect_platform_facts, evaluate_windows_support
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.tasks import Task
from agentx.infrastructure.event_bus import EventBus
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.audit import SecurityAuditRecord
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="real Windows host only")


def _envelope() -> ResourceEnvelope:
    return ResourceEnvelope(
        max_wall_clock=timedelta(seconds=30),
        max_model_calls=0,
        max_model_tokens=0,
        max_research_queries=0,
        max_machine_actions=8,
        max_repair_attempts=0,
        max_external_cost=Decimal("0"),
        max_risk_level=RiskLevel.R4,
    )


def test_governed_clipboard_mutation_is_independently_verified_on_real_windows() -> None:
    if (
        os.environ.get("CI", "").casefold() != "true"
        and os.environ.get("AGENTX_M6_REAL_HOST") != "1"
    ):
        pytest.skip("set AGENTX_M6_REAL_HOST=1 to opt in on a non-CI Windows host")

    support = evaluate_windows_support(detect_platform_facts())
    assert support.is_supported

    surface = WindowsNativeMutationAdapter()
    execution_port = CanonicalNativeClipboardPort(
        surface,
        readback=Win32ClipboardReadbackPort(),
    )
    read_capability = VerifiedWindowsClipboardReadTextCapability(
        support,
        execution_port=execution_port,
        verification_port=Win32ClipboardReadbackPort(),
    )
    write_capability = VerifiedWindowsClipboardWriteTextCapability(
        support,
        execution_port=execution_port,
        verification_port=Win32ClipboardReadbackPort(),
    )
    clear_capability = VerifiedWindowsClipboardClearCapability(
        support,
        execution_port=execution_port,
        verification_port=Win32ClipboardReadbackPort(),
    )

    registry = CapabilityRegistry()
    for capability in (read_capability, write_capability, clear_capability):
        registry.register(capability)

    bus = EventBus()
    audit: list[SecurityAuditRecord] = []
    budget = ResourceBudget(_envelope())
    execution_loop = CapabilityExecutionLoop(
        registry=registry,
        action_gate=ActionGate(),
        authority=AuthorityContext(frozenset({Permission.READ, Permission.EXTERNAL_EFFECT})),
        emergency_stop=EmergencyStop(),
        budget=budget,
        publish_event=bus.publish,
        publish_audit=audit.append,
    )
    executor = Executor(execution_loop=execution_loop)
    correlation_id = uuid4()

    def execute(request: CapabilityRequest[Any]) -> ClosedLoopOutcome:
        task = Task.create("M6 isolated real-host clipboard acceptance")
        context = ExecutionContext(
            correlation_id=correlation_id,
            cancellation_token=CancellationSource().token,
            task_id=task.task_id,
        )
        approval_requests = execution_loop.approval_requests(task, request, context).unwrap()
        approvals = tuple(
            HumanApprovalDecision(
                request=item,
                outcome=HumanApprovalOutcome.APPROVED,
            )
            for item in approval_requests
        )
        return executor.execute(
            ExecutorRequest(
                task=task,
                capability_request=request,
                context=context,
            ),
            approvals=approvals,
        ).unwrap()

    original = execute(clipboard_read_request())
    assert original.kind is LoopOutcome.VERIFIED
    assert original.observation is not None
    original_data = original.observation.to_dict()["data"]
    assert isinstance(original_data, dict)
    original_has_text = original_data.get("has_text") is True
    original_text = original_data.get("text")
    assert original_text is None or isinstance(original_text, str)

    marker = "AgentX-M6-real-host-verification"
    mutated = execute(clipboard_write_request(marker))
    assert mutated.kind is LoopOutcome.VERIFIED
    assert mutated.verification is not None and mutated.verification.passed is True

    direct_readback = Win32ClipboardReadbackPort().read_text().unwrap()
    assert direct_readback.text == marker

    if original_has_text and original_text:
        cleanup = execute(clipboard_write_request(original_text))
    else:
        cleanup = execute(clipboard_clear_request())
    assert cleanup.kind is LoopOutcome.VERIFIED

    snapshot = budget.snapshot()
    assert snapshot.machine_actions >= 3
    assert any(record.operation == "runtime.action_gate" for record in audit)
    assert any(record.operation == "runtime.human_approval" for record in audit)

    report = {
        "acceptance": "AX-298",
        "platform": platform.platform(),
        "windows_release": platform.release(),
        "windows_version": platform.version(),
        "correlation_id": str(correlation_id),
        "correlation_id_valid": isinstance(correlation_id, UUID),
        "mutation": "clipboard.write_text",
        "governed_executor": True,
        "independent_readback": True,
        "verified_outcome": True,
        "machine_actions_consumed": snapshot.machine_actions,
        "interactive_session_claimed": False,
    }
    print("M6_REAL_HOST_ACCEPTANCE=" + json.dumps(report, sort_keys=True))
