from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agentx.capabilities.windows import _screen_native
from agentx.capabilities.windows.provider import (
    PlatformFacts,
    WindowsSupport,
    evaluate_windows_support,
)
from agentx.capabilities.windows.screen_capture import (
    ScreenFrame,
    ScreenRect,
    WindowsScreenCapture,
    WindowsScreenCaptureCapability,
    screen_capture_request,
)
from agentx.core.errors import AgentXError
from agentx.core.execution import CancellationSource, ExecutionContext
from agentx.core.knowledge import ProvenanceKind, ProvenanceReference
from agentx.core.result import Result
from agentx.kernel.permissions import Permission
from agentx.perception import (
    GroundingEvidenceKind,
    GroundingRequest,
    GroundingStatus,
    PerceptionGrounder,
    PixelContrastRegionDetector,
    StaleScreenError,
)
from agentx.world_model import (
    ObservationMetadata,
    PerceptionObservation,
    PerceptionRegion,
    ScreenBounds,
    WorldEntityId,
    WorldEntityKind,
)

NOW = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)
SOURCE = ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="tests.m10.screen")


def _support() -> WindowsSupport:
    return evaluate_windows_support(
        PlatformFacts(system="Windows", release="11", version="10.0.26100", machine="AMD64")
    )


def _pixels(width: int, height: int, *, changed: bool = False) -> bytes:
    data = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 4
            value = 230 if (x < width // 2 and (x + y) % 2 == 0) else 20
            if changed and x == width - 1 and y == height - 1:
                value = 111
            data[offset : offset + 4] = bytes((value, value, value, 255))
    return bytes(data)


class FakeScreenSurface:
    def __init__(self, raw: _screen_native.RawScreenFrame) -> None:
        self.raw = raw
        self.calls = 0

    def capture(
        self, *, max_pixels: int
    ) -> Result[_screen_native.RawScreenFrame, AgentXError]:
        self.calls += 1
        assert self.raw.width * self.raw.height <= max_pixels
        return Result.success(self.raw)


def _raw(*, changed: bool = False) -> _screen_native.RawScreenFrame:
    width = 32
    height = 32
    return _screen_native.RawScreenFrame(
        captured_at=NOW + (timedelta(milliseconds=1) if changed else timedelta()),
        left=-16,
        top=0,
        width=width,
        height=height,
        row_stride=width * 4,
        pixels_bgra=_pixels(width, height, changed=changed),
        monitors=(
            _screen_native.RawMonitorObservation(
                device_name="DISPLAY1",
                left=-16,
                top=0,
                right=0,
                bottom=32,
                work_left=-16,
                work_top=0,
                work_right=0,
                work_bottom=32,
                dpi_x=96,
                dpi_y=96,
                primary=False,
            ),
            _screen_native.RawMonitorObservation(
                device_name="DISPLAY2",
                left=0,
                top=0,
                right=16,
                bottom=32,
                work_left=0,
                work_top=0,
                work_right=16,
                work_bottom=32,
                dpi_x=144,
                dpi_y=144,
                primary=True,
            ),
        ),
    )


def _frame(*, changed: bool = False) -> ScreenFrame:
    result = WindowsScreenCapture(
        _support(), native_surface=FakeScreenSurface(_raw(changed=changed))
    ).capture(environment_id="env-local")
    assert result.is_success
    return result.unwrap()


def _observation(
    frame: ScreenFrame,
    *,
    regions: tuple[PerceptionRegion, ...],
    observed_at: datetime = NOW,
) -> PerceptionObservation:
    return PerceptionObservation(
        entity_id=WorldEntityId(
            "env-local",
            WorldEntityKind.PERCEPTION,
            f"frame:{frame.frame_id.value}",
        ),
        metadata=ObservationMetadata(
            observation_id=f"obs:{frame.frame_id.value}",
            source=SOURCE,
            observed_at=observed_at,
            ttl=timedelta(seconds=5),
            environment_id="env-local",
        ),
        surface_id=frame.surface_id,
        bounds=ScreenBounds(
            frame.bounds.x,
            frame.bounds.y,
            frame.bounds.width,
            frame.bounds.height,
        ),
        regions=regions,
    )


def test_ax422_ax423_capture_is_bounded_read_only_and_frame_identity_changes() -> None:
    surface = FakeScreenSurface(_raw())
    capture = WindowsScreenCapture(_support(), native_surface=surface)
    first = capture.capture(environment_id="env-local").unwrap()
    same = capture.capture(environment_id="env-local").unwrap()
    changed = (
        WindowsScreenCapture(_support(), native_surface=FakeScreenSurface(_raw(changed=True)))
        .capture(environment_id="env-local")
        .unwrap()
    )

    assert surface.calls == 2
    assert first.frame_id == same.frame_id
    assert first.frame_id != changed.frame_id
    assert first.pixel_digest != changed.pixel_digest
    capability = WindowsScreenCaptureCapability(capture)
    assert capability.descriptor.required_permissions == frozenset({Permission.READ})
    assert capability.descriptor.risk_assessment.level.value == "R0"


def test_ax431_ax432_dpi_and_multi_monitor_coordinates_are_explicit() -> None:
    frame = _frame()
    assert frame.bounds == ScreenRect(-16, 0, 32, 32)
    assert len(frame.displays) == 2
    primary = next(display for display in frame.displays if display.primary)
    secondary = next(display for display in frame.displays if not display.primary)

    assert primary.scale_x == 1.5
    assert primary.logical_to_physical(4.0, 4.0) == (6, 6)
    assert primary.physical_to_logical(6, 6) == (4.0, 4.0)
    assert secondary.bounds.x == -16
    assert frame.display_for_point(-1, 5) is secondary
    assert frame.display_for_point(1, 5) is primary


def test_ax422_capture_capability_honors_cancellation_without_native_read() -> None:
    surface = FakeScreenSurface(_raw())
    capability = WindowsScreenCaptureCapability(
        WindowsScreenCapture(_support(), native_surface=surface)
    )
    source = CancellationSource()
    source.cancel("stop requested")
    context = ExecutionContext(correlation_id=uuid4(), cancellation_token=source.token)

    result = capability.execute(screen_capture_request(environment_id="env-local"), context)

    assert result.succeeded is False
    assert surface.calls == 0


def test_ax425_ax428_structured_evidence_beats_visual_fallback() -> None:
    frame = _frame()
    observation = _observation(
        frame,
        regions=(
            PerceptionRegion(
                region_id="structured-save",
                bounds=ScreenBounds(2, 2, 8, 8),
                text="Save",
                role="button",
                confidence=0.8,
                structured_observation_ref="uia:window-7:path-2",
            ),
        ),
    )
    grounder = PerceptionGrounder(
        visual_detector=PixelContrastRegionDetector(
            grid_size=16, minimum_contrast=10, max_regions=8
        )
    )

    outcome = grounder.ground(
        observation=observation,
        frame=frame,
        request=GroundingRequest(
            query="Save",
            approximate_bounds=ScreenBounds(-16, 0, 16, 16),
            minimum_confidence=0.1,
        ),
        at=NOW + timedelta(seconds=1),
    )

    assert outcome.status is GroundingStatus.GROUNDED
    assert outcome.proposal is not None
    assert outcome.proposal.region_id == "structured-save"
    assert outcome.proposal.evidence_kind is GroundingEvidenceKind.STRUCTURED


def test_ax426_ax427_ocr_free_visual_fallback_localizes_without_inventing_text() -> None:
    frame = _frame()
    observation = _observation(frame, regions=())
    grounder = PerceptionGrounder(
        visual_detector=PixelContrastRegionDetector(
            grid_size=16, minimum_contrast=10, max_regions=8
        ),
        ambiguity_margin=0.0,
    )

    outcome = grounder.ground(
        observation=observation,
        frame=frame,
        request=GroundingRequest(
            query="unavailable semantic label",
            approximate_bounds=ScreenBounds(-16, 0, 16, 16),
            minimum_confidence=0.1,
        ),
        at=NOW + timedelta(seconds=1),
    )

    assert outcome.status is GroundingStatus.GROUNDED
    assert outcome.proposal is not None
    assert outcome.proposal.evidence_kind is GroundingEvidenceKind.VISUAL
    assert outcome.proposal.evidence_refs == (f"frame:{frame.frame_id.value}",)


def test_ax429_structured_ambiguity_refuses_to_choose() -> None:
    frame = _frame()
    observation = _observation(
        frame,
        regions=(
            PerceptionRegion(
                "a",
                ScreenBounds(0, 0, 8, 8),
                text="Open",
                confidence=0.9,
                structured_observation_ref="uia:a",
            ),
            PerceptionRegion(
                "b",
                ScreenBounds(12, 0, 8, 8),
                text="Open",
                confidence=0.9,
                structured_observation_ref="uia:b",
            ),
        ),
    )

    outcome = PerceptionGrounder().ground(
        observation=observation,
        frame=frame,
        request=GroundingRequest(query="Open"),
        at=NOW + timedelta(seconds=1),
    )

    assert outcome.status is GroundingStatus.AMBIGUOUS
    assert outcome.proposal is None
    assert {item.region_id for item in outcome.alternatives} == {"a", "b"}


def test_ax430_stale_or_replaced_screen_proposal_is_rejected() -> None:
    frame = _frame()
    changed = _frame(changed=True)
    observation = _observation(
        frame,
        regions=(
            PerceptionRegion(
                "target",
                ScreenBounds(0, 0, 8, 8),
                text="Run",
                confidence=1.0,
                structured_observation_ref="uia:run",
            ),
        ),
    )
    grounder = PerceptionGrounder(max_visual_age=timedelta(seconds=2))
    outcome = grounder.ground(
        observation=observation,
        frame=frame,
        request=GroundingRequest(query="Run"),
        at=NOW + timedelta(seconds=1),
    )
    assert outcome.proposal is not None

    with pytest.raises(StaleScreenError, match="different screen frame"):
        grounder.require_current(
            outcome.proposal,
            current_frame=changed,
            at=NOW + timedelta(seconds=1),
        )
    with pytest.raises(StaleScreenError, match="freshness"):
        grounder.require_current(
            outcome.proposal,
            current_frame=frame,
            at=NOW + timedelta(seconds=3),
        )
