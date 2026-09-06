"""Canonical inert repair-candidate contract (C4.04).

AgentX must be able to record, from an explicit canonical failure diagnosis,
*which repair category or action may be considered* as a hypothesis. This
module owns that boundary and the smallest immutable record that carries it.
It owns nothing else.

What this module is:

    * :class:`RepairCandidateKind` — the canonical, closed repair-candidate
      vocabulary. It is deliberately tiny: every member is justified by a
      typed fact that the landed C4.01-C4.03 diagnosis contracts actually
      carry, and no member exists that those contracts cannot evidence.
    * :class:`RepairCandidate` — one immutable, deterministic,
      JSON-serializable record: a candidate kind, the exact canonical
      :class:`~agentx.core.failure_diagnosis.FailureDiagnosis` it arose from
      (embedded by value), the canonical evidence links of that diagnosis the
      candidate arose from, and the instant the candidate was recorded.
    * :func:`derive_repair_candidates` — pure, deterministic derivation of
      the candidate set from one typed ``FailureDiagnosis``. No store query,
      no model, no keyword scan, no inference from text.

C4.04 answers "What possible repair category/action may be considered based on
the explicit structured diagnosis?" It does NOT answer "Which repair should
AgentX perform?" and it does not perform repair.

A candidate is a hypothesis, never a verdict. In this contract:

    candidate != correct    candidate != safe        candidate != selected
    candidate != authorized candidate != executed    candidate != verified

Nothing here selects, ranks, scores, approves, executes, verifies, or
suppresses a candidate, and nothing here states that a candidate is right,
safe, or even admissible for a runtime — selection and execution policy live
in the kernel and in later repair tasks.

What this module is emphatically NOT:

    * It is not an inference engine. Nothing here inspects arbitrary text,
      summaries, details, exception messages, stack traces, tracebacks,
      webpages, model output, or hostile strings and derives a candidate kind.
      A kind is always supplied as a typed enum member or derived only from
      the typed :class:`~agentx.core.failure_diagnosis.DiagnosticConclusion`
      and the explicit evidence tuple of a canonical diagnosis record. The
      text ``"permission denied"`` never proves a canonical ``PERMISSION``
      failure here, because only the canonical structured diagnosis says what
      the failure is.
    * It is not a repair taxonomy expansion. The vocabulary is the smallest
      one the canonical failure categories and the closed diagnosis conclusion
      vocabulary justify. There is deliberately NO member for permission
      acquisition, retry scheduling, environment re-sensing, knowledge
      promotion, procedure rollback, capability re-registration, or "do not
      repair": none of those can be evidenced by the structured facts a
      C4.03 record carries, and speculative members would fabricate
      repair intent the diagnosis never contained. Later repair tasks (patch
      generation, repair validation, shadow repair, version replacement/
      rollback, repair budgets) own those concerns as actions and policies;
      this module never pre-enacts them.
    * It is not authority. A candidate grants nothing: it cannot grant a
      Permission, create an AuthorityContext, bypass an ActionGate, lower a
      RiskLevel, widen a ResourceEnvelope, clear an EmergencyStop, transition
      a Task, claim or fabricate verification, activate or mutate a
      Procedure, mutate a stored procedure revision or the Hive memory,
      or alter any store. Authority belongs exclusively to ``agentx.kernel``.
    * It is not a mutation. It never modifies files, rewrites code, mutates
      procedures, retries, executes capabilities or tasks, shells out, spawns
      subprocesses, touches the network or a browser, calls a model, does
      research, or compiles/activates skills. Constructing, comparing,
      serializing, or decoding a record executes none of that.
    * It carries no probability, confidence, score, ranking, ordering, or
      model invocation. The vocabulary is flat; no member is above or below
      another.

Determinism and provenance:

    Candidate derivation and representation are deterministic. The same typed
    inputs produce byte-identical records and byte-identical canonical JSON.
    Provenance is preserved by value: the exact embedded diagnosis record —
    and through it the exact C4.01 classification and C4.02 localization —
    is carried inside the candidate and is never rewritten, trimmed,
    reordered, or re-derived. Evidence linkage uses positions into the
    embedded diagnosis' evidence tuple, so two materially distinct evidence
    items (even two byte-identical items at distinct positions) are recorded
    as two distinct links and are never silently collapsed. Links must be
    strictly increasing: the only "deduplication" the contract performs is on
    *identical* link positions, which carry no distinct provenance and whose
    collapse is therefore justified by the canonical identity semantics
    themselves.

Fail-closed policy: :attr:`RepairCandidateKind.UNKNOWN` is a first-class,
always-valid candidate kind and the only result derivable when the structured
diagnosis cannot justify a more specific category — it records "this contract
names no candidate category for that diagnosis," which is not a decision that
no repair exists or may be attempted. Deserialization never coerces an
unrecognized kind string into ``UNKNOWN`` — unknown input is rejected, because
silently accepting foreign vocabulary would fabricate a candidate the data
never contained.

Identity reuse: this module introduces no competing identifier types, no
competing error hierarchy, and no new reference fields. It embeds the C4.03
:class:`~agentx.core.failure_diagnosis.FailureDiagnosis` by value (which
already carries the validated C4.01 classification, C4.02 localization,
canonical ``AgentXError`` code references, and canonical identity references)
and links its evidence items by position. It never re-validates, resolves, or
re-derives what the canonical diagnosis already owns; it never queries a
store to confirm that a procedure or node still exists, because existence
checks are execution-time concerns of later consumers, not of this pure data
boundary.

Deliberate non-goals owned by later tasks: patch generation (C4.05), repair
validation (C4.06), shadow repair (C4.07), procedure version replacement /
rollback (C4.08), repair budgets / anti-loop (C4.09), plus repair selection,
repair execution, procedure rewriting, interpreter changes, persistence or
migration, and any model-driven repair reasoning.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from agentx.core.failure_diagnosis import (
    DiagnosticConclusion,
    FailureDiagnosis,
    FailureDiagnosisDeserializationError,
)

__all__ = [
    "CANONICAL_REPAIR_CANDIDATE_KINDS",
    "REPAIR_CANDIDATE_SCHEMA_VERSION",
    "RepairCandidate",
    "RepairCandidateDeserializationError",
    "RepairCandidateKind",
    "RepairCandidateValidationError",
    "UnsupportedRepairCandidateSchemaVersionError",
    "derive_repair_candidates",
]

REPAIR_CANDIDATE_SCHEMA_VERSION: Final[int] = 1

_CANDIDATE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "kind",
        "diagnosis",
        "supporting_evidence_indices",
        "proposed_at",
    }
)


class RepairCandidateValidationError(ValueError):
    """Raised when repair-candidate data violates the canonical contract."""


class RepairCandidateDeserializationError(RepairCandidateValidationError):
    """Raised when encoded repair-candidate data cannot be decoded safely."""


class UnsupportedRepairCandidateSchemaVersionError(RepairCandidateDeserializationError):
    """Raised when encoded data uses an unsupported repair-candidate schema."""


class RepairCandidateKind(StrEnum):
    """The canonical, closed repair-candidate vocabulary.

    Each member names one *possible repair category that may be considered*
    on the basis of an explicit canonical diagnosis. No member asserts that
    the repair is correct, safe, selected, authorized, executed, or verified,
    and no member is ordered above or below another.

    Members:
        UNKNOWN: The structured diagnosis does not justify naming any more
            specific candidate category. This is the fail-closed default and
            is always a valid candidate kind. It records an absence of
            justification, not a verdict: it never claims that no repair is
            possible, never suppresses other subsystems (escalation, human
            approval, retry policy), and never grants or withholds anything.
        NODE_DEFINITION_REVISION: A revision of the localized procedure
            node's own definition/implementation may be *considered*. This is
            the only candidate category the closed C4.03 conclusion vocabulary
            can justify, and it is derivable only from an explicit
            :attr:`~agentx.core.failure_diagnosis.DiagnosticConclusion.NODE_IMPLICATED`
            conclusion backed by explicit structured evidence. Considering a
            category is not generating, applying, approving, executing, or
            verifying a patch; patch generation is C4.05, validation is
            C4.06/C4.07, and version replacement/rollback is C4.08.
    """

    UNKNOWN = "unknown"
    NODE_DEFINITION_REVISION = "node_definition_revision"


#: The canonical candidate kinds in their declared order. Exported so callers
#: may enumerate the vocabulary without re-declaring it.
CANONICAL_REPAIR_CANDIDATE_KINDS: Final[tuple[RepairCandidateKind, ...]] = tuple(
    RepairCandidateKind
)


def _validate_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise RepairCandidateValidationError("proposed_at must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise RepairCandidateValidationError("proposed_at must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise RepairCandidateDeserializationError("proposed_at must be a string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RepairCandidateDeserializationError(
            "proposed_at is not a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RepairCandidateDeserializationError("proposed_at must be timezone-aware")
    return parsed.astimezone(UTC)


def _validate_kind(value: object) -> RepairCandidateKind:
    if not isinstance(value, RepairCandidateKind):
        raise RepairCandidateValidationError(
            "kind must be a RepairCandidateKind member; "
            "this contract never infers a candidate from text"
        )
    return value


def _validate_diagnosis(value: object) -> FailureDiagnosis:
    if not isinstance(value, FailureDiagnosis):
        raise RepairCandidateValidationError(
            "diagnosis must be a FailureDiagnosis; this contract never infers "
            "candidates from text, payloads, or arbitrary objects"
        )
    return value


def _validate_supporting_indices(
    value: object,
    *,
    kind: RepairCandidateKind,
    evidence_count: int,
) -> tuple[int, ...]:
    """Validate the canonical evidence links of one candidate.

    A candidate cites the exact positions in its embedded diagnosis' evidence
    tuple that it arose from. The links must be distinct, in-range, and
    strictly increasing so representation is canonical and deterministic:
    materially distinct evidence items (distinct positions) are always
    recorded as distinct links, while re-citing one identical position would
    add no provenance and is therefore rejected rather than stored twice.
    """
    if not isinstance(value, tuple):
        raise RepairCandidateValidationError(
            "supporting_evidence_indices must be a tuple of integer evidence positions"
        )
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise RepairCandidateValidationError(
                "supporting_evidence_indices must contain only integers"
            )
    for position in range(1, len(value)):
        if value[position] <= value[position - 1]:
            raise RepairCandidateValidationError(
                "supporting_evidence_indices must be strictly increasing so distinct "
                "evidence positions are preserved and never silently collapsed"
            )
    for item in value:
        if item < 0 or item >= evidence_count:
            raise RepairCandidateValidationError(
                "supporting_evidence_indices positions must reference existing evidence items"
            )
    if kind is RepairCandidateKind.UNKNOWN:
        if value:
            raise RepairCandidateValidationError(
                "an UNKNOWN candidate cites no repair justification and must not carry "
                "supporting evidence links"
            )
        return value
    if not value:
        raise RepairCandidateValidationError(
            "a NODE_DEFINITION_REVISION candidate requires at least one explicit "
            "structured evidence link; a diagnosis pointer alone justifies no candidate"
        )
    return value


def _require_exact_fields(raw: Mapping[str, object]) -> None:
    actual = set(raw)
    missing = _CANDIDATE_FIELDS - actual
    unknown = actual - _CANDIDATE_FIELDS
    if missing:
        raise RepairCandidateDeserializationError(
            f"repair candidate missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise RepairCandidateDeserializationError(
            f"repair candidate contains unknown fields: {sorted(unknown)}"
        )


def _parse_kind(value: object) -> RepairCandidateKind:
    """Decode exactly one canonical candidate kind and fail closed otherwise.

    Unrecognized input is rejected. It is deliberately NOT downgraded to
    ``UNKNOWN``: silently accepting foreign vocabulary would fabricate a
    candidate the data never contained.
    """
    if not isinstance(value, str):
        raise RepairCandidateDeserializationError("kind must be a string")
    try:
        return RepairCandidateKind(value)
    except ValueError as exc:
        values = ", ".join(member.value for member in CANONICAL_REPAIR_CANDIDATE_KINDS)
        raise RepairCandidateDeserializationError(f"kind must be one of {values}") from exc


def _parse_diagnosis(value: object) -> FailureDiagnosis:
    if not isinstance(value, Mapping):
        raise RepairCandidateDeserializationError("diagnosis must be a JSON object")
    try:
        return FailureDiagnosis.from_dict(value)
    except FailureDiagnosisDeserializationError as exc:
        raise RepairCandidateDeserializationError(f"diagnosis is invalid: {exc}") from exc


def _parse_supporting_indices(value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise RepairCandidateDeserializationError(
            "supporting_evidence_indices must be a JSON array"
        )
    items: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise RepairCandidateDeserializationError(
                "supporting_evidence_indices entries must be integers"
            )
        items.append(item)
    return tuple(items)


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairCandidate:
    """One immutable statement of *which repair category may be considered*.

    The record is pure data. Constructing, comparing, serializing, or decoding
    it executes nothing, repairs nothing, patches nothing, mutates no
    procedure, Task, store, or Hive state, grants no authority, changes no
    risk, budget, or stop state, and never claims verification. Because the
    record carries no free-text field of its own, hostile content can only
    reach it inside the embedded diagnosis, where it remains exactly what it
    was there: inert string data inside an already-canonical historical
    record, never a source of a kind, a link, or an instruction.

    A candidate is a hypothesis/option only. It is never ``correct``,
    ``safe``, ``selected``, ``authorized``, ``executed``, or ``verified``, and
    the record deliberately has no field or method that could claim any of
    those.

    Attributes:
        kind: The canonical :class:`RepairCandidateKind`. Must be supplied as
            a typed enum member or derived from a canonical diagnosis; this
            contract never infers it from text.
        diagnosis: The exact canonical C4.03 :class:`FailureDiagnosis` the
            candidate arose from, embedded by value and preserved untouched —
            the full provenance chain to the C4.01 classification, the C4.02
            ``PROCEDURE_NODE`` localization, and every evidence item.
        supporting_evidence_indices: Positions into ``diagnosis.evidence``
            that this candidate arose from. Distinct, in-range, strictly
            increasing; empty exactly for :attr:`RepairCandidateKind.UNKNOWN`
            candidates; non-empty for any specific candidate kind.
        proposed_at: Timezone-aware instant the candidate record was made,
            normalized to UTC. A record-keeping timestamp only; it authorizes
            nothing and schedules nothing.
        schema_version: Canonical serialization schema version.
    """

    kind: RepairCandidateKind
    diagnosis: FailureDiagnosis
    proposed_at: datetime
    supporting_evidence_indices: tuple[int, ...] = ()
    schema_version: int = REPAIR_CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_kind(self.kind)
        _validate_diagnosis(self.diagnosis)
        object.__setattr__(self, "proposed_at", _validate_timestamp(self.proposed_at))
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise RepairCandidateValidationError("schema_version must be an integer")
        if self.schema_version != REPAIR_CANDIDATE_SCHEMA_VERSION:
            raise RepairCandidateValidationError(
                f"schema_version must be {REPAIR_CANDIDATE_SCHEMA_VERSION}"
            )
        if (
            self.kind is RepairCandidateKind.NODE_DEFINITION_REVISION
            and self.diagnosis.conclusion is not DiagnosticConclusion.NODE_IMPLICATED
        ):
            raise RepairCandidateValidationError(
                "a NODE_DEFINITION_REVISION candidate is justified only by an explicit "
                "NODE_IMPLICATED conclusion in the embedded diagnosis; no other structured "
                "fact may fabricate one"
            )
        object.__setattr__(
            self,
            "supporting_evidence_indices",
            _validate_supporting_indices(
                self.supporting_evidence_indices,
                kind=self.kind,
                evidence_count=len(self.diagnosis.evidence),
            ),
        )

    @property
    def is_unknown(self) -> bool:
        """Whether this record fails closed as :attr:`RepairCandidateKind.UNKNOWN`."""
        return self.kind is RepairCandidateKind.UNKNOWN

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic schema-v1 JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "diagnosis": self.diagnosis.to_dict(),
            "supporting_evidence_indices": list(self.supporting_evidence_indices),
            "proposed_at": _format_timestamp(self.proposed_at),
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
    def from_dict(cls, raw: Mapping[str, object]) -> RepairCandidate:
        """Validate and reconstruct one canonical candidate, failing closed."""
        if not isinstance(raw, Mapping):
            raise RepairCandidateDeserializationError("repair candidate must be a JSON object")
        if "schema_version" not in raw:
            raise RepairCandidateDeserializationError(
                "repair candidate missing required field: schema_version"
            )
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise RepairCandidateDeserializationError("schema_version must be an integer")
        if version != REPAIR_CANDIDATE_SCHEMA_VERSION:
            raise UnsupportedRepairCandidateSchemaVersionError(
                f"unsupported repair candidate schema version {version}; "
                f"supported version is {REPAIR_CANDIDATE_SCHEMA_VERSION}"
            )
        _require_exact_fields(raw)

        try:
            return cls(
                schema_version=version,
                kind=_parse_kind(raw["kind"]),
                diagnosis=_parse_diagnosis(raw["diagnosis"]),
                supporting_evidence_indices=_parse_supporting_indices(
                    raw["supporting_evidence_indices"]
                ),
                proposed_at=_parse_timestamp(raw["proposed_at"]),
            )
        except RepairCandidateDeserializationError:
            raise
        except RepairCandidateValidationError as exc:
            raise RepairCandidateDeserializationError(
                f"repair candidate is invalid: {exc}"
            ) from exc

    @classmethod
    def from_json(cls, text: str) -> RepairCandidate:
        """Decode canonical JSON and fail closed on malformed or non-object data."""
        if not isinstance(text, str):
            raise RepairCandidateDeserializationError("repair candidate JSON must be text")
        try:
            decoded = json.loads(text)
        except ValueError as exc:
            raise RepairCandidateDeserializationError("repair candidate JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise RepairCandidateDeserializationError(
                "repair candidate JSON root must be an object"
            )
        return cls.from_dict(decoded)


def derive_repair_candidates(
    *,
    diagnosis: FailureDiagnosis,
    proposed_at: datetime,
) -> tuple[RepairCandidate, ...]:
    """Derive the conservative repair-candidate set of one canonical diagnosis.

    This is a pure data transform, and it is the only derivation this contract
    performs:

    - a diagnosis whose explicit conclusion is
      :attr:`~agentx.core.failure_diagnosis.DiagnosticConclusion.NODE_IMPLICATED`
      yields exactly one :attr:`RepairCandidateKind.NODE_DEFINITION_REVISION`
      candidate that cites every explicit evidence item of the diagnosis, in
      order — so materially distinct (even byte-identical) evidence positions
      are each preserved and never collapsed;
    - any other diagnosis (conclusion ``UNKNOWN``) yields exactly one
      :attr:`RepairCandidateKind.UNKNOWN` candidate with no evidence links,
      because no more specific category is justified.

    The C4.01 category is deliberately *not* consulted: a category is a
    classification of the observed failure, not a repair instruction, and
    mapping categories to repairs would be an inference this contract never
    performs. The function never inspects summary or detail text, never
    queries a store or graph, never invokes a model, and never mutates the
    diagnosis or any runtime state. The result is deterministic: the same
    typed inputs always produce byte-identical candidates.
    """
    if not isinstance(diagnosis, FailureDiagnosis):
        raise RepairCandidateValidationError(
            "diagnosis must be a FailureDiagnosis; this contract never infers "
            "candidates from text, payloads, or arbitrary objects"
        )
    if diagnosis.conclusion is DiagnosticConclusion.NODE_IMPLICATED:
        kind = RepairCandidateKind.NODE_DEFINITION_REVISION
        links = tuple(range(len(diagnosis.evidence)))
    else:
        kind = RepairCandidateKind.UNKNOWN
        links = ()
    return (
        RepairCandidate(
            kind=kind,
            diagnosis=diagnosis,
            supporting_evidence_indices=links,
            proposed_at=proposed_at,
        ),
    )
