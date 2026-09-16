"""Attributable DOM-snapshot invalidation after browser navigation (AX-408).

The canonical C5.03 DOM observation already owns node identity, target
provenance, structured DOM data, timestamps and deterministic serialization.
This module adds the missing conservative invalidation seam. It observes
nothing, performs no navigation, and grants no browser authority.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from agentx.capabilities.browser_connection import BrowserTargetRef, BrowserTargetState
from agentx.capabilities.browser_dom import (
    BrowserDomObservation,
    BrowserDomObservationState,
)

__all__ = [
    "BrowserDomInvalidationEvidence",
    "BrowserDomInvalidationReason",
    "BrowserDomInvalidationResult",
    "invalidate_dom_after_navigation",
]


class BrowserDomInvalidationReason(StrEnum):
    TARGET_CHANGED = "target_changed"
    TARGET_UNAVAILABLE = "target_unavailable"
    URL_CHANGED = "url_changed"
    DOCUMENT_VERSION_CHANGED = "document_version_changed"


def _time(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True, kw_only=True)
class BrowserDomInvalidationEvidence:
    """Evidence explaining why a particular DOM snapshot became stale."""

    reasons: tuple[BrowserDomInvalidationReason, ...]
    observed_at: datetime
    snapshot_target: BrowserTargetRef
    current_target: BrowserTargetRef
    snapshot_document_version: str | None
    current_document_version: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.reasons, tuple) or not self.reasons:
            raise ValueError("reasons must be a non-empty tuple")
        if any(not isinstance(item, BrowserDomInvalidationReason) for item in self.reasons):
            raise TypeError("reasons must contain BrowserDomInvalidationReason values")
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("reasons must not contain duplicates")
        object.__setattr__(
            self,
            "reasons",
            tuple(sorted(self.reasons, key=lambda item: item.value)),
        )
        object.__setattr__(self, "observed_at", _time(self.observed_at, field_name="observed_at"))
        if not isinstance(self.snapshot_target, BrowserTargetRef):
            raise TypeError("snapshot_target must be a BrowserTargetRef")
        if not isinstance(self.current_target, BrowserTargetRef):
            raise TypeError("current_target must be a BrowserTargetRef")
        for name in ("snapshot_document_version", "current_document_version"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name} must be a string or None")

    def to_dict(self) -> dict[str, object]:
        return {
            "reasons": [item.value for item in self.reasons],
            "observed_at": self.observed_at.isoformat(timespec="microseconds").replace(
                "+00:00", "Z"
            ),
            "snapshot_target": self.snapshot_target.to_dict(),
            "current_target": self.current_target.to_dict(),
            "snapshot_document_version": self.snapshot_document_version,
            "current_document_version": self.current_document_version,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class BrowserDomInvalidationResult:
    """Original-or-stale observation plus optional attributable evidence."""

    observation: BrowserDomObservation
    evidence: BrowserDomInvalidationEvidence | None

    @property
    def invalidated(self) -> bool:
        return self.evidence is not None


def invalidate_dom_after_navigation(
    observation: BrowserDomObservation,
    *,
    current_target: BrowserTargetRef,
    observed_at: datetime,
    current_document_version: str | None = None,
) -> BrowserDomInvalidationResult:
    """Conservatively mark a DOM observation STALE on definite navigation evidence.

    A changed target identity, an unavailable target, a changed known URL, or a
    changed known document version is sufficient evidence. Missing URL/document
    metadata is never interpreted as proof that the snapshot is still fresh.
    If none of those explicit invalidators is observed, this function returns
    the original snapshot unchanged; that means only "no invalidation observed",
    not verified freshness.
    """
    if not isinstance(observation, BrowserDomObservation):
        raise TypeError("observation must be a BrowserDomObservation")
    if not isinstance(current_target, BrowserTargetRef):
        raise TypeError("current_target must be a BrowserTargetRef")
    moment = _time(observed_at, field_name="observed_at")
    if moment < observation.observed_at:
        raise ValueError("navigation evidence cannot predate the DOM observation")
    if current_document_version is not None and not isinstance(current_document_version, str):
        raise TypeError("current_document_version must be a string or None")

    reasons: list[BrowserDomInvalidationReason] = []
    if current_target.target_id != observation.target_id:
        reasons.append(BrowserDomInvalidationReason.TARGET_CHANGED)
    if current_target.state is not BrowserTargetState.AVAILABLE:
        reasons.append(BrowserDomInvalidationReason.TARGET_UNAVAILABLE)
    if (
        observation.target.url is not None
        and current_target.url is not None
        and observation.target.url != current_target.url
    ):
        reasons.append(BrowserDomInvalidationReason.URL_CHANGED)
    if (
        observation.document_version is not None
        and current_document_version is not None
        and observation.document_version != current_document_version
    ):
        reasons.append(BrowserDomInvalidationReason.DOCUMENT_VERSION_CHANGED)

    if not reasons:
        return BrowserDomInvalidationResult(observation=observation, evidence=None)

    evidence = BrowserDomInvalidationEvidence(
        reasons=tuple(reasons),
        observed_at=moment,
        snapshot_target=observation.target,
        current_target=current_target,
        snapshot_document_version=observation.document_version,
        current_document_version=current_document_version,
    )
    stale = replace(observation, state=BrowserDomObservationState.STALE)
    return BrowserDomInvalidationResult(observation=stale, evidence=evidence)
