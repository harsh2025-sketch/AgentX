"""M10 perception grounding, evidence ranking and stale-frame rejection.

This module consumes existing typed world/perception data and Windows screen
frames. Structured UIA/DOM-linked regions are preferred. Visual fallback is
bounded and OCR-free: it can localize high-contrast image regions but cannot
manufacture text or semantic authority. Grounding proposals are evidence only;
they never grant permission, lower risk, execute input, or verify task success.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from agentx.capabilities.windows.screen_capture import ScreenFrame, ScreenFrameId, ScreenRect
from agentx.world_model import PerceptionObservation, PerceptionRegion, ScreenBounds, WorldFreshness

__all__ = [
    "GroundingEvidenceKind",
    "GroundingOutcome",
    "GroundingRequest",
    "GroundingStatus",
    "PerceptionGrounder",
    "PixelContrastRegionDetector",
    "StaleScreenError",
    "VisualTargetProposal",
    "rank_grounding_evidence",
]

_MAX_VISUAL_REGIONS: Final[int] = 256
_DEFAULT_GRID: Final[int] = 32
_DEFAULT_MIN_CONTRAST: Final[int] = 36
_DEFAULT_AMBIGUITY_MARGIN: Final[float] = 0.05


class StaleScreenError(ValueError):
    """Raised when a visual proposal no longer belongs to the current frame."""


class GroundingEvidenceKind(StrEnum):
    """Closed evidence vocabulary; ordering is implemented explicitly."""

    STRUCTURED = "structured"
    VISUAL = "visual"


class GroundingStatus(StrEnum):
    GROUNDED = "grounded"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    STALE = "stale"


def rank_grounding_evidence(kind: GroundingEvidenceKind) -> int:
    """Return deterministic quality rank without changing action authority."""
    if kind is GroundingEvidenceKind.STRUCTURED:
        return 2
    if kind is GroundingEvidenceKind.VISUAL:
        return 1
    raise AssertionError(f"unhandled evidence kind: {kind!r}")


@dataclass(frozen=True, slots=True)
class GroundingRequest:
    """One target request over inert observation data."""

    query: str
    approximate_bounds: ScreenBounds | None = None
    allow_visual_fallback: bool = True
    minimum_confidence: float = 0.5

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must be non-empty")
        if len(self.query) > 512:
            raise ValueError("query exceeds bound")
        if self.approximate_bounds is not None and not isinstance(
            self.approximate_bounds, ScreenBounds
        ):
            raise TypeError("approximate_bounds must be ScreenBounds or None")
        if type(self.allow_visual_fallback) is not bool:
            raise TypeError("allow_visual_fallback must be bool")
        if isinstance(self.minimum_confidence, bool) or not isinstance(
            self.minimum_confidence, int | float
        ):
            raise TypeError("minimum_confidence must be numeric")
        value = float(self.minimum_confidence)
        if not 0.0 <= value <= 1.0:
            raise ValueError("minimum_confidence must be within [0, 1]")
        object.__setattr__(self, "minimum_confidence", value)


@dataclass(frozen=True, slots=True)
class VisualTargetProposal:
    """Evidence-backed target location tied to exactly one immutable frame."""

    frame_id: ScreenFrameId
    region_id: str
    bounds: ScreenBounds
    confidence: float
    evidence_kind: GroundingEvidenceKind
    evidence_refs: tuple[str, ...]
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.frame_id, ScreenFrameId):
            raise TypeError("frame_id must be ScreenFrameId")
        if not isinstance(self.region_id, str) or not self.region_id.strip():
            raise ValueError("region_id must be non-empty")
        if not isinstance(self.bounds, ScreenBounds):
            raise TypeError("bounds must be ScreenBounds")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, int | float):
            raise TypeError("confidence must be numeric")
        confidence = float(self.confidence)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")
        object.__setattr__(self, "confidence", confidence)
        if not isinstance(self.evidence_kind, GroundingEvidenceKind):
            raise TypeError("evidence_kind must be GroundingEvidenceKind")
        if not isinstance(self.evidence_refs, tuple):
            raise TypeError("evidence_refs must be a tuple")
        if not self.evidence_refs:
            raise ValueError("proposal requires evidence")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("evidence refs must be unique")
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))

    def assert_current(
        self,
        frame: ScreenFrame,
        *,
        at: datetime,
        max_age: timedelta,
    ) -> None:
        """Fail closed if frame identity or temporal freshness no longer matches."""
        if not isinstance(frame, ScreenFrame):
            raise TypeError("frame must be ScreenFrame")
        if not isinstance(at, datetime) or at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        if not isinstance(max_age, timedelta) or max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        if frame.frame_id != self.frame_id:
            raise StaleScreenError("target proposal belongs to a different screen frame")
        if at.astimezone(UTC) >= self.observed_at + max_age:
            raise StaleScreenError("target proposal exceeded its visual freshness window")


@dataclass(frozen=True, slots=True)
class GroundingOutcome:
    status: GroundingStatus
    proposal: VisualTargetProposal | None
    alternatives: tuple[VisualTargetProposal, ...]
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, GroundingStatus):
            raise TypeError("status must be GroundingStatus")
        if self.proposal is not None and not isinstance(self.proposal, VisualTargetProposal):
            raise TypeError("proposal must be VisualTargetProposal or None")
        if not isinstance(self.alternatives, tuple) or any(
            not isinstance(item, VisualTargetProposal) for item in self.alternatives
        ):
            raise TypeError("alternatives must contain VisualTargetProposal values")
        if self.status is GroundingStatus.GROUNDED and self.proposal is None:
            raise ValueError("grounded outcome requires a proposal")
        if self.status is not GroundingStatus.GROUNDED and self.proposal is not None:
            raise ValueError("non-grounded outcome cannot select a proposal")
        if not isinstance(self.detail, str) or not self.detail:
            raise ValueError("detail must be non-empty")


def _screen_bounds(rect: ScreenRect) -> ScreenBounds:
    return ScreenBounds(rect.x, rect.y, rect.width, rect.height)


def _luminance(pixels: bytes, index: int) -> int:
    blue = pixels[index]
    green = pixels[index + 1]
    red = pixels[index + 2]
    return (red * 77 + green * 150 + blue * 29) >> 8


class PixelContrastRegionDetector:
    """Bounded OCR-free visual fallback using coarse local contrast.

    It deliberately does not infer text, roles or instructions. It only proposes
    geometrically bounded image regions that contain enough local luminance
    contrast to be plausible visual targets.
    """

    def __init__(
        self,
        *,
        grid_size: int = _DEFAULT_GRID,
        minimum_contrast: int = _DEFAULT_MIN_CONTRAST,
        max_regions: int = _MAX_VISUAL_REGIONS,
    ) -> None:
        if type(grid_size) is not int or not 8 <= grid_size <= 256:
            raise ValueError("grid_size must be within [8, 256]")
        if type(minimum_contrast) is not int or not 1 <= minimum_contrast <= 255:
            raise ValueError("minimum_contrast must be within [1, 255]")
        if type(max_regions) is not int or not 1 <= max_regions <= _MAX_VISUAL_REGIONS:
            raise ValueError("max_regions is outside the supported bound")
        self._grid_size = grid_size
        self._minimum_contrast = minimum_contrast
        self._max_regions = max_regions

    def detect(self, frame: ScreenFrame) -> tuple[PerceptionRegion, ...]:
        if not isinstance(frame, ScreenFrame):
            raise TypeError("frame must be ScreenFrame")
        regions: list[PerceptionRegion] = []
        step = self._grid_size
        for top in range(0, frame.bounds.height, step):
            if len(regions) >= self._max_regions:
                break
            cell_height = min(step, frame.bounds.height - top)
            for left in range(0, frame.bounds.width, step):
                if len(regions) >= self._max_regions:
                    break
                cell_width = min(step, frame.bounds.width - left)
                minimum = 255
                maximum = 0
                samples = 0
                sample_step_x = max(1, cell_width // 4)
                sample_step_y = max(1, cell_height // 4)
                for y in range(top, top + cell_height, sample_step_y):
                    row = y * frame.row_stride
                    for x in range(left, left + cell_width, sample_step_x):
                        value = _luminance(frame.pixels_bgra, row + x * 4)
                        minimum = min(minimum, value)
                        maximum = max(maximum, value)
                        samples += 1
                contrast = maximum - minimum
                if samples and contrast >= self._minimum_contrast:
                    confidence = min(1.0, contrast / 255.0)
                    regions.append(
                        PerceptionRegion(
                            region_id=f"visual:{left}:{top}:{cell_width}:{cell_height}",
                            bounds=ScreenBounds(
                                frame.bounds.x + left,
                                frame.bounds.y + top,
                                cell_width,
                                cell_height,
                            ),
                            role="visual-region",
                            confidence=confidence,
                            structured_observation_ref=None,
                        )
                    )
        return tuple(regions)


def _center(bounds: ScreenBounds) -> tuple[float, float]:
    return (bounds.x + bounds.width / 2.0, bounds.y + bounds.height / 2.0)


def _distance_score(candidate: ScreenBounds, expected: ScreenBounds) -> float:
    candidate_center = _center(candidate)
    expected_center = _center(expected)
    distance = math.hypot(
        candidate_center[0] - expected_center[0],
        candidate_center[1] - expected_center[1],
    )
    diagonal = max(1.0, math.hypot(expected.width, expected.height))
    return max(0.0, 1.0 - distance / (diagonal * 4.0))


class PerceptionGrounder:
    """Structured-first bounded target grounding with explicit ambiguity."""

    def __init__(
        self,
        *,
        visual_detector: PixelContrastRegionDetector | None = None,
        ambiguity_margin: float = _DEFAULT_AMBIGUITY_MARGIN,
        max_visual_age: timedelta = timedelta(seconds=2),
    ) -> None:
        if not 0.0 <= ambiguity_margin <= 0.5:
            raise ValueError("ambiguity_margin must be within [0, 0.5]")
        if max_visual_age <= timedelta(0):
            raise ValueError("max_visual_age must be positive")
        self._visual_detector = visual_detector or PixelContrastRegionDetector()
        self._ambiguity_margin = ambiguity_margin
        self._max_visual_age = max_visual_age

    @property
    def max_visual_age(self) -> timedelta:
        return self._max_visual_age

    def _proposal(
        self,
        *,
        frame: ScreenFrame,
        observation: PerceptionObservation,
        region: PerceptionRegion,
        evidence_kind: GroundingEvidenceKind,
        confidence: float,
    ) -> VisualTargetProposal:
        references = (
            (region.structured_observation_ref,)
            if region.structured_observation_ref is not None
            else (f"frame:{frame.frame_id.value}",)
        )
        return VisualTargetProposal(
            frame_id=frame.frame_id,
            region_id=region.region_id,
            bounds=region.bounds,
            confidence=confidence,
            evidence_kind=evidence_kind,
            evidence_refs=references,
            observed_at=observation.metadata.observed_at,
        )

    def ground(
        self,
        *,
        observation: PerceptionObservation,
        frame: ScreenFrame,
        request: GroundingRequest,
        at: datetime,
    ) -> GroundingOutcome:
        if not isinstance(observation, PerceptionObservation):
            raise TypeError("observation must be PerceptionObservation")
        if not isinstance(frame, ScreenFrame):
            raise TypeError("frame must be ScreenFrame")
        if not isinstance(request, GroundingRequest):
            raise TypeError("request must be GroundingRequest")
        if observation.metadata.environment_id != frame.environment_id:
            raise ValueError("perception observation and frame cross environment scope")
        if observation.surface_id != frame.surface_id:
            raise ValueError("perception observation and frame use different surfaces")
        if observation.metadata.freshness(at) is not WorldFreshness.FRESH:
            return GroundingOutcome(
                GroundingStatus.STALE,
                None,
                (),
                "perception observation is stale; a fresh observation is required",
            )

        needle = request.query.strip().casefold()
        structured: list[VisualTargetProposal] = []
        for region in observation.regions:
            if region.structured_observation_ref is None:
                continue
            text = "" if region.text is None else region.text.strip().casefold()
            role = "" if region.role is None else region.role.strip().casefold()
            if needle not in {text, role}:
                continue
            confidence = 1.0 if region.confidence is None else region.confidence
            structured.append(
                self._proposal(
                    frame=frame,
                    observation=observation,
                    region=region,
                    evidence_kind=GroundingEvidenceKind.STRUCTURED,
                    confidence=confidence,
                )
            )
        structured.sort(key=lambda item: (-item.confidence, item.region_id))
        if structured:
            best = structured[0]
            tied = tuple(
                item
                for item in structured
                if best.confidence - item.confidence <= self._ambiguity_margin
            )
            if len(tied) > 1:
                return GroundingOutcome(
                    GroundingStatus.AMBIGUOUS,
                    None,
                    tied,
                    "multiple structured targets remain materially equivalent",
                )
            if best.confidence >= request.minimum_confidence:
                return GroundingOutcome(
                    GroundingStatus.GROUNDED,
                    best,
                    tuple(structured[1:]),
                    "structured observation uniquely grounded the target",
                )

        if not request.allow_visual_fallback:
            return GroundingOutcome(
                GroundingStatus.NOT_FOUND,
                None,
                tuple(structured),
                "structured evidence did not ground the target and visual fallback is disabled",
            )
        if request.approximate_bounds is None:
            return GroundingOutcome(
                GroundingStatus.NOT_FOUND,
                None,
                tuple(structured),
                (\n                    "visual fallback requires an explicit approximate region; "\n                    "no semantics are invented"\n                ),
            )

        visual_regions = self._visual_detector.detect(frame)
        visual: list[VisualTargetProposal] = []
        for region in visual_regions:
            region_confidence = 0.0 if region.confidence is None else region.confidence
            location_score = _distance_score(region.bounds, request.approximate_bounds)
            confidence = region_confidence * location_score
            if confidence < request.minimum_confidence:
                continue
            visual.append(
                self._proposal(
                    frame=frame,
                    observation=observation,
                    region=region,
                    evidence_kind=GroundingEvidenceKind.VISUAL,
                    confidence=confidence,
                )
            )
        visual.sort(key=lambda item: (-item.confidence, item.region_id))
        if not visual:
            return GroundingOutcome(
                GroundingStatus.NOT_FOUND,
                None,
                (),
                "bounded OCR-free visual fallback found no sufficiently supported target",
            )
        best = visual[0]
        tied = tuple(
            item for item in visual if best.confidence - item.confidence <= self._ambiguity_margin
        )
        if len(tied) > 1:
            return GroundingOutcome(
                GroundingStatus.AMBIGUOUS,
                None,
                tied,
                "visual evidence is ambiguous; action selection is refused",
            )
        return GroundingOutcome(
            GroundingStatus.GROUNDED,
            best,
            tuple(visual[1:]),
            "bounded OCR-free visual fallback uniquely localized the requested region",
        )

    def require_current(
        self,
        proposal: VisualTargetProposal,
        *,
        current_frame: ScreenFrame,
        at: datetime,
    ) -> None:
        proposal.assert_current(current_frame, at=at, max_age=self._max_visual_age)
