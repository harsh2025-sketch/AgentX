"""Real-host AX-299 benchmark across two standard Windows applications."""

from __future__ import annotations

import json
import os
import platform
import sys
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.executor import Executor, ExecutorRequest
from agentx.capabilities.human_approval import HumanApprovalDecision, HumanApprovalOutcome
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, ClosedLoopOutcome, LoopOutcome
from agentx.capabilities.windows.application_launch_native_adapter import (
    CanonicalNativeApplicationLaunchAdapter,
)
from agentx.capabilities.windows.application_launch_v2 import (
    APPLICATION_LAUNCH_V2_IDENTITY,
    ApplicationLaunchParams,
    WindowsApplicationLaunchV2Capability,
)
from agentx.capabilities.windows.native_mutation import WindowsNativeMutationAdapter
from agentx.capabilities.windows.process_discovery import (
    WindowsProcessDiscovery,
    WindowsProcessDiscoveryCapability,
    discovery_request,
)
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
        max_wall_clock=timedelta(seconds=45),
        max_model_calls=0,
        max_model_tokens=0,
        max_research_queries=0,
        max_machine_actions=24,
        max_repair_attempts=0,
        max_external_cost=Decimal("0"),
        max_risk_level=RiskLevel.R4,
    )


def test_two_standard_windows_apps_launch_and_are_independently_rediscovered() -> None:
    if (
        os.environ.get("CI", "").casefold() != "true"
        and os.environ.get("AGENTX_M6_REAL_HOST") != "1"
    ):
        pytest.skip("set AGENTX_M6_REAL_HOST=1 to opt in on a non-CI Windows host")

    root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    candidates = (
        (root / "System32" / "ping.exe", ("127.0.0.1", "-n", "7", "-w", "1000")),
        (
            root / "System32" / "waitfor.exe",
            ("/T", "6", "AgentXM6NeverSignaled"),
        ),
    )
    missing = [str(path) for path, _argv in candidates if not path.is_file()]
    assert not missing, f"AX-299 host lacks required deterministic fixture app(s): {missing}"

    support = evaluate_windows_support(detect_platform_facts())
    discovery = WindowsProcessDiscovery(support)
    surface = WindowsNativeMutationAdapter()
    launch = WindowsApplicationLaunchV2Capability(
        CanonicalNativeApplicationLaunchAdapter(surface),
        process_observer=discovery,
    )
    discovery_capability = WindowsProcessDiscoveryCapability(discovery)

    registry = CapabilityRegistry()
    registry.register(launch)
    registry.register(discovery_capability)
    bus = EventBus()
    audit: list[SecurityAuditRecord] = []
    budget = ResourceBudget(_envelope())
    loop = CapabilityExecutionLoop(
        registry=registry,
        action_gate=ActionGate(),
        authority=AuthorityContext(frozenset({Permission.READ, Permission.EXTERNAL_EFFECT})),
        emergency_stop=EmergencyStop(),
        budget=budget,
        publish_event=bus.publish,
        publish_audit=audit.append,
    )
    executor = Executor(execution_loop=loop)
    correlation_id = uuid4()

    def execute(
        request: CapabilityRequest[Any],
        objective: str,
    ) -> ClosedLoopOutcome:
        task = Task.create(objective)
        context = ExecutionContext(
            correlation_id=correlation_id,
            cancellation_token=CancellationSource().token,
            task_id=task.task_id,
        )
        needed = loop.approval_requests(task, request, context).unwrap()
        approvals = tuple(
            HumanApprovalDecision(
                request=item,
                outcome=HumanApprovalOutcome.APPROVED,
            )
            for item in needed
        )
        return executor.execute(
            ExecutorRequest(
                task=task,
                capability_request=request,
                context=context,
            ),
            approvals=approvals,
        ).unwrap()

    before = execute(discovery_request(), "M6 AX-299 discovery before launch")
    assert before.kind is LoopOutcome.VERIFIED

    launched_pids: list[int] = []
    launched_names: list[str] = []
    for path, argv in candidates:
        request = CapabilityRequest(
            identity=APPLICATION_LAUNCH_V2_IDENTITY,
            params=ApplicationLaunchParams(str(path), argv),
        )
        outcome = execute(request, f"M6 AX-299 launch {path.name}")
        assert outcome.kind is LoopOutcome.VERIFIED
        assert outcome.observation is not None
        data = outcome.observation.to_dict()["data"]
        assert isinstance(data, dict)
        process_id = data.get("process_id")
        assert type(process_id) is int and process_id > 0
        launched_pids.append(process_id)
        launched_names.append(path.name.casefold())

    after = execute(discovery_request(), "M6 AX-299 discovery after both launches")
    assert after.kind is LoopOutcome.VERIFIED
    assert after.observation is not None
    data = after.observation.to_dict()["data"]
    assert isinstance(data, dict)
    processes = data.get("processes")
    assert isinstance(processes, list)
    seen = {
        (item.get("process_id"), str(item.get("executable_name", "")).casefold())
        for item in processes
        if isinstance(item, dict)
    }
    for process_id, executable_name in zip(launched_pids, launched_names, strict=True):
        assert (process_id, executable_name) in seen

    # The fixtures terminate by themselves. Poll through the governed read-only
    # discovery capability until both exact PIDs are absent, proving cleanup
    # without a hidden TerminateProcess/taskkill bypass.
    remaining = set(launched_pids)
    for attempt in range(10):
        cleanup_observation = execute(
            discovery_request(),
            f"M6 AX-299 cleanup observation {attempt + 1}",
        )
        assert cleanup_observation.kind is LoopOutcome.VERIFIED
        assert cleanup_observation.observation is not None
        cleanup_data = cleanup_observation.observation.to_dict()["data"]
        assert isinstance(cleanup_data, dict)
        cleanup_processes = cleanup_data.get("processes")
        assert isinstance(cleanup_processes, list)
        current = {item.get("process_id") for item in cleanup_processes if isinstance(item, dict)}
        remaining.intersection_update(current)
        if not remaining:
            break
        time.sleep(1)
    assert not remaining, f"AX-299 self-terminating fixtures remained alive: {sorted(remaining)}"

    usage = budget.snapshot()
    assert usage.machine_actions >= 5
    assert len({*launched_pids}) == 2
    assert any(record.operation == "runtime.human_approval" for record in audit)

    print(
        "M6_MULTI_APP_ACCEPTANCE="
        + json.dumps(
            {
                "acceptance": "AX-299",
                "platform": platform.platform(),
                "applications": launched_names,
                "fixtures_self_terminated": True,
                "distinct_processes": len(set(launched_pids)),
                "shared_correlation": str(correlation_id),
                "shared_budget_machine_actions": usage.machine_actions,
                "governed_executor": True,
                "independent_process_rediscovery": True,
                "interactive_session_claimed": False,
            },
            sort_keys=True,
        )
    )
