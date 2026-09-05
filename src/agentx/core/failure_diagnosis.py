"""Canonical inert procedure-node failure-diagnosis contract (C4.03).

AgentX must be able to say *what explicit diagnostic facts are known* about a
failure that C4.02 has already localized to a canonical Procedure node. This
module owns that boundary and the smallest immutable record that carries it.
It owns nothing else.

What this module is:

    * :class:`DiagnosticEvidenceKind` — the canonical, closed vocabulary of
      *observed evidence* categories. Each member binds to an existing
      canonical identity or reference already recorded elsewhere (an
      ``AgentXError`` code, a C2.06 negative-experience record, an
      execution-chain correlation UUID, an episode, a task).
    * :class:`DiagnosticEvidence` — one explicit structured fact: a typed
      evidence kind plus exactly one matching canonical reference. Free text,
      keywords, stack traces, and model output are never evidence here.
    * :class:`DiagnosticConclusion` — the canonical, closed vocabulary of
      *conclusions* (``UNKNOWN`` fail-closed default and the single explicit
      conclusion that the localized node itself is implicated). A conclusion
      is always supplied by the caller as a typed enum member; it is never
      computed, inferred, or derived from evidence.
    * :class:`FailureDiagnosis` — one immutable, deterministic,
      JSON-serializable record embedding a canonical C4.01
      ``FailureClassification`` and a canonical C4.02 ``FailureLocalization``
      (whose kind must be ``PROCEDURE_NODE``), carrying explicit structured
      evidence, an explicit conclusion, and inert summary/detail.
    * :func:`package_diagnosis` — pure packaging of already-validated typed
      data into a ``FailureDiagnosis`` record. No store query, no model, no
      inference, no text analysis.

C4.03 answers "What explicit diagnostic facts are known about the localized
procedure-node failure?" It does NOT answer "How should we repair it?".

What this module is emphatically NOT:

    * It is not an inference engine. Nothing here inspects arbitrary text,
      keywords, summaries, stack traces, tracebacks, exception objects, or
      observations and derives an evidence kind, a conclusion, or a category.
      An evidence kind and a conclusion are always supplied explicitly as
      typed enum members. A string containing ``"node_implicated"`` or
      ``"unknown"`` never becomes a conclusion here.
    * It is not root-cause proof. A C4.02 ``PROCEDURE_NODE`` localization is
      the *subject pointer* of this record — it is NOT itself proof that the
      node implementation is the root cause, and the record never converts the
      pointer, the classification, correlation, or any evidence into causation.
      Evidence and conclusion are separate typed components, and nothing turns
      one into the other automatically.
    * It is not classification. ``FailureCategory`` and
      ``FailureClassification`` remain owned by C4.01 and are embedded here by
      value, never rewritten, never inferred, never mutated.
    * It is not localization. ``FailureLocationKind`` and
      ``FailureLocalization`` remain owned by C4.02 and are embedded here by
      value; the embedded location is never recomputed from category or
      evidence.
    * It is not authority. A diagnosis grants nothing, revokes nothing,
      retries nothing, repairs nothing, and proves nothing. Authority belongs
      exclusively to ``agentx.kernel``.
    * It is not a repair, retry, fallback, rollback, escalation, suppression,
      or verification instruction. Recording a diagnosis never executes,
      patches, rewrites, or versions a procedure; never changes Task state;
      never grants a Permission; never bypasses an ActionGate; never lowers
      risk; never widens a budget; never clears an EmergencyStop; never
      fabricates verification; never invokes models; never activates a
      procedure; and never mutates Hive.
    * It carries no probability, confidence, score, ranking, embedding, or
      model invocation.

Evidence-driven fail-closed policy: :attr:`DiagnosticConclusion.UNKNOWN` is a
first-class, always-valid conclusion and the only default. A record whose
pointer or evidence is insufficient for a conclusion says so by carrying
``UNKNOWN``; evidence never promotes itself to a conclusion. Deserialization
never coerces an unrecognized conclusion or evidence-kind string into a
canonical value — unknown input is rejected. A diagnosis with an empty
``evidence`` tuple is valid only while the conclusion remains ``UNKNOWN``; an
explicit :attr:`DiagnosticConclusion.NODE_IMPLICATED` conclusion without at
least one explicit structured evidence item is rejected (fail closed), because
a localized node pointer is not evidence of implication.

Identity reuse: this module introduces no competing identifier types and no
competing error hierarchy. It reuses ``TaskId``, ``EpisodeId``,
``ProcedureId``, and ``NegativeExperienceId`` from :mod:`agentx.core.ids`, the
canonical ``AgentXError`` code rule from :mod:`agentx.core.errors`, the
execution-chain ``correlation_id`` UUID already used by C2.06/C2.10/C4.01/C4.02,
the C4.01 :class:`~agentx.core.failure_taxonomy.FailureClassification` and the
C4.02 :class:`~agentx.core.failure_localization.FailureLocalization` records,
and the graph-local procedure-node identity as a validated string exactly as
C4.02 carries it (the canonical ``ProcedureNodeId`` type lives outward in
``agentx.procedures`` and must not be imported into ``agentx.core``). The
procedure-node subject is *only* the one embedded C4.02 localization carries,
so this record cannot disagree with the localization it consumes. The record
never queries a store or a graph to check that a node still exists: presence
checks are execution-time concerns owned by later consumers, not by this pure
data boundary, and the record never fabricates such knowledge.

Deliberate non-goals owned by later tasks: environment-change detection
(C4.04), patch generation (C4.05), repair validation (C4.06), shadow repair
(C4.07), version/rollback (C4.08), and repair budgets/anti-loop (C4.09), plus
repair execution, procedure rewriting, interpreter changes, persistence or
migration, and model diagnosis.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from agentx.core.errors import _validate_error_code as _validate_canonical_error_code
from agentx.core.failure_localization import (
    FailureLocalization,
    FailureLocalizationDeserializationError,
    FailureLocationKind,
)
from agentx.core.failure_taxonomy import (
    FailureClassification,
    FailureClassificationDeserializationError,
)
from agentx.core.ids import (
    EpisodeId,
    NegativeExperienceId,
    ProcedureId,
    TaskId,
)

__all__ = [
    "CANONICAL_DIAGNOSTIC_CONCLUSIONS",
    "CANONICAL_DIAGNOSTIC_EVIDENCE_KINDS",
    "FAILURE_DIAGNOSIS_SCHEMA_VERSION",
    "DiagnosticConclusion",
    "DiagnosticEvidence",
    "DiagnosticEvidenceKind",
    "FailureDiagnosis",
    "FailureDiagnosisDeserializationError",
    "FailureDiagnosisValidationError",
    "UnsupportedFailureDiagnosisSchemaVersionError",
    "package_diagnosis",
]

FAILURE_DIAGNOSIS_SCHEMA_VERSION: Final[int] = 1

_MAX_SUMMARY_LENGTH: Final[int] = 512
_MAX_DETAIL_LENGTH: Final[int] = 4_096
_MAX_ERROR_CODE_LENGTH: Final[int] = 256

_DIAGNOSIS_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "classification",
        "localization",
        "evidence",
        "conclusion",
        "summary",
        "diagnosed_at",
        "detail",
    }
)

_EVIDENCE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "kind",
        "error_code",
        "negative_experience_id",
        "correlation_id",
        "episode_id",
        "task_id",
    }
)


class FailureDiagnosisValidationError(ValueError):
    """Raised when failure-diagnosis data violates the canonical contract."""


class FailureDiagnosisDeserializationError(FailureDiagnosisValidationError):
    """Raised when encoded failure-diagnosis data cannot be decoded safely."""


class UnsupportedFailureDiagnosisSchemaVersionError(FailureDiagnosisDeserializationError):
    """Raised when encoded data uses an unsupported failure-diagnosis schema."""


class DiagnosticConclusion(StrEnum):
    """The canonical, closed conclusion vocabulary for a procedure-node diagnosis.

    A conclusion is an explicit assertion supplied by the caller as a typed
    member. It is never computed, inferred, or derived from evidence, and no
    member implies a remedy, a retry, a permission change, or a prohibition.
    A conclusion never states *how to repair* anything and never claims proof:
    members are candidate-locus statements about the localized node, kept
    separate from the observed evidence.

    Members:
        UNKNOWN: No conclusion is asserted. This is the fail-closed default
            and is always valid — with or without evidence — because a
            localized node pointer and correlated facts are not proof of
            implication. A record that concludes nothing remains fully
            representable.
        NODE_IMPLICATED: Explicit conclusion that the localized procedure
            node's own implementation or definition is implicated as a
            candidate failure locus. Supplying this member requires at least
            one explicit structured evidence item; the pointer, category, and
            correlation alone are never sufficient, and the module never
            manufactures this member by itself. It is a claim about the node,
            not proof of causation and not a repair instruction.
    """

    UNKNOWN = "unknown"
    NODE_IMPLICATED = "node_implicated"


#: The canonical conclusions in their declared order. Exported so callers may
#: enumerate the vocabulary without re-declaring it.
CANONICAL_DIAGNOSTIC_CONCLUSIONS: Final[tuple[DiagnosticConclusion, ...]] = tuple(
    DiagnosticConclusion
)


class DiagnosticEvidenceKind(StrEnum):
    """The canonical, closed vocabulary of observed-evidence categories.

    Each member names one *kind of explicit structured fact* that can be known
    about the localized node failure. Every member binds to an existing
    canonical identity or reference; no member is derived from free text,
    keywords, stack traces, or model output, and none of them is a conclusion.

    Members:
        CANONICAL_ERROR: A canonical ``AgentXError`` code of an error already
            recorded on the node's attempt was observed. The code is validated
            by the canonical A1.04 rule and is never resolved, dereferenced, or
            interpreted here.
        NEGATIVE_EXPERIENCE: A canonical C2.06 negative-experience record that
            already remembers the failed attempt is referenced by identity.
        CHAIN_CORRELATION: The canonical execution-chain correlation UUID
            (C2.10/A1.07 identity) of the failed attempt is referenced.
        EPISODE: A canonical episode in which the failure was observed is
            referenced by identity.
        TASK: A canonical task under which the failure was observed is
            referenced by identity.
    """

    CANONICAL_ERROR = "canonical_error"
    NEGATIVE_EXPERIENCE = "negative_experience"
    CHAIN_CORRELATION = "chain_correlation"
    EPISODE = "episode"
    TASK = "task"


#: The canonical evidence kinds in their declared order. Exported so callers
#: may enumerate the vocabulary without re-declaring it.
CANONICAL_DIAGNOSTIC_EVIDENCE_KINDS: Final[tuple[DiagnosticEvidenceKind, ...]] = tuple(
    DiagnosticEvidenceKind
)


def _has_control_characters(value: str, *, allow_whitespace: bool) -> bool:
    allowed = {"\t", "\n", "\r"} if allow_whitespace else frozenset()
    return any(
        character < " " or character == "\x7f" for character in value if character not in allowed
    )


def _validate_summary(value: object) -> str:
    if not isinstance(value, str):
        raise FailureDiagnosisValidationError("summary must be a string")
    if not value or value != value.strip():
        raise FailureDiagnosisValidationError("summary must be non-empty and trimmed")
    if len(value) > _MAX_SUMMARY_LENGTH:
        raise FailureDiagnosisValidationError(
            f"summary must be at most {_MAX_SUMMARY_LENGTH} characters"
        )
    if _has_control_characters(value, allow_whitespace=False):
        raise FailureDiagnosisValidationError("summary must not contain control characters")
    return value


def _validate_optional_detail(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureDiagnosisValidationError("detail must be a string or None")
    if not value or value != value.strip():
        raise FailureDiagnosisValidationError("detail must be non-empty and trimmed when provided")
    if len(value) > _MAX_DETAIL_LENGTH:
        raise FailureDiagnosisValidationError(
            f"detail must be at most {_MAX_DETAIL_LENGTH} characters"
        )
    if _has_control_characters(value, allow_whitespace=True):
        raise FailureDiagnosisValidationError("detail must not contain control characters")
    return value


def _validate_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise FailureDiagnosisValidationError("diagnosed_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise FailureDiagnosisValidationError("diagnosed_at must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise FailureDiagnosisDeserializationError("diagnosed_at must be a string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FailureDiagnosisDeserializationError(
            "diagnosed_at is not a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FailureDiagnosisDeserializationError("diagnosed_at must be timezone-aware")
    return parsed.astimezone(UTC)


def _validate_optional_task_id(value: object) -> TaskId | None:
    if value is None or isinstance(value, TaskId):
        return value
    raise FailureDiagnosisValidationError("task_id must be a TaskId or None")


def _validate_optional_episode_id(value: object) -> EpisodeId | None:
    if value is None or isinstance(value, EpisodeId):
        return value
    raise FailureDiagnosisValidationError("episode_id must be an EpisodeId or None")


def _validate_optional_negative_experience_id(value: object) -> NegativeExperienceId | None:
    if value is None or isinstance(value, NegativeExperienceId):
        return value
    raise FailureDiagnosisValidationError(
        "negative_experience_id must be a NegativeExperienceId or None"
    )


def _validate_optional_correlation_id(value: object) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, UUID):
        raise FailureDiagnosisValidationError("correlation_id must be a UUID or None")
    if value.int == 0:
        raise FailureDiagnosisValidationError("correlation_id must not be the nil UUID")
    return value


def _validate_optional_error_code(value: object) -> str | None:
    """Validate an optional reference to a canonical ``AgentXError`` code.

    The reference is validated with the canonical A1.04 rule so this module
    cannot drift into a second, competing error vocabulary. The code is never
    resolved, dereferenced, or interpreted here.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureDiagnosisValidationError("error_code must be a string or None")
    try:
        code = _validate_canonical_error_code(value)
    except ValueError as exc:
        raise FailureDiagnosisValidationError(
            f"error_code must be a canonical AgentXError code: {exc}"
        ) from exc
    if len(code) > _MAX_ERROR_CODE_LENGTH:
        raise FailureDiagnosisValidationError(
            f"error_code must be at most {_MAX_ERROR_CODE_LENGTH} characters"
        )
    return code


def _validate_optional_classification(value: object) -> FailureClassification:
    if not isinstance(value, FailureClassification):
        raise FailureDiagnosisValidationError(
            "classification must be a FailureClassification; "
            "this contract never infers one from text"
        )
    return value


def _validate_optional_localization(value: object) -> FailureLocalization:
    if not isinstance(value, FailureLocalization):
        raise FailureDiagnosisValidationError(
            "localization must be a FailureLocalization; this contract never infers one from text"
        )
    if value.kind is not FailureLocationKind.PROCEDURE_NODE:
        raise FailureDiagnosisValidationError(
            "procedure-node diagnosis requires a PROCEDURE_NODE localization "
            "carrying the canonical procedure and node subject"
        )
    return value


def _parse_conclusion(value: object) -> DiagnosticConclusion:
    """Decode exactly one canonical conclusion value and fail closed otherwise.

    Unrecognized input is rejected. It is deliberately NOT downgraded to
    ``UNKNOWN``: silently accepting foreign vocabulary would fabricate a
    conclusion the data never contained.
    """
    if not isinstance(value, str):
        raise FailureDiagnosisDeserializationError("conclusion must be a string")
    try:
        return DiagnosticConclusion(value)
    except ValueError as exc:
        values = ", ".join(member.value for member in CANONICAL_DIAGNOSTIC_CONCLUSIONS)
        raise FailureDiagnosisDeserializationError(f"conclusion must be one of {values}") from exc


def _parse_evidence_kind(value: object) -> DiagnosticEvidenceKind:
    """Decode exactly one canonical evidence-kind value and fail closed otherwise.

    Unrecognized input is rejected. It is deliberately NOT downgraded or
    dropped: silently accepting foreign vocabulary would fabricate evidence the
    data never contained.
    """
    if not isinstance(value, str):
        raise FailureDiagnosisDeserializationError("evidence kind must be a string")
    try:
        return DiagnosticEvidenceKind(value)
    except ValueError as exc:
        raise FailureDiagnosisDeserializationError(
            "evidence kind must be one of "
            f"{[member.value for member in CANONICAL_DIAGNOSTIC_EVIDENCE_KINDS]}"
        ) from exc


def _parse_optional_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureDiagnosisDeserializationError("task_id must be a string or null")
    try:
        return TaskId.parse(value)
    except ValueError as exc:
        raise FailureDiagnosisDeserializationError("task_id is not a valid TaskId") from exc


def _parse_optional_episode_id(value: object) -> EpisodeId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureDiagnosisDeserializationError("episode_id must be a string or null")
    try:
        return EpisodeId.parse(value)
    except ValueError as exc:
        raise FailureDiagnosisDeserializationError("episode_id is not a valid EpisodeId") from exc


def _parse_optional_negative_experience_id(value: object) -> NegativeExperienceId | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureDiagnosisDeserializationError(
            "negative_experience_id must be a string or null"
        )
    try:
        return NegativeExperienceId.parse(value)
    except ValueError as exc:
        raise FailureDiagnosisDeserializationError(
            "negative_experience_id is not a valid NegativeExperienceId"
        ) from exc


def _parse_optional_correlation_id(value: object) -> UUID | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureDiagnosisDeserializationError("correlation_id must be a string or null")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise FailureDiagnosisDeserializationError("correlation_id is not a valid UUID") from exc
    if parsed.int == 0:
        raise FailureDiagnosisDeserializationError("correlation_id must not be the nil UUID")
    return parsed


def _parse_optional_error_code(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FailureDiagnosisDeserializationError("error_code must be a string or null")
    try:
        code = _validate_canonical_error_code(value)
    except ValueError as exc:
        raise FailureDiagnosisDeserializationError(
            "error_code must be a canonical AgentXError code"
        ) from exc
    if len(code) > _MAX_ERROR_CODE_LENGTH:
        raise FailureDiagnosisDeserializationError(
            f"error_code must be at most {_MAX_ERROR_CODE_LENGTH} characters"
        )
    return code


def _require_exact_fields(
    raw: Mapping[str, object], *, record_name: str, fields: frozenset[str]
) -> None:
    actual = set(raw)
    missing = fields - actual
    unknown = actual - fields
    if missing:
        raise FailureDiagnosisDeserializationError(
            f"{record_name} missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise FailureDiagnosisDeserializationError(
            f"{record_name} contains unknown fields: {sorted(unknown)}"
        )


def _present_evidence_refs(
    *,
    error_code: str | None,
    negative_experience_id: NegativeExperienceId | None,
    correlation_id: UUID | None,
    episode_id: EpisodeId | None,
    task_id: TaskId | None,
) -> frozenset[str]:
    present: set[str] = set()
    if error_code is not None:
        present.add("error_code")
    if negative_experience_id is not None:
        present.add("negative_experience_id")
    if correlation_id is not None:
        present.add("correlation_id")
    if episode_id is not None:
        present.add("episode_id")
    if task_id is not None:
        present.add("task_id")
    return frozenset(present)


def _validate_evidence_kind_refs(
    kind: DiagnosticEvidenceKind,
    *,
    error_code: str | None,
    negative_experience_id: NegativeExperienceId | None,
    correlation_id: UUID | None,
    episode_id: EpisodeId | None,
    task_id: TaskId | None,
) -> None:
    """Enforce that each evidence item carries exactly its own canonical reference.

    An evidence item declares one kind and supplies the matching canonical
    reference. Foreign or additional references are rejected so hostile or
    mismatched payloads cannot smuggle unrelated identity claims into a fact.
    """
    present = _present_evidence_refs(
        error_code=error_code,
        negative_experience_id=negative_experience_id,
        correlation_id=correlation_id,
        episode_id=episode_id,
        task_id=task_id,
    )

    if kind is DiagnosticEvidenceKind.CANONICAL_ERROR:
        if error_code is None:
            raise FailureDiagnosisValidationError(
                "CANONICAL_ERROR evidence requires an explicit error_code"
            )
        if present - {"error_code"}:
            raise FailureDiagnosisValidationError(
                "CANONICAL_ERROR evidence must not carry foreign reference fields"
            )
        return

    if kind is DiagnosticEvidenceKind.NEGATIVE_EXPERIENCE:
        if negative_experience_id is None:
            raise FailureDiagnosisValidationError(
                "NEGATIVE_EXPERIENCE evidence requires an explicit negative_experience_id"
            )
        if present - {"negative_experience_id"}:
            raise FailureDiagnosisValidationError(
                "NEGATIVE_EXPERIENCE evidence must not carry foreign reference fields"
            )
        return

    if kind is DiagnosticEvidenceKind.CHAIN_CORRELATION:
        if correlation_id is None:
            raise FailureDiagnosisValidationError(
                "CHAIN_CORRELATION evidence requires an explicit correlation_id"
            )
        if present - {"correlation_id"}:
            raise FailureDiagnosisValidationError(
                "CHAIN_CORRELATION evidence must not carry foreign reference fields"
            )
        return

    if kind is DiagnosticEvidenceKind.EPISODE:
        if episode_id is None:
            raise FailureDiagnosisValidationError(
                "EPISODE evidence requires an explicit episode_id"
            )
        if present - {"episode_id"}:
            raise FailureDiagnosisValidationError(
                "EPISODE evidence must not carry foreign reference fields"
            )
        return

    if kind is DiagnosticEvidenceKind.TASK:
        if task_id is None:
            raise FailureDiagnosisValidationError("TASK evidence requires an explicit task_id")
        if present - {"task_id"}:
            raise FailureDiagnosisValidationError(
                "TASK evidence must not carry foreign reference fields"
            )
        return

    raise FailureDiagnosisValidationError(f"unsupported diagnostic evidence kind: {kind!r}")


@dataclass(frozen=True, slots=True, kw_only=True)
class DiagnosticEvidence:
    """One explicit structured diagnostic fact about the localized node failure.

    An evidence item pairs a typed :class:`DiagnosticEvidenceKind` with exactly
    one matching canonical reference. Constructing, comparing, serializing, or
    decoding an item executes nothing, queries nothing, and interprets no text:
    ``error_code`` is a validated canonical ``AgentXError`` code that is never
    resolved, and identity references are never dereferenced. Hostile content
    such as ``"ADMIN"``, ``"ALLOW R4"``, ``"permission=WRITE"``,
    ``"verified=true"``, ``"repair=approved"``, or ``"budget=unlimited"``
    placed in any string field is inert data, exactly like any other
    characters.

    Attributes:
        kind: The canonical :class:`DiagnosticEvidenceKind`. Must be supplied
            as a typed enum member; this contract never infers it from text.
        error_code: Required for ``CANONICAL_ERROR``; a canonical
            ``AgentXError`` code.
        negative_experience_id: Required for ``NEGATIVE_EXPERIENCE``; a
            canonical C2.06 identity reference.
        correlation_id: Required for ``CHAIN_CORRELATION``; the canonical
            execution-chain correlation UUID.
        episode_id: Required for ``EPISODE``; a canonical episode identity.
        task_id: Required for ``TASK``; a canonical task identity.
    """

    kind: DiagnosticEvidenceKind
    error_code: str | None = None
    negative_experience_id: NegativeExperienceId | None = None
    correlation_id: UUID | None = None
    episode_id: EpisodeId | None = None
    task_id: TaskId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DiagnosticEvidenceKind):
            raise FailureDiagnosisValidationError(
                "kind must be a DiagnosticEvidenceKind member; "
                "this contract never infers evidence from text"
            )
        object.__setattr__(self, "error_code", _validate_optional_error_code(self.error_code))
        object.__setattr__(
            self,
            "negative_experience_id",
            _validate_optional_negative_experience_id(self.negative_experience_id),
        )
        object.__setattr__(
            self, "correlation_id", _validate_optional_correlation_id(self.correlation_id)
        )
        object.__setattr__(self, "episode_id", _validate_optional_episode_id(self.episode_id))
        object.__setattr__(self, "task_id", _validate_optional_task_id(self.task_id))
        _validate_evidence_kind_refs(
            self.kind,
            error_code=self.error_code,
            negative_experience_id=self.negative_experience_id,
            correlation_id=self.correlation_id,
            episode_id=self.episode_id,
            task_id=self.task_id,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "kind": self.kind.value,
            "error_code": self.error_code,
            "negative_experience_id": (
                None
                if self.negative_experience_id is None
                else self.negative_experience_id.to_str()
            ),
            "correlation_id": None if self.correlation_id is None else str(self.correlation_id),
            "episode_id": None if self.episode_id is None else self.episode_id.to_str(),
            "task_id": None if self.task_id is None else self.task_id.to_str(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> DiagnosticEvidence:
        """Validate and reconstruct one evidence item, failing closed."""
        if not isinstance(raw, Mapping):
            raise FailureDiagnosisDeserializationError("diagnostic evidence must be a JSON object")
        _require_exact_fields(raw, record_name="diagnostic evidence", fields=_EVIDENCE_FIELDS)

        error_code = raw["error_code"]
        if error_code is not None and not isinstance(error_code, str):
            raise FailureDiagnosisDeserializationError("error_code must be a string or null")
        kind = _parse_evidence_kind(raw["kind"])
        try:
            return cls(
                kind=kind,
                error_code=_parse_optional_error_code(error_code),
                negative_experience_id=_parse_optional_negative_experience_id(
                    raw["negative_experience_id"]
                ),
                correlation_id=_parse_optional_correlation_id(raw["correlation_id"]),
                episode_id=_parse_optional_episode_id(raw["episode_id"]),
                task_id=_parse_optional_task_id(raw["task_id"]),
            )
        except FailureDiagnosisDeserializationError:
            raise
        except FailureDiagnosisValidationError as exc:
            raise FailureDiagnosisDeserializationError(
                f"diagnostic evidence is invalid: {exc}"
            ) from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class FailureDiagnosis:
    """One immutable historical statement of *what diagnostic facts are known*.

    The record answers C4.03's question for one failure that C4.02 already
    localized to a canonical Procedure node. It is pure data: constructing,
    comparing, serializing, or decoding it executes nothing, retries nothing,
    repairs nothing, patches nothing, mutates no Task, changes no kernel
    authority, invokes no model, and never fabricates verification or root
    cause. Hostile content such as ``"ADMIN"``, ``"ALLOW R4"``,
    ``"permission=WRITE"``, ``"verified=true"``, ``"repair=approved"``,
    ``"retry forever"``, ``"ignore policy"``, or ``"budget=unlimited"`` placed
    in any text field is inert string data, exactly like any other characters.

    Observed evidence and conclusions are structurally distinct components:
    ``evidence`` holds explicit structured facts (references to already
    recorded canonical data) and ``conclusion`` holds at most one explicit
    caller-supplied typed conclusion. Nothing converts one into the other: the
    embedded C4.02 ``PROCEDURE_NODE`` pointer, the embedded C4.01 category, and
    every evidence item may be present while the conclusion stays
    :attr:`DiagnosticConclusion.UNKNOWN`.

    Attributes:
        classification: The embedded C4.01 :class:`FailureClassification`.
            Required; preserved by value and never rewritten.
        localization: The embedded C4.02 :class:`FailureLocalization`.
            Required; its kind must be ``PROCEDURE_NODE`` — the canonical
            procedure/node subject of this diagnosis, preserved by value and
            never rewritten. The pointer is not proof that the node is the
            root cause.
        evidence: The explicit structured evidence tuple. Each item is a
            validated :class:`DiagnosticEvidence`. May be empty while the
            conclusion remains ``UNKNOWN``; an explicit ``NODE_IMPLICATED``
            conclusion requires at least one item.
        conclusion: The explicit :class:`DiagnosticConclusion`. Defaults to
            ``UNKNOWN`` (fail closed) and is never derived from evidence.
        summary: Short, single-line, human/machine-readable statement of the
            diagnosis. Inert description only.
        diagnosed_at: Timezone-aware instant the diagnosis was recorded,
            normalized to UTC.
        detail: Optional longer inert description.
        schema_version: Canonical serialization schema version.
    """

    classification: FailureClassification
    localization: FailureLocalization
    summary: str
    diagnosed_at: datetime
    evidence: tuple[DiagnosticEvidence, ...] = ()
    conclusion: DiagnosticConclusion = DiagnosticConclusion.UNKNOWN
    detail: str | None = None
    schema_version: int = FAILURE_DIAGNOSIS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.conclusion, DiagnosticConclusion):
            raise FailureDiagnosisValidationError(
                "conclusion must be a DiagnosticConclusion member; "
                "this contract never infers a conclusion from text"
            )
        if not isinstance(self.evidence, tuple):
            raise FailureDiagnosisValidationError(
                "evidence must be a tuple of DiagnosticEvidence items"
            )
        for item in self.evidence:
            if not isinstance(item, DiagnosticEvidence):
                raise FailureDiagnosisValidationError(
                    "evidence must contain only DiagnosticEvidence items; "
                    "free-form text is not accepted as diagnostic evidence"
                )
        if self.conclusion is DiagnosticConclusion.NODE_IMPLICATED and not self.evidence:
            raise FailureDiagnosisValidationError(
                "NODE_IMPLICATED conclusion requires at least one explicit "
                "structured evidence item; a localized node pointer alone is "
                "not evidence of implication"
            )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise FailureDiagnosisValidationError("schema_version must be an integer")
        if self.schema_version != FAILURE_DIAGNOSIS_SCHEMA_VERSION:
            raise FailureDiagnosisValidationError(
                f"schema_version must be {FAILURE_DIAGNOSIS_SCHEMA_VERSION}"
            )
        object.__setattr__(
            self, "classification", _validate_optional_classification(self.classification)
        )
        object.__setattr__(self, "localization", _validate_optional_localization(self.localization))
        object.__setattr__(self, "summary", _validate_summary(self.summary))
        object.__setattr__(self, "diagnosed_at", _validate_timestamp(self.diagnosed_at))
        object.__setattr__(self, "detail", _validate_optional_detail(self.detail))

    @property
    def is_unknown(self) -> bool:
        """Whether this record fails closed with no asserted conclusion."""
        return self.conclusion is DiagnosticConclusion.UNKNOWN

    @property
    def procedure_id(self) -> ProcedureId:
        """The canonical procedure identity of the embedded node subject."""
        procedure_id = self.localization.procedure_id
        assert procedure_id is not None  # guaranteed by the PROCEDURE_NODE rule
        return procedure_id

    @property
    def procedure_node_id(self) -> str:
        """The graph-local node identity string of the embedded node subject."""
        node_id = self.localization.procedure_node_id
        assert node_id is not None  # guaranteed by the PROCEDURE_NODE rule
        return node_id

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "classification": self.classification.to_dict(),
            "localization": self.localization.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
            "conclusion": self.conclusion.value,
            "summary": self.summary,
            "diagnosed_at": _format_timestamp(self.diagnosed_at),
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
    def from_dict(cls, raw: Mapping[str, object]) -> FailureDiagnosis:
        """Validate and reconstruct one canonical diagnosis, failing closed."""
        if not isinstance(raw, Mapping):
            raise FailureDiagnosisDeserializationError("failure diagnosis must be a JSON object")
        if "schema_version" not in raw:
            raise FailureDiagnosisDeserializationError(
                "failure diagnosis missing required field: schema_version"
            )
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise FailureDiagnosisDeserializationError("schema_version must be an integer")
        if version != FAILURE_DIAGNOSIS_SCHEMA_VERSION:
            raise UnsupportedFailureDiagnosisSchemaVersionError(
                f"unsupported failure diagnosis schema version {version}; "
                f"supported version is {FAILURE_DIAGNOSIS_SCHEMA_VERSION}"
            )
        _require_exact_fields(raw, record_name="failure diagnosis", fields=_DIAGNOSIS_FIELDS)

        summary = raw["summary"]
        if not isinstance(summary, str):
            raise FailureDiagnosisDeserializationError("summary must be a string")
        detail = raw["detail"]
        if detail is not None and not isinstance(detail, str):
            raise FailureDiagnosisDeserializationError("detail must be a string or null")

        classification_raw = raw["classification"]
        if not isinstance(classification_raw, Mapping):
            raise FailureDiagnosisDeserializationError("classification must be a JSON object")
        localization_raw = raw["localization"]
        if not isinstance(localization_raw, Mapping):
            raise FailureDiagnosisDeserializationError("localization must be a JSON object")
        evidence_raw = raw["evidence"]
        if not isinstance(evidence_raw, list):
            raise FailureDiagnosisDeserializationError("evidence must be a JSON array")

        try:
            classification = FailureClassification.from_dict(classification_raw)
        except FailureClassificationDeserializationError as exc:
            raise FailureDiagnosisDeserializationError(f"classification is invalid: {exc}") from exc

        try:
            localization = FailureLocalization.from_dict(localization_raw)
        except FailureLocalizationDeserializationError as exc:
            raise FailureDiagnosisDeserializationError(f"localization is invalid: {exc}") from exc

        evidence: list[DiagnosticEvidence] = []
        for item in evidence_raw:
            if not isinstance(item, Mapping):
                raise FailureDiagnosisDeserializationError("evidence items must be JSON objects")
            evidence.append(DiagnosticEvidence.from_dict(item))

        try:
            return cls(
                schema_version=version,
                classification=classification,
                localization=localization,
                evidence=tuple(evidence),
                conclusion=_parse_conclusion(raw["conclusion"]),
                summary=summary,
                diagnosed_at=_parse_timestamp(raw["diagnosed_at"]),
                detail=detail,
            )
        except FailureDiagnosisDeserializationError:
            raise
        except FailureDiagnosisValidationError as exc:
            raise FailureDiagnosisDeserializationError(
                f"failure diagnosis is invalid: {exc}"
            ) from exc

    @classmethod
    def from_json(cls, text: str) -> FailureDiagnosis:
        """Decode canonical JSON and fail closed on malformed or non-object data."""
        if not isinstance(text, str):
            raise FailureDiagnosisDeserializationError("failure diagnosis JSON must be text")
        try:
            decoded = json.loads(text)
        except ValueError as exc:
            raise FailureDiagnosisDeserializationError(
                "failure diagnosis JSON is malformed"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise FailureDiagnosisDeserializationError(
                "failure diagnosis JSON root must be an object"
            )
        return cls.from_dict(decoded)


def package_diagnosis(
    *,
    classification: FailureClassification,
    localization: FailureLocalization,
    summary: str,
    diagnosed_at: datetime,
    evidence: Sequence[DiagnosticEvidence] = (),
    conclusion: DiagnosticConclusion = DiagnosticConclusion.UNKNOWN,
    detail: str | None = None,
) -> FailureDiagnosis:
    """Package explicit typed data into one immutable procedure-node diagnosis.

    This is a pure data transform. It validates and copies the embedded C4.01
    classification and the C4.02 ``PROCEDURE_NODE`` localization, freezes the
    caller-supplied evidence sequence into the record's evidence tuple, and
    attaches the caller-supplied conclusion and inert summary/detail. It does
    not:

    - inspect summary/detail/evidence text for keywords;
    - derive a conclusion, category, or location from evidence or from text;
    - query stores, consult the Procedure Graph, invoke models, or analyze
      trajectories;
    - repair, retry, execute, escalate, or mutate any runtime state.

    *evidence* must contain only :class:`DiagnosticEvidence` instances; free
    text is never accepted as evidence. When *conclusion* is omitted the
    result fails closed as :attr:`DiagnosticConclusion.UNKNOWN`.
    """
    if not isinstance(classification, FailureClassification):
        raise FailureDiagnosisValidationError(
            "classification must be a FailureClassification; "
            "this contract never infers one from text"
        )
    if not isinstance(localization, FailureLocalization):
        raise FailureDiagnosisValidationError(
            "localization must be a FailureLocalization; this contract never infers one from text"
        )
    frozen_evidence = tuple(evidence)
    try:
        return FailureDiagnosis(
            classification=classification,
            localization=localization,
            summary=summary,
            diagnosed_at=diagnosed_at,
            evidence=frozen_evidence,
            conclusion=conclusion,
            detail=detail,
        )
    except FailureDiagnosisValidationError:
        raise
