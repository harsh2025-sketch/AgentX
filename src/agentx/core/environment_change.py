"""Canonical inert environment-change detection contract (C4.04).

AgentX must be able to say, for one canonical failure diagnosis, whether the
explicit environment evidence associated with that failure shows that a
*relevant execution-environment assumption has changed*. This module owns that
boundary and the smallest immutable record that carries it. It owns nothing
else.

What this module is:

    * :class:`EnvironmentFactKind` — the canonical, closed vocabulary of
      *environment fact categories*. Every member is an index into facts that
      landed canonical vocabularies already name; :data:`CANONICAL_ENVIRONMENT_FACT_ANCHORS`
      records that mapping explicitly so the vocabulary cannot drift into a
      private world model.
    * :class:`EnvironmentFactKey` — one typed fact identity: a fact kind plus
      the canonical identity/reference string the fact is about (an
      application id, a capability id, a dependency reference, an endpoint, a
      UI selector, ...). The subject is inert text: it is never resolved,
      dereferenced, opened, or executed here.
    * :class:`EnvironmentFactValueKind` / :class:`EnvironmentFactValue` — the
      closed *typed* value vocabulary (``TEXT``, ``FLAG``, ``ABSENT``) that
      makes comparison type-aware. ``"true"`` (text) and ``True`` (flag) are
      different values and are never coerced into each other, and an explicit
      ``ABSENT`` is a positive observation, not a missing one.
    * :class:`EnvironmentObservation` — one typed fact observed at an explicit
      instant with an explicit TTL and canonical C2.02 provenance. Freshness
      is the canonical C2.09 environmental-observation rule, reproduced exactly:
      ``expires_at = observed_at + ttl`` and fresh exactly while
      ``at < expires_at``.
    * :class:`EnvironmentSnapshot` — one evidence-backed set of observations
      about exactly one environment scope: the canonical C2.02
      :class:`~agentx.core.knowledge.KnowledgeScope`, the canonical C2.07
      :class:`~agentx.core.provenance.EvidenceReference` the observations were
      read from, and the observations themselves.
    * :class:`EnvironmentFactChange` — one deterministic, typed before/after
      pair for a fact that was observed on both sides and differs.
    * :class:`EnvironmentChangeResult` / :class:`EnvironmentChangeReason` — the
      closed three-way result vocabulary and the closed reason-code vocabulary.
    * :class:`EnvironmentChangeDetection` — one immutable, deterministic,
      JSON-serializable record: the canonical C4.03
      :class:`~agentx.core.failure_diagnosis.FailureDiagnosis` this detection
      is associated with (embedded by value), the three-way result, both
      snapshots (prior and current evidence/references), the environment
      scope, the changed facts, the unchanged relevant facts, the reason
      codes, and inert summary/detail.
    * :func:`detect_environment_change` — the pure, deterministic,
      type-aware comparison of two typed snapshots inside one environment
      scope. No store query, no model, no keyword scan, no text analysis.

C4.04 answers "Did relevant execution-environment assumptions change, according
to the explicit environment evidence?" It does NOT answer "How should we repair
it?" and it does not repair anything.

What this module is emphatically NOT:

    * It is not a repair, and it holds no authority. It never generates a
      repair or a patch, never applies or proposes a diff, never modifies,
      versions, activates, or rolls back a procedure or ``ProcedureStore``,
      never executes a capability or a task, never shells out or spawns a
      subprocess, never touches the network or a browser, never researches the
      web, never invokes a model, never grants or revokes a Permission, never
      creates an ``AuthorityContext``, never bypasses an ``ActionGate``, never
      lowers a ``RiskLevel``, never widens a ``ResourceEnvelope``, never clears
      an ``EmergencyStop``, never transitions a ``Task``, never mutates Hive,
      never claims or fabricates verification, and never declares success.
      Environment-change detection is DIAGNOSIS DATA: authority belongs
      exclusively to ``agentx.kernel``.
    * It is not an inference engine. Nothing here inspects summaries, details,
      exception messages, stack traces, tracebacks, webpages, or model output
      and derives a fact, a value, or a result. Facts and values are always
      supplied as typed members of the closed vocabularies above; the only
      computation performed is an equality comparison between two explicitly
      typed values of the same typed fact key. A summary containing
      ``"the app was updated"`` never produces a fact here.
    * It is not a world model and it does not sense anything. It holds no
      inventory of machines, applications, capabilities, or dependencies, and
      it never queries, probes, or observes the environment. Both sides of the
      comparison are caller-supplied typed snapshots.
    * It carries no probability, confidence, score, ranking, ordering, or
      model invocation, and no result is ordered above another.

Three-way output:

    :class:`EnvironmentChangeResult` has exactly three members and every call
    lands on exactly one of them:

        RELEVANT_CHANGE_DETECTED — at least one typed fact was observed on
            both sides with equal type and differing value.
        NO_RELEVANT_CHANGE — every fact in the comparison was observed on both
            sides, at the same type, with an equal value.
        INSUFFICIENT_EVIDENCE — the fail-closed default. The comparison could
            not be completed or could not be trusted: a snapshot is missing,
            the two sides describe different environment scopes, no fact was
            observed, a fact was observed on only one side, or an observation
            is stale, future-dated, duplicated, or temporally unordered.

Absence is not change:

    A fact key that is *not observed* on one side is never reported as a
    change, and never fabricates one. Only an explicit
    :attr:`EnvironmentFactValueKind.ABSENT` value — a positive observation that
    the canonical contract makes meaningful — can differ from another value and
    therefore count as a change. Missing coverage degrades the result to
    :attr:`EnvironmentChangeResult.INSUFFICIENT_EVIDENCE` with an explicit
    ``FACT_UNOBSERVED_IN_*`` reason code.

Fail-conservative policy:

    Malformed data is rejected, never repaired: validation and deserialization
    raise, unknown enum strings are rejected rather than downgraded, unknown
    fields are rejected, and non-typed inputs (dicts, JSON strings, exception
    objects, stores) are refused by :func:`detect_environment_change`. When
    evidence is conflicting, stale, or out of order, the comparison is
    invalidated *as a whole* and the result is
    :attr:`EnvironmentChangeResult.INSUFFICIENT_EVIDENCE` — a change is never
    asserted from contradictory data, and "no change" is never asserted from
    data that could not be trusted.

Determinism:

    The same typed inputs always produce a byte-identical record and
    byte-identical canonical JSON. Changed and unchanged facts are emitted in
    canonical fact order (declaration order of
    :data:`CANONICAL_ENVIRONMENT_FACT_KINDS`, then subject) and reason codes in
    canonical declaration order without duplicates; both orders are enforced
    structurally by the record, not merely by the comparison function.

Identity reuse (no parallel world model):

    This module introduces no competing identifier type, no competing error
    hierarchy, no competing scope ontology, and no competing provenance or
    evidence record. It reuses:

    * the C4.03 :class:`~agentx.core.failure_diagnosis.FailureDiagnosis`
      (which itself embeds the C4.01
      :class:`~agentx.core.failure_taxonomy.FailureClassification` and the
      C4.02 :class:`~agentx.core.failure_localization.FailureLocalization`),
      embedded here by value and never rewritten, so a detection can never
      disagree with the failure it consumes;
    * the C2.02 :class:`~agentx.core.knowledge.KnowledgeScope` and
      :class:`~agentx.core.knowledge.ScopeDimension` as *the* environment
      scope representation;
    * the C2.02 :class:`~agentx.core.knowledge.ProvenanceReference` and
      :class:`~agentx.core.knowledge.ProvenanceKind` for observation
      provenance, and the C2.07
      :class:`~agentx.core.provenance.EvidenceReference` /
      :class:`~agentx.core.provenance.EvidenceKind` for the prior/current
      evidence references;
    * the C2.09 ``agentx.hive.environmental_cache`` freshness contract
      (``observed_at`` + explicit ``ttl``, strict ``<`` boundary) reproduced
      here by rule, because ``agentx.core`` is an inward leaf and must not
      import ``agentx.hive`` — exactly the same reuse-by-value discipline C4.03
      applies to the outward ``ProcedureNodeId``;
    * the canonical ``ScopeDimension``, ``FailureCategory``, and
      ``FailureLocationKind`` member names as the anchors of
      :data:`CANONICAL_ENVIRONMENT_FACT_ANCHORS`.

Deliberate non-goals owned by later tasks: patch generation (C4.05), repair
validation (C4.06), shadow repair (C4.07), procedure version replacement /
rollback (C4.08), and repair budgets / anti-loop (C4.09), plus repair
selection, repair execution, procedure rewriting, interpreter changes,
persistence or migration, environment sensing, and any model-driven diagnosis.

Historical note on the C4.04 label collision: an earlier merged task also used
the identifier C4.04 for the repair-candidate contract in
``agentx.core.repair_candidates``. That contract is preserved untouched and
remains valid support code; it is not the canonical C4.04. This module is the
canonical C4.04 (environment-change detection). Nothing was removed or renamed,
the two contracts coexist, and neither imports the other.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from typing import Final

from agentx.core.failure_diagnosis import (
    FailureDiagnosis,
    FailureDiagnosisDeserializationError,
)
from agentx.core.failure_localization import FailureLocationKind
from agentx.core.failure_taxonomy import FailureCategory
from agentx.core.knowledge import (
    KnowledgeScope,
    KnowledgeValidationError,
    ProvenanceKind,
    ProvenanceReference,
    ScopeDimension,
)
from agentx.core.provenance import EvidenceReference

__all__ = [
    "CANONICAL_ENVIRONMENT_CHANGE_REASONS",
    "CANONICAL_ENVIRONMENT_CHANGE_RESULTS",
    "CANONICAL_ENVIRONMENT_FACT_ANCHORS",
    "CANONICAL_ENVIRONMENT_FACT_KINDS",
    "CANONICAL_ENVIRONMENT_FACT_VALUE_KINDS",
    "ENVIRONMENT_CHANGE_DETECTION_SCHEMA_VERSION",
    "EnvironmentChangeDeserializationError",
    "EnvironmentChangeDetection",
    "EnvironmentChangeReason",
    "EnvironmentChangeResult",
    "EnvironmentChangeValidationError",
    "EnvironmentFactChange",
    "EnvironmentFactKey",
    "EnvironmentFactKind",
    "EnvironmentFactValue",
    "EnvironmentFactValueKind",
    "EnvironmentObservation",
    "EnvironmentSnapshot",
    "UnsupportedEnvironmentChangeSchemaVersionError",
    "detect_environment_change",
]

ENVIRONMENT_CHANGE_DETECTION_SCHEMA_VERSION: Final[int] = 1

_MAX_SUBJECT_LENGTH: Final[int] = 512
_MAX_SUMMARY_LENGTH: Final[int] = 512
_MAX_DETAIL_LENGTH: Final[int] = 4_096

_SNAPSHOT_FIELDS: Final[frozenset[str]] = frozenset({"scope", "evidence", "observations"})
_FACT_KEY_FIELDS: Final[frozenset[str]] = frozenset({"kind", "subject"})
_FACT_VALUE_FIELDS: Final[frozenset[str]] = frozenset({"kind", "text", "flag"})
_OBSERVATION_FIELDS: Final[frozenset[str]] = frozenset(
    {"fact", "value", "observed_at", "ttl_microseconds", "provenance"}
)
_FACT_CHANGE_FIELDS: Final[frozenset[str]] = frozenset(
    {"fact", "baseline_value", "current_value", "baseline_observed_at", "current_observed_at"}
)
_DETECTION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "diagnosis",
        "result",
        "reason_codes",
        "scope",
        "baseline",
        "current",
        "changed_facts",
        "unchanged_facts",
        "compared_at",
        "summary",
        "detail",
    }
)


class EnvironmentChangeValidationError(ValueError):
    """Raised when environment-change data violates the canonical contract."""


class EnvironmentChangeDeserializationError(EnvironmentChangeValidationError):
    """Raised when encoded environment-change data cannot be decoded safely."""


class UnsupportedEnvironmentChangeSchemaVersionError(EnvironmentChangeDeserializationError):
    """Raised when encoded data uses an unsupported environment-change schema."""


class EnvironmentFactKind(StrEnum):
    """The canonical, closed vocabulary of environment-fact categories.

    Each member names one category of *explicitly represented execution
    environment fact* whose change is relevant to a failure. Members are
    categories of fact, not conclusions: naming a category never asserts that
    anything changed, that a repair is needed, or that any capability,
    permission, or dependency may or may not be used.

    Every member is anchored to names that already exist in landed canonical
    vocabularies (see :data:`CANONICAL_ENVIRONMENT_FACT_ANCHORS`); this
    vocabulary deliberately adds no environment ontology of its own.

    Members:
        PLATFORM_IDENTITY: Which platform/host environment the attempt ran in
            (operating system, machine/environment label). Anchored to
            ``ScopeDimension.OPERATING_SYSTEM`` / ``ScopeDimension.ENVIRONMENT``.
        APPLICATION_IDENTITY: Which application the attempt depended on, by
            canonical application identity. Anchored to
            ``ScopeDimension.APPLICATION``.
        APPLICATION_VERSION: The observed version of that application.
            Anchored to ``ScopeDimension.APPLICATION_VERSION``.
        CAPABILITY_AVAILABILITY: Whether a governed capability was available at
            all. Anchored to ``FailureLocationKind.CAPABILITY`` /
            ``FailureCategory.CAPABILITY``.
        CAPABILITY_VERSION: The observed version/interface revision of a
            governed capability. Anchored to ``FailureLocationKind.CAPABILITY``.
        API_SCHEMA: A programmatic interface/schema fact the attempt depended
            on. Anchored to ``FailureCategory.API_CHANGE``.
        UI_STRUCTURE: A user-interface structure fact the attempt depended on.
            Anchored to ``FailureCategory.UI_CHANGE``.
        DEPENDENCY_AVAILABILITY: Whether an external dependency the attempt
            relied on was reachable/present. Anchored to
            ``FailureLocationKind.DEPENDENCY`` / ``FailureCategory.DEPENDENCY``.
        CONTEXT_CONFIGURATION: An explicitly represented project/context
            configuration fact. Anchored to ``ScopeDimension.PROJECT`` /
            ``ScopeDimension.CONTEXT``.
    """

    PLATFORM_IDENTITY = "platform_identity"
    APPLICATION_IDENTITY = "application_identity"
    APPLICATION_VERSION = "application_version"
    CAPABILITY_AVAILABILITY = "capability_availability"
    CAPABILITY_VERSION = "capability_version"
    API_SCHEMA = "api_schema"
    UI_STRUCTURE = "ui_structure"
    DEPENDENCY_AVAILABILITY = "dependency_availability"
    CONTEXT_CONFIGURATION = "context_configuration"


#: The canonical fact kinds in their declared order. The declared order is also
#: the canonical output order of changed/unchanged facts, so exported for
#: callers that enumerate or sort the vocabulary.
CANONICAL_ENVIRONMENT_FACT_KINDS: Final[tuple[EnvironmentFactKind, ...]] = tuple(
    EnvironmentFactKind
)

#: Explicit anchor map: each canonical fact kind indexes only names that
#: already exist in landed canonical vocabularies (C2.02 ``ScopeDimension``,
#: C4.01 ``FailureCategory``, C4.02 ``FailureLocationKind``). This is the
#: machine-checkable statement that C4.04 reuses existing AgentX
#: representations instead of inventing a parallel world model.
CANONICAL_ENVIRONMENT_FACT_ANCHORS: Final[Mapping[EnvironmentFactKind, tuple[str, ...]]] = (
    MappingProxyType(
        {
            EnvironmentFactKind.PLATFORM_IDENTITY: (
                ScopeDimension.OPERATING_SYSTEM.value,
                ScopeDimension.ENVIRONMENT.value,
                FailureCategory.ENVIRONMENT.value,
            ),
            EnvironmentFactKind.APPLICATION_IDENTITY: (ScopeDimension.APPLICATION.value,),
            EnvironmentFactKind.APPLICATION_VERSION: (ScopeDimension.APPLICATION_VERSION.value,),
            EnvironmentFactKind.CAPABILITY_AVAILABILITY: (
                FailureLocationKind.CAPABILITY.value,
                FailureCategory.CAPABILITY.value,
            ),
            EnvironmentFactKind.CAPABILITY_VERSION: (FailureLocationKind.CAPABILITY.value,),
            EnvironmentFactKind.API_SCHEMA: (FailureCategory.API_CHANGE.value,),
            EnvironmentFactKind.UI_STRUCTURE: (FailureCategory.UI_CHANGE.value,),
            EnvironmentFactKind.DEPENDENCY_AVAILABILITY: (
                FailureLocationKind.DEPENDENCY.value,
                FailureCategory.DEPENDENCY.value,
            ),
            EnvironmentFactKind.CONTEXT_CONFIGURATION: (
                ScopeDimension.PROJECT.value,
                ScopeDimension.CONTEXT.value,
            ),
        }
    )
)


class EnvironmentFactValueKind(StrEnum):
    """The canonical, closed *typed* value vocabulary for one environment fact.

    Comparison in this contract is type-aware: two values are comparable only
    when their kinds match, and a kind is never coerced into another. Text
    ``"true"`` and flag ``True`` are different values; text ``"1"`` and flag
    ``True`` are different values; nothing here parses, casts, or normalizes a
    value into another kind.

    Members:
        TEXT: An inert text value (an identity, a version string, a schema or
            UI-structure signature, a configuration label). Compared by exact
            string equality; no trimming, case folding, or version parsing is
            performed, because doing so would silently declare two distinct
            observations equal.
        FLAG: An explicit boolean value, normally availability. ``True`` and
            ``False`` are the only members of this kind.
        ABSENT: An explicit positive observation that the subject is *not*
            present/available. This is a value, not a gap: it is the only way
            this contract can represent a meaningful absence, and it can
            therefore differ from another value and count as a change. A fact
            that was simply never observed is not ``ABSENT``; it is missing
            from the snapshot and never counts as a change.

    Numeric values are deliberately absent from this vocabulary: numeric
    equality is not a deterministic identity comparison for environment facts
    (``1`` vs ``1.0``, float rounding, locale formatting), and versions are
    text by convention.
    """

    TEXT = "text"
    FLAG = "boolean"
    ABSENT = "absent"


#: The canonical value kinds in their declared order.
CANONICAL_ENVIRONMENT_FACT_VALUE_KINDS: Final[tuple[EnvironmentFactValueKind, ...]] = tuple(
    EnvironmentFactValueKind
)


class EnvironmentChangeResult(StrEnum):
    """The canonical, closed three-way result vocabulary.

    Members:
        INSUFFICIENT_EVIDENCE: The fail-closed default. The comparison could not
            be completed or could not be trusted. It records an absence of
            justification, not a verdict: it never claims that nothing changed,
            never suppresses any other subsystem, and never grants or withholds
            anything.
        RELEVANT_CHANGE_DETECTED: At least one typed fact was observed on both
            sides with matching kind and differing value. This is a statement
            about evidence, not a cause: it never asserts that the change
            caused the failure, and it never instructs a repair.
        NO_RELEVANT_CHANGE: Every fact in the comparison was observed on both
            sides, at matching kind, with equal value. This is scoped to the
            facts actually compared; it is not a claim that the environment is
            unchanged in general.
    """

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    RELEVANT_CHANGE_DETECTED = "relevant_change_detected"
    NO_RELEVANT_CHANGE = "no_relevant_change"


#: The canonical results in their declared order.
CANONICAL_ENVIRONMENT_CHANGE_RESULTS: Final[tuple[EnvironmentChangeResult, ...]] = tuple(
    EnvironmentChangeResult
)


class EnvironmentChangeReason(StrEnum):
    """The canonical, closed reason-code vocabulary for one detection result.

    Reason codes state *why* the result is what it is. They are diagnostic
    data: no code authorizes, forbids, schedules, or suppresses anything, and
    no code is ordered above another. Declaration order is the canonical output
    order.

    Members:
        BASELINE_MISSING: No prior snapshot was supplied, so nothing can be
            compared against.
        CURRENT_MISSING: No current snapshot was supplied.
        SCOPE_MISMATCH: The declared scope and the two snapshots do not all
            describe the same canonical environment scope. Facts observed in
            different scopes are not comparable.
        NO_FACTS_OBSERVED: Neither snapshot contains any observation.
        CONFLICTING_OBSERVATIONS: One snapshot carries more than one
            observation for the same fact key. Which one is current is
            unknowable, so the comparison is invalid as a whole.
        FUTURE_OBSERVATION: An observation is stamped after ``compared_at``.
        UNORDERED_OBSERVATIONS: The "current" snapshot contains an observation
            older than a baseline observation, so the two sides are not
            temporally ordered.
        STALE_BASELINE_OBSERVATION: A baseline observation is past its explicit
            freshness boundary at ``compared_at``.
        STALE_CURRENT_OBSERVATION: A current observation is past its explicit
            freshness boundary at ``compared_at``.
        FACT_UNOBSERVED_IN_BASELINE: A fact key observed only in the current
            snapshot. Not a change: new coverage is not evidence of change.
        FACT_UNOBSERVED_IN_CURRENT: A fact key observed only in the baseline
            snapshot. Not a change: lost coverage is not evidence of change.
        FACT_VALUE_CHANGED: At least one fact was observed on both sides with
            matching kind and differing value.
        ALL_COMPARED_FACTS_UNCHANGED: Every fact in the comparison was observed
            on both sides with matching kind and equal value.
    """

    BASELINE_MISSING = "baseline_missing"
    CURRENT_MISSING = "current_missing"
    SCOPE_MISMATCH = "scope_mismatch"
    NO_FACTS_OBSERVED = "no_facts_observed"
    CONFLICTING_OBSERVATIONS = "conflicting_observations"
    FUTURE_OBSERVATION = "future_observation"
    UNORDERED_OBSERVATIONS = "unordered_observations"
    STALE_BASELINE_OBSERVATION = "stale_baseline_observation"
    STALE_CURRENT_OBSERVATION = "stale_current_observation"
    FACT_UNOBSERVED_IN_BASELINE = "fact_unobserved_in_baseline"
    FACT_UNOBSERVED_IN_CURRENT = "fact_unobserved_in_current"
    FACT_VALUE_CHANGED = "fact_value_changed"
    ALL_COMPARED_FACTS_UNCHANGED = "all_compared_facts_unchanged"


#: The canonical reason codes in their declared (canonical output) order.
CANONICAL_ENVIRONMENT_CHANGE_REASONS: Final[tuple[EnvironmentChangeReason, ...]] = tuple(
    EnvironmentChangeReason
)


def _has_control_characters(value: str, *, allow_whitespace: bool) -> bool:
    allowed = {"\t", "\n", "\r"} if allow_whitespace else frozenset()
    return any(
        character < " " or character == "\x7f" for character in value if character not in allowed
    )


def _validate_inert_text(value: object, *, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise EnvironmentChangeValidationError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise EnvironmentChangeValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > max_length:
        raise EnvironmentChangeValidationError(
            f"{field_name} must be at most {max_length} characters"
        )
    if _has_control_characters(value, allow_whitespace=False):
        raise EnvironmentChangeValidationError(f"{field_name} must not contain control characters")
    return value


def _validate_subject(value: object) -> str:
    return _validate_inert_text(value, field_name="fact subject", max_length=_MAX_SUBJECT_LENGTH)


def _validate_summary(value: object) -> str:
    return _validate_inert_text(value, field_name="summary", max_length=_MAX_SUMMARY_LENGTH)


def _validate_optional_detail(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise EnvironmentChangeValidationError("detail must be a string or None")
    if not value or value != value.strip():
        raise EnvironmentChangeValidationError("detail must be non-empty and trimmed when provided")
    if len(value) > _MAX_DETAIL_LENGTH:
        raise EnvironmentChangeValidationError(
            f"detail must be at most {_MAX_DETAIL_LENGTH} characters"
        )
    if _has_control_characters(value, allow_whitespace=True):
        raise EnvironmentChangeValidationError("detail must not contain control characters")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise EnvironmentChangeValidationError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise EnvironmentChangeValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_ttl(value: object) -> timedelta:
    if not isinstance(value, timedelta):
        raise EnvironmentChangeValidationError("ttl must be a timedelta")
    if value <= timedelta(0):
        raise EnvironmentChangeValidationError("ttl must be strictly positive")
    return value


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise EnvironmentChangeDeserializationError(f"{field_name} must be a string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise EnvironmentChangeDeserializationError(
            f"{field_name} is not a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EnvironmentChangeDeserializationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _require_exact_fields(
    raw: Mapping[str, object], *, record_name: str, fields: frozenset[str]
) -> None:
    actual = set(raw)
    missing = fields - actual
    unknown = actual - fields
    if missing:
        raise EnvironmentChangeDeserializationError(
            f"{record_name} missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise EnvironmentChangeDeserializationError(
            f"{record_name} contains unknown fields: {sorted(unknown)}"
        )


def _parse_fact_kind(value: object, *, field_name: str) -> EnvironmentFactKind:
    if not isinstance(value, str):
        raise EnvironmentChangeDeserializationError(f"{field_name} must be a string")
    try:
        return EnvironmentFactKind(value)
    except ValueError as exc:
        raise EnvironmentChangeDeserializationError(
            f"unknown environment fact kind: {value!r}"
        ) from exc


def _parse_value_kind(value: object, *, field_name: str) -> EnvironmentFactValueKind:
    if not isinstance(value, str):
        raise EnvironmentChangeDeserializationError(f"{field_name} must be a string")
    try:
        return EnvironmentFactValueKind(value)
    except ValueError as exc:
        raise EnvironmentChangeDeserializationError(
            f"unknown environment fact value kind: {value!r}"
        ) from exc


def _parse_result(value: object) -> EnvironmentChangeResult:
    if not isinstance(value, str):
        raise EnvironmentChangeDeserializationError("result must be a string")
    try:
        return EnvironmentChangeResult(value)
    except ValueError as exc:
        raise EnvironmentChangeDeserializationError(
            f"unknown environment change result: {value!r}"
        ) from exc


def _parse_reason(value: object) -> EnvironmentChangeReason:
    if not isinstance(value, str):
        raise EnvironmentChangeDeserializationError("reason codes must contain strings")
    try:
        return EnvironmentChangeReason(value)
    except ValueError as exc:
        raise EnvironmentChangeDeserializationError(
            f"unknown environment change reason: {value!r}"
        ) from exc


def _require_object(value: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise EnvironmentChangeDeserializationError(f"{field_name} must be a JSON object")
    return value


def _parse_evidence_reference(value: object, *, field_name: str) -> EvidenceReference:
    try:
        return EvidenceReference.from_dict(_require_object(value, field_name=field_name))
    except KnowledgeValidationError as exc:
        raise EnvironmentChangeDeserializationError(f"{field_name} is invalid: {exc}") from exc


def _parse_scope(value: object, *, field_name: str) -> KnowledgeScope:
    try:
        return KnowledgeScope.from_dict(_require_object(value, field_name=field_name))
    except KnowledgeValidationError as exc:
        raise EnvironmentChangeDeserializationError(f"{field_name} is invalid: {exc}") from exc


def _parse_provenance_reference(value: object, *, field_name: str) -> ProvenanceReference:
    """Decode a canonical C2.02 provenance reference into the canonical record."""
    if not isinstance(value, Mapping):
        raise EnvironmentChangeDeserializationError(f"{field_name} must be a JSON object")
    _require_exact_fields(value, record_name=field_name, fields=frozenset({"kind", "reference"}))
    kind_raw = value["kind"]
    if not isinstance(kind_raw, str):
        raise EnvironmentChangeDeserializationError(f"{field_name} kind must be a string")
    try:
        kind = ProvenanceKind(kind_raw)
    except ValueError as exc:
        raise EnvironmentChangeDeserializationError(
            f"unknown provenance kind: {kind_raw!r}"
        ) from exc
    reference = value["reference"]
    if not isinstance(reference, str) or not reference or reference != reference.strip():
        raise EnvironmentChangeDeserializationError(
            f"{field_name} reference must be a non-empty trimmed string"
        )
    return ProvenanceReference(kind=kind, reference=reference)


def _fact_sort_key(fact: EnvironmentFactKey) -> tuple[int, str]:
    """Return the canonical ordering key of one fact: kind order, then subject."""
    return (CANONICAL_ENVIRONMENT_FACT_KINDS.index(fact.kind), fact.subject)


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentFactKey:
    """One typed environment-fact identity.

    The key is pure data. ``kind`` is a typed :class:`EnvironmentFactKind`
    member (never inferred from text) and ``subject`` is the canonical identity
    or reference string the fact is about — an application id, a capability id,
    a dependency reference, an endpoint, a UI selector, a platform label. The
    subject is inert: it is never resolved, dereferenced, opened, executed, or
    interpreted here, and hostile content inside it grants nothing.

    Equality is by ``(kind, subject)``, so two facts about different subjects
    are never compared with each other, and the same subject under two kinds
    (for example an application identity and its version) are two distinct
    facts.
    """

    kind: EnvironmentFactKind
    subject: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EnvironmentFactKind):
            raise EnvironmentChangeValidationError(
                "fact kind must be an EnvironmentFactKind member; "
                "this contract never infers one from text"
            )
        object.__setattr__(self, "subject", _validate_subject(self.subject))

    @property
    def canonical_order(self) -> tuple[int, str]:
        """The deterministic ordering key used for canonical fact order."""
        return _fact_sort_key(self)

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {"kind": self.kind.value, "subject": self.subject}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EnvironmentFactKey:
        """Validate and reconstruct one fact key, failing closed."""
        if not isinstance(raw, Mapping):
            raise EnvironmentChangeDeserializationError("fact key must be a JSON object")
        _require_exact_fields(raw, record_name="environment fact key", fields=_FACT_KEY_FIELDS)
        try:
            return cls(
                kind=_parse_fact_kind(raw["kind"], field_name="fact kind"),
                subject=_validate_subject(raw["subject"]),
            )
        except EnvironmentChangeDeserializationError:
            raise
        except EnvironmentChangeValidationError as exc:
            raise EnvironmentChangeDeserializationError(f"fact key is invalid: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentFactValue:
    """One explicitly typed environment-fact value.

    Exactly one payload slot is populated, matching ``kind``:

    * ``TEXT`` -> non-empty trimmed ``text``, ``flag`` is ``None``;
    * ``FLAG`` -> ``flag`` is ``True``/``False``, ``text`` is ``None``;
    * ``ABSENT`` -> both are ``None``.

    Comparison is plain dataclass equality over ``(kind, text, flag)``, which
    is what makes it type-aware: values of different kinds are never equal and
    are never coerced. The record is inert data; nothing here executes,
    dereferences, or interprets the text.
    """

    kind: EnvironmentFactValueKind
    text: str | None = None
    flag: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EnvironmentFactValueKind):
            raise EnvironmentChangeValidationError(
                "value kind must be an EnvironmentFactValueKind member"
            )
        if self.kind is EnvironmentFactValueKind.TEXT:
            if self.flag is not None:
                raise EnvironmentChangeValidationError("a TEXT value must not carry a flag")
            if self.text is None:
                raise EnvironmentChangeValidationError("a TEXT value requires text")
            object.__setattr__(
                self,
                "text",
                _validate_inert_text(
                    self.text, field_name="fact value text", max_length=_MAX_SUBJECT_LENGTH
                ),
            )
        elif self.kind is EnvironmentFactValueKind.FLAG:
            if self.text is not None:
                raise EnvironmentChangeValidationError("a FLAG value must not carry text")
            if not isinstance(self.flag, bool):
                raise EnvironmentChangeValidationError(
                    "a FLAG value requires a real bool; "
                    "this contract never coerces a string or number into a flag"
                )
        elif self.text is not None or self.flag is not None:
            raise EnvironmentChangeValidationError("an ABSENT value must not carry text or a flag")

    @property
    def is_absent(self) -> bool:
        """Whether this value is an explicit, meaningful absence observation."""
        return self.kind is EnvironmentFactValueKind.ABSENT

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {"kind": self.kind.value, "text": self.text, "flag": self.flag}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EnvironmentFactValue:
        """Validate and reconstruct one typed value, failing closed."""
        if not isinstance(raw, Mapping):
            raise EnvironmentChangeDeserializationError("fact value must be a JSON object")
        _require_exact_fields(raw, record_name="environment fact value", fields=_FACT_VALUE_FIELDS)
        text = raw["text"]
        if text is not None and not isinstance(text, str):
            raise EnvironmentChangeDeserializationError("fact value text must be a string or null")
        flag = raw["flag"]
        if flag is not None and not isinstance(flag, bool):
            raise EnvironmentChangeDeserializationError("fact value flag must be a bool or null")
        try:
            return cls(
                kind=_parse_value_kind(raw["kind"], field_name="fact value kind"),
                text=text,
                flag=flag,
            )
        except EnvironmentChangeDeserializationError:
            raise
        except EnvironmentChangeValidationError as exc:
            raise EnvironmentChangeDeserializationError(f"fact value is invalid: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentObservation:
    """One typed environment fact observed at one explicit instant.

    The observation is pure data. ``provenance`` is the canonical C2.02
    :class:`~agentx.core.knowledge.ProvenanceReference` saying where the
    observation claims to come from; it is never dereferenced, trusted, or
    verified here, and provenance never grants authority.

    Freshness is the canonical C2.09 environmental-observation rule reproduced
    exactly: ``expires_at = observed_at + ttl``, and the observation is fresh
    exactly while ``at < expires_at``. At the boundary instant itself it is
    already stale (fail closed), so stale data can never present itself as
    fresh.
    """

    fact: EnvironmentFactKey
    value: EnvironmentFactValue
    observed_at: datetime
    ttl: timedelta
    provenance: ProvenanceReference

    def __post_init__(self) -> None:
        if not isinstance(self.fact, EnvironmentFactKey):
            raise EnvironmentChangeValidationError("fact must be an EnvironmentFactKey")
        if not isinstance(self.value, EnvironmentFactValue):
            raise EnvironmentChangeValidationError("value must be an EnvironmentFactValue")
        object.__setattr__(
            self, "observed_at", _validate_timestamp(self.observed_at, field_name="observed_at")
        )
        object.__setattr__(self, "ttl", _validate_ttl(self.ttl))
        if not isinstance(self.provenance, ProvenanceReference):
            raise EnvironmentChangeValidationError(
                "provenance must be a canonical C2.02 ProvenanceReference; "
                "this contract invents no competing provenance record"
            )

    @property
    def expires_at(self) -> datetime:
        """The explicit freshness boundary ``observed_at + ttl``."""
        return self.observed_at + self.ttl

    def is_fresh(self, at: datetime) -> bool:
        """Whether the observation is fresh at ``at`` (strict ``<``, fail closed)."""
        moment = _validate_timestamp(at, field_name="at")
        return moment < self.expires_at

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "fact": self.fact.to_dict(),
            "value": self.value.to_dict(),
            "observed_at": _format_timestamp(self.observed_at),
            "ttl_microseconds": self.ttl // timedelta(microseconds=1),
            "provenance": self.provenance.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EnvironmentObservation:
        """Validate and reconstruct one observation, failing closed."""
        if not isinstance(raw, Mapping):
            raise EnvironmentChangeDeserializationError("observation must be a JSON object")
        _require_exact_fields(
            raw, record_name="environment observation", fields=_OBSERVATION_FIELDS
        )
        fact_raw = _require_object(raw["fact"], field_name="observation fact")
        value_raw = _require_object(raw["value"], field_name="observation value")
        ttl_raw = raw["ttl_microseconds"]
        if isinstance(ttl_raw, bool) or not isinstance(ttl_raw, int):
            raise EnvironmentChangeDeserializationError(
                "observation ttl_microseconds must be an integer"
            )
        if ttl_raw <= 0:
            raise EnvironmentChangeDeserializationError(
                "observation ttl_microseconds must be strictly positive"
            )
        provenance = _parse_provenance_reference(
            raw["provenance"], field_name="observation provenance"
        )
        try:
            return cls(
                fact=EnvironmentFactKey.from_dict(fact_raw),
                value=EnvironmentFactValue.from_dict(value_raw),
                observed_at=_parse_timestamp(
                    raw["observed_at"], field_name="observation observed_at"
                ),
                ttl=timedelta(microseconds=ttl_raw),
                provenance=provenance,
            )
        except EnvironmentChangeDeserializationError:
            raise
        except EnvironmentChangeValidationError as exc:
            raise EnvironmentChangeDeserializationError(f"observation is invalid: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentSnapshot:
    """One evidence-backed set of environment observations in one scope.

    A snapshot is the unit of comparison. ``scope`` is the canonical C2.02
    :class:`~agentx.core.knowledge.KnowledgeScope` describing *which*
    environment these observations describe; two snapshots with different
    scopes are never comparable, because a fact observed in one environment
    says nothing about another. ``evidence`` is the canonical C2.07
    :class:`~agentx.core.provenance.EvidenceReference` the observations were
    read from, so the record can always point at its prior/current evidence
    instead of paraphrasing it.

    An empty ``observations`` tuple is representable and means "nothing was
    observed", which is explicitly *not* evidence that nothing changed.
    """

    scope: KnowledgeScope
    evidence: EvidenceReference
    observations: tuple[EnvironmentObservation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scope, KnowledgeScope):
            raise EnvironmentChangeValidationError("scope must be a canonical KnowledgeScope")
        if not isinstance(self.evidence, EvidenceReference):
            raise EnvironmentChangeValidationError(
                "evidence must be a canonical EvidenceReference; "
                "a snapshot without an evidence reference is not evidence"
            )
        if not isinstance(self.observations, tuple):
            raise EnvironmentChangeValidationError("observations must be a tuple")
        for item in self.observations:
            if not isinstance(item, EnvironmentObservation):
                raise EnvironmentChangeValidationError(
                    "observations must contain only EnvironmentObservation items"
                )

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "scope": self.scope.to_dict(),
            "evidence": self.evidence.to_dict(),
            "observations": [item.to_dict() for item in self.observations],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EnvironmentSnapshot:
        """Validate and reconstruct one snapshot, failing closed."""
        if not isinstance(raw, Mapping):
            raise EnvironmentChangeDeserializationError("snapshot must be a JSON object")
        _require_exact_fields(raw, record_name="environment snapshot", fields=_SNAPSHOT_FIELDS)
        observations_raw = raw["observations"]
        if not isinstance(observations_raw, list):
            raise EnvironmentChangeDeserializationError("snapshot observations must be an array")
        observations: list[EnvironmentObservation] = []
        for index, item in enumerate(observations_raw):
            if not isinstance(item, Mapping):
                raise EnvironmentChangeDeserializationError(
                    f"snapshot observations[{index}] must be a JSON object"
                )
            observations.append(EnvironmentObservation.from_dict(item))
        try:
            return cls(
                scope=_parse_scope(raw["scope"], field_name="snapshot scope"),
                evidence=_parse_evidence_reference(raw["evidence"], field_name="snapshot evidence"),
                observations=tuple(observations),
            )
        except EnvironmentChangeDeserializationError:
            raise
        except EnvironmentChangeValidationError as exc:
            raise EnvironmentChangeDeserializationError(f"snapshot is invalid: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentFactChange:
    """One deterministic typed before/after pair for a changed fact.

    Both sides are required: a change is only asserted when the same typed fact
    key was observed in both snapshots and the two values differ. The two
    observation instants are carried so the record can show *when* each side
    was seen, and ``baseline_observed_at`` must not be later than
    ``current_observed_at`` (an inverted pair is rejected as malformed).
    """

    fact: EnvironmentFactKey
    baseline_value: EnvironmentFactValue
    current_value: EnvironmentFactValue
    baseline_observed_at: datetime
    current_observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.fact, EnvironmentFactKey):
            raise EnvironmentChangeValidationError("fact must be an EnvironmentFactKey")
        for name, value in (
            ("baseline_value", self.baseline_value),
            ("current_value", self.current_value),
        ):
            if not isinstance(value, EnvironmentFactValue):
                raise EnvironmentChangeValidationError(f"{name} must be an EnvironmentFactValue")
        object.__setattr__(
            self,
            "baseline_observed_at",
            _validate_timestamp(self.baseline_observed_at, field_name="baseline_observed_at"),
        )
        object.__setattr__(
            self,
            "current_observed_at",
            _validate_timestamp(self.current_observed_at, field_name="current_observed_at"),
        )
        if self.baseline_observed_at > self.current_observed_at:
            raise EnvironmentChangeValidationError(
                "baseline_observed_at must not be later than current_observed_at; "
                "temporally inverted evidence fails closed"
            )
        if self.baseline_value == self.current_value:
            raise EnvironmentChangeValidationError(
                "a fact change requires two differing values; "
                "identical values are an unchanged fact"
            )

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "fact": self.fact.to_dict(),
            "baseline_value": self.baseline_value.to_dict(),
            "current_value": self.current_value.to_dict(),
            "baseline_observed_at": _format_timestamp(self.baseline_observed_at),
            "current_observed_at": _format_timestamp(self.current_observed_at),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EnvironmentFactChange:
        """Validate and reconstruct one fact change, failing closed."""
        if not isinstance(raw, Mapping):
            raise EnvironmentChangeDeserializationError("fact change must be a JSON object")
        _require_exact_fields(
            raw, record_name="environment fact change", fields=_FACT_CHANGE_FIELDS
        )
        fact_raw = _require_object(raw["fact"], field_name="fact change fact")
        baseline_raw = _require_object(
            raw["baseline_value"], field_name="fact change baseline_value"
        )
        current_raw = _require_object(raw["current_value"], field_name="fact change current_value")
        try:
            return cls(
                fact=EnvironmentFactKey.from_dict(fact_raw),
                baseline_value=EnvironmentFactValue.from_dict(baseline_raw),
                current_value=EnvironmentFactValue.from_dict(current_raw),
                baseline_observed_at=_parse_timestamp(
                    raw["baseline_observed_at"], field_name="fact change baseline_observed_at"
                ),
                current_observed_at=_parse_timestamp(
                    raw["current_observed_at"], field_name="fact change current_observed_at"
                ),
            )
        except EnvironmentChangeDeserializationError:
            raise
        except EnvironmentChangeValidationError as exc:
            raise EnvironmentChangeDeserializationError(f"fact change is invalid: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class EnvironmentChangeDetection:
    """One immutable, deterministic environment-change detection record.

    The record is pure DIAGNOSIS DATA. Constructing, comparing, serializing, or
    decoding it repairs nothing, patches nothing, mutates no procedure,
    executes no capability, researches nothing, grants no Permission, lowers no
    risk, widens no budget, clears no stop, transitions no Task, fabricates no
    verification, and never declares success. Hostile content in any text field
    (``"ADMIN"``, ``"verified=true"``, ``"repair=approved"``, ``"risk=R0"``,
    ``"budget=unlimited"``) is inert string data, exactly like any other
    characters.

    Attributes:
        diagnosis: The canonical C4.03 failure diagnosis this detection is
            associated with, embedded by value and never rewritten. The
            detection can therefore never disagree with the failure it
            consumes, and full C4.01/C4.02 provenance travels with it.
        result: The canonical three-way
            :class:`EnvironmentChangeResult`.
        reason_codes: Why the result is what it is, in canonical declaration
            order without duplicates. Never empty.
        scope: The canonical environment scope the detection is about. It must
            equal the scope of every supplied snapshot *unless* the record
            explicitly reports :attr:`EnvironmentChangeReason.SCOPE_MISMATCH`,
            in which case at least one snapshot must actually differ — so a
            record can neither hide a scope mismatch nor invent one.
        baseline: The prior snapshot (prior evidence/reference), or ``None``
            when no baseline exists — which is itself an explicit
            ``BASELINE_MISSING`` reason, never a silent "no change".
        current: The current snapshot (current evidence/reference), or ``None``.
        changed_facts: The typed before/after pairs, in canonical fact order.
            Non-empty exactly when the result is ``RELEVANT_CHANGE_DETECTED``.
        unchanged_facts: The fact keys observed on both sides with equal
            values, in canonical fact order. Useful context; never a claim
            about facts that were not observed.
        compared_at: The timezone-aware instant the comparison is stated
            against. All freshness decisions are evaluated at this instant, and
            it is supplied by the caller: this contract never reads a clock.
        summary: Short inert statement of the detection.
        detail: Optional longer inert description.
    """

    diagnosis: FailureDiagnosis
    result: EnvironmentChangeResult
    reason_codes: tuple[EnvironmentChangeReason, ...]
    scope: KnowledgeScope
    baseline: EnvironmentSnapshot | None
    current: EnvironmentSnapshot | None
    changed_facts: tuple[EnvironmentFactChange, ...]
    unchanged_facts: tuple[EnvironmentFactKey, ...]
    compared_at: datetime
    summary: str
    detail: str | None = None
    schema_version: int = ENVIRONMENT_CHANGE_DETECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.diagnosis, FailureDiagnosis):
            raise EnvironmentChangeValidationError(
                "diagnosis must be a canonical C4.03 FailureDiagnosis; "
                "this contract never infers one from text"
            )
        if not isinstance(self.result, EnvironmentChangeResult):
            raise EnvironmentChangeValidationError("result must be an EnvironmentChangeResult")
        if not isinstance(self.scope, KnowledgeScope):
            raise EnvironmentChangeValidationError("scope must be a canonical KnowledgeScope")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise EnvironmentChangeValidationError("schema_version must be an integer")
        if self.schema_version != ENVIRONMENT_CHANGE_DETECTION_SCHEMA_VERSION:
            raise EnvironmentChangeValidationError(
                f"schema_version must be {ENVIRONMENT_CHANGE_DETECTION_SCHEMA_VERSION}"
            )
        reasons = _validate_reason_codes(self.reason_codes)
        object.__setattr__(self, "reason_codes", reasons)
        mismatched: list[str] = []
        for name, snapshot in (("baseline", self.baseline), ("current", self.current)):
            if snapshot is not None and not isinstance(snapshot, EnvironmentSnapshot):
                raise EnvironmentChangeValidationError(f"{name} must be an EnvironmentSnapshot")
            if snapshot is not None and snapshot.scope != self.scope:
                mismatched.append(name)
        scope_mismatch_reported = EnvironmentChangeReason.SCOPE_MISMATCH in reasons
        if mismatched and not scope_mismatch_reported:
            raise EnvironmentChangeValidationError(
                f"{'/'.join(mismatched)} snapshot scope must equal the detection scope unless the "
                "record reports SCOPE_MISMATCH; facts from a different environment scope are not "
                "comparable and the mismatch must be stated, not hidden"
            )
        if scope_mismatch_reported and not mismatched:
            raise EnvironmentChangeValidationError(
                "SCOPE_MISMATCH may only be reported when a supplied snapshot actually describes a "
                "different environment scope"
            )
        object.__setattr__(self, "changed_facts", _validate_changed_facts(self.changed_facts))
        object.__setattr__(self, "unchanged_facts", _validate_unchanged_facts(self.unchanged_facts))
        _validate_fact_key_disjointness(self.changed_facts, self.unchanged_facts)
        if self.result is EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED and not (
            self.changed_facts
        ):
            raise EnvironmentChangeValidationError(
                "RELEVANT_CHANGE_DETECTED requires at least one explicit changed fact; "
                "this contract never asserts a change it cannot show"
            )
        if self.result is not EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED and (
            self.changed_facts
        ):
            raise EnvironmentChangeValidationError(
                "changed facts are only representable with a RELEVANT_CHANGE_DETECTED result"
            )
        object.__setattr__(
            self, "compared_at", _validate_timestamp(self.compared_at, field_name="compared_at")
        )
        object.__setattr__(self, "summary", _validate_summary(self.summary))
        object.__setattr__(self, "detail", _validate_optional_detail(self.detail))

    @property
    def has_relevant_change(self) -> bool:
        """Whether a relevant environment change was explicitly detected."""
        return self.result is EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED

    @property
    def is_insufficient_evidence(self) -> bool:
        """Whether this record fails closed for lack of trustworthy evidence."""
        return self.result is EnvironmentChangeResult.INSUFFICIENT_EVIDENCE

    @property
    def baseline_evidence(self) -> EvidenceReference | None:
        """The canonical prior evidence reference, or ``None`` if no baseline."""
        return None if self.baseline is None else self.baseline.evidence

    @property
    def current_evidence(self) -> EvidenceReference | None:
        """The canonical current evidence reference, or ``None`` if no current."""
        return None if self.current is None else self.current.evidence

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "diagnosis": self.diagnosis.to_dict(),
            "result": self.result.value,
            "reason_codes": [reason.value for reason in self.reason_codes],
            "scope": self.scope.to_dict(),
            "baseline": None if self.baseline is None else self.baseline.to_dict(),
            "current": None if self.current is None else self.current.to_dict(),
            "changed_facts": [item.to_dict() for item in self.changed_facts],
            "unchanged_facts": [item.to_dict() for item in self.unchanged_facts],
            "compared_at": _format_timestamp(self.compared_at),
            "summary": self.summary,
            "detail": self.detail,
        }

    def to_json(self) -> str:
        """Serialize deterministically without object hooks or executable types."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EnvironmentChangeDetection:
        """Validate and reconstruct one canonical detection, failing closed."""
        if not isinstance(raw, Mapping):
            raise EnvironmentChangeDeserializationError("detection must be a JSON object")
        if "schema_version" not in raw:
            raise EnvironmentChangeDeserializationError(
                "detection missing required field: schema_version"
            )
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise EnvironmentChangeDeserializationError("schema_version must be an integer")
        if version != ENVIRONMENT_CHANGE_DETECTION_SCHEMA_VERSION:
            raise UnsupportedEnvironmentChangeSchemaVersionError(
                f"unsupported environment change schema version {version}; "
                f"supported version is {ENVIRONMENT_CHANGE_DETECTION_SCHEMA_VERSION}"
            )
        _require_exact_fields(
            raw, record_name="environment change detection", fields=_DETECTION_FIELDS
        )

        diagnosis_raw = raw["diagnosis"]
        if not isinstance(diagnosis_raw, Mapping):
            raise EnvironmentChangeDeserializationError("diagnosis must be a JSON object")
        try:
            diagnosis = FailureDiagnosis.from_dict(diagnosis_raw)
        except FailureDiagnosisDeserializationError as exc:
            raise EnvironmentChangeDeserializationError(f"diagnosis is invalid: {exc}") from exc

        reason_raw = raw["reason_codes"]
        if not isinstance(reason_raw, list):
            raise EnvironmentChangeDeserializationError("reason_codes must be a JSON array")
        reasons = tuple(_parse_reason(item) for item in reason_raw)

        changed_raw = raw["changed_facts"]
        if not isinstance(changed_raw, list):
            raise EnvironmentChangeDeserializationError("changed_facts must be a JSON array")
        changed: list[EnvironmentFactChange] = []
        for index, item in enumerate(changed_raw):
            if not isinstance(item, Mapping):
                raise EnvironmentChangeDeserializationError(
                    f"changed_facts[{index}] must be a JSON object"
                )
            changed.append(EnvironmentFactChange.from_dict(item))

        unchanged_raw = raw["unchanged_facts"]
        if not isinstance(unchanged_raw, list):
            raise EnvironmentChangeDeserializationError("unchanged_facts must be a JSON array")
        unchanged: list[EnvironmentFactKey] = []
        for index, item in enumerate(unchanged_raw):
            if not isinstance(item, Mapping):
                raise EnvironmentChangeDeserializationError(
                    f"unchanged_facts[{index}] must be a JSON object"
                )
            unchanged.append(EnvironmentFactKey.from_dict(item))

        try:
            return cls(
                schema_version=version,
                diagnosis=diagnosis,
                result=_parse_result(raw["result"]),
                reason_codes=reasons,
                scope=_parse_scope(raw["scope"], field_name="scope"),
                baseline=_parse_optional_snapshot(raw["baseline"], field_name="baseline"),
                current=_parse_optional_snapshot(raw["current"], field_name="current"),
                changed_facts=tuple(changed),
                unchanged_facts=tuple(unchanged),
                compared_at=_parse_timestamp(raw["compared_at"], field_name="compared_at"),
                summary=_validate_summary(raw["summary"]),
                detail=_validate_optional_detail(raw["detail"]),
            )
        except EnvironmentChangeDeserializationError:
            raise
        except EnvironmentChangeValidationError as exc:
            raise EnvironmentChangeDeserializationError(f"detection is invalid: {exc}") from exc

    @classmethod
    def from_json(cls, text: str) -> EnvironmentChangeDetection:
        """Decode canonical JSON and fail closed on malformed or non-object data."""
        if not isinstance(text, str):
            raise EnvironmentChangeDeserializationError("detection JSON must be text")
        try:
            decoded = json.loads(text)
        except ValueError as exc:
            raise EnvironmentChangeDeserializationError("detection JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise EnvironmentChangeDeserializationError("detection JSON root must be an object")
        return cls.from_dict(decoded)


def _parse_optional_snapshot(value: object, *, field_name: str) -> EnvironmentSnapshot | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise EnvironmentChangeDeserializationError(f"{field_name} must be a JSON object or null")
    return EnvironmentSnapshot.from_dict(value)


def _validate_reason_codes(value: object) -> tuple[EnvironmentChangeReason, ...]:
    if not isinstance(value, tuple):
        raise EnvironmentChangeValidationError("reason_codes must be a tuple")
    if not value:
        raise EnvironmentChangeValidationError(
            "reason_codes must not be empty; every detection states why"
        )
    for item in value:
        if not isinstance(item, EnvironmentChangeReason):
            raise EnvironmentChangeValidationError(
                "reason_codes must contain only EnvironmentChangeReason members"
            )
    ordered = tuple(
        reason for reason in CANONICAL_ENVIRONMENT_CHANGE_REASONS if reason in set(value)
    )
    if ordered != value:
        raise EnvironmentChangeValidationError(
            "reason_codes must be in canonical order without duplicates"
        )
    return ordered


def _validate_changed_facts(value: object) -> tuple[EnvironmentFactChange, ...]:
    if not isinstance(value, tuple):
        raise EnvironmentChangeValidationError("changed_facts must be a tuple")
    for item in value:
        if not isinstance(item, EnvironmentFactChange):
            raise EnvironmentChangeValidationError(
                "changed_facts must contain only EnvironmentFactChange items"
            )
    _require_canonical_fact_order([item.fact for item in value], field_name="changed_facts")
    return value


def _validate_unchanged_facts(value: object) -> tuple[EnvironmentFactKey, ...]:
    if not isinstance(value, tuple):
        raise EnvironmentChangeValidationError("unchanged_facts must be a tuple")
    for item in value:
        if not isinstance(item, EnvironmentFactKey):
            raise EnvironmentChangeValidationError(
                "unchanged_facts must contain only EnvironmentFactKey items"
            )
    _require_canonical_fact_order(list(value), field_name="unchanged_facts")
    return value


def _require_canonical_fact_order(facts: list[EnvironmentFactKey], *, field_name: str) -> None:
    keys = [fact.canonical_order for fact in facts]
    if any(current <= previous for previous, current in pairwise(keys)):
        raise EnvironmentChangeValidationError(
            f"{field_name} must be strictly increasing in canonical fact order "
            "(fact kind declaration order, then subject)"
        )


def _validate_fact_key_disjointness(
    changed: tuple[EnvironmentFactChange, ...], unchanged: tuple[EnvironmentFactKey, ...]
) -> None:
    changed_keys = {item.fact for item in changed}
    overlap = changed_keys & set(unchanged)
    if overlap:
        raise EnvironmentChangeValidationError(
            "a fact cannot be both changed and unchanged in one detection"
        )


def _index_observations(
    snapshot: EnvironmentSnapshot,
) -> dict[EnvironmentFactKey, EnvironmentObservation]:
    indexed: dict[EnvironmentFactKey, EnvironmentObservation] = {}
    for observation in snapshot.observations:
        indexed[observation.fact] = observation
    return indexed


def detect_environment_change(
    *,
    diagnosis: FailureDiagnosis,
    scope: KnowledgeScope,
    baseline: EnvironmentSnapshot | None,
    current: EnvironmentSnapshot | None,
    compared_at: datetime,
    summary: str,
    detail: str | None = None,
) -> EnvironmentChangeDetection:
    """Deterministically compare two typed snapshots in one environment scope.

    This is a pure, type-aware data transform. It compares explicitly typed
    facts of the same typed fact key by exact value equality and never:

    - inspects summaries, details, exception text, stack traces, webpages, or
      model output for keywords, and never derives a fact, a value, or a result
      from any text;
    - senses, probes, queries, or observes the environment, a store, a graph,
      a browser, or the network, and never invokes a model;
    - coerces a value into another kind, trims or case-folds a text value, or
      parses a version string — ``"1.10"`` and ``"1.9"`` differ as text and
      nothing here decides which is newer;
    - repairs, patches, rewrites, versions, activates, or rolls back anything,
      executes any capability or task, grants any permission, lowers any risk,
      widens any budget, clears any stop, transitions any task, fabricates any
      verification, or declares success.

    Decision order (fail conservative):

    1. a missing snapshot, a scope mismatch, an empty comparison, conflicting
       observations, future-dated observations, unordered observations, or a
       stale observation invalidates the comparison as a whole and yields
       ``INSUFFICIENT_EVIDENCE`` with the explicit reason codes;
    2. otherwise any fact observed on both sides with matching kind and
       differing value yields ``RELEVANT_CHANGE_DETECTED``;
    3. otherwise any fact observed on only one side yields
       ``INSUFFICIENT_EVIDENCE`` — lost or new coverage is never a change;
    4. otherwise every compared fact was equal and the result is
       ``NO_RELEVANT_CHANGE``.

    *compared_at* is caller-supplied and is the instant all freshness is
    evaluated against; this function never reads a clock.
    """
    if not isinstance(diagnosis, FailureDiagnosis):
        raise EnvironmentChangeValidationError(
            "diagnosis must be a canonical C4.03 FailureDiagnosis; "
            "this contract never accepts a dict, JSON text, or exception as a diagnosis"
        )
    if not isinstance(scope, KnowledgeScope):
        raise EnvironmentChangeValidationError("scope must be a canonical KnowledgeScope")
    for name, snapshot in (("baseline", baseline), ("current", current)):
        if snapshot is not None and not isinstance(snapshot, EnvironmentSnapshot):
            raise EnvironmentChangeValidationError(f"{name} must be an EnvironmentSnapshot or None")
    moment = _validate_timestamp(compared_at, field_name="compared_at")

    def build(
        result: EnvironmentChangeResult,
        reasons: tuple[EnvironmentChangeReason, ...],
        changed: tuple[EnvironmentFactChange, ...] = (),
        unchanged: tuple[EnvironmentFactKey, ...] = (),
    ) -> EnvironmentChangeDetection:
        return EnvironmentChangeDetection(
            schema_version=ENVIRONMENT_CHANGE_DETECTION_SCHEMA_VERSION,
            diagnosis=diagnosis,
            result=result,
            reason_codes=reasons,
            scope=scope,
            baseline=baseline,
            current=current,
            changed_facts=changed,
            unchanged_facts=unchanged,
            compared_at=moment,
            summary=summary,
            detail=detail,
        )

    insufficient = EnvironmentChangeResult.INSUFFICIENT_EVIDENCE
    if baseline is None or current is None:
        reasons: list[EnvironmentChangeReason] = []
        if baseline is None:
            reasons.append(EnvironmentChangeReason.BASELINE_MISSING)
        if current is None:
            reasons.append(EnvironmentChangeReason.CURRENT_MISSING)
        return build(insufficient, tuple(reasons))

    if baseline.scope != scope or current.scope != scope:
        return build(insufficient, (EnvironmentChangeReason.SCOPE_MISMATCH,))

    validity: list[EnvironmentChangeReason] = []
    baseline_index = _index_observations(baseline)
    current_index = _index_observations(current)
    if len(baseline_index) != len(baseline.observations) or len(current_index) != len(
        current.observations
    ):
        validity.append(EnvironmentChangeReason.CONFLICTING_OBSERVATIONS)
    for observation in (*baseline.observations, *current.observations):
        if observation.observed_at > moment:
            validity.append(EnvironmentChangeReason.FUTURE_OBSERVATION)
            break
    for snapshot, reason in (
        (baseline, EnvironmentChangeReason.STALE_BASELINE_OBSERVATION),
        (current, EnvironmentChangeReason.STALE_CURRENT_OBSERVATION),
    ):
        if any(not observation.is_fresh(moment) for observation in snapshot.observations):
            validity.append(reason)
    baseline_instants = [item.observed_at for item in baseline.observations]
    current_instants = [item.observed_at for item in current.observations]
    if baseline_instants and current_instants and max(baseline_instants) > min(current_instants):
        validity.append(EnvironmentChangeReason.UNORDERED_OBSERVATIONS)

    if validity:
        return build(insufficient, _canonical_reasons(validity))

    changed: list[EnvironmentFactChange] = []
    unchanged: list[EnvironmentFactKey] = []
    unobserved: list[EnvironmentChangeReason] = []
    for fact in sorted(
        set(baseline_index) | set(current_index), key=lambda item: item.canonical_order
    ):
        before = baseline_index.get(fact)
        after = current_index.get(fact)
        if before is not None and after is not None:
            if before.value == after.value:
                unchanged.append(fact)
            else:
                changed.append(
                    EnvironmentFactChange(
                        fact=fact,
                        baseline_value=before.value,
                        current_value=after.value,
                        baseline_observed_at=before.observed_at,
                        current_observed_at=after.observed_at,
                    )
                )
        elif before is not None:
            unobserved.append(EnvironmentChangeReason.FACT_UNOBSERVED_IN_CURRENT)
        else:
            unobserved.append(EnvironmentChangeReason.FACT_UNOBSERVED_IN_BASELINE)

    if changed:
        return build(
            EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED,
            (EnvironmentChangeReason.FACT_VALUE_CHANGED,),
            changed=tuple(changed),
            unchanged=tuple(unchanged),
        )
    if unobserved:
        return build(insufficient, _canonical_reasons(unobserved), unchanged=tuple(unchanged))
    if not unchanged:
        return build(insufficient, (EnvironmentChangeReason.NO_FACTS_OBSERVED,))
    return build(
        EnvironmentChangeResult.NO_RELEVANT_CHANGE,
        (EnvironmentChangeReason.ALL_COMPARED_FACTS_UNCHANGED,),
        unchanged=tuple(unchanged),
    )


def _canonical_reasons(
    reasons: list[EnvironmentChangeReason],
) -> tuple[EnvironmentChangeReason, ...]:
    """Deduplicate reason codes into canonical declaration order."""
    present = set(reasons)
    return tuple(reason for reason in CANONICAL_ENVIRONMENT_CHANGE_REASONS if reason in present)
