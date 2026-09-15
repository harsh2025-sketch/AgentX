"""Explicit environment freshness and invalidation evidence (AX-412/413).

This module composes over canonical provenance and world-state timing rules. It
observes nothing and grants nothing. Freshness is descriptive evidence only:
FRESH is not VERIFIED, INVALIDATED is not FALSE, and source text can never
change permissions, risk, task state, procedure lifecycle, or capability
availability.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final

from agentx.core.knowledge import ProvenanceReference

__all__ = [
    "ENVIRONMENT_FRESHNESS_SCHEMA_VERSION",
    "EnvironmentFreshness",
    "EnvironmentFreshnessState",
    "EnvironmentFreshnessValidationError",
    "EnvironmentInvalidationEvidence",
    "EnvironmentInvalidationReason",
]

ENVIRONMENT_FRESHNESS_SCHEMA_VERSION: Final[int] = 1
_MAX_TEXT: Final[int] = 512
_MAX_DETAIL: Final[int] = 4096
_MAX_INVALIDATIONS: Final[int] = 32


class EnvironmentFreshnessValidationError(ValueError):
    """Raised when freshness/invalidation data violates the inert contract."""


class EnvironmentFreshnessState(StrEnum):
    NOT_YET_OBSERVED = "not_yet_observed"
    FRESH = "fresh"
    STALE = "stale"
    INVALIDATED = "invalidated"


class EnvironmentInvalidationReason(StrEnum):
    NAVIGATION = "navigation"
    SOURCE_CHANGED = "source_changed"
    ENVIRONMENT_CHANGED = "environment_changed"
    PROVIDER_DISCONNECTED = "provider_disconnected"
    PROVIDER_RESTARTED = "provider_restarted"
    EXPLICIT_INVALIDATION = "explicit_invalidation"


def _time(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise EnvironmentFreshnessValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _text(value: object, *, field_name: str, maximum: int = _MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise EnvironmentFreshnessValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > maximum:
        raise EnvironmentFreshnessValidationError(f"{field_name} exceeds {maximum} characters")
    return value


def _format(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_time(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise EnvironmentFreshnessValidationError(f"{field_name} must be a string")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise EnvironmentFreshnessValidationError(f"{field_name} is invalid") from exc
    return _time(parsed, field_name=field_name)


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentInvalidationEvidence:
    """One attributable reason a prior environmental observation became unusable."""

    subject: str
    environment_reference: str
    reason: EnvironmentInvalidationReason
    observed_at: datetime
    source: ProvenanceReference
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _text(self.subject, field_name="subject"))
        object.__setattr__(
            self,
            "environment_reference",
            _text(self.environment_reference, field_name="environment_reference"),
        )
        if not isinstance(self.reason, EnvironmentInvalidationReason):
            raise TypeError("reason must be an EnvironmentInvalidationReason")
        object.__setattr__(self, "observed_at", _time(self.observed_at, field_name="observed_at"))
        if not isinstance(self.source, ProvenanceReference):
            raise TypeError("source must be a ProvenanceReference")
        if self.detail is not None:
            object.__setattr__(
                self,
                "detail",
                _text(self.detail, field_name="detail", maximum=_MAX_DETAIL),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "environment_reference": self.environment_reference,
            "reason": self.reason.value,
            "observed_at": _format(self.observed_at),
            "source": self.source.to_dict(),
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EnvironmentInvalidationEvidence:
        expected = {
            "subject",
            "environment_reference",
            "reason",
            "observed_at",
            "source",
            "detail",
        }
        if set(raw) != expected:
            raise EnvironmentFreshnessValidationError(
                "invalidation fields are incomplete or unknown"
            )
        reason = raw["reason"]
        if not isinstance(reason, str):
            raise EnvironmentFreshnessValidationError("reason must be a string")
        try:
            parsed_reason = EnvironmentInvalidationReason(reason)
        except ValueError as exc:
            raise EnvironmentFreshnessValidationError("unknown invalidation reason") from exc
        source = raw["source"]
        if not isinstance(source, Mapping):
            raise EnvironmentFreshnessValidationError("source must be an object")
        detail = raw["detail"]
        if detail is not None and not isinstance(detail, str):
            raise EnvironmentFreshnessValidationError("detail must be a string or null")
        return cls(
            subject=_text(raw["subject"], field_name="subject"),
            environment_reference=_text(
                raw["environment_reference"], field_name="environment_reference"
            ),
            reason=parsed_reason,
            observed_at=_parse_time(raw["observed_at"], field_name="observed_at"),
            source=ProvenanceReference.from_dict(source),
            detail=detail,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentFreshness:
    """Immutable freshness window plus explicit invalidation/refresh lineage."""

    subject: str
    environment_reference: str
    observed_at: datetime
    ttl: timedelta
    source: ProvenanceReference
    invalidations: tuple[EnvironmentInvalidationEvidence, ...] = ()
    generation: int = 0
    previous_observed_at: datetime | None = None
    schema_version: int = ENVIRONMENT_FRESHNESS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _text(self.subject, field_name="subject"))
        object.__setattr__(
            self,
            "environment_reference",
            _text(self.environment_reference, field_name="environment_reference"),
        )
        object.__setattr__(self, "observed_at", _time(self.observed_at, field_name="observed_at"))
        if not isinstance(self.ttl, timedelta):
            raise TypeError("ttl must be a timedelta")
        if self.ttl <= timedelta(0):
            raise EnvironmentFreshnessValidationError("ttl must be strictly positive")
        if not isinstance(self.source, ProvenanceReference):
            raise TypeError("source must be a ProvenanceReference")
        if not isinstance(self.invalidations, tuple):
            raise TypeError("invalidations must be a tuple")
        if len(self.invalidations) > _MAX_INVALIDATIONS:
            raise EnvironmentFreshnessValidationError("too many invalidation records")
        if type(self.generation) is not int or self.generation < 0:
            raise EnvironmentFreshnessValidationError("generation must be a non-negative integer")
        if self.previous_observed_at is not None:
            previous = _time(self.previous_observed_at, field_name="previous_observed_at")
            if previous > self.observed_at:
                raise EnvironmentFreshnessValidationError(
                    "previous_observed_at must not follow observed_at"
                )
            object.__setattr__(self, "previous_observed_at", previous)
        if self.generation == 0 and self.previous_observed_at is not None:
            raise EnvironmentFreshnessValidationError(
                "generation zero cannot carry previous_observed_at"
            )
        if self.generation > 0 and self.previous_observed_at is None:
            raise EnvironmentFreshnessValidationError(
                "refreshed generations require previous_observed_at"
            )
        if self.schema_version != ENVIRONMENT_FRESHNESS_SCHEMA_VERSION:
            raise EnvironmentFreshnessValidationError("unsupported freshness schema version")

        seen: set[tuple[EnvironmentInvalidationReason, datetime, str]] = set()
        normalized: list[EnvironmentInvalidationEvidence] = []
        for evidence in self.invalidations:
            if not isinstance(evidence, EnvironmentInvalidationEvidence):
                raise TypeError("invalidations must contain EnvironmentInvalidationEvidence")
            if evidence.subject != self.subject:
                raise EnvironmentFreshnessValidationError("invalidation subject does not match")
            if evidence.environment_reference != self.environment_reference:
                raise EnvironmentFreshnessValidationError(
                    "invalidation environment_reference does not match"
                )
            if evidence.observed_at < self.observed_at:
                raise EnvironmentFreshnessValidationError(
                    "invalidation cannot predate the observation it invalidates"
                )
            key = (evidence.reason, evidence.observed_at, evidence.source.reference)
            if key in seen:
                raise EnvironmentFreshnessValidationError("duplicate invalidation evidence")
            seen.add(key)
            normalized.append(evidence)
        object.__setattr__(
            self,
            "invalidations",
            tuple(sorted(normalized, key=lambda item: (item.observed_at, item.reason.value))),
        )

    @property
    def expires_at(self) -> datetime:
        return self.observed_at + self.ttl

    def state_at(self, at: datetime) -> EnvironmentFreshnessState:
        moment = _time(at, field_name="at")
        if moment < self.observed_at:
            return EnvironmentFreshnessState.NOT_YET_OBSERVED
        if any(item.observed_at <= moment for item in self.invalidations):
            return EnvironmentFreshnessState.INVALIDATED
        if moment < self.expires_at:
            return EnvironmentFreshnessState.FRESH
        return EnvironmentFreshnessState.STALE

    def is_fresh(self, at: datetime) -> bool:
        return self.state_at(at) is EnvironmentFreshnessState.FRESH

    def invalidate(self, evidence: EnvironmentInvalidationEvidence) -> EnvironmentFreshness:
        if not isinstance(evidence, EnvironmentInvalidationEvidence):
            raise TypeError("evidence must be EnvironmentInvalidationEvidence")
        return replace(self, invalidations=(*self.invalidations, evidence))

    def refresh(
        self,
        *,
        observed_at: datetime,
        ttl: timedelta,
        source: ProvenanceReference,
    ) -> EnvironmentFreshness:
        refreshed_at = _time(observed_at, field_name="observed_at")
        if refreshed_at < self.observed_at:
            raise EnvironmentFreshnessValidationError(
                "refresh cannot move observation time backward"
            )
        return EnvironmentFreshness(
            subject=self.subject,
            environment_reference=self.environment_reference,
            observed_at=refreshed_at,
            ttl=ttl,
            source=source,
            invalidations=(),
            generation=self.generation + 1,
            previous_observed_at=self.observed_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "subject": self.subject,
            "environment_reference": self.environment_reference,
            "observed_at": _format(self.observed_at),
            "ttl_microseconds": self.ttl // timedelta(microseconds=1),
            "source": self.source.to_dict(),
            "invalidations": [item.to_dict() for item in self.invalidations],
            "generation": self.generation,
            "previous_observed_at": (
                None if self.previous_observed_at is None else _format(self.previous_observed_at)
            ),
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EnvironmentFreshness:
        expected = {
            "schema_version",
            "subject",
            "environment_reference",
            "observed_at",
            "ttl_microseconds",
            "source",
            "invalidations",
            "generation",
            "previous_observed_at",
        }
        if set(raw) != expected:
            raise EnvironmentFreshnessValidationError("freshness fields are incomplete or unknown")
        version = raw["schema_version"]
        if type(version) is not int or version != ENVIRONMENT_FRESHNESS_SCHEMA_VERSION:
            raise EnvironmentFreshnessValidationError("unsupported freshness schema version")
        ttl = raw["ttl_microseconds"]
        if type(ttl) is not int or ttl <= 0:
            raise EnvironmentFreshnessValidationError("ttl_microseconds must be positive")
        source = raw["source"]
        if not isinstance(source, Mapping):
            raise EnvironmentFreshnessValidationError("source must be an object")
        raw_invalidations = raw["invalidations"]
        if not isinstance(raw_invalidations, list):
            raise EnvironmentFreshnessValidationError("invalidations must be an array")
        if len(raw_invalidations) > _MAX_INVALIDATIONS:
            raise EnvironmentFreshnessValidationError("too many invalidation records")
        invalidations: list[EnvironmentInvalidationEvidence] = []
        for item in raw_invalidations:
            if not isinstance(item, Mapping):
                raise EnvironmentFreshnessValidationError("invalidation entries must be objects")
            invalidations.append(EnvironmentInvalidationEvidence.from_dict(item))
        generation = raw["generation"]
        if type(generation) is not int:
            raise EnvironmentFreshnessValidationError("generation must be an integer")
        previous_raw = raw["previous_observed_at"]
        if previous_raw is not None and not isinstance(previous_raw, str):
            raise EnvironmentFreshnessValidationError(
                "previous_observed_at must be a string or null"
            )
        return cls(
            subject=_text(raw["subject"], field_name="subject"),
            environment_reference=_text(
                raw["environment_reference"], field_name="environment_reference"
            ),
            observed_at=_parse_time(raw["observed_at"], field_name="observed_at"),
            ttl=timedelta(microseconds=ttl),
            source=ProvenanceReference.from_dict(source),
            invalidations=tuple(invalidations),
            generation=generation,
            previous_observed_at=(
                None
                if previous_raw is None
                else _parse_time(previous_raw, field_name="previous_observed_at")
            ),
        )

    @classmethod
    def from_json(cls, text: str) -> EnvironmentFreshness:
        if not isinstance(text, str):
            raise TypeError("freshness JSON must be a string")
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise EnvironmentFreshnessValidationError("freshness JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise EnvironmentFreshnessValidationError("freshness JSON root must be an object")
        return cls.from_dict(decoded)
