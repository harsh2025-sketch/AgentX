"""Canonical inert capability availability / health assessment (M7.03).

AgentX must be able to tell two different things apart:

    1. *a capability exists in the registry*, and
    2. *a capability is currently usable*.

Registry presence is not availability. A provider may be unsupported on this
platform, temporarily unavailable, missing a dependency, degraded, or known
healthy from explicit probe evidence. This module owns the smallest
deterministic **data/assessment boundary** that turns explicit, typed,
caller-supplied evidence about exactly one capability identity into one
immutable health verdict. It owns nothing else.

M7.03 answers: *According to the explicit evidence supplied by the caller, is
this exact capability version currently usable?*

It does NOT answer: *May we do this?* and *How should we execute this?*

What this module is
-------------------

    * :class:`CapabilityHealthState` — the canonical, closed five-state health
      vocabulary (``UNKNOWN``, ``AVAILABLE``, ``DEGRADED``, ``UNAVAILABLE``,
      ``UNSUPPORTED``). Every assessment lands on exactly one member.
    * :class:`CapabilityHealthEvidenceKind` — the canonical, closed vocabulary
      of *evidence channels*. Every member is anchored by
      :data:`CANONICAL_CAPABILITY_HEALTH_EVIDENCE_ANCHORS` to names that
      already exist in landed canonical vocabularies (C4.01
      ``FailureCategory``, C4.04 ``EnvironmentFactKind`` /
      ``EnvironmentChangeResult``), so this module invents no parallel world
      model of what can be observed about a capability.
    * :class:`CapabilityHealthFact` — the canonical, closed *typed* fact
      vocabulary. A fact is always supplied explicitly by the caller; nothing
      here derives one from text. Each fact is legal for exactly one evidence
      kind, and that pairing is enforced structurally by
      :data:`CANONICAL_CAPABILITY_HEALTH_EVIDENCE_FACTS`.
    * :class:`CapabilityVersionKey` — the core-safe, explicit ``major.minor.
      patch`` version key. ``agentx.core`` is an inward leaf and must not
      import ``agentx.capabilities``, so the A1.08 ``CapabilityVersion`` rule
      is reproduced here *by value* — exactly the reuse discipline C4.04
      applies to the C2.09 freshness rule.
    * :class:`CapabilityHealthSubject` — the exact identity an assessment is
      about: a canonical :class:`~agentx.core.ids.CapabilityId` plus an
      explicit :class:`CapabilityVersionKey`. There is no wildcard version and
      no prefix matching anywhere in this module.
    * :class:`CapabilityHealthEvidence` — one typed observation about one
      subject at one explicit instant with an explicit TTL. Freshness is the
      canonical C2.09 environmental-observation rule reproduced exactly:
      ``expires_at = observed_at + ttl`` and fresh exactly while
      ``at < expires_at``, so at the boundary instant the evidence is already
      stale (fail closed).
    * :class:`CapabilityHealthConflict` — one explicitly represented
      contradiction: the same evidence channel observed at the same instant
      with differing facts.
    * :class:`CapabilityHealthReason` / :class:`CapabilityHealthAssessment` —
      the closed reason vocabulary and the one immutable, deterministic,
      JSON-serializable verdict record.
    * :func:`assess_capability_health` — the pure, deterministic assessment.

Assessment rules (ordered, first match wins)
--------------------------------------------

    1. no evidence at all → ``UNKNOWN``;
    2. any evidence dated after ``assessed_at`` → ``UNKNOWN`` — a broken
       timeline proves nothing, so the assessment is invalidated as a whole;
    3. any explicitly represented conflict → ``UNKNOWN``;
    4. no *fresh* evidence → ``UNKNOWN``;
    5. explicit unsupported platform → ``UNSUPPORTED``;
    6. explicit required dependency unavailable → ``UNAVAILABLE``;
    7. fresh provider unavailable → ``UNAVAILABLE``;
    8. fresh failed execution (structural) → ``DEGRADED``;
    9. fresh transient execution failure → ``DEGRADED``;
    10. fresh failed verification → ``DEGRADED``;
    11. fresh degraded provider → ``DEGRADED``;
    12. fresh degraded dependency → ``DEGRADED``;
    13. fresh relevant environment change → ``DEGRADED``;
    14. fresh succeeded execution **and** fresh passed verification, with no
        conflicting current failure → ``AVAILABLE``;
    15. otherwise → ``UNKNOWN`` with the reasons for what *was* observed.

There is no averaging, no majority vote, no weighting, no scoring, and no
inspection of text. Within one evidence channel the *latest* fresh observation
supersedes earlier ones — that selection is justified by the canonical
``observed_at`` timestamp and by the channel semantics (a later observation of
the same channel is a newer statement about the same thing). Ties on
``observed_at`` are never broken arbitrarily: they are surfaced as an explicit
:class:`CapabilityHealthConflict` and the assessment fails closed.

Staleness
---------

Stale evidence is **excluded** from state determination and reported with the
``EVIDENCE_STALE`` reason; it never invalidates fresh evidence. This is what
makes both required properties hold at once: a stale success cannot prove
current availability, and a stale failure cannot permanently prohibit a
capability. When *every* record is stale the verdict is ``UNKNOWN``, never
``UNAVAILABLE``.

Freshness is never stored on the assessment: the caller supplies
``assessed_at`` and this function never reads a clock.

Health is not authority
-----------------------

**``AVAILABLE`` grants nothing.** A health state is not a permission, not an
``AuthorityContext``, not a clearance, and not a routing decision.
``AVAILABLE`` never grants a :class:`~agentx.kernel.permissions.Permission`,
never widens a ``ResourceEnvelope``, never lowers a ``RiskLevel``, never
clears an ``EmergencyStop``, and never bypasses the ``ActionGate``; the
Action Gate remains the only authority. ``UNAVAILABLE`` never *revokes* a
policy permission either — health and authorization are independent axes, and
this module touches neither. Nothing on any M7.03 value can grant authority.

Health is not routing
---------------------

This module never selects an ``ExecutionLevel`` (L0-L5) and holds no router
state. It produces evidence a future router may choose to consume.

Deliberate non-scope
--------------------

M7.03 never probes a capability, provider, process, file, registry, browser,
or network; never spawns a background or periodic check; never retries,
repairs, or restarts anything; never mutates the capability registry; never
reroutes the AgentLoop; never persists health or adds a migration; never
implements a missing-capability detector; and never maintains unbounded health
history. All observations are caller-supplied values. There is no store, no
service, no monitor, and no prober here.

Owner: M7.03. Belongs to ``agentx.core``; imports only the standard library
and inward ``agentx.core`` contracts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from typing import Final

from agentx.core.environment_change import EnvironmentChangeResult, EnvironmentFactKind
from agentx.core.failure_taxonomy import FailureCategory
from agentx.core.ids import CapabilityId

__all__ = [
    "CANONICAL_CAPABILITY_HEALTH_EVIDENCE_ANCHORS",
    "CANONICAL_CAPABILITY_HEALTH_EVIDENCE_FACTS",
    "CANONICAL_CAPABILITY_HEALTH_EVIDENCE_KINDS",
    "CANONICAL_CAPABILITY_HEALTH_FACTS",
    "CANONICAL_CAPABILITY_HEALTH_REASONS",
    "CANONICAL_CAPABILITY_HEALTH_STATES",
    "CAPABILITY_HEALTH_SCHEMA_VERSION",
    "MAX_HEALTH_EVIDENCE_RECORDS",
    "MAX_HEALTH_EVIDENCE_TTL",
    "CapabilityHealthAssessment",
    "CapabilityHealthConflict",
    "CapabilityHealthEvidence",
    "CapabilityHealthEvidenceKind",
    "CapabilityHealthFact",
    "CapabilityHealthReason",
    "CapabilityHealthState",
    "CapabilityHealthSubject",
    "CapabilityHealthValidationError",
    "CapabilityVersionKey",
    "assess_capability_health",
]

CAPABILITY_HEALTH_SCHEMA_VERSION: Final[int] = 1

#: Hard bound on the number of evidence records one assessment may consume.
#: There is no unbounded health history here: callers must bound what they
#: hand over, and exceeding the bound is a rejected input rather than a
#: silently truncated one.
MAX_HEALTH_EVIDENCE_RECORDS: Final[int] = 32

#: Hard bound on a single record's TTL. A freshness claim may not be
#: unbounded; a week is the longest availability claim this contract accepts.
MAX_HEALTH_EVIDENCE_TTL: Final[timedelta] = timedelta(days=7)

_MAX_SUMMARY_LENGTH: Final[int] = 512
_MAX_DETAIL_LENGTH: Final[int] = 1024
_MAX_COUNTER: Final[int] = (1 << 63) - 1
_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class CapabilityHealthValidationError(ValueError):
    """Raised when capability-health data violates the canonical contract."""


def _validate_text(value: object, *, field_name: str, max_length: int) -> str:
    """Validate explicit, non-empty, trimmed, control-character-free text."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise CapabilityHealthValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > max_length:
        raise CapabilityHealthValidationError(
            f"{field_name} must not exceed {max_length} characters"
        )
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise CapabilityHealthValidationError(f"{field_name} must not contain control characters")
    return value


def _validate_optional_text(value: object, *, field_name: str, max_length: int) -> str | None:
    """Validate optional inert text. The text is preserved, never parsed."""
    if value is None:
        return None
    return _validate_text(value, field_name=field_name, max_length=max_length)


def _validate_counter(value: object, *, field_name: str) -> int:
    """Validate an explicit non-negative integer (bools rejected)."""
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise CapabilityHealthValidationError(f"{field_name} must not be negative")
    if value > _MAX_COUNTER:
        raise OverflowError(f"{field_name} exceeds the supported counter range")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    """Validate a timezone-aware datetime and normalize it to UTC."""
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise CapabilityHealthValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    """Return the canonical UTC ISO-8601 ``Z`` form used by AgentX."""
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_ttl(value: object) -> timedelta:
    """Validate a strictly positive, bounded TTL."""
    if not isinstance(value, timedelta):
        raise TypeError(f"ttl must be a timedelta, got {type(value).__name__}")
    if value <= timedelta(0):
        raise CapabilityHealthValidationError("ttl must be strictly positive")
    if value > MAX_HEALTH_EVIDENCE_TTL:
        raise CapabilityHealthValidationError(
            f"ttl must not exceed {MAX_HEALTH_EVIDENCE_TTL}; "
            "this contract accepts no unbounded freshness claim"
        )
    return value


# --------------------------------------------------------------------------
# State vocabulary.
# --------------------------------------------------------------------------


class CapabilityHealthState(StrEnum):
    """The canonical, closed capability-health vocabulary.

    Every assessment lands on exactly one member. No member is ordered above
    or below another, no member carries a scalar confidence, and no member
    grants or revokes anything.

    Members:
        UNKNOWN: The fail-closed default. The supplied evidence does not
            justify any more specific verdict: there was no evidence, no
            *fresh* evidence, the timeline was not trustworthy, or the current
            evidence contradicted itself. ``UNKNOWN`` is an absence of
            justification — it never claims the capability is broken and never
            prohibits an attempt.
        AVAILABLE: Fresh, positive evidence: a succeeded execution *and* a
            passed verification for this exact capability version, with no
            conflicting current failure. ``AVAILABLE`` is a statement about
            usability, never a permission to execute.
        DEGRADED: The capability is present but the current evidence is mixed
            or weakened — a transient or structural execution failure, a
            failed verification, a degraded provider or dependency, or a
            relevant environment change. Degraded is not prohibited.
        UNAVAILABLE: An explicit current fact says the capability cannot be
            used right now: its provider is unavailable, or a required
            dependency is missing. This is a present-tense statement that
            expires with the evidence; it is never a permanent ban.
        UNSUPPORTED: An explicit observation says the capability is not
            supported in this platform/environment. This is structural, not
            transient, but it is still only a statement about the observed
            environment and never a permission decision.
    """

    UNKNOWN = "unknown"
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"


#: The canonical states in their declared order.
CANONICAL_CAPABILITY_HEALTH_STATES: Final[tuple[CapabilityHealthState, ...]] = tuple(
    CapabilityHealthState
)


# --------------------------------------------------------------------------
# Evidence vocabulary.
# --------------------------------------------------------------------------


class CapabilityHealthEvidenceKind(StrEnum):
    """The canonical, closed vocabulary of capability-health evidence channels.

    A kind names *what was observed*, never a conclusion. Every member is
    anchored to names that already exist in landed canonical vocabularies (see
    :data:`CANONICAL_CAPABILITY_HEALTH_EVIDENCE_ANCHORS`); this vocabulary
    deliberately adds no observation ontology of its own.

    Members:
        PLATFORM_SUPPORT: An explicit observation of whether this capability
            is supported in the current platform/environment.
        PROVIDER_AVAILABILITY: An explicit observation of whether the provider
            backing this capability is currently available.
        DEPENDENCY_AVAILABILITY: An explicit observation of whether an
            external dependency this capability requires is present.
        EXECUTION_OUTCOME: The outcome of a recent invocation of this exact
            capability version, supplied as a typed fact. Exception text is
            never an input to this contract.
        VERIFICATION_OUTCOME: The verdict of a recent verification of this
            exact capability version. Verification is separate from execution
            in the canonical ABI, and stays separate here.
        ENVIRONMENT_CHANGE: An explicit environment-change observation for the
            environment this capability depends on.
    """

    PLATFORM_SUPPORT = "platform_support"
    PROVIDER_AVAILABILITY = "provider_availability"
    DEPENDENCY_AVAILABILITY = "dependency_availability"
    EXECUTION_OUTCOME = "execution_outcome"
    VERIFICATION_OUTCOME = "verification_outcome"
    ENVIRONMENT_CHANGE = "environment_change"


#: The canonical evidence kinds in their declared order. The declared order is
#: also the primary canonical ordering key for evidence and reasons.
CANONICAL_CAPABILITY_HEALTH_EVIDENCE_KINDS: Final[tuple[CapabilityHealthEvidenceKind, ...]] = tuple(
    CapabilityHealthEvidenceKind
)

#: Explicit anchor map: each canonical evidence kind indexes only names that
#: already exist in landed canonical vocabularies (C4.01 ``FailureCategory``,
#: C4.04 ``EnvironmentFactKind`` / ``EnvironmentChangeResult``). This is the
#: machine-checkable statement that M7.03 reuses existing AgentX
#: representations instead of inventing a parallel world model.
CANONICAL_CAPABILITY_HEALTH_EVIDENCE_ANCHORS: Final[
    Mapping[CapabilityHealthEvidenceKind, tuple[str, ...]]
] = MappingProxyType(
    {
        CapabilityHealthEvidenceKind.PLATFORM_SUPPORT: (
            EnvironmentFactKind.PLATFORM_IDENTITY.value,
            FailureCategory.ENVIRONMENT.value,
        ),
        CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY: (
            EnvironmentFactKind.CAPABILITY_AVAILABILITY.value,
            FailureCategory.CAPABILITY.value,
        ),
        CapabilityHealthEvidenceKind.DEPENDENCY_AVAILABILITY: (
            EnvironmentFactKind.DEPENDENCY_AVAILABILITY.value,
            FailureCategory.DEPENDENCY.value,
        ),
        CapabilityHealthEvidenceKind.EXECUTION_OUTCOME: (
            FailureCategory.CAPABILITY.value,
            FailureCategory.TRANSIENT.value,
        ),
        CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME: (FailureCategory.VERIFICATION.value,),
        CapabilityHealthEvidenceKind.ENVIRONMENT_CHANGE: (
            EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED.value,
            EnvironmentChangeResult.NO_RELEVANT_CHANGE.value,
        ),
    }
)


class CapabilityHealthFact(StrEnum):
    """The canonical, closed *typed* fact vocabulary.

    A fact is a member of this enum, always supplied explicitly by the caller.
    Nothing in this module derives a fact from a message, an exception, a
    stack trace, a detail string, or any other text: a detail containing
    ``"available=true"`` or ``"verified=true"`` never becomes a fact here.

    Each fact is legal for exactly one evidence kind; the pairing is enforced
    structurally by :data:`CANONICAL_CAPABILITY_HEALTH_EVIDENCE_FACTS`.
    """

    PLATFORM_SUPPORTED = "platform_supported"
    PLATFORM_UNSUPPORTED = "platform_unsupported"
    PROVIDER_AVAILABLE = "provider_available"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_DEGRADED = "provider_degraded"
    DEPENDENCY_SATISFIED = "dependency_satisfied"
    DEPENDENCY_MISSING = "dependency_missing"
    DEPENDENCY_DEGRADED = "dependency_degraded"
    EXECUTION_SUCCEEDED = "execution_succeeded"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_FAILED_TRANSIENT = "execution_failed_transient"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    ENVIRONMENT_CHANGED = "environment_changed"
    ENVIRONMENT_UNCHANGED = "environment_unchanged"


#: The canonical facts in their declared order. The declared order is the
#: secondary canonical ordering key for evidence.
CANONICAL_CAPABILITY_HEALTH_FACTS: Final[tuple[CapabilityHealthFact, ...]] = tuple(
    CapabilityHealthFact
)

#: The exact legal kind/fact pairings. A fact outside its own channel is a
#: rejected input, never a coerced one.
CANONICAL_CAPABILITY_HEALTH_EVIDENCE_FACTS: Final[
    Mapping[CapabilityHealthEvidenceKind, frozenset[CapabilityHealthFact]]
] = MappingProxyType(
    {
        CapabilityHealthEvidenceKind.PLATFORM_SUPPORT: frozenset(
            {
                CapabilityHealthFact.PLATFORM_SUPPORTED,
                CapabilityHealthFact.PLATFORM_UNSUPPORTED,
            }
        ),
        CapabilityHealthEvidenceKind.PROVIDER_AVAILABILITY: frozenset(
            {
                CapabilityHealthFact.PROVIDER_AVAILABLE,
                CapabilityHealthFact.PROVIDER_UNAVAILABLE,
                CapabilityHealthFact.PROVIDER_DEGRADED,
            }
        ),
        CapabilityHealthEvidenceKind.DEPENDENCY_AVAILABILITY: frozenset(
            {
                CapabilityHealthFact.DEPENDENCY_SATISFIED,
                CapabilityHealthFact.DEPENDENCY_MISSING,
                CapabilityHealthFact.DEPENDENCY_DEGRADED,
            }
        ),
        CapabilityHealthEvidenceKind.EXECUTION_OUTCOME: frozenset(
            {
                CapabilityHealthFact.EXECUTION_SUCCEEDED,
                CapabilityHealthFact.EXECUTION_FAILED,
                CapabilityHealthFact.EXECUTION_FAILED_TRANSIENT,
            }
        ),
        CapabilityHealthEvidenceKind.VERIFICATION_OUTCOME: frozenset(
            {
                CapabilityHealthFact.VERIFICATION_PASSED,
                CapabilityHealthFact.VERIFICATION_FAILED,
            }
        ),
        CapabilityHealthEvidenceKind.ENVIRONMENT_CHANGE: frozenset(
            {
                CapabilityHealthFact.ENVIRONMENT_CHANGED,
                CapabilityHealthFact.ENVIRONMENT_UNCHANGED,
            }
        ),
    }
)


class CapabilityHealthReason(StrEnum):
    """The canonical, closed reason-code vocabulary for one assessment.

    Every assessment carries at least one reason, in canonical declaration
    order without duplicates. Reasons explain *which typed facts and which
    evidence conditions* produced the state; they are never free text and
    never a remedy.
    """

    NO_EVIDENCE = "no_evidence"
    NO_FRESH_EVIDENCE = "no_fresh_evidence"
    EVIDENCE_STALE = "evidence_stale"
    EVIDENCE_FUTURE_DATED = "evidence_future_dated"
    EVIDENCE_CONFLICTING = "evidence_conflicting"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PLATFORM_UNSUPPORTED = "platform_unsupported"
    PLATFORM_SUPPORTED = "platform_supported"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_AVAILABLE = "provider_available"
    PROVIDER_DEGRADED = "provider_degraded"
    DEPENDENCY_MISSING = "dependency_missing"
    DEPENDENCY_SATISFIED = "dependency_satisfied"
    DEPENDENCY_DEGRADED = "dependency_degraded"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_FAILED_TRANSIENT = "execution_failed_transient"
    EXECUTION_SUCCEEDED = "execution_succeeded"
    VERIFICATION_FAILED = "verification_failed"
    VERIFICATION_PASSED = "verification_passed"
    ENVIRONMENT_CHANGED = "environment_changed"
    ENVIRONMENT_UNCHANGED = "environment_unchanged"


#: The canonical reasons in their declared order, which is also the canonical
#: output order of ``reasons`` on every assessment.
CANONICAL_CAPABILITY_HEALTH_REASONS: Final[tuple[CapabilityHealthReason, ...]] = tuple(
    CapabilityHealthReason
)

#: The exact fact-to-reason mapping. Reasons mirror facts one-to-one; the
#: remaining reasons name evidence conditions rather than observed facts.
_FACT_REASONS: Final[Mapping[CapabilityHealthFact, CapabilityHealthReason]] = MappingProxyType(
    {
        CapabilityHealthFact.PLATFORM_SUPPORTED: CapabilityHealthReason.PLATFORM_SUPPORTED,
        CapabilityHealthFact.PLATFORM_UNSUPPORTED: CapabilityHealthReason.PLATFORM_UNSUPPORTED,
        CapabilityHealthFact.PROVIDER_AVAILABLE: CapabilityHealthReason.PROVIDER_AVAILABLE,
        CapabilityHealthFact.PROVIDER_UNAVAILABLE: CapabilityHealthReason.PROVIDER_UNAVAILABLE,
        CapabilityHealthFact.PROVIDER_DEGRADED: CapabilityHealthReason.PROVIDER_DEGRADED,
        CapabilityHealthFact.DEPENDENCY_SATISFIED: CapabilityHealthReason.DEPENDENCY_SATISFIED,
        CapabilityHealthFact.DEPENDENCY_MISSING: CapabilityHealthReason.DEPENDENCY_MISSING,
        CapabilityHealthFact.DEPENDENCY_DEGRADED: CapabilityHealthReason.DEPENDENCY_DEGRADED,
        CapabilityHealthFact.EXECUTION_SUCCEEDED: CapabilityHealthReason.EXECUTION_SUCCEEDED,
        CapabilityHealthFact.EXECUTION_FAILED: CapabilityHealthReason.EXECUTION_FAILED,
        CapabilityHealthFact.EXECUTION_FAILED_TRANSIENT: (
            CapabilityHealthReason.EXECUTION_FAILED_TRANSIENT
        ),
        CapabilityHealthFact.VERIFICATION_PASSED: CapabilityHealthReason.VERIFICATION_PASSED,
        CapabilityHealthFact.VERIFICATION_FAILED: CapabilityHealthReason.VERIFICATION_FAILED,
        CapabilityHealthFact.ENVIRONMENT_CHANGED: CapabilityHealthReason.ENVIRONMENT_CHANGED,
        CapabilityHealthFact.ENVIRONMENT_UNCHANGED: CapabilityHealthReason.ENVIRONMENT_UNCHANGED,
    }
)

_KIND_ORDER: Final[Mapping[CapabilityHealthEvidenceKind, int]] = MappingProxyType(
    {kind: index for index, kind in enumerate(CANONICAL_CAPABILITY_HEALTH_EVIDENCE_KINDS)}
)

_FACT_ORDER: Final[Mapping[CapabilityHealthFact, int]] = MappingProxyType(
    {fact: index for index, fact in enumerate(CANONICAL_CAPABILITY_HEALTH_FACTS)}
)


def _canonical_reasons(
    reasons: Sequence[CapabilityHealthReason],
) -> tuple[CapabilityHealthReason, ...]:
    """Deduplicate reason codes into canonical declaration order."""
    present = set(reasons)
    return tuple(reason for reason in CANONICAL_CAPABILITY_HEALTH_REASONS if reason in present)


# --------------------------------------------------------------------------
# Core-safe identity.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityVersionKey:
    """Explicit typed capability version as ``major.minor.patch`` integers.

    This is the core-safe representation of the A1.08 ``CapabilityVersion``
    rule, reproduced by value because ``agentx.core`` is an inward leaf and
    must not import ``agentx.capabilities``. It imports nothing outward and
    creates no competing identity for the capability itself: the capability is
    named by the canonical :class:`~agentx.core.ids.CapabilityId`.

    Versions are structured data, never an opaque string, and comparison is
    exact. There is deliberately **no wildcard member, no "latest" member, and
    no prefix or range matching**: ``1.2.3`` and ``1.2.0`` are different
    versions, and evidence for one never establishes health for the other.
    """

    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        _validate_counter(self.major, field_name="version.major")
        _validate_counter(self.minor, field_name="version.minor")
        _validate_counter(self.patch, field_name="version.patch")

    def to_str(self) -> str:
        """Return the canonical ``"major.minor.patch"`` form."""
        return f"{self.major}.{self.minor}.{self.patch}"

    def __str__(self) -> str:
        return self.to_str()

    @classmethod
    def from_str(cls, raw: str) -> CapabilityVersionKey:
        """Parse the canonical ``"major.minor.patch"`` form, failing closed.

        Exactly three dot-separated ASCII-digit components are accepted. A
        two-component version, a wildcard component, a leading ``+``, or a
        padded component is rejected rather than widened into a range.
        """
        if not isinstance(raw, str):
            raise TypeError(f"version string must be a string, got {type(raw).__name__}")
        if not raw or raw != raw.strip():
            raise CapabilityHealthValidationError("version string must be non-empty and trimmed")
        parts = raw.split(".")
        if len(parts) != 3 or any(not part.isascii() or not part.isdigit() for part in parts):
            raise CapabilityHealthValidationError(
                "version string must have the form 'major.minor.patch' with non-negative "
                f"integer components and no wildcard, got {raw!r}"
            )
        major, minor, patch = (int(part) for part in parts)
        return cls(major=major, minor=minor, patch=patch)

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {"major": self.major, "minor": self.minor, "patch": self.patch}


@dataclass(frozen=True, slots=True)
class CapabilityHealthSubject:
    """The exact capability identity an assessment is about.

    Identity binding is exact by construction: a canonical
    :class:`~agentx.core.ids.CapabilityId` (UUID-backed, so a lookalike string
    is not a lookalike identity) plus one explicit
    :class:`CapabilityVersionKey`. There is no wildcard subject and no
    version-optional subject, so no assessment can ever be produced for "any
    version" of a capability.
    """

    capability_id: CapabilityId
    version: CapabilityVersionKey

    def __post_init__(self) -> None:
        if not isinstance(self.capability_id, CapabilityId):
            raise TypeError(
                "capability_id must be a canonical CapabilityId, "
                f"got {type(self.capability_id).__name__}"
            )
        if not isinstance(self.version, CapabilityVersionKey):
            raise TypeError(
                f"version must be a CapabilityVersionKey, got {type(self.version).__name__}"
            )

    def __str__(self) -> str:
        return f"{self.capability_id}@{self.version}"

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "capability_id": self.capability_id.to_str(),
            "version": self.version.to_dict(),
        }


# --------------------------------------------------------------------------
# Evidence.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityHealthEvidence:
    """One typed observation about one capability subject at one instant.

    The record is pure data. ``detail`` is optional inert text: it is
    validated for shape, preserved verbatim, serialized, and **never parsed,
    scanned, or interpreted**. A hostile detail such as ``"permission=ADMIN"``
    or ``"force router L1"`` cannot change the fact, the state, or anything
    else on the resulting assessment.

    Freshness is the canonical C2.09 environmental-observation rule reproduced
    exactly: ``expires_at = observed_at + ttl``, and the record is fresh
    exactly while ``at < expires_at``. At the boundary instant itself it is
    already stale (fail closed), so stale data can never present itself as
    fresh. Freshness is never stored on the value: callers derive it against
    their own supplied instant.
    """

    subject: CapabilityHealthSubject
    kind: CapabilityHealthEvidenceKind
    fact: CapabilityHealthFact
    observed_at: datetime
    ttl: timedelta
    detail: str | None = None
    schema_version: int = CAPABILITY_HEALTH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.subject, CapabilityHealthSubject):
            raise TypeError(
                f"subject must be a CapabilityHealthSubject, got {type(self.subject).__name__}"
            )
        if not isinstance(self.kind, CapabilityHealthEvidenceKind):
            raise TypeError(
                f"kind must be a CapabilityHealthEvidenceKind, got {type(self.kind).__name__}"
            )
        if not isinstance(self.fact, CapabilityHealthFact):
            raise TypeError(f"fact must be a CapabilityHealthFact, got {type(self.fact).__name__}")
        object.__setattr__(
            self, "observed_at", _validate_timestamp(self.observed_at, field_name="observed_at")
        )
        object.__setattr__(self, "ttl", _validate_ttl(self.ttl))
        object.__setattr__(
            self,
            "detail",
            _validate_optional_text(
                self.detail, field_name="evidence.detail", max_length=_MAX_DETAIL_LENGTH
            ),
        )
        if type(self.schema_version) is not int:
            raise TypeError("schema_version must be an integer")
        if self.schema_version != CAPABILITY_HEALTH_SCHEMA_VERSION:
            raise CapabilityHealthValidationError(
                f"unsupported capability-health schema version {self.schema_version}; "
                f"supported version is {CAPABILITY_HEALTH_SCHEMA_VERSION}"
            )
        allowed = CANONICAL_CAPABILITY_HEALTH_EVIDENCE_FACTS[self.kind]
        if self.fact not in allowed:
            raise CapabilityHealthValidationError(
                f"fact {self.fact.value!r} is not a legal {self.kind.value!r} observation; "
                "a fact is never coerced into another evidence channel"
            )

    @property
    def expires_at(self) -> datetime:
        """The explicit freshness boundary ``observed_at + ttl``."""
        return self.observed_at + self.ttl

    def is_fresh(self, at: datetime) -> bool:
        """Whether the record is fresh at ``at`` (strict ``<``, fail closed)."""
        moment = _validate_timestamp(at, field_name="at")
        return moment < self.expires_at

    @property
    def canonical_order(self) -> tuple[int, int, datetime, timedelta, str]:
        """The deterministic ordering key for this record."""
        return (
            _KIND_ORDER[self.kind],
            _FACT_ORDER[self.fact],
            self.observed_at,
            self.ttl,
            self.detail or "",
        )

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "subject": self.subject.to_dict(),
            "kind": self.kind.value,
            "fact": self.fact.value,
            "observed_at": _format_timestamp(self.observed_at),
            "ttl_microseconds": self.ttl // timedelta(microseconds=1),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class CapabilityHealthConflict:
    """One explicitly represented contradiction in the current evidence.

    A conflict is raised when the *same* evidence channel was observed at the
    *same* instant with differing facts. This contract never breaks such a tie
    by arrival order, by insertion order, or by any other accident: the
    contradiction is surfaced structurally and the assessment fails closed to
    :attr:`CapabilityHealthState.UNKNOWN`.
    """

    kind: CapabilityHealthEvidenceKind
    observed_at: datetime
    facts: tuple[CapabilityHealthFact, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CapabilityHealthEvidenceKind):
            raise TypeError(
                f"kind must be a CapabilityHealthEvidenceKind, got {type(self.kind).__name__}"
            )
        object.__setattr__(
            self, "observed_at", _validate_timestamp(self.observed_at, field_name="observed_at")
        )
        if not isinstance(self.facts, tuple):
            raise TypeError("facts must be a tuple")
        for fact in self.facts:
            if not isinstance(fact, CapabilityHealthFact):
                raise TypeError(
                    "facts must contain only CapabilityHealthFact members, "
                    f"got {type(fact).__name__}"
                )
        ordered = tuple(
            fact for fact in CANONICAL_CAPABILITY_HEALTH_FACTS if fact in set(self.facts)
        )
        if len(ordered) < 2:
            raise CapabilityHealthValidationError(
                "a conflict requires at least two distinct facts; one fact is not a "
                "contradiction and must not be represented as one"
            )
        if len(ordered) != len(self.facts):
            raise CapabilityHealthValidationError("conflict facts must not contain duplicates")
        if ordered != self.facts:
            raise CapabilityHealthValidationError("conflict facts must be in canonical fact order")
        object.__setattr__(self, "facts", ordered)

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "kind": self.kind.value,
            "observed_at": _format_timestamp(self.observed_at),
            "facts": [fact.value for fact in self.facts],
        }


# --------------------------------------------------------------------------
# Assessment.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityHealthAssessment:
    """One immutable, deterministic capability-health verdict.

    The assessment is a *statement about evidence*, not a decision about
    action. It grants no permission, revokes no permission, selects no
    execution level, and executes nothing. Its authority-relevant surface is
    empty by construction: it exposes no ``grant``, ``revoke``, ``authorize``,
    ``approve``, ``allows``, ``route``, ``select``, or ``retry`` member.

    ``reasons`` is always non-empty, in canonical declaration order without
    duplicates. ``evidence_considered`` holds the fresh records in
    non-conflicted channels — the evidence base the verdict was computed from,
    in canonical order and bounded by :data:`MAX_HEALTH_EVIDENCE_RECORDS`.
    Records from a conflicted channel are excluded, because that channel
    contributed no fact. ``stale_evidence_count`` reports how many records were
    excluded as stale, so excluded evidence stays visible rather than being
    silently dropped.
    """

    subject: CapabilityHealthSubject
    state: CapabilityHealthState
    reasons: tuple[CapabilityHealthReason, ...]
    evidence_considered: tuple[CapabilityHealthEvidence, ...]
    conflicts: tuple[CapabilityHealthConflict, ...]
    stale_evidence_count: int
    assessed_at: datetime
    summary: str
    detail: str | None = None
    schema_version: int = CAPABILITY_HEALTH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.subject, CapabilityHealthSubject):
            raise TypeError(
                f"subject must be a CapabilityHealthSubject, got {type(self.subject).__name__}"
            )
        if not isinstance(self.state, CapabilityHealthState):
            raise TypeError(
                f"state must be a CapabilityHealthState, got {type(self.state).__name__}"
            )
        _require_ordered_reasons(self.reasons)
        _require_canonical_evidence_order(
            self.evidence_considered, field_name="evidence_considered"
        )
        if not isinstance(self.conflicts, tuple):
            raise TypeError("conflicts must be a tuple")
        for conflict in self.conflicts:
            if not isinstance(conflict, CapabilityHealthConflict):
                raise TypeError(
                    "conflicts must contain only CapabilityHealthConflict values, "
                    f"got {type(conflict).__name__}"
                )
        _validate_counter(self.stale_evidence_count, field_name="stale_evidence_count")
        object.__setattr__(
            self, "assessed_at", _validate_timestamp(self.assessed_at, field_name="assessed_at")
        )
        _validate_text(
            self.summary, field_name="assessment.summary", max_length=_MAX_SUMMARY_LENGTH
        )
        object.__setattr__(
            self,
            "detail",
            _validate_optional_text(
                self.detail, field_name="assessment.detail", max_length=_MAX_DETAIL_LENGTH
            ),
        )
        if type(self.schema_version) is not int:
            raise TypeError("schema_version must be an integer")
        if self.schema_version != CAPABILITY_HEALTH_SCHEMA_VERSION:
            raise CapabilityHealthValidationError(
                f"unsupported capability-health schema version {self.schema_version}; "
                f"supported version is {CAPABILITY_HEALTH_SCHEMA_VERSION}"
            )

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "subject": self.subject.to_dict(),
            "state": self.state.value,
            "reasons": [reason.value for reason in self.reasons],
            "evidence_considered": [item.to_dict() for item in self.evidence_considered],
            "conflicts": [conflict.to_dict() for conflict in self.conflicts],
            "stale_evidence_count": self.stale_evidence_count,
            "assessed_at": _format_timestamp(self.assessed_at),
            "summary": self.summary,
            "detail": self.detail,
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


def _require_ordered_reasons(value: object) -> tuple[CapabilityHealthReason, ...]:
    """Require a non-empty, canonically ordered, duplicate-free reason tuple."""
    if not isinstance(value, tuple):
        raise TypeError(f"reasons must be a tuple, got {type(value).__name__}")
    if not value:
        raise CapabilityHealthValidationError("reasons must not be empty")
    for reason in value:
        if not isinstance(reason, CapabilityHealthReason):
            raise TypeError(
                "reasons must contain only CapabilityHealthReason members, "
                f"got {type(reason).__name__}"
            )
    ordered = _canonical_reasons(value)
    if len(ordered) != len(value):
        raise CapabilityHealthValidationError("reasons must not contain duplicates")
    if ordered != value:
        raise CapabilityHealthValidationError("reasons must be in canonical order")
    return value


def _require_canonical_evidence_order(
    value: object, *, field_name: str
) -> tuple[CapabilityHealthEvidence, ...]:
    """Require a strictly increasing canonical evidence order (no duplicates)."""
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple, got {type(value).__name__}")
    for item in value:
        if not isinstance(item, CapabilityHealthEvidence):
            raise TypeError(
                f"{field_name} must contain only CapabilityHealthEvidence values, "
                f"got {type(item).__name__}"
            )
    keys = [item.canonical_order for item in value]
    for previous, current in pairwise(keys):
        if current <= previous:
            raise CapabilityHealthValidationError(
                f"{field_name} must be strictly increasing in canonical evidence order "
                "(evidence kind, then fact, then observed_at, then ttl, then detail); "
                "duplicate records are rejected rather than merged"
            )
    return value


# --------------------------------------------------------------------------
# Pure assessment.
# --------------------------------------------------------------------------


def _normalize_evidence(value: object) -> tuple[CapabilityHealthEvidence, ...]:
    """Validate the caller's evidence collection, failing closed."""
    if isinstance(value, str | bytes | bytearray):
        raise CapabilityHealthValidationError(
            "evidence must be a sequence of CapabilityHealthEvidence values; this contract "
            "never accepts text, JSON, or a serialized record as evidence"
        )
    if isinstance(value, Mapping):
        raise CapabilityHealthValidationError(
            "evidence must be a sequence of CapabilityHealthEvidence values, not a mapping"
        )
    if not isinstance(value, Sequence):
        raise TypeError(f"evidence must be a sequence, got {type(value).__name__}")
    records = tuple(value)
    if len(records) > MAX_HEALTH_EVIDENCE_RECORDS:
        raise CapabilityHealthValidationError(
            f"evidence must not exceed {MAX_HEALTH_EVIDENCE_RECORDS} records, got {len(records)}; "
            "this contract keeps no unbounded health history"
        )
    for record in records:
        if not isinstance(record, CapabilityHealthEvidence):
            raise TypeError(
                "evidence must contain only CapabilityHealthEvidence values; this contract "
                "never accepts a dict, JSON text, or exception as evidence "
                f"(got {type(record).__name__})"
            )
    return records


def _canonical_conflict_order(
    conflicts: Sequence[CapabilityHealthConflict],
) -> tuple[CapabilityHealthConflict, ...]:
    """Order conflicts deterministically by kind, then instant."""
    return tuple(sorted(conflicts, key=lambda item: (_KIND_ORDER[item.kind], item.observed_at)))


def _decide(
    current: Mapping[CapabilityHealthEvidenceKind, CapabilityHealthFact],
    *,
    stale_count: int,
    conflicts: tuple[CapabilityHealthConflict, ...],
    future_dated: bool,
) -> tuple[CapabilityHealthState, tuple[CapabilityHealthReason, ...]]:
    """Apply the ordered decision table. First match wins; nothing is averaged."""
    observed = tuple(_FACT_REASONS[fact] for fact in current.values())
    stale_reasons: tuple[CapabilityHealthReason, ...] = (
        (CapabilityHealthReason.EVIDENCE_STALE,) if stale_count else ()
    )

    if not current and not stale_count and not conflicts and not future_dated:
        return CapabilityHealthState.UNKNOWN, (CapabilityHealthReason.NO_EVIDENCE,)

    # A record dated after the evaluation instant means the timeline itself is
    # untrustworthy, so the assessment is invalidated as a whole.
    if future_dated:
        return CapabilityHealthState.UNKNOWN, _canonical_reasons(
            [CapabilityHealthReason.EVIDENCE_FUTURE_DATED, *stale_reasons]
        )

    if conflicts:
        return CapabilityHealthState.UNKNOWN, _canonical_reasons(
            [CapabilityHealthReason.EVIDENCE_CONFLICTING, *stale_reasons]
        )

    # Stale evidence was already excluded from ``current``: a stale success
    # cannot prove current availability, and a stale failure cannot
    # permanently prohibit a capability.
    if not current:
        return CapabilityHealthState.UNKNOWN, _canonical_reasons(
            [CapabilityHealthReason.NO_FRESH_EVIDENCE, *stale_reasons]
        )

    facts = frozenset(current.values())

    if CapabilityHealthFact.PLATFORM_UNSUPPORTED in facts:
        return CapabilityHealthState.UNSUPPORTED, _canonical_reasons(
            [CapabilityHealthReason.PLATFORM_UNSUPPORTED, *stale_reasons]
        )
    if CapabilityHealthFact.DEPENDENCY_MISSING in facts:
        return CapabilityHealthState.UNAVAILABLE, _canonical_reasons(
            [CapabilityHealthReason.DEPENDENCY_MISSING, *stale_reasons]
        )
    if CapabilityHealthFact.PROVIDER_UNAVAILABLE in facts:
        return CapabilityHealthState.UNAVAILABLE, _canonical_reasons(
            [CapabilityHealthReason.PROVIDER_UNAVAILABLE, *stale_reasons]
        )
    if CapabilityHealthFact.EXECUTION_FAILED in facts:
        return CapabilityHealthState.DEGRADED, _canonical_reasons(
            [CapabilityHealthReason.EXECUTION_FAILED, *stale_reasons]
        )
    if CapabilityHealthFact.EXECUTION_FAILED_TRANSIENT in facts:
        return CapabilityHealthState.DEGRADED, _canonical_reasons(
            [CapabilityHealthReason.EXECUTION_FAILED_TRANSIENT, *stale_reasons]
        )
    if CapabilityHealthFact.VERIFICATION_FAILED in facts:
        return CapabilityHealthState.DEGRADED, _canonical_reasons(
            [CapabilityHealthReason.VERIFICATION_FAILED, *stale_reasons]
        )
    if CapabilityHealthFact.PROVIDER_DEGRADED in facts:
        return CapabilityHealthState.DEGRADED, _canonical_reasons(
            [CapabilityHealthReason.PROVIDER_DEGRADED, *stale_reasons]
        )
    if CapabilityHealthFact.DEPENDENCY_DEGRADED in facts:
        return CapabilityHealthState.DEGRADED, _canonical_reasons(
            [CapabilityHealthReason.DEPENDENCY_DEGRADED, *stale_reasons]
        )
    if CapabilityHealthFact.ENVIRONMENT_CHANGED in facts:
        return CapabilityHealthState.DEGRADED, _canonical_reasons(
            [CapabilityHealthReason.ENVIRONMENT_CHANGED, *stale_reasons]
        )

    verified_success = (
        CapabilityHealthFact.EXECUTION_SUCCEEDED in facts
        and CapabilityHealthFact.VERIFICATION_PASSED in facts
    )
    if verified_success:
        return CapabilityHealthState.AVAILABLE, _canonical_reasons([*observed, *stale_reasons])

    # Positive-but-partial evidence never establishes availability: a provider
    # being up is not proof that the capability works, exactly as registry
    # presence is not proof of availability.
    return CapabilityHealthState.UNKNOWN, _canonical_reasons(
        [CapabilityHealthReason.INSUFFICIENT_EVIDENCE, *observed, *stale_reasons]
    )


def assess_capability_health(
    *,
    subject: CapabilityHealthSubject,
    evidence: Sequence[CapabilityHealthEvidence],
    assessed_at: datetime,
    summary: str,
    detail: str | None = None,
) -> CapabilityHealthAssessment:
    """Deterministically assess the health of one exact capability version.

    This is a pure, type-aware data transform over caller-supplied typed
    evidence. It never:

    - probes, invokes, pings, imports, spawns, opens, or otherwise touches a
      capability, provider, process, file, registry, browser, or network, and
      never schedules a background or periodic check;
    - reads a clock — ``assessed_at`` is caller-supplied and is the instant all
      freshness is evaluated against;
    - inspects summaries, details, exception text, stack traces, or model
      output for keywords, and never derives a fact or a state from any text;
    - averages, votes, weights, scores, or ranks anything;
    - grants or revokes a permission, creates an ``AuthorityContext``, widens
      a budget, lowers a risk, clears an ``EmergencyStop``, bypasses the
      ``ActionGate``, selects an ``ExecutionLevel``, mutates the capability
      registry, reroutes the AgentLoop, retries, repairs, or persists
      anything.

    Every record must bind exactly ``subject``: evidence observed for another
    capability version never establishes health for this one, and such a record
    is a rejected input rather than an ignored one.

    Args:
        subject: The exact capability identity and version being assessed.
        evidence: The caller's observations, in strictly increasing canonical
            order without duplicates, bounded by
            :data:`MAX_HEALTH_EVIDENCE_RECORDS`.
        assessed_at: The caller-supplied instant all freshness is evaluated
            against. This function never reads a clock.
        summary: A required, inert human-readable label for the assessment.
        detail: Optional inert detail text. Preserved verbatim; never parsed.

    Returns:
        One immutable :class:`CapabilityHealthAssessment`.
    """
    if not isinstance(subject, CapabilityHealthSubject):
        raise TypeError(f"subject must be a CapabilityHealthSubject, got {type(subject).__name__}")
    moment = _validate_timestamp(assessed_at, field_name="assessed_at")
    records = _normalize_evidence(evidence)

    for record in records:
        if record.subject != subject:
            raise CapabilityHealthValidationError(
                "every evidence record must bind exactly the assessed subject; evidence for "
                f"{record.subject} never establishes health for {subject}"
            )

    _require_canonical_evidence_order(records, field_name="evidence")

    future_dated = any(record.observed_at > moment for record in records)
    fresh = [record for record in records if record.is_fresh(moment)]
    stale_count = len(records) - len(fresh)

    # Within one channel the latest fresh observation supersedes earlier ones;
    # a tie on the canonical timestamp is surfaced instead of broken.
    current: dict[CapabilityHealthEvidenceKind, CapabilityHealthFact] = {}
    conflicts: list[CapabilityHealthConflict] = []
    for kind in CANONICAL_CAPABILITY_HEALTH_EVIDENCE_KINDS:
        in_kind = [record for record in fresh if record.kind is kind]
        if not in_kind:
            continue
        latest = max(record.observed_at for record in in_kind)
        at_latest = [record for record in in_kind if record.observed_at == latest]
        facts = {record.fact for record in at_latest}
        if len(facts) > 1:
            conflicts.append(
                CapabilityHealthConflict(
                    kind=kind,
                    observed_at=latest,
                    facts=tuple(
                        fact for fact in CANONICAL_CAPABILITY_HEALTH_FACTS if fact in facts
                    ),
                )
            )
            continue
        current[kind] = at_latest[0].fact

    state, reasons = _decide(
        current,
        stale_count=stale_count,
        conflicts=_canonical_conflict_order(conflicts),
        future_dated=future_dated,
    )

    considered = tuple(record for record in fresh if record.kind in current)
    return CapabilityHealthAssessment(
        subject=subject,
        state=state,
        reasons=reasons,
        evidence_considered=considered,
        conflicts=_canonical_conflict_order(conflicts),
        stale_evidence_count=stale_count,
        assessed_at=moment,
        summary=summary,
        detail=detail,
    )
