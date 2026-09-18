from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from agentx.capabilities.windows.screen_capture import (
    DisplayObservation,
    ScreenFrame,
    ScreenFrameId,
    ScreenRect,
)
from agentx.core.knowledge import ProvenanceKind, ProvenanceReference
from agentx.perception import (
    GroundingRequest,
    GroundingStatus,
    PerceptionGrounder,
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

NOW = datetime(2026, 9, 18, 9, 30, tzinfo=UTC)
SOURCE = ProvenanceReference(kind=ProvenanceKind.SYSTEM, reference="benchmark.m10")


def _frame(seed: int) -> ScreenFrame:
    pixels = bytes((index + seed) % 256 for index in range(16 * 16 * 4))
    digest = hashlib.sha256(pixels).hexdigest()
    return ScreenFrame(
        frame_id=ScreenFrameId(hashlib.sha256(f"frame-{seed}".encode()).hexdigest()),
        environment_id="env-benchmark",
        surface_id="virtual-desktop",
        captured_at_iso=(NOW + timedelta(milliseconds=seed)).isoformat(),
        bounds=ScreenRect(0, 0, 16, 16),
        row_stride=64,
        pixel_digest=digest,
        pixels_bgra=pixels,
        displays=(
            DisplayObservation(
                display_id="DISPLAY1",
                bounds=ScreenRect(0, 0, 16, 16),
                work_area=ScreenRect(0, 0, 16, 16),
                dpi_x=96,
                dpi_y=96,
                primary=True,
            ),
        ),
    )


def _observation(
    frame: ScreenFrame,
    *,
    target_name: str,
    target_bounds: ScreenBounds,
    distractor: bool = False,
) -> PerceptionObservation:
    regions = [
        PerceptionRegion(
            region_id=f"target:{target_name}",
            bounds=target_bounds,
            text=target_name,
            role="button",
            confidence=0.98,
            structured_observation_ref=f"uia:{target_name}",
        )
    ]
    if distractor:
        regions.append(
            PerceptionRegion(
                region_id=f"distractor:{target_name}",
                bounds=ScreenBounds(0, 0, 2, 2),
                text="different",
                role="button",
                confidence=1.0,
                structured_observation_ref=f"uia:distractor:{target_name}",
            )
        )
    return PerceptionObservation(
        entity_id=WorldEntityId(
            "env-benchmark",
            WorldEntityKind.PERCEPTION,
            f"perception:{frame.frame_id.value}",
        ),
        metadata=ObservationMetadata(
            observation_id=f"obs:{frame.frame_id.value}",
            source=SOURCE,
            observed_at=NOW,
            ttl=timedelta(minutes=1),
            environment_id="env-benchmark",
        ),
        surface_id="virtual-desktop",
        bounds=ScreenBounds(0, 0, 16, 16),
        regions=tuple(regions),
    )


def test_ax433_layout_change_recovery_benchmark_rejects_old_target_and_rebinds() -> None:
    grounder = PerceptionGrounder()
    before_frame = _frame(1)
    after_frame = _frame(2)
    before = _observation(
        before_frame,
        target_name="Continue",
        target_bounds=ScreenBounds(1, 1, 4, 4),
    )
    after = _observation(
        after_frame,
        target_name="Continue",
        target_bounds=ScreenBounds(10, 9, 4, 4),
    )

    first = grounder.ground(
        observation=before,
        frame=before_frame,
        request=GroundingRequest(query="Continue"),
        at=NOW + timedelta(seconds=1),
    )
    assert first.status is GroundingStatus.GROUNDED
    assert first.proposal is not None

    with pytest.raises(StaleScreenError):
        grounder.require_current(
            first.proposal,
            current_frame=after_frame,
            at=NOW + timedelta(seconds=1),
        )

    recovered = grounder.ground(
        observation=after,
        frame=after_frame,
        request=GroundingRequest(query="Continue"),
        at=NOW + timedelta(seconds=1),
    )
    assert recovered.status is GroundingStatus.GROUNDED
    assert recovered.proposal is not None
    assert recovered.proposal.bounds == ScreenBounds(10, 9, 4, 4)
    assert recovered.proposal.frame_id == after_frame.frame_id


def test_ax434_perception_accuracy_benchmark_measures_twelve_ground_truth_cases() -> None:
    grounder = PerceptionGrounder()
    total = 12
    correct = 0
    for index in range(total):
        frame = _frame(index + 10)
        expected = ScreenBounds(2 + index % 3, 3 + index % 4, 4, 3)
        name = f"Target-{index}"
        observation = _observation(
            frame,
            target_name=name,
            target_bounds=expected,
            distractor=True,
        )
        outcome = grounder.ground(
            observation=observation,
            frame=frame,
            request=GroundingRequest(query=name),
            at=NOW + timedelta(seconds=1),
        )
        if (
            outcome.status is GroundingStatus.GROUNDED
            and outcome.proposal is not None
            and outcome.proposal.bounds == expected
        ):
            correct += 1

    measured_accuracy = correct / total
    assert total == 12
    assert correct == 12
    assert measured_accuracy == 1.0
