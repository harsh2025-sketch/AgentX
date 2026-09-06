"""C5.06 provider-neutral screen/perception representation boundary.

This module is the canonical inert DATA MODEL for visual/perception
observations: what a screen, window, or page capture observation *is*, in a
form any provider (browser raster provider, Windows screen provider, or a
future capture implementation) can produce and any consumer can validate.
It contains representation, validation, and deterministic serialization only.

It is not a vision model, does no capture, performs no OCR, recognizes no
content, grounds nothing, fuses nothing with DOM/UIA data, and executes no
browser, Windows, or machine action. There is no reader, no request, no
provider port, and no capability here; read/capture request contracts belong
to later tasks.

Represented content
-------------------

* :class:`PerceptionProviderId` -- canonical provider identity (same
  lowercase dotted grammar as the C5.01 browser-provider identity, so a
  browser, Windows, or future provider can map its canonical identity 1:1).
* :class:`PerceptionSourceId` -- provider-scoped source/capture reference
  (which screen region/window/target the observation describes).
* :class:`PerceptionObservationId` -- source-scoped identity of one
  observation snapshot.
* :class:`PerceptionCandidateId` -- observation-scoped identity of one
  candidate visual element.
* :class:`PerceptionCoordinateSpace` -- declared coordinate convention with
  explicit ``unknown``/``unsupported`` members.
* :class:`PerceptionCanvas` -- validated raster image dimensions.
* :class:`PerceptionBoundingBox` -- validated region geometry.
* :class:`PerceptionCandidate` -- one candidate element: optional region,
  untrusted label/text, bounded confidence, optional parent grouping link.
* :class:`PerceptionObservation` -- the immutable versioned envelope with
  explicit state/freshness, provenance to an optional captured C2.04
  artifact, and ordered candidates.

Canonical coordinate convention
-------------------------------

The only supported coordinate space is ``IMAGE_PIXELS_TOP_LEFT``:

* coordinates are measured in the pixels of the observed raster (the
  capture), not in physical screen, CSS, or device-independent units;
* the origin ``(0, 0)`` is the top-left pixel of the raster;
* the x axis increases rightward and the y axis increases downward;
* a region is fully contained when ``0 <= x``, ``0 <= y``,
  ``x + width <= canvas.width`` and ``y + height <= canvas.height``.

Geometry whose convention is not declared, or that uses a convention this
canonical model cannot interpret, is represented explicitly as
``UNKNOWN``/``UNSUPPORTED`` and then carries **no** canvas and **no**
candidates (fails closed). Mapping raster geometry to DOM, UIA, physical
screen, or input coordinates belongs to later grounding/fusion tasks, not
here.

Determinism
-----------

Identities are deterministic and provider-scoped (never randomly generated).
Candidate order is the provider's supplied order and is preserved because
perception reading order is semantic; duplicates are rejected. Geometry
values are stored as normalized finite floats (negative zero becomes zero).
Timestamps are normalized to UTC. JSON serialization is deterministic
(sorted keys, compact separators, ``allow_nan=False``). Unknown JSON fields
and unsupported schema versions are rejected, never silently dropped or
guessed at.

Trust and authority
-------------------

Visual labels, extracted text, provider identifiers, and detail strings are
**untrusted data**. They are bounded, inert strings that authorize nothing:
they cannot grant permission, become instructions, change state, lower risk,
clear an emergency stop, fabricate verification, or register anything. A
candidate visual element is a provider proposal only. It is NOT
automatically live, interactable, safe, authorized, or verified, and this
model deliberately carries no field that claims otherwise. Confidence is an
optional finite value in ``[0.0, 1.0]``; ``None`` means the provider supplied
none. State values are snapshot facts supplied by the caller, never liveness
proof or authority.

Owner: C5.06. Belongs to ``agentx.capabilities``; imports only the standard
library plus the canonical ``agentx.core.ids`` artifact identity for the
optional durable-capture evidence link.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from agentx.core.ids import ArtifactId

__all__ = [
    "CANONICAL_PERCEPTION_COORDINATE_SPACES",
    "CANONICAL_PERCEPTION_OBSERVATION_STATES",
    "PERCEPTION_SCHEMA_VERSION",
    "PerceptionBoundingBox",
    "PerceptionCandidate",
    "PerceptionCandidateId",
    "PerceptionCanvas",
    "PerceptionCoordinateSpace",
    "PerceptionObservation",
    "PerceptionObservationId",
    "PerceptionObservationState",
    "PerceptionProviderId",
    "PerceptionSourceId",
    "PerceptionValidationError",
    "UnsupportedPerceptionSchemaVersionError",
]

PERCEPTION_SCHEMA_VERSION: Final[int] = 1

_MAX_PROVIDER_ID_LENGTH: Final[int] = 128
_MAX_OPAQUE_ID_LENGTH: Final[int] = 512
_MAX_DETAIL_LENGTH: Final[int] = 1_024
_MAX_LABEL_LENGTH: Final[int] = 16_384
_MAX_TEXT_LENGTH: Final[int] = 1_048_576
_MAX_CANVAS_DIMENSION: Final[int] = 16_777_216
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")
_PROVIDER_ID_PATTERN: Final = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")


class PerceptionValidationError(ValueError):
    """Raised when C5.06 perception representation data is malformed."""


class UnsupportedPerceptionSchemaVersionError(PerceptionValidationError):
    """Raised when encoded perception data uses an unsupported schema version."""


class PerceptionObservationState(StrEnum):
    """Explicit freshness/availability of one perception observation snapshot.

    Values are snapshot facts supplied by a capture provider. They never prove
    liveness of the observed content and never authorize interaction.
    """

    OBSERVED = "observed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


CANONICAL_PERCEPTION_OBSERVATION_STATES: Final[tuple[PerceptionObservationState, ...]] = (
    PerceptionObservationState.OBSERVED,
    PerceptionObservationState.STALE,
    PerceptionObservationState.UNAVAILABLE,
)


class PerceptionCoordinateSpace(StrEnum):
    """Declared coordinate convention for perception geometry.

    ``IMAGE_PIXELS_TOP_LEFT`` is the only supported canonical space (see the
    module docstring for its exact semantics). ``UNKNOWN`` means the provider
    did not declare a convention; ``UNSUPPORTED`` means the provider declared
    a convention this canonical model cannot interpret. Neither unsupported
    member carries geometry: an observation in either state contains no
    canvas and no candidates.
    """

    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    IMAGE_PIXELS_TOP_LEFT = "image_pixels_top_left"


CANONICAL_PERCEPTION_COORDINATE_SPACES: Final[tuple[PerceptionCoordinateSpace, ...]] = (
    PerceptionCoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
)


def _validate_opaque_id(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise PerceptionValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > _MAX_OPAQUE_ID_LENGTH:
        raise PerceptionValidationError(
            f"{field_name} must not exceed {_MAX_OPAQUE_ID_LENGTH} characters"
        )
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise PerceptionValidationError(f"{field_name} must not contain control characters")
    return value


def _validate_untrusted_text(
    value: object,
    *,
    field_name: str,
    max_length: int,
    allow_none: bool = True,
) -> str | None:
    """Bound untrusted perceptual text without interpreting its content."""
    if value is None:
        if allow_none:
            return None
        raise TypeError(f"{field_name} must be a string")
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string or None, got {type(value).__name__}")
    if len(value) > max_length:
        raise PerceptionValidationError(f"{field_name} must not exceed {max_length} characters")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise PerceptionValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise PerceptionValidationError(f"{field_name} must be an ISO-8601 string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PerceptionValidationError(f"{field_name} must be a valid ISO-8601 datetime") from exc
    return _validate_timestamp(parsed, field_name=field_name)


def _require_string(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise PerceptionValidationError(f"{field_name} must be a string")
    return value


def _require_string_mapping(value: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PerceptionValidationError(f"{field_name} must be a JSON object")
    copied: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise PerceptionValidationError(f"{field_name} contains a non-string object key")
        copied[key] = item
    return copied


def _require_exact_keys(
    raw: Mapping[str, object],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    context: str,
) -> None:
    actual = set(raw)
    missing = required - actual
    unknown = actual - required - optional
    if missing:
        raise PerceptionValidationError(f"{context} missing required fields: {sorted(missing)}")
    if unknown:
        raise PerceptionValidationError(f"{context} contains unknown fields: {sorted(unknown)}")


def _validate_schema_version(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("schema_version must be an integer")
    if value != PERCEPTION_SCHEMA_VERSION:
        raise UnsupportedPerceptionSchemaVersionError(
            f"unsupported perception schema version {value}; "
            f"supported version is {PERCEPTION_SCHEMA_VERSION}"
        )
    return value


def _normalize_real(value: object, *, field_name: str) -> float:
    """Return a finite normalized float from an int/float, rejecting bools."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{field_name} must be a real number, got {type(value).__name__}")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise PerceptionValidationError(f"{field_name} must be finite")
    return normalized + 0.0


def _require_real_number(value: object, *, field_name: str) -> float:
    """Parse-path variant of ``_normalize_real`` with validation errors."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PerceptionValidationError(f"{field_name} must be a real number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise PerceptionValidationError(f"{field_name} must be finite")
    return normalized + 0.0


def _validate_confidence(value: object) -> float | None:
    if value is None:
        return None
    normalized = _normalize_real(value, field_name="confidence")
    if not 0.0 <= normalized <= 1.0:
        raise PerceptionValidationError("confidence must be within the closed interval [0.0, 1.0]")
    return normalized


@dataclass(frozen=True, slots=True, order=True)
class PerceptionProviderId:
    """Stable deterministic identity for one perception provider."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError(f"value must be a string, got {type(self.value).__name__}")
        if not self.value or self.value != self.value.strip():
            raise PerceptionValidationError("perception provider id must be non-empty and trimmed")
        if len(self.value) > _MAX_PROVIDER_ID_LENGTH:
            raise PerceptionValidationError(
                f"perception provider id must not exceed {_MAX_PROVIDER_ID_LENGTH} characters"
            )
        if any(character in self.value for character in _CONTROL_CHARACTERS):
            raise PerceptionValidationError(
                "perception provider id must not contain control characters"
            )
        if _PROVIDER_ID_PATTERN.fullmatch(self.value) is None:
            raise PerceptionValidationError(
                "perception provider id must be lowercase alphanumeric segments separated by "
                f"single '._-' characters, got {self.value!r}"
            )

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> dict[str, object]:
        return {"provider_id": self.value}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> PerceptionProviderId:
        mapping = _require_string_mapping(raw, field_name="provider identity")
        _require_exact_keys(
            mapping, required=frozenset({"provider_id"}), context="provider identity"
        )
        return cls(_require_string(mapping["provider_id"], field_name="provider_id"))


@dataclass(frozen=True, slots=True, order=True)
class PerceptionSourceId:
    """Provider-scoped source/capture reference for perception content.

    ``value`` is an opaque provider-supplied identity for the captured source
    (for example a window handle, monitor ordinal, or target identity as
    text). The module never dereferences or interprets it.
    """

    provider_id: PerceptionProviderId
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, PerceptionProviderId):
            raise TypeError(
                f"provider_id must be a PerceptionProviderId, got {type(self.provider_id).__name__}"
            )
        _validate_opaque_id(self.value, field_name="perception source id")

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> dict[str, object]:
        return {"provider_id": str(self.provider_id), "source_id": self.value}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> PerceptionSourceId:
        mapping = _require_string_mapping(raw, field_name="source identity")
        _require_exact_keys(
            mapping, required=frozenset({"provider_id", "source_id"}), context="source identity"
        )
        return cls(
            provider_id=PerceptionProviderId(
                _require_string(mapping["provider_id"], field_name="provider_id")
            ),
            value=_require_string(mapping["source_id"], field_name="source_id"),
        )


@dataclass(frozen=True, slots=True, order=True)
class PerceptionObservationId:
    """Source-scoped deterministic identity of one perception observation."""

    source_id: PerceptionSourceId
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, PerceptionSourceId):
            raise TypeError(
                f"source_id must be a PerceptionSourceId, got {type(self.source_id).__name__}"
            )
        _validate_opaque_id(self.value, field_name="perception observation id")

    @property
    def provider_id(self) -> PerceptionProviderId:
        return self.source_id.provider_id

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": str(self.source_id.provider_id),
            "source_id": self.source_id.value,
            "observation_id": self.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> PerceptionObservationId:
        mapping = _require_string_mapping(raw, field_name="observation identity")
        _require_exact_keys(
            mapping,
            required=frozenset({"provider_id", "source_id", "observation_id"}),
            context="observation identity",
        )
        return cls(
            source_id=PerceptionSourceId(
                provider_id=PerceptionProviderId(
                    _require_string(mapping["provider_id"], field_name="provider_id")
                ),
                value=_require_string(mapping["source_id"], field_name="source_id"),
            ),
            value=_require_string(mapping["observation_id"], field_name="observation_id"),
        )


@dataclass(frozen=True, slots=True, order=True)
class PerceptionCandidateId:
    """Observation-scoped deterministic identity of one candidate element."""

    observation_id: PerceptionObservationId
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.observation_id, PerceptionObservationId):
            raise TypeError(
                "observation_id must be a PerceptionObservationId, "
                f"got {type(self.observation_id).__name__}"
            )
        _validate_opaque_id(self.value, field_name="perception candidate id")

    @property
    def provider_id(self) -> PerceptionProviderId:
        return self.observation_id.provider_id

    @property
    def source_id(self) -> PerceptionSourceId:
        return self.observation_id.source_id

    def __str__(self) -> str:
        return self.value

    def to_dict(self) -> dict[str, object]:
        return {
            "provider_id": str(self.observation_id.provider_id),
            "source_id": self.observation_id.source_id.value,
            "observation_id": self.observation_id.value,
            "candidate_id": self.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> PerceptionCandidateId:
        mapping = _require_string_mapping(raw, field_name="candidate identity")
        _require_exact_keys(
            mapping,
            required=frozenset({"provider_id", "source_id", "observation_id", "candidate_id"}),
            context="candidate identity",
        )
        return cls(
            observation_id=PerceptionObservationId(
                source_id=PerceptionSourceId(
                    provider_id=PerceptionProviderId(
                        _require_string(mapping["provider_id"], field_name="provider_id")
                    ),
                    value=_require_string(mapping["source_id"], field_name="source_id"),
                ),
                value=_require_string(mapping["observation_id"], field_name="observation_id"),
            ),
            value=_require_string(mapping["candidate_id"], field_name="candidate_id"),
        )


@dataclass(frozen=True, slots=True)
class PerceptionCanvas:
    """Validated raster image dimensions for one perception observation.

    Dimensions are counts of raster pixels along the horizontal (``width``)
    and vertical (``height``) axes of the canonical coordinate space.
    """

    width: int
    height: int

    def __post_init__(self) -> None:
        for field_name, value in (("width", self.width), ("height", self.height)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{field_name} must be an integer, got {type(value).__name__}")
            if not 1 <= value <= _MAX_CANVAS_DIMENSION:
                raise PerceptionValidationError(
                    f"{field_name} must be within [1, {_MAX_CANVAS_DIMENSION}]"
                )

    def to_dict(self) -> dict[str, object]:
        return {"width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> PerceptionCanvas:
        mapping = _require_string_mapping(raw, field_name="canvas")
        _require_exact_keys(mapping, required=frozenset({"width", "height"}), context="canvas")
        return cls(
            width=_require_canvas_dimension(mapping["width"], field_name="width"),
            height=_require_canvas_dimension(mapping["height"], field_name="height"),
        )


def _require_canvas_dimension(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PerceptionValidationError(f"{field_name} must be an integer")
    if not 1 <= value <= _MAX_CANVAS_DIMENSION:
        raise PerceptionValidationError(f"{field_name} must be within [1, {_MAX_CANVAS_DIMENSION}]")
    return value


@dataclass(frozen=True, slots=True)
class PerceptionBoundingBox:
    """Validated axis-aligned region in the canonical coordinate space.

    ``x``/``y`` are the top-left corner and ``width``/``height`` the positive
    extents, all measured in raster pixels of the canonical coordinate space
    and normalized to finite floats (negative zero becomes zero). Containment
    against a canvas is enforced by the containing observation.
    """

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _normalize_real(self.x, field_name="x"))
        object.__setattr__(self, "y", _normalize_real(self.y, field_name="y"))
        object.__setattr__(self, "width", _normalize_real(self.width, field_name="width"))
        object.__setattr__(self, "height", _normalize_real(self.height, field_name="height"))
        if self.width <= 0.0:
            raise PerceptionValidationError("bounding box width must be greater than zero")
        if self.height <= 0.0:
            raise PerceptionValidationError("bounding box height must be greater than zero")
        if self.x < 0.0:
            raise PerceptionValidationError("bounding box x must not be negative")
        if self.y < 0.0:
            raise PerceptionValidationError("bounding box y must not be negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> PerceptionBoundingBox:
        mapping = _require_string_mapping(raw, field_name="bounding box")
        _require_exact_keys(
            mapping,
            required=frozenset({"x", "y", "width", "height"}),
            context="bounding box",
        )
        return cls(
            x=_require_real_number(mapping["x"], field_name="x"),
            y=_require_real_number(mapping["y"], field_name="y"),
            width=_require_real_number(mapping["width"], field_name="width"),
            height=_require_real_number(mapping["height"], field_name="height"),
        )


@dataclass(frozen=True, slots=True)
class PerceptionCandidate:
    """One provider-proposed candidate visual element.

    ``region`` localizes the candidate when the provider supplied geometry;
    ``None`` means the provider did not localize it. ``label`` and ``text``
    are untrusted perceptual strings (label: short provider caption; text:
    extracted visual text); both are optional and either may be present
    without the other. ``confidence`` is an optional finite value in
    ``[0.0, 1.0]``; ``None`` means the provider supplied none. ``parent_id``
    optionally groups this candidate under another candidate of the same
    observation.

    A candidate is a proposal, never a fact about liveness, interactability,
    safety, authorization, or verification.
    """

    candidate_id: PerceptionCandidateId
    region: PerceptionBoundingBox | None = None
    label: str | None = None
    text: str | None = None
    confidence: float | None = None
    parent_id: PerceptionCandidateId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, PerceptionCandidateId):
            raise TypeError(
                "candidate_id must be a PerceptionCandidateId, "
                f"got {type(self.candidate_id).__name__}"
            )
        if self.region is not None and not isinstance(self.region, PerceptionBoundingBox):
            raise TypeError(
                f"region must be a PerceptionBoundingBox or None, got {type(self.region).__name__}"
            )
        _validate_untrusted_text(
            self.label,
            field_name="candidate label",
            max_length=_MAX_LABEL_LENGTH,
        )
        _validate_untrusted_text(
            self.text,
            field_name="candidate text",
            max_length=_MAX_TEXT_LENGTH,
        )
        object.__setattr__(self, "confidence", _validate_confidence(self.confidence))
        if self.parent_id is not None:
            if not isinstance(self.parent_id, PerceptionCandidateId):
                raise TypeError(
                    "parent_id must be a PerceptionCandidateId or None, "
                    f"got {type(self.parent_id).__name__}"
                )
            if self.parent_id == self.candidate_id:
                raise PerceptionValidationError("a candidate cannot be its own parent")

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id.to_dict(),
            "region": None if self.region is None else self.region.to_dict(),
            "label": self.label,
            "text": self.text,
            "confidence": self.confidence,
            "parent_id": None if self.parent_id is None else self.parent_id.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> PerceptionCandidate:
        mapping = _require_string_mapping(raw, field_name="candidate")
        _require_exact_keys(
            mapping,
            required=frozenset({"candidate_id"}),
            optional=frozenset({"region", "label", "text", "confidence", "parent_id"}),
            context="candidate",
        )
        return cls(
            candidate_id=PerceptionCandidateId.from_dict(
                _require_string_mapping(mapping["candidate_id"], field_name="candidate_id")
            ),
            region=(
                None
                if mapping.get("region") is None
                else PerceptionBoundingBox.from_dict(
                    _require_string_mapping(mapping["region"], field_name="region")
                )
            ),
            label=_require_optional_untrusted_string(mapping.get("label"), field_name="label"),
            text=_require_optional_untrusted_string(mapping.get("text"), field_name="text"),
            confidence=_require_optional_confidence(mapping.get("confidence")),
            parent_id=(
                None
                if mapping.get("parent_id") is None
                else PerceptionCandidateId.from_dict(
                    _require_string_mapping(mapping["parent_id"], field_name="parent_id")
                )
            ),
        )


def _require_optional_untrusted_string(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field_name=f"candidate {field_name}")


def _require_optional_confidence(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise PerceptionValidationError("candidate confidence must be a real number or null")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise PerceptionValidationError("candidate confidence must be finite")
    if not 0.0 <= normalized <= 1.0:
        raise PerceptionValidationError(
            "candidate confidence must be within the closed interval [0.0, 1.0]"
        )
    return normalized + 0.0


@dataclass(frozen=True, slots=True)
class PerceptionObservation:
    """Immutable versioned perception observation snapshot for one source.

    The observation binds one observation identity to one captured source,
    an explicit state/freshness value, a declared coordinate space, an
    optional validated raster canvas, and ordered candidate elements.
    ``capture_artifact_id`` optionally references the canonical C2.04
    artifact record of the captured raster as durable evidence; this module
    never dereferences it. ``state_detail`` is optional untrusted provider
    text explaining a non-``OBSERVED`` state.

    State/space invariants:

    * ``OBSERVED`` and ``STALE`` observations declare the canonical
      ``IMAGE_PIXELS_TOP_LEFT`` space and carry a canvas; candidates are
      optional and every candidate belongs to this exact observation.
    * ``UNAVAILABLE`` observations carry no canvas and no candidates and
      declare an explicit ``UNKNOWN``/``UNSUPPORTED`` coordinate space.
    * Regions are contained by the canvas; grouping references resolve inside
      the observation; candidate identities are unique; candidate order is
      provider order and is preserved.
    """

    observation_id: PerceptionObservationId
    state: PerceptionObservationState
    observed_at: datetime
    coordinate_space: PerceptionCoordinateSpace
    canvas: PerceptionCanvas | None = None
    candidates: tuple[PerceptionCandidate, ...] = ()
    capture_artifact_id: ArtifactId | None = None
    state_detail: str | None = None
    schema_version: int = PERCEPTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.observation_id, PerceptionObservationId):
            raise TypeError(
                "observation_id must be a PerceptionObservationId, "
                f"got {type(self.observation_id).__name__}"
            )
        if not isinstance(self.state, PerceptionObservationState):
            raise TypeError(
                f"state must be a PerceptionObservationState, got {type(self.state).__name__}"
            )
        object.__setattr__(
            self,
            "observed_at",
            _validate_timestamp(self.observed_at, field_name="observed_at"),
        )
        if not isinstance(self.coordinate_space, PerceptionCoordinateSpace):
            raise TypeError(
                "coordinate_space must be a PerceptionCoordinateSpace, "
                f"got {type(self.coordinate_space).__name__}"
            )
        if self.canvas is not None and not isinstance(self.canvas, PerceptionCanvas):
            raise TypeError(
                f"canvas must be a PerceptionCanvas or None, got {type(self.canvas).__name__}"
            )
        if self.capture_artifact_id is not None and not isinstance(
            self.capture_artifact_id, ArtifactId
        ):
            raise TypeError(
                "capture_artifact_id must be an ArtifactId or None, "
                f"got {type(self.capture_artifact_id).__name__}"
            )
        object.__setattr__(
            self,
            "state_detail",
            _validate_untrusted_text(
                self.state_detail,
                field_name="state_detail",
                max_length=_MAX_DETAIL_LENGTH,
            ),
        )
        object.__setattr__(self, "schema_version", _validate_schema_version(self.schema_version))

        if not isinstance(self.candidates, tuple):
            raise TypeError("candidates must be a tuple")

        if self.state is PerceptionObservationState.UNAVAILABLE:
            if self.coordinate_space is PerceptionCoordinateSpace.IMAGE_PIXELS_TOP_LEFT:
                raise PerceptionValidationError(
                    "an unavailable perception observation cannot declare the canonical "
                    "coordinate space"
                )
            if self.canvas is not None:
                raise PerceptionValidationError(
                    "an unavailable perception observation cannot contain a canvas"
                )
            if self.candidates:
                raise PerceptionValidationError(
                    "an unavailable perception observation cannot contain candidates"
                )
        else:
            if self.coordinate_space is not PerceptionCoordinateSpace.IMAGE_PIXELS_TOP_LEFT:
                raise PerceptionValidationError(
                    "an observed or stale perception observation must declare the canonical "
                    "IMAGE_PIXELS_TOP_LEFT coordinate space"
                )
            if self.canvas is None:
                raise PerceptionValidationError(
                    "an observed or stale perception observation requires a canvas"
                )
        if self.state is PerceptionObservationState.OBSERVED and self.state_detail is not None:
            raise PerceptionValidationError(
                "an observed perception observation cannot carry state_detail"
            )

        for candidate in self.candidates:
            if not isinstance(candidate, PerceptionCandidate):
                raise TypeError(
                    "candidates must contain PerceptionCandidate values, "
                    f"got {type(candidate).__name__}"
                )
            if candidate.candidate_id.observation_id != self.observation_id:
                raise PerceptionValidationError(
                    "every perception candidate must belong to the exact observation identity"
                )
            if candidate.region is not None:
                assert self.canvas is not None  # guaranteed by the state invariants above
                self._validate_containment(candidate.region)

        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise PerceptionValidationError(
                "perception observation candidates must have unique identities"
            )
        for candidate in self.candidates:
            self._validate_parent(candidate, candidate_ids)

    def _validate_containment(self, region: PerceptionBoundingBox) -> None:
        assert self.canvas is not None
        if region.x + region.width > self.canvas.width:
            raise PerceptionValidationError("bounding box right edge exceeds the canvas width")
        if region.y + region.height > self.canvas.height:
            raise PerceptionValidationError("bounding box bottom edge exceeds the canvas height")

    def _validate_parent(
        self,
        candidate: PerceptionCandidate,
        candidate_ids: list[PerceptionCandidateId],
    ) -> None:
        parent = candidate.parent_id
        if parent is None:
            return
        if parent.observation_id != self.observation_id:
            raise PerceptionValidationError(
                "candidate parent must belong to the exact observation identity"
            )
        if parent not in candidate_ids:
            raise PerceptionValidationError(
                "candidate parent must identify a candidate present in the observation"
            )

    @property
    def source_id(self) -> PerceptionSourceId:
        return self.observation_id.source_id

    @property
    def provider_id(self) -> PerceptionProviderId:
        return self.observation_id.provider_id

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic versioned JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id.to_dict(),
            "state": self.state.value,
            "observed_at": _format_timestamp(self.observed_at),
            "coordinate_space": self.coordinate_space.value,
            "canvas": None if self.canvas is None else self.canvas.to_dict(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "capture_artifact_id": (
                None if self.capture_artifact_id is None else self.capture_artifact_id.to_str()
            ),
            "state_detail": self.state_detail,
        }

    def to_json(self) -> str:
        """Serialize deterministically without dynamic-code or execution hooks."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> PerceptionObservation:
        """Reconstruct one canonical observation from strict versioned data.

        Unknown fields, missing fields, malformed geometry, unbounded or
        non-finite numbers, and unsupported schema versions are rejected with
        explicit errors; nothing is guessed at or silently dropped.
        """
        mapping = _require_string_mapping(raw, field_name="perception observation")
        _require_exact_keys(
            mapping,
            required=frozenset(
                {
                    "schema_version",
                    "observation_id",
                    "state",
                    "observed_at",
                    "coordinate_space",
                    "canvas",
                    "candidates",
                }
            ),
            optional=frozenset({"capture_artifact_id", "state_detail"}),
            context="perception observation",
        )
        schema_version = mapping["schema_version"]
        if not isinstance(schema_version, int) or isinstance(schema_version, bool):
            raise PerceptionValidationError(
                "perception observation schema_version must be an integer"
            )
        _validate_schema_version(schema_version)

        state_raw = _require_string(mapping["state"], field_name="state")
        try:
            state = PerceptionObservationState(state_raw)
        except ValueError as exc:
            raise PerceptionValidationError(
                f"unknown perception observation state: {state_raw!r}"
            ) from exc

        space_raw = _require_string(mapping["coordinate_space"], field_name="coordinate_space")
        try:
            coordinate_space = PerceptionCoordinateSpace(space_raw)
        except ValueError as exc:
            raise PerceptionValidationError(
                f"unknown perception coordinate space: {space_raw!r}"
            ) from exc

        candidates_raw = mapping["candidates"]
        if not isinstance(candidates_raw, list):
            raise PerceptionValidationError("candidates must be an array")
        candidates = tuple(
            PerceptionCandidate.from_dict(
                _require_string_mapping(item, field_name=f"candidates[{index}]")
            )
            for index, item in enumerate(candidates_raw)
        )

        artifact_raw = mapping.get("capture_artifact_id")
        artifact_id: ArtifactId | None = None
        if artifact_raw is not None:
            if not isinstance(artifact_raw, str):
                raise PerceptionValidationError("capture_artifact_id must be a UUID string or null")
            try:
                artifact_id = ArtifactId.parse(artifact_raw)
            except ValueError as exc:
                raise PerceptionValidationError(
                    "capture_artifact_id must be a valid non-nil UUID string"
                ) from exc

        return cls(
            observation_id=PerceptionObservationId.from_dict(
                _require_string_mapping(mapping["observation_id"], field_name="observation_id")
            ),
            state=state,
            observed_at=_parse_timestamp(mapping["observed_at"], field_name="observed_at"),
            coordinate_space=coordinate_space,
            canvas=(
                None
                if mapping["canvas"] is None
                else PerceptionCanvas.from_dict(
                    _require_string_mapping(mapping["canvas"], field_name="canvas")
                )
            ),
            candidates=candidates,
            capture_artifact_id=artifact_id,
            state_detail=(
                None
                if mapping.get("state_detail") is None
                else _require_string(mapping["state_detail"], field_name="state_detail")
            ),
        )

    @classmethod
    def from_json(cls, raw: str) -> PerceptionObservation:
        """Parse one canonical observation from deterministic JSON text."""
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PerceptionValidationError("perception observation is not valid JSON") from exc
        return cls.from_dict(decoded)
