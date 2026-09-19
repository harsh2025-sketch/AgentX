from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from agentx.capabilities.abi import CapabilityRequest
from agentx.capabilities.android import (
    AdbCommandResult,
    AdbTransport,
    AndroidActionParams,
    AndroidOperation,
    AndroidProvider,
)
from agentx.capabilities.device_registry import DeviceRegistry
from agentx.capabilities.executor import Executor, ExecutorRequest
from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, LoopOutcome
from agentx.core.errors import AgentXError
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.result import Result
from agentx.core.tasks import Task
from agentx.device_orchestration import (
    DeviceHandoff,
    DeviceHandoffDirection,
    DeviceHandoffExecutor,
    DeviceRequirement,
    DeviceRouter,
)
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


class GovernedAndroidRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        *,
        executable: str,
        args: tuple[str, ...],
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> Result[AdbCommandResult, AgentXError]:
        del timeout_seconds, max_output_bytes
        self.calls.append(args)
        stdout = b""
        returncode = 0
        if args == ("devices", "-l"):
            stdout = b"List of devices attached\nemulator-5554 device model:Pixel\n"
        elif "shell" in args:
            command = args[-1]
            values = {
                "getprop ro.build.version.release": b"14\n",
                "getprop ro.product.model": b"Pixel Test\n",
                "getprop ro.build.version.sdk": b"34\n",
                "pm list packages": b"package:com.example.app\n",
            }
            if command in values:
                stdout = values[command]
            else:
                returncode = 1
        return Result.success(
            AdbCommandResult(
                argv=(executable, *args),
                returncode=returncode,
                stdout=stdout,
                stderr=b"" if returncode == 0 else b"unsupported",
            )
        )


def _budget() -> ResourceBudget:
    return ResourceBudget(
        ResourceEnvelope(
            max_wall_clock=timedelta(seconds=30),
            max_model_calls=0,
            max_model_tokens=0,
            max_research_queries=0,
            max_machine_actions=10,
            max_repair_attempts=0,
            max_external_cost=Decimal("0"),
            max_risk_level=RiskLevel.R2,
        )
    )


def _executor(
    capability_registry: CapabilityRegistry,
    authority: AuthorityContext | None,
) -> Executor:
    loop = CapabilityExecutionLoop(
        registry=capability_registry,
        action_gate=ActionGate(),
        authority=authority,
        emergency_stop=EmergencyStop(),
        budget=_budget(),
        publish_event=lambda _event: None,
        publish_audit=lambda _record: None,
    )
    return Executor(execution_loop=loop)


def _task_context(task: Task) -> ExecutionContext:
    cancellation = CancellationSource()
    return ExecutionContext(
        correlation_id=uuid4(),
        cancellation_token=cancellation.token,
        task_id=task.task_id,
    )


def test_android_read_action_requires_canonical_read_permission() -> None:
    runner = GovernedAndroidRunner()
    provider = AndroidProvider(AdbTransport(runner=runner))
    capability = next(
        item
        for item in provider.capabilities()
        if item.descriptor.identity.name.value == "android.package_discovery"
    )
    registry = CapabilityRegistry()
    registry.register(capability)

    task = Task.create("discover Android packages")
    request = CapabilityRequest(
        identity=capability.descriptor.identity,
        params=AndroidActionParams(
            operation=AndroidOperation.PACKAGE_DISCOVERY,
            serial="emulator-5554",
        ),
    )
    denied = _executor(registry, AuthorityContext(frozenset())).execute(
        ExecutorRequest(
            task=task,
            capability_request=request,
            context=_task_context(task),
        )
    )
    assert denied.is_success
    assert denied.unwrap().kind is LoopOutcome.DENIED

    allowed_task = Task.create("discover Android packages with READ authority")
    allowed = _executor(
        registry,
        AuthorityContext(frozenset({Permission.READ})),
    ).execute(
        ExecutorRequest(
            task=allowed_task,
            capability_request=request,
            context=_task_context(allowed_task),
        )
    )
    assert allowed.is_success
    assert allowed.unwrap().kind is LoopOutcome.VERIFIED


def test_device_handoff_uses_canonical_executor_and_target_identity() -> None:
    runner = GovernedAndroidRunner()
    provider = AndroidProvider(AdbTransport(runner=runner))
    descriptor = provider.discover(context=_task_context(Task.create("discover")), observed_at=_T0)
    assert descriptor.is_success
    phone = descriptor.unwrap()[0]

    device_registry = DeviceRegistry()
    device_registry.observe(phone)
    router = DeviceRouter(device_registry)

    capability = next(
        item
        for item in provider.capabilities()
        if item.descriptor.identity.name.value == "android.package_discovery"
    )
    capability_registry = CapabilityRegistry()
    capability_registry.register(capability)
    executor = _executor(
        capability_registry,
        AuthorityContext(frozenset({Permission.READ})),
    )

    task = Task.create("handoff package discovery to phone")
    context = _task_context(task)
    capability_request = CapabilityRequest(
        identity=capability.descriptor.identity,
        params=AndroidActionParams(
            operation=AndroidOperation.PACKAGE_DISCOVERY,
            serial=phone.device_id.value,
        ),
    )
    request = ExecutorRequest(
        task=task,
        capability_request=capability_request,
        context=context,
    )
    handoff = DeviceHandoff(
        direction=DeviceHandoffDirection.PC_TO_PHONE,
        source_device=None,
        target_device=phone.device_id,
        task_id=task.task_id,
        capability=capability.descriptor.identity,
    )

    result = DeviceHandoffExecutor(router=router, executor=executor).execute(
        handoff=handoff,
        request=request,
        now=_T0,
        max_age=timedelta(seconds=30),
    )
    assert result.is_success
    assert result.unwrap().kind is LoopOutcome.VERIFIED

    wrong = DeviceRequirement(
        capability=capability.descriptor.identity,
        device_id=phone.device_id,
    )
    assert router.select(wrong, now=_T0, max_age=timedelta(seconds=30)).is_success


def test_android_permission_mapping_is_canonical_and_operation_specific() -> None:
    provider = AndroidProvider(AdbTransport(runner=GovernedAndroidRunner()))
    by_name = {
        item.descriptor.identity.name.value: item.descriptor for item in provider.capabilities()
    }
    assert by_name["android.package_discovery"].required_permissions == frozenset({Permission.READ})
    assert by_name["android.accessibility_tree"].required_permissions == frozenset(
        {Permission.READ}
    )
    assert by_name["android.screen_capture"].required_permissions == frozenset({Permission.READ})
    assert by_name["android.tap"].required_permissions == frozenset({Permission.EXECUTE})
    assert by_name["android.swipe"].required_permissions == frozenset({Permission.EXECUTE})
    assert by_name["android.navigation"].required_permissions == frozenset({Permission.EXECUTE})
    assert by_name["android.text_entry"].required_permissions == frozenset(
        {Permission.WRITE, Permission.EXECUTE}
    )
