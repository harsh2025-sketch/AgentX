"""Deterministic salience and archive-tier policy for Hive records (C6.05).

This module is the canonical policy for deciding which Hive records remain
**active/high-salience** and which belong in the **archival/low-salience**
tier. It is a pure decision boundary over evidence that already exists on
canonical records — memory status, provenance, timestamps, verification,
supersession, scope, negative/failure importance — plus caller-supplied
context (an injected clock, an optional live-reference count, and the
optionally current environment scope).

ARCHIVE IS NOT DELETE
=====================

The outcome vocabulary :class:`SalienceTier` contains exactly ``ACTIVE`` and
``ARCHIVAL``. There is deliberately no delete/destroy/purge outcome anywhere
in this contract. An ``ARCHIVAL`` decision lowers salience only: the record
stays durably stored, byte-for-byte, and remains retrievable exactly as
before. Audit evidence, important failures, negative experience,
contradictions, provenance, and superseded history are never deleted by (or
through) this policy — the policy holds no store reference at all, so it is
structurally incapable of writing, rewriting, or destroying anything.

Deliberate non-goals (owned elsewhere, asserted absent here):

    * **No consolidation.** Records are never merged, deduplicated,
      summarized, or rewritten. Two similar records stay two records.
    * **No revalidation.** The policy reads ``status`` verbatim and never
      performs (or schedules) a lifecycle transition. ``ARCHIVAL`` is a
      salience projection, NOT a ``KnowledgeStatus`` change; status changes
      remain explicit C2.08 acts through the canonical store.
    * **No model calls.** There are no model providers, no reasoning
      imports, no network, and no callables except the injected clock (a
      pure time source validated to return a timezone-aware datetime).
    * **No deletion of protected evidence.** Negative experience and failed
      episodes are always ACTIVE (:data:`SalienceReasonCode.NEGATIVE_IMPORTANCE`
      / ``FAILURE_IMPORTANCE``) because AgentX invariant #8 requires failed
      approaches to be remembered. Conflicted records remain active and
      visible (``CONFLICT_VISIBLE``) until an explicit C2.08 resolution.

Evidence-based factors (all deterministic, all read from canonical fields):

    * **recency** — age measured from the record's last activity timestamp
      (``max(created_at, verified_at)`` for knowledge; ``observed_at`` for
      negative experience; ``max(created_at, ended_at)`` for episodes);
    * **reuse/reference count** — only when the caller explicitly supplies a
      live-reference count; the policy never infers reuse from content;
    * **verification/support status** — the canonical ``KnowledgeStatus``
      selects which configured age threshold applies;
    * **negative/failure importance** — unconditional protection;
    * **supersession** — ``SUPERSEDED`` records are archival history;
    * **scope/environment relevance** — a record scoped to a named
      application/environment that disagrees with the caller-supplied current
      environment on a shared dimension uses a shorter threshold;
    * **provenance quality** — presence of the provenance hook is required
      for full trust-evidence retention (architecture invariant: every
      remembered claim eventually has provenance). Provenance KIND is never
      ranked: channels of origin carry no trust.

Thresholds are canonical configuration, not magic constants: every knob
lives on the frozen, validated :class:`SaliencePolicyConfig`.

Inertness: every decision returned here is DATA, never authority. A decision
cannot grant Permission, change RiskLevel, enlarge a ResourceEnvelope, bypass
the Action Gate, clear an EmergencyStop, execute a Capability, transition a
Task, promote/demote a ``KnowledgeStatus``, or change which records a store
contains. Resource budgets are untouched by definition: archiving frees no
space, so no resource accounting happens here.

This module belongs to ``agentx.hive`` and imports only ``agentx.core``
contracts plus the standard library.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from typing import Final

from agentx.core.episodes import EpisodeOutcome, EpisodeRecord
from agentx.core.ids import EpisodeId, KnowledgeId, NegativeExperienceId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
)
from agentx.core.negative_experience import NegativeExperienceRecord

__all__ = [
    "DEFAULT_SALIENCE_CONFIG",
    "SalienceClockError",
    "SalienceConfigError",
    "SalienceDecision",
    "SalienceEvidence",
    "SalienceEvidenceError",
    "SalienceFactors",
    "SaliencePolicy",
    "SaliencePolicyConfig",
    "SaliencePolicyError",
    "SalienceReasonCode",
    "SalienceSubject",
    "SalienceSubjectKind",
    "SalienceTier",
]

#: Knowledge statuses whose trust/support evidence earns the trusted age
#: threshold (when the provenance hook is present).
_TRUSTED_STATUSES: Final[frozenset[KnowledgeStatus]] = frozenset(
    {
        KnowledgeStatus.PROVISIONAL,
        KnowledgeStatus.SUPPORTED,
        KnowledgeStatus.VERIFIED,
    }
)


class SaliencePolicyError(ValueError):
    """Base error for the salience/archive policy contract."""


class SalienceConfigError(SaliencePolicyError):
    """Raised when a salience-policy configuration violates the contract."""


class SalienceClockError(SaliencePolicyError):
    """Raised when the injected clock does not return a timezone-aware datetime."""


class SalienceEvidenceError(SaliencePolicyError):
    """Raised when caller-supplied evaluation evidence violates the contract."""


class SalienceTier(StrEnum):
    """The complete decision vocabulary: archive is not delete.

    ``ACTIVE`` — high salience; belongs in the working set of current
    knowledge. ``ARCHIVAL`` — low salience; historical/low-relevance, retained
    durably and retrievably, never deleted and never rewritten.
    """

    ACTIVE = "active"
    ARCHIVAL = "archival"


class SalienceReasonCode(StrEnum):
    """Deterministic reason codes attached to every salience decision.

    Members are declared in their canonical explanation order; decisions
    always emit a non-empty tuple whose order is fixed by construction.
    """

    #: The record is within its applicable age threshold.
    RECENTLY_CREATED = "recently_created"
    #: PROVISIONAL/SUPPORTED evidence resists archival.
    TRUST_EVIDENCE = "trust_evidence"
    #: VERIFIED evidence resists archival (verification is history, not authority).
    VERIFIED_RESISTANCE = "verified_resistance"
    #: Caller-supplied live reference count reached the configured minimum.
    REUSE_RESISTANCE = "reuse_resistance"
    #: A contradictory pair stays active and visible until explicit resolution.
    CONFLICT_VISIBLE = "conflict_visible"
    #: Negative experience is protected: failed approaches must be remembered.
    NEGATIVE_IMPORTANCE = "negative_importance"
    #: A FAILED episode is important failure evidence and stays active.
    FAILURE_IMPORTANCE = "failure_importance"
    #: A SUPERSEDED record is replaced history: archival, preserved verbatim.
    SUPERSEDED_HISTORY = "superseded_history"
    #: UNVERIFIED content aged beyond the unverified threshold.
    STALE_UNVERIFIED = "stale_unverified"
    #: DEGRADED (or provenance-capped trusted) content beyond that threshold.
    STALE_DEGRADED = "stale_degraded"
    #: Trusted content aged beyond the trusted threshold with no other resistance.
    ARCHIVAL_BY_AGE = "archival_by_age"
    #: A non-failed episode aged beyond the episode threshold.
    EPISODE_HISTORY = "episode_history"
    #: Record scope disagrees with the current environment on a shared dimension.
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    #: Trusted status retained without the provenance hook (threshold capped).
    PROVENANCE_ABSENT = "provenance_absent"


class SalienceSubjectKind(StrEnum):
    """The canonical Hive record classes this policy evaluates."""

    KNOWLEDGE = "knowledge"
    NEGATIVE_EXPERIENCE = "negative_experience"
    EPISODE = "episode"


@dataclass(frozen=True, slots=True)
class SalienceSubject:
    """Opaque identity of the evaluated record (kind plus canonical id string)."""

    kind: SalienceSubjectKind
    identifier: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SalienceSubjectKind):
            raise TypeError("subject kind must be a SalienceSubjectKind")
        if (
            not isinstance(self.identifier, str)
            or not self.identifier
            or self.identifier != (self.identifier.strip())
        ):
            raise ValueError("subject identifier must be a non-empty trimmed string")


@dataclass(frozen=True, slots=True)
class SaliencePolicyConfig:
    """Canonical, validated thresholds for the salience/archive policy.

    Every configurable behavior is an explicit field — there are no scattered
    magic constants. Defaults are the canonical AgentX baseline.

    Validation contract (fail-closed):

    * every duration is a strictly positive ``timedelta``;
    * ``min_reuse_resistance`` is an integer >= 1 (booleans rejected);
    * the age ladder must be monotone: ``mismatched_environment_max_age <=
      unverified_max_age <= degraded_max_age <= trusted_max_age``, encoding
      that less-relevant content reaches the archival tier no later than
      more-relevant content. ``episode_max_age`` is independent (a different
      record class with its own retention character).
    """

    unverified_max_age: timedelta = timedelta(days=30)
    degraded_max_age: timedelta = timedelta(days=90)
    trusted_max_age: timedelta = timedelta(days=365)
    mismatched_environment_max_age: timedelta = timedelta(days=7)
    episode_max_age: timedelta = timedelta(days=90)
    min_reuse_resistance: int = 2

    def __post_init__(self) -> None:
        for name in (
            "unverified_max_age",
            "degraded_max_age",
            "trusted_max_age",
            "mismatched_environment_max_age",
            "episode_max_age",
        ):
            value = getattr(self, name)
            if not isinstance(value, timedelta):
                raise TypeError(f"{name} must be a timedelta")
            if value <= timedelta(0):
                raise SalienceConfigError(f"{name} must be strictly positive")
        if type(self.min_reuse_resistance) is not int:
            raise TypeError("min_reuse_resistance must be an int")
        if self.min_reuse_resistance < 1:
            raise SalienceConfigError("min_reuse_resistance must be at least 1")
        ladder = (
            ("mismatched_environment_max_age", self.mismatched_environment_max_age),
            ("unverified_max_age", self.unverified_max_age),
            ("degraded_max_age", self.degraded_max_age),
            ("trusted_max_age", self.trusted_max_age),
        )
        for (_, earlier), (later_name, later) in pairwise(ladder):
            if earlier > later:
                raise SalienceConfigError(
                    f"{later_name} must not be shorter than "
                    f"{earlier} ({earlier!r} > {later_name} {later!r})"
                )


#: The canonical default configuration, exported as the single named baseline.
DEFAULT_SALIENCE_CONFIG: Final[SaliencePolicyConfig] = SaliencePolicyConfig()


@dataclass(frozen=True, slots=True)
class SalienceEvidence:
    """Caller-supplied evaluation context that the record itself cannot carry.

    ``reuse_count`` is the number of live references to the record at
    evaluation time, as known to the caller (for example procedure or
    retrieval references). The policy never infers, caches, or validates the
    origin of this count; it is claimed evidence, used exactly as given.
    ``current_environment`` is the caller's description of the environment now
    in effect; ``None`` (or an empty scope) applies no environment factor.
    """

    reuse_count: int = 0
    current_environment: KnowledgeScope | None = None

    def __post_init__(self) -> None:
        if type(self.reuse_count) is not int:
            raise TypeError("reuse_count must be an int")
        if self.reuse_count < 0:
            raise SalienceEvidenceError("reuse_count must not be negative")
        if self.current_environment is not None and not isinstance(
            self.current_environment, KnowledgeScope
        ):
            raise TypeError("current_environment must be a KnowledgeScope or None")


@dataclass(frozen=True, slots=True)
class SalienceFactors:
    """Immutable snapshot of the deterministic factors behind one decision.

    Provided for auditability and reproducibility: the same factors plus the
    same configuration always reproduce the same tier and reason codes.
    Fields that do not apply to a record class are ``None``/``False``/``0``.
    """

    age: timedelta | None = None
    effective_threshold: timedelta | None = None
    environment_mismatch: bool = False
    provenance_cap_applied: bool = False
    reuse_count: int = 0


@dataclass(frozen=True, slots=True)
class SalienceDecision:
    """One immutable, fully reason-coded salience decision.

    DATA, never authority: the decision mutates nothing and can grant
    nothing. ``reason_codes`` is a non-empty, duplicate-free, deterministically
    ordered tuple. ``evaluated_at`` is the (UTC) clock reading used.
    """

    subject: SalienceSubject
    tier: SalienceTier
    reason_codes: tuple[SalienceReasonCode, ...]
    evaluated_at: datetime
    factors: SalienceFactors = field(default_factory=SalienceFactors)

    def __post_init__(self) -> None:
        if not isinstance(self.subject, SalienceSubject):
            raise TypeError("subject must be a SalienceSubject")
        if not isinstance(self.tier, SalienceTier):
            raise TypeError("tier must be a SalienceTier")
        if not isinstance(self.reason_codes, tuple) or not self.reason_codes:
            raise ValueError("reason_codes must be a non-empty tuple")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason_codes must not contain duplicates")
        for code in self.reason_codes:
            if not isinstance(code, SalienceReasonCode):
                raise TypeError("reason_codes entries must be SalienceReasonCode values")
        if not isinstance(self.evaluated_at, datetime):
            raise TypeError("evaluated_at must be a datetime")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must be timezone-aware")
        if not isinstance(self.factors, SalienceFactors):
            raise TypeError("factors must be a SalienceFactors")

    @property
    def is_active(self) -> bool:
        """Whether the record stays in the active/high-salience tier."""
        return self.tier is SalienceTier.ACTIVE

    @property
    def is_archival(self) -> bool:
        """Whether the record belongs in the archival/low-salience tier."""
        return self.tier is SalienceTier.ARCHIVAL


def _system_utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_nonempty_trimmed(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty trimmed string")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be a non-empty trimmed string")
    return value


def _environment_mismatch(
    record_scope: KnowledgeScope, current_environment: KnowledgeScope | None
) -> bool:
    """Whether the record scope disagrees with the current environment.

    Deterministic exact comparison: a mismatch exists when both scopes are
    non-empty and at least one shared dimension maps to different values. A
    globally scoped record (empty scope) is relevant everywhere by definition
    and never mismatches; no current environment supplied means no signal.
    """
    if current_environment is None:
        return False
    if not record_scope.dimensions or not current_environment.dimensions:
        return False
    return any(
        dimension in current_environment.dimensions
        and current_environment.dimensions[dimension] != value
        for dimension, value in record_scope.dimensions.items()
    )


@dataclass(frozen=True, slots=True)
class SaliencePolicy:
    """Deterministic salience/archive decision boundary over Hive records.

    The policy is a frozen value object holding exactly its configuration and
    its injected clock — no store, no cache, no thread, no scheduler, no model
    client. Every ``evaluate_*`` call is a pure function of (record, evidence,
    config, clock reading) and returns a fresh immutable decision. Time is
    read exclusively through ``clock`` so decisions are fully reproducible in
    tests; the clock must return a timezone-aware datetime.
    """

    config: SaliencePolicyConfig = field(default_factory=SaliencePolicyConfig)
    clock: Callable[[], datetime] = field(default=_system_utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.config, SaliencePolicyConfig):
            raise TypeError("config must be a SaliencePolicyConfig")
        if not callable(self.clock):
            raise TypeError("clock must be a callable returning a timezone-aware datetime")

    def _now(self) -> datetime:
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise SalienceClockError("clock must return a timezone-aware datetime")
        return now.astimezone(UTC)

    # -- Knowledge records ---------------------------------------------------

    def evaluate_knowledge(
        self,
        record: KnowledgeRecord,
        evidence: SalienceEvidence | None = None,
    ) -> SalienceDecision:
        """Decide the salience tier of one canonical knowledge record.

        Decision order is fixed and documented; the first protective/
        historical rule that applies decides the tier, otherwise the
        configured age ladder decides:

        1. ``CONFLICTED`` — ACTIVE: contradictions stay visible until an
           explicit C2.08-owned resolution;
        2. ``SUPERSEDED`` — ARCHIVAL: replaced history, preserved verbatim;
        3. live-reference resistance — ACTIVE when the caller-supplied
           ``reuse_count`` reaches ``min_reuse_resistance``;
        4. otherwise the effective age threshold is selected by status —
           ``trusted_max_age`` for PROVISIONAL/SUPPORTED/VERIFIED (capped to
           ``degraded_max_age`` when the provenance hook is absent),
           ``degraded_max_age`` for DEGRADED, ``unverified_max_age`` for
           UNVERIFIED — and further capped to
           ``mismatched_environment_max_age`` when the record scope mismatches
           the current environment. Age is measured from
           ``max(created_at, verified_at)`` and a record archives exactly
           when ``age >= effective_threshold`` (fail-closed at the boundary:
           the archival tier loses nothing, so the boundary instant already
           archives).
        """
        if not isinstance(record, KnowledgeRecord):
            raise TypeError("record must be a canonical KnowledgeRecord")
        checked_evidence = SalienceEvidence() if evidence is None else evidence
        now = self._now()
        mismatch = _environment_mismatch(record.scope, checked_evidence.current_environment)
        last_activity = (
            record.created_at
            if record.verified_at is None or record.verified_at <= record.created_at
            else record.verified_at
        )
        age = now - last_activity

        if record.status is KnowledgeStatus.CONFLICTED:
            return self._decision(
                record.knowledge_id,
                SalienceSubjectKind.KNOWLEDGE,
                SalienceTier.ACTIVE,
                (SalienceReasonCode.CONFLICT_VISIBLE,),
                now,
                SalienceFactors(reuse_count=checked_evidence.reuse_count),
            )
        if record.status is KnowledgeStatus.SUPERSEDED:
            return self._decision(
                record.knowledge_id,
                SalienceSubjectKind.KNOWLEDGE,
                SalienceTier.ARCHIVAL,
                (SalienceReasonCode.SUPERSEDED_HISTORY,),
                now,
                SalienceFactors(
                    age=age,
                    environment_mismatch=mismatch,
                    reuse_count=checked_evidence.reuse_count,
                ),
            )
        if checked_evidence.reuse_count >= self.config.min_reuse_resistance:
            return self._decision(
                record.knowledge_id,
                SalienceSubjectKind.KNOWLEDGE,
                SalienceTier.ACTIVE,
                (SalienceReasonCode.REUSE_RESISTANCE,),
                now,
                SalienceFactors(
                    age=age, environment_mismatch=mismatch, reuse_count=checked_evidence.reuse_count
                ),
            )

        provenance_cap = record.status in _TRUSTED_STATUSES and record.provenance is None
        if record.status in _TRUSTED_STATUSES:
            threshold = (
                self.config.degraded_max_age if provenance_cap else (self.config.trusted_max_age)
            )
        elif record.status is KnowledgeStatus.DEGRADED:
            threshold = self.config.degraded_max_age
        else:
            threshold = self.config.unverified_max_age
        if mismatch and self.config.mismatched_environment_max_age < threshold:
            threshold = self.config.mismatched_environment_max_age

        reasons: list[SalienceReasonCode]
        if age >= threshold:
            tier = SalienceTier.ARCHIVAL
            if record.status is KnowledgeStatus.UNVERIFIED:
                reasons = [SalienceReasonCode.STALE_UNVERIFIED]
            elif record.status is KnowledgeStatus.DEGRADED or provenance_cap:
                reasons = [SalienceReasonCode.STALE_DEGRADED]
            else:
                reasons = [SalienceReasonCode.ARCHIVAL_BY_AGE]
        else:
            tier = SalienceTier.ACTIVE
            if record.status is KnowledgeStatus.VERIFIED:
                reasons = [SalienceReasonCode.VERIFIED_RESISTANCE]
            elif record.status in _TRUSTED_STATUSES:
                reasons = [SalienceReasonCode.TRUST_EVIDENCE]
            else:
                reasons = [SalienceReasonCode.RECENTLY_CREATED]
        if mismatch:
            reasons.append(SalienceReasonCode.ENVIRONMENT_MISMATCH)
        if provenance_cap:
            reasons.append(SalienceReasonCode.PROVENANCE_ABSENT)
        return self._decision(
            record.knowledge_id,
            SalienceSubjectKind.KNOWLEDGE,
            tier,
            tuple(reasons),
            now,
            SalienceFactors(
                age=age,
                effective_threshold=threshold,
                environment_mismatch=mismatch,
                provenance_cap_applied=provenance_cap,
                reuse_count=checked_evidence.reuse_count,
            ),
        )

    # -- Negative experience -------------------------------------------------

    def evaluate_negative_experience(self, record: NegativeExperienceRecord) -> SalienceDecision:
        """Decide the salience tier of one remembered failed approach.

        Unconditionally ACTIVE: AgentX requires failed approaches to be
        remembered, so negative experience — important failures included —
        is never demoted to the archival tier and never deleted, whatever
        its age and whatever configuration is supplied.
        """
        if not isinstance(record, NegativeExperienceRecord):
            raise TypeError("record must be a canonical NegativeExperienceRecord")
        now = self._now()
        return self._decision(
            record.negative_experience_id,
            SalienceSubjectKind.NEGATIVE_EXPERIENCE,
            SalienceTier.ACTIVE,
            (SalienceReasonCode.NEGATIVE_IMPORTANCE,),
            now,
            SalienceFactors(age=now - record.observed_at),
        )

    # -- Episodes ------------------------------------------------------------

    def evaluate_episode(self, record: EpisodeRecord) -> SalienceDecision:
        """Decide the salience tier of one canonical historical episode.

        A ``FAILED`` episode is important failure evidence and stays ACTIVE
        unconditionally. Other outcomes are history: they archive exactly
        when their age (from ``max(created_at, ended_at)``) reaches
        ``episode_max_age``. Episodes are append-only evidence either way —
        archival here never deletes the audit trail.
        """
        if not isinstance(record, EpisodeRecord):
            raise TypeError("record must be a canonical EpisodeRecord")
        now = self._now()
        if record.outcome is EpisodeOutcome.FAILED:
            return self._decision(
                record.episode_id,
                SalienceSubjectKind.EPISODE,
                SalienceTier.ACTIVE,
                (SalienceReasonCode.FAILURE_IMPORTANCE,),
                now,
                SalienceFactors(age=now - record.created_at),
            )
        last_activity = (
            record.created_at
            if record.ended_at is None or record.ended_at <= record.created_at
            else record.ended_at
        )
        age = now - last_activity
        threshold = self.config.episode_max_age
        if age >= threshold:
            return self._decision(
                record.episode_id,
                SalienceSubjectKind.EPISODE,
                SalienceTier.ARCHIVAL,
                (SalienceReasonCode.EPISODE_HISTORY,),
                now,
                SalienceFactors(age=age, effective_threshold=threshold),
            )
        return self._decision(
            record.episode_id,
            SalienceSubjectKind.EPISODE,
            SalienceTier.ACTIVE,
            (SalienceReasonCode.RECENTLY_CREATED,),
            now,
            SalienceFactors(age=age, effective_threshold=threshold),
        )

    # -- Shared construction -------------------------------------------------

    def _decision(
        self,
        record_id: KnowledgeId | NegativeExperienceId | EpisodeId,
        kind: SalienceSubjectKind,
        tier: SalienceTier,
        reason_codes: tuple[SalienceReasonCode, ...],
        evaluated_at: datetime,
        factors: SalienceFactors,
    ) -> SalienceDecision:
        _validate_nonempty_trimmed(record_id.to_str(), field_name="record identity")
        return SalienceDecision(
            subject=SalienceSubject(kind=kind, identifier=record_id.to_str()),
            tier=tier,
            reason_codes=reason_codes,
            evaluated_at=evaluated_at,
            factors=factors,
        )
