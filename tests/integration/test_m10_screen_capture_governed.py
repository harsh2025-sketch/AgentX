from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from agentx.capabilities.registry import CapabilityRegistry
from agentx.capabilities.runtime import CapabilityExecutionLoop, LoopOutcome
from agentx.capabilities.windows import _screen_native
from agentx.capabilities.windows.provider import (
    PlatformFacts,
    WindowsSupport,
    evaluate_windows_support,
)
from agentx.capabilities.windows.screen_capture import (
    WindowsScreenCapture,
    WindowsScreenCaptureCapability,
    screen_capture_request,
)
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.events import Event
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.knowledge import ProvenanceKind, ProvenanceReference
from agentx.core.result import Result
from agentx.core.tasks import Task, TaskStatus
from agentx.infrastructure.event_bus import EventBus
from agentx.kernel.action_gate import ActionGate
from agentx.kernel.emergency_stop import EmergencyStop
from agentx.kernel.permissions import AuthorityContext, Permission
from agentx.kernel.resource_budget import ResourceBudget, ResourceEnvelope
from agentx.kernel.risk import RiskLevel
from agentx.perception import (
    GroundingRequest,
    GroundingStatus,
    PerceptionGrounder,
    build_perception_observation,
)
from agentx.world_model import PerceptionRegion, ScreenBounds, WorldModel

NOW = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)
SOURCE = ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="m10.governed.capture")


class FakeScreenSurface:
    def __init__(self) -> None:
        self.calls = 0

    def capture(self, *, max_pixels: int) -> Result[_screen_native.RawScreenFrame, AgentXError]:
        self.calls += 1
        assert max_pixels >= 4
        return Result.success(
            _screen_native.RawScreenFrame(
                captured_at=NOW,
                left=0,
                top=0,
                width=2,
                height=2,
                row_stride=8,
                pixels_bgra=bytes([1, 2, 3, 255] * 4),
                monitors=(
                    _screen_native.RawMonitorObservation(
                        device_name="DISPLAY1",
                        left=0,
                        top=0,
                        right=2,
                        bottom=2,
                        work_left=0,
                        work_top=0,
                        work_right=2,
                        work_bottom=2,
                        dpi_x=96,
                        dpi_y=96,
                        primary=True,
                    ),
                ),
            )
        )


def _support() -> WindowsSupport:
    return evaluate_windows_support(
        PlatformFacts(system="Windows", release="11", version="test", machine="AMD64")
    )


def _envelope(*, max_machine_actions: int = 2) -> ResourceEnvelope:
    return ResourceEnvelope(
        max_wall_clock=timedelta(seconds=30),
        max_model_calls=0,
        max_model_tokens=0,
        max_research_queries=0,
        max_machine_actions=max_machine_actions,
        max_repair_attempts=0,
        max_external_cost=Decimal("0"),
        max_risk_level=RiskLevel.R2,
    )


def _loop(
    surface: FakeScreenSurface,
    *,
    authority: AuthorityContext | None,
    max_machine_actions: int = 2,
) -> CapabilityExecutionLoop:
    registry = CapabilityRegistry()
    registry.register(
        WindowsScreenCaptureCapability(WindowsScreenCapture(_support(), native_surface=surface))
    )
    events: list[Event] = []
    bus = EventBus()
    bus.subscribe(events.append)
    return CapabilityExecutionLoop(
        registry=registry,
        action_gate=ActionGate(),
        authority=authority,
        emergency_stop=EmergencyStop(),
        budget=ResourceBudget(_envelope(max_machine_actions=max_machine_actions)),
        publish_event=bus.publish,
        publish_audit=lambda record: None,
    )


def _task_context() -> tuple[Task, ExecutionContext]:
    task = Task.create(objective="observe the current virtual desktop")
    return (
        task,
        ExecutionContext(
            correlation_id=uuid4(),
            cancellation_token=CancellationSource().token,
            task_id=task.task_id,
        ),
    )


def test_m10_screen_observation_runs_through_action_gate_budget_and_verification() -> None:
    surface = FakeScreenSurface()
    loop = _loop(
        surface,
        authority=AuthorityContext(permissions=frozenset({Permission.READ})),
    )
    task, context = _task_context()

    result = loop.run(task, screen_capture_request(environment_id="env-local"), context)

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.VERIFIED
    assert outcome.task.status is TaskStatus.SUCCEEDED
    assert outcome.verification is not None and outcome.verification.passed is True
    assert surface.calls == 1


def test_m10_screen_observation_cannot_bypass_action_gate() -> None:
    surface = FakeScreenSurface()
    loop = _loop(surface, authority=None)
    task, context = _task_context()

    result = loop.run(task, screen_capture_request(environment_id="env-local"), context)

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.task.status is not TaskStatus.SUCCEEDED
    assert surface.calls == 0


def test_m10_screen_observation_cannot_increase_resource_budget() -> None:
    surface = FakeScreenSurface()
    loop = _loop(
        surface,
        authority=AuthorityContext(permissions=frozenset({Permission.READ})),
        max_machine_actions=0,
    )
    task, context = _task_context()

    result = loop.run(task, screen_capture_request(environment_id="env-local"), context)

    assert result.is_success
    outcome = result.unwrap()
    assert outcome.kind is LoopOutcome.DENIED
    assert outcome.error is not None
    assert outcome.error.category is ErrorCategory.RESOURCE
    assert outcome.task.status is not TaskStatus.SUCCEEDED
    assert surface.calls == 0


def test_m10_governed_capture_flows_into_world_state_and_grounding() -> None:
    surface = FakeScreenSurface()
    capture = WindowsScreenCapture(_support(), native_surface=surface)
    registry = CapabilityRegistry()
    registry.register(WindowsScreenCaptureCapability(capture))
    loop = CapabilityExecutionLoop(
        registry=registry,
        action_gate=ActionGate(),
        authority=AuthorityContext(permissions=frozenset({Permission.READ})),
        emergency_stop=EmergencyStop(),
        budget=ResourceBudget(_envelope()),
        publish_event=lambda event: None,
        publish_audit=lambda record: None,
    )
    task, context = _task_context()

    result = loop.run(task, screen_capture_request(environment_id="env-local"), context)
    assert result.is_success
    assert result.unwrap().kind is LoopOutcome.VERIFIED

    frame = capture.latest_frame(
        environment_id="env-local",
        surface_id="virtual-desktop",
    )
    assert frame is not None
    region = PerceptionRegion(
        region_id="uia:save",
        bounds=ScreenBounds(0, 0, 1, 1),
        text="Save",
        role="button",
        confidence=1.0,
        structured_observation_ref="uia:save",
    )
    observation = build_perception_observation(
        frame,
        source=SOURCE,
        ttl=timedelta(seconds=5),
        regions=(region,),
        structured_observation_refs=("uia:tree-current",),
        task_id=task.task_id,
        correlation_id=context.correlation_id,
    )
    world = WorldModel()
    world.ingest_perception_observation(observation)
    lookup = world.cache.lookup(observation.entity_id, at=NOW + timedelta(seconds=1))
    assert lookup.value == observation

    grounded = PerceptionGrounder().ground(
        observation=observation,
        frame=frame,
        request=GroundingRequest(query="Save"),
        at=NOW + timedelta(seconds=1),
    )
    assert grounded.status is GroundingStatus.GROUNDED
    assert grounded.proposal is not None
    assert grounded.proposal.evidence_refs == ("uia:save",)
