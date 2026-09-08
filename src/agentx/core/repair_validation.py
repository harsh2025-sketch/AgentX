"""Canonical inert repair-validation evidence contract (C4.06).

AgentX must be able to record, from already-produced typed evidence only,
*whether a specific proposed repair has been validated against explicit
deterministic criteria*. This module owns that boundary and the smallest
immutable evidence vocabulary that carries it. It owns nothing else.

What this module is:

    * :class:`RepairValidationCriterion` — the canonical, closed criterion
      vocabulary. Every member names one typed check that can be represented
      with structured evidence; free-text criteria never become authority.
    * :class:`RepairValidationOutcome` — the closed four-way per-criterion
      result vocabulary (``PASSED`` / ``FAILED`` / ``INSUFFICIENT_EVIDENCE`` /
      ``NOT_APPLICABLE``). A boolean alone is never a verdict.
    * :class:`RepairValidationDisposition` — the closed report-level
      disposition vocabulary. A report may say ``VALIDATED`` only when every
      *required* criterion has explicit passing evidence and there is no
      contradictory/failing required evidence.
    * :class:`RepairValidationTarget` — exact binding to one
      ``(ProcedureId, revision[, node])``. No wildcards, no "latest", no fuzzy
      matching. Evidence for revision N never validates revision N+1.
    * :class:`RepairValidationEvidence` — one immutable typed evidence item
      for one criterion against one exact target and one stable repair
      reference.
    * :class:`RepairValidationReport` — one immutable aggregated report.
    * :func:`evaluate_repair_validation` — pure, deterministic, fail-closed
      aggregation of already-produced evidence. No store, no model, no
      execution, no clock.

C4.06 answers: "Has this specific proposed repair been validated against the
explicit required criteria, according to the typed evidence supplied?"

It does NOT answer: "Which repair should AgentX perform?" and it does not
generate, apply, shadow-execute, authorize, or promote a repair.

The structural distinction this contract preserves:

    VALIDATION_EVIDENCE  !=  EXECUTION AUTHORITY

    validated  !=  authorized   validated  !=  executed
    validated  !=  applied      validated  !=  safe-to-run
    validated  !=  selected     validated  !=  procedure-activated

A RepairCandidate (C4.04) means only "a possible repair category may be
considered." This module never upgrades a candidate into authority. A
validated repair is still subject to governed execution/application later
(C4.07+ / kernel).

What this module is emphatically NOT:

    * It is not patch generation (C4.05 / Worker-11). It never depends on a
      repair-patch object. The stable ``repair_reference`` is an opaque
      external identity string so a future integration layer can bind a patch
      object without changing validation semantics.
    * It is not shadow repair (C4.07 / Worker-18). It never executes the
      original or modified procedure, never runs a Capability, AgentLoop, or
      interpreter, never writes files, never launches a process, never invokes
      a model or research, and never touches OS/browser.
    * It is not authority. A report grants nothing: no Permission, no
      AuthorityContext, no ActionGate approval, no RiskLevel downgrade, no
      ResourceEnvelope increase, no EmergencyStop reset, no Task success, no
      Procedure activation or status change. Authority belongs exclusively to
      ``agentx.kernel``.
    * It is not an inference engine. Nothing here inspects free text, hostile
      strings, summaries, details, exception messages, stack traces, model
      output, or payloads that merely *spell* ``"validated=true"`` /
      ``"passed=true"`` / ``"permission=ADMIN"`` and derives an outcome or
      disposition. Outcomes are always typed enum members.
    * It carries no probability, confidence, score, ranking, majority vote,
      or model invocation. Aggregation is deterministic and fail-closed.

Fail-closed aggregation (see :func:`evaluate_repair_validation`):

    * missing required criterion => ``INSUFFICIENT``;
    * one explicit required failure => ``FAILED``;
    * conflicting evidence for the same required criterion => ``FAILED``;
    * duplicated identical evidence must not count as independent proof;
    * wrong-target / wrong-revision / wrong-node / wrong-repair evidence must
      not validate the report target;
    * text saying ``"passed=true"`` is irrelevant;
    * an empty required-criteria set is rejected (never a success bypass);
    * the default required set is a non-empty safe minimum.

Determinism: caller supplies every timestamp. Same inputs => same report.
No clock reads, randomness, database, filesystem, network, model, research,
subprocess, thread, or sleep.

Identity reuse: this module introduces no competing identifier types. It
reuses :class:`~agentx.core.ids.ProcedureId` and the positive integer revision
convention already owned by :mod:`agentx.core.procedures`. Graph-local node
identity is a validated string exactly as C4.02 carries it (the outward
``ProcedureNodeId`` type must not be imported into ``agentx.core``).

Deliberate non-goals owned by later tasks: patch generation (C4.05), shadow
repair (C4.07), procedure version replacement/rollback (C4.08), repair
budgets/anti-loop (C4.09), repair selection, repair execution, procedure
rewriting, interpreter changes, persistence or migration, and any
model-driven repair reasoning.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from agentx.core.ids import ProcedureId

__all__ = [
    "CANONICAL_REPAIR_VALIDATION_CRITERIA",
    "CANONICAL_REPAIR_VALIDATION_DISPOSITIONS",
    "CANONICAL_REPAIR_VALIDATION_OUTCOMES",
    "DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA",
    "MAX_REPAIR_VALIDATION_DETAIL_LENGTH",
    "MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS",
    "MAX_REPAIR_VALIDATION_NODE_ID_LENGTH",
    "MAX_REPAIR_VALIDATION_REFERENCE_LENGTH",
    "MAX_REPAIR_VALIDATION_REQUIRED_CRITERIA",
    "REPAIR_VALIDATION_SCHEMA_VERSION",
    "RepairValidationCriterion",
    "RepairValidationDeserializationError",
    "RepairValidationDisposition",
    "RepairValidationEvidence",
    "RepairValidationOutcome",
    "RepairValidationReport",
    "RepairValidationTarget",
    "RepairValidationValidationError",
    "UnsupportedRepairValidationSchemaVersionError",
    "evaluate_repair_validation",
]

REPAIR_VALIDATION_SCHEMA_VERSION: Final[int] = 1

MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS: Final[int] = 64
MAX_REPAIR_VALIDATION_REQUIRED_CRITERIA: Final[int] = 16
MAX_REPAIR_VALIDATION_DETAIL_LENGTH: Final[int] = 4_096
MAX_REPAIR_VALIDATION_REFERENCE_LENGTH: Final[int] = 256
MAX_REPAIR_VALIDATION_NODE_ID_LENGTH: Final[int] = 256

_TARGET_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "procedure_id",
        "procedure_revision",
        "procedure_node_id",
    }
)

_EVIDENCE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "repair_reference",
        "procedure_id",
        "procedure_revision",
        "procedure_node_id",
        "criterion",
        "outcome",
        "evidence_reference",
        "evaluated_at",
        "detail",
    }
)

_REPORT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "repair_reference",
        "target",
        "disposition",
        "required_criteria",
        "evidence",
        "evaluated_at",
        "detail",
    }
)


class RepairValidationValidationError(ValueError):
    """Raised when repair-validation data violates the canonical contract."""


class RepairValidationDeserializationError(RepairValidationValidationError):
    """Raised when encoded repair-validation data cannot be decoded safely."""


class UnsupportedRepairValidationSchemaVersionError(RepairValidationDeserializationError):
    """Raised when encoded data uses an unsupported repair-validation schema."""


class RepairValidationCriterion(StrEnum):
    """The canonical, closed repair-validation criterion vocabulary.

    Each member names one *typed check* that can be represented with structured
    evidence. No member is free-text, no member grants authority, and no member
    is ordered above or below another. Human-readable meaning is documentation
    only; evaluation reads the enum member, never prose.

    Members:
        STRUCTURAL_VALIDITY: The proposed repair is structurally well-formed
            against the typed target (shape/identity constraints only).
        TARGET_BINDING: The evidence is bound to the exact procedure identity,
            revision, and (where applicable) node the report names.
        PRECONDITION_PRESERVATION: Required preconditions of the target remain
            representable/compatible under the proposed repair.
        POSTCONDITION_COMPATIBILITY: Expected postconditions remain compatible
            with the proposed repair.
        REGRESSION_FREE: Explicit regression-free evidence was recorded for the
            proposed repair (still not execution; only already-produced facts).
        VERIFICATION_EVIDENCE: Explicit verification evidence was recorded for
            the proposed repair (consumes prior verification facts; does not
            run verification).
        SCOPE_COMPATIBILITY: The proposed repair remains inside the declared
            applicability/scope boundary of the target.
    """

    STRUCTURAL_VALIDITY = "structural_validity"
    TARGET_BINDING = "target_binding"
    PRECONDITION_PRESERVATION = "precondition_preservation"
    POSTCONDITION_COMPATIBILITY = "postcondition_compatibility"
    REGRESSION_FREE = "regression_free"
    VERIFICATION_EVIDENCE = "verification_evidence"
    SCOPE_COMPATIBILITY = "scope_compatibility"


#: Canonical criteria in declaration order.
CANONICAL_REPAIR_VALIDATION_CRITERIA: Final[tuple[RepairValidationCriterion, ...]] = tuple(
    RepairValidationCriterion
)


#: Safe non-empty default required set. Callers may narrow or widen within the
#: closed vocabulary, but may never supply an empty required set to obtain
#: ``VALIDATED``.
DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA: Final[tuple[RepairValidationCriterion, ...]] = (
    RepairValidationCriterion.STRUCTURAL_VALIDITY,
    RepairValidationCriterion.TARGET_BINDING,
    RepairValidationCriterion.PRECONDITION_PRESERVATION,
    RepairValidationCriterion.POSTCONDITION_COMPATIBILITY,
)


class RepairValidationOutcome(StrEnum):
    """Closed per-criterion outcome vocabulary.

    A boolean alone is never a verdict. ``PASSED`` for one criterion never
    means the repair is globally validated.
    """

    PASSED = "passed"
    FAILED = "failed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NOT_APPLICABLE = "not_applicable"


CANONICAL_REPAIR_VALIDATION_OUTCOMES: Final[tuple[RepairValidationOutcome, ...]] = tuple(
    RepairValidationOutcome
)


class RepairValidationDisposition(StrEnum):
    """Closed report-level disposition vocabulary.

    ``VALIDATED`` is reachable only when every required criterion has explicit
    ``PASSED`` evidence, no required criterion has ``FAILED`` evidence, and no
    required criterion has conflicting outcomes. It still grants no authority.
    """

    VALIDATED = "validated"
    FAILED = "failed"
    INSUFFICIENT = "insufficient"
    NOT_APPLICABLE = "not_applicable"


CANONICAL_REPAIR_VALIDATION_DISPOSITIONS: Final[tuple[RepairValidationDisposition, ...]] = tuple(
    RepairValidationDisposition
)


def _has_control_characters(value: str, *, allow_whitespace: bool) -> bool:
    allowed = {"\t", "\n", "\r"} if allow_whitespace else frozenset()
    return any(
        character < " " or character == "\x7f" for character in value if character not in allowed
    )


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise RepairValidationValidationError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise RepairValidationValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise RepairValidationDeserializationError(f"{field_name} must be a string")
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RepairValidationDeserializationError(
            f"{field_name} is not a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RepairValidationDeserializationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _validate_reference(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise RepairValidationValidationError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise RepairValidationValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > MAX_REPAIR_VALIDATION_REFERENCE_LENGTH:
        raise RepairValidationValidationError(
            f"{field_name} must be at most {MAX_REPAIR_VALIDATION_REFERENCE_LENGTH} characters"
        )
    if _has_control_characters(value, allow_whitespace=False):
        raise RepairValidationValidationError(f"{field_name} must not contain control characters")
    # Reject wildcard / fuzzy target tokens that would break exact binding.
    if value in {"*", "latest", "any", "ALL", "LATEST", "ANY"}:
        raise RepairValidationValidationError(
            f"{field_name} must be an exact stable reference; wildcards and "
            f"'latest'/'any' are rejected"
        )
    return value


def _validate_optional_node_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RepairValidationValidationError("procedure_node_id must be a string or None")
    if not value or value != value.strip():
        raise RepairValidationValidationError(
            "procedure_node_id must be non-empty and trimmed when provided"
        )
    if len(value) > MAX_REPAIR_VALIDATION_NODE_ID_LENGTH:
        raise RepairValidationValidationError(
            f"procedure_node_id must be at most {MAX_REPAIR_VALIDATION_NODE_ID_LENGTH} characters"
        )
    if _has_control_characters(value, allow_whitespace=False):
        raise RepairValidationValidationError(
            "procedure_node_id must not contain control characters"
        )
    lowered = value.casefold()
    if value in {"*", "latest", "any"} or lowered in {"*", "latest", "any"}:
        raise RepairValidationValidationError(
            "procedure_node_id must be an exact node identity; wildcards are rejected"
        )
    return value


def _validate_revision(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepairValidationValidationError(f"{field_name} must be an integer")
    if value < 1:
        raise RepairValidationValidationError(
            f"{field_name} must be a positive integer (first revision is 1)"
        )
    return value


def _validate_procedure_id(value: object) -> ProcedureId:
    if not isinstance(value, ProcedureId):
        raise RepairValidationValidationError("procedure_id must be a ProcedureId")
    return value


def _validate_optional_detail(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RepairValidationValidationError("detail must be a string or None")
    if not value or value != value.strip():
        raise RepairValidationValidationError("detail must be non-empty and trimmed when provided")
    if len(value) > MAX_REPAIR_VALIDATION_DETAIL_LENGTH:
        raise RepairValidationValidationError(
            f"detail must be at most {MAX_REPAIR_VALIDATION_DETAIL_LENGTH} characters"
        )
    if _has_control_characters(value, allow_whitespace=True):
        raise RepairValidationValidationError("detail must not contain control characters")
    return value


def _validate_criterion(value: object) -> RepairValidationCriterion:
    if not isinstance(value, RepairValidationCriterion):
        raise RepairValidationValidationError(
            "criterion must be a RepairValidationCriterion member; "
            "this contract never infers a criterion from text"
        )
    return value


def _validate_outcome(value: object) -> RepairValidationOutcome:
    if not isinstance(value, RepairValidationOutcome):
        raise RepairValidationValidationError(
            "outcome must be a RepairValidationOutcome member; "
            "this contract never infers an outcome from text or bool"
        )
    return value


def _validate_disposition(value: object) -> RepairValidationDisposition:
    if not isinstance(value, RepairValidationDisposition):
        raise RepairValidationValidationError(
            "disposition must be a RepairValidationDisposition member; "
            "this contract never infers a disposition from text or bool"
        )
    return value


def _canonical_criteria(
    value: object,
    *,
    field_name: str,
    allow_empty: bool,
    max_items: int,
) -> tuple[RepairValidationCriterion, ...]:
    if not isinstance(value, tuple | list):
        raise RepairValidationValidationError(
            f"{field_name} must be a sequence of RepairValidationCriterion members"
        )
    if len(value) > max_items:
        raise RepairValidationValidationError(
            f"{field_name} must contain at most {max_items} criteria"
        )
    items: list[RepairValidationCriterion] = []
    seen: set[RepairValidationCriterion] = set()
    for item in value:
        if not isinstance(item, RepairValidationCriterion):
            raise RepairValidationValidationError(
                f"{field_name} entries must be RepairValidationCriterion members; "
                "text never names a criterion"
            )
        if item in seen:
            # Duplicates add no independent requirement; collapse deterministically.
            continue
        seen.add(item)
        items.append(item)
    if not items and not allow_empty:
        raise RepairValidationValidationError(
            f"{field_name} must be a non-empty set; an empty required set cannot produce VALIDATED"
        )
    # Canonical declaration order for determinism.
    order = {member: index for index, member in enumerate(CANONICAL_REPAIR_VALIDATION_CRITERIA)}
    return tuple(sorted(items, key=lambda member: order[member]))


def _require_exact_fields(
    raw: Mapping[str, object],
    *,
    expected: frozenset[str],
    label: str,
) -> None:
    actual = set(raw)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise RepairValidationDeserializationError(
            f"{label} missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise RepairValidationDeserializationError(
            f"{label} contains unknown fields: {sorted(unknown)}"
        )


def _parse_criterion(value: object) -> RepairValidationCriterion:
    if not isinstance(value, str):
        raise RepairValidationDeserializationError("criterion must be a string")
    try:
        return RepairValidationCriterion(value)
    except ValueError as exc:
        values = ", ".join(member.value for member in CANONICAL_REPAIR_VALIDATION_CRITERIA)
        raise RepairValidationDeserializationError(f"criterion must be one of {values}") from exc


def _parse_outcome(value: object) -> RepairValidationOutcome:
    if not isinstance(value, str):
        raise RepairValidationDeserializationError("outcome must be a string")
    try:
        return RepairValidationOutcome(value)
    except ValueError as exc:
        values = ", ".join(member.value for member in CANONICAL_REPAIR_VALIDATION_OUTCOMES)
        raise RepairValidationDeserializationError(f"outcome must be one of {values}") from exc


def _parse_disposition(value: object) -> RepairValidationDisposition:
    if not isinstance(value, str):
        raise RepairValidationDeserializationError("disposition must be a string")
    try:
        return RepairValidationDisposition(value)
    except ValueError as exc:
        values = ", ".join(member.value for member in CANONICAL_REPAIR_VALIDATION_DISPOSITIONS)
        raise RepairValidationDeserializationError(f"disposition must be one of {values}") from exc


def _parse_procedure_id(value: object) -> ProcedureId:
    if not isinstance(value, str):
        raise RepairValidationDeserializationError("procedure_id must be a string")
    try:
        return ProcedureId.parse(value)
    except ValueError as exc:
        raise RepairValidationDeserializationError(
            "procedure_id is not a valid ProcedureId"
        ) from exc


def _parse_criteria_list(
    value: object, *, field_name: str
) -> tuple[RepairValidationCriterion, ...]:
    if not isinstance(value, list):
        raise RepairValidationDeserializationError(f"{field_name} must be a JSON array")
    items: list[RepairValidationCriterion] = []
    for item in value:
        items.append(_parse_criterion(item))
    return tuple(items)


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairValidationTarget:
    """Exact procedure target a validation report or evidence item binds to.

    Identity is the pair ``(procedure_id, procedure_revision)`` plus an optional
    exact ``procedure_node_id``. There is no "latest", wildcard, prefix, or
    case-insensitive fuzzy matching. If the target changes, prior evidence
    becomes historical only and cannot validate the new target.
    """

    procedure_id: ProcedureId
    procedure_revision: int
    procedure_node_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "procedure_id", _validate_procedure_id(self.procedure_id))
        object.__setattr__(
            self,
            "procedure_revision",
            _validate_revision(self.procedure_revision, field_name="procedure_revision"),
        )
        object.__setattr__(
            self,
            "procedure_node_id",
            _validate_optional_node_id(self.procedure_node_id),
        )

    def binds_evidence(
        self,
        *,
        procedure_id: ProcedureId,
        procedure_revision: int,
        procedure_node_id: str | None,
    ) -> bool:
        """Return whether evidence identity exactly matches this target.

        Exact equality only. A missing node on evidence does not match a
        node-specific target, and a node-specific evidence item does not match
        a procedure-only target.
        """
        if procedure_id != self.procedure_id:
            return False
        if procedure_revision != self.procedure_revision:
            return False
        return procedure_node_id == self.procedure_node_id

    def to_dict(self) -> dict[str, object]:
        return {
            "procedure_id": self.procedure_id.to_str(),
            "procedure_revision": self.procedure_revision,
            "procedure_node_id": self.procedure_node_id,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> RepairValidationTarget:
        if not isinstance(raw, Mapping):
            raise RepairValidationDeserializationError("target must be a JSON object")
        _require_exact_fields(raw, expected=_TARGET_FIELDS, label="repair validation target")
        node_raw = raw["procedure_node_id"]
        if node_raw is not None and not isinstance(node_raw, str):
            raise RepairValidationDeserializationError("procedure_node_id must be a string or null")
        try:
            return cls(
                procedure_id=_parse_procedure_id(raw["procedure_id"]),
                procedure_revision=_validate_revision(
                    raw["procedure_revision"], field_name="procedure_revision"
                ),
                procedure_node_id=node_raw,
            )
        except RepairValidationValidationError as exc:
            raise RepairValidationDeserializationError(f"target is invalid: {exc}") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairValidationEvidence:
    """One immutable typed evidence item for one criterion of one repair.

    The record is pure data. Constructing, comparing, serializing, or decoding
    it executes nothing, repairs nothing, grants no authority, and never claims
    that the repair is globally validated. Hostile content can only appear
    inside the optional inert ``detail`` string, where it remains inert text
    and never flips ``outcome``.

    Attributes:
        repair_reference: Stable opaque external reference for the proposed
            repair (not a second ProcedureId; not a patch object dependency).
        procedure_id: Exact canonical procedure identity this evidence binds to.
        procedure_revision: Exact positive revision this evidence binds to.
        procedure_node_id: Optional exact graph-local node identity.
        criterion: Typed criterion this evidence addresses.
        outcome: Typed per-criterion outcome.
        evidence_reference: Opaque reference to the supporting fact/record.
        evaluated_at: Caller-supplied timezone-aware instant.
        detail: Optional bounded inert annotation; never authority-bearing.
    """

    repair_reference: str
    procedure_id: ProcedureId
    procedure_revision: int
    criterion: RepairValidationCriterion
    outcome: RepairValidationOutcome
    evidence_reference: str
    evaluated_at: datetime
    procedure_node_id: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "repair_reference",
            _validate_reference(self.repair_reference, field_name="repair_reference"),
        )
        object.__setattr__(self, "procedure_id", _validate_procedure_id(self.procedure_id))
        object.__setattr__(
            self,
            "procedure_revision",
            _validate_revision(self.procedure_revision, field_name="procedure_revision"),
        )
        object.__setattr__(
            self,
            "procedure_node_id",
            _validate_optional_node_id(self.procedure_node_id),
        )
        object.__setattr__(self, "criterion", _validate_criterion(self.criterion))
        object.__setattr__(self, "outcome", _validate_outcome(self.outcome))
        object.__setattr__(
            self,
            "evidence_reference",
            _validate_reference(self.evidence_reference, field_name="evidence_reference"),
        )
        object.__setattr__(
            self, "evaluated_at", _validate_timestamp(self.evaluated_at, field_name="evaluated_at")
        )
        object.__setattr__(self, "detail", _validate_optional_detail(self.detail))

    @property
    def identity_key(self) -> tuple[object, ...]:
        """Canonical identity used to collapse duplicate identical evidence."""
        return (
            self.repair_reference,
            self.procedure_id,
            self.procedure_revision,
            self.procedure_node_id,
            self.criterion,
            self.outcome,
            self.evidence_reference,
            self.detail,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "repair_reference": self.repair_reference,
            "procedure_id": self.procedure_id.to_str(),
            "procedure_revision": self.procedure_revision,
            "procedure_node_id": self.procedure_node_id,
            "criterion": self.criterion.value,
            "outcome": self.outcome.value,
            "evidence_reference": self.evidence_reference,
            "evaluated_at": _format_timestamp(self.evaluated_at),
            "detail": self.detail,
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
    def from_dict(cls, raw: Mapping[str, object]) -> RepairValidationEvidence:
        if not isinstance(raw, Mapping):
            raise RepairValidationDeserializationError("evidence must be a JSON object")
        _require_exact_fields(raw, expected=_EVIDENCE_FIELDS, label="repair validation evidence")
        node_raw = raw["procedure_node_id"]
        if node_raw is not None and not isinstance(node_raw, str):
            raise RepairValidationDeserializationError("procedure_node_id must be a string or null")
        detail_raw = raw["detail"]
        if detail_raw is not None and not isinstance(detail_raw, str):
            raise RepairValidationDeserializationError("detail must be a string or null")
        repair_reference = raw["repair_reference"]
        evidence_reference = raw["evidence_reference"]
        if not isinstance(repair_reference, str):
            raise RepairValidationDeserializationError("repair_reference must be a string")
        if not isinstance(evidence_reference, str):
            raise RepairValidationDeserializationError("evidence_reference must be a string")
        try:
            return cls(
                repair_reference=repair_reference,
                procedure_id=_parse_procedure_id(raw["procedure_id"]),
                procedure_revision=_validate_revision(
                    raw["procedure_revision"], field_name="procedure_revision"
                ),
                procedure_node_id=node_raw,
                criterion=_parse_criterion(raw["criterion"]),
                outcome=_parse_outcome(raw["outcome"]),
                evidence_reference=evidence_reference,
                evaluated_at=_parse_timestamp(raw["evaluated_at"], field_name="evaluated_at"),
                detail=detail_raw,
            )
        except RepairValidationDeserializationError:
            raise
        except RepairValidationValidationError as exc:
            raise RepairValidationDeserializationError(f"evidence is invalid: {exc}") from exc

    @classmethod
    def from_json(cls, text: str) -> RepairValidationEvidence:
        if not isinstance(text, str):
            raise RepairValidationDeserializationError("evidence JSON must be text")
        try:
            decoded = json.loads(text)
        except ValueError as exc:
            raise RepairValidationDeserializationError("evidence JSON is malformed") from exc
        if not isinstance(decoded, Mapping):
            raise RepairValidationDeserializationError("evidence JSON root must be an object")
        return cls.from_dict(decoded)


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairValidationReport:
    """One immutable aggregated validation report for one repair proposal.

    ``disposition is VALIDATED`` means only that every required criterion has
    explicit passing evidence under fail-closed aggregation. It does **not**
    mean authorized, executed, applied, safe, selected, or procedure-activated.

    Attributes:
        repair_reference: Stable opaque external reference for the proposal.
        target: Exact procedure target binding.
        disposition: Closed report-level disposition.
        required_criteria: Non-empty required criterion set used for
            aggregation (canonical order).
        evidence: Deduplicated evidence items that bound to the target and
            repair reference (canonical order preserved from evaluation).
        evaluated_at: Caller-supplied aggregation instant.
        detail: Optional bounded inert annotation.
        schema_version: Canonical serialization schema version.
    """

    repair_reference: str
    target: RepairValidationTarget
    disposition: RepairValidationDisposition
    required_criteria: tuple[RepairValidationCriterion, ...]
    evidence: tuple[RepairValidationEvidence, ...]
    evaluated_at: datetime
    detail: str | None = None
    schema_version: int = REPAIR_VALIDATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "repair_reference",
            _validate_reference(self.repair_reference, field_name="repair_reference"),
        )
        if not isinstance(self.target, RepairValidationTarget):
            raise RepairValidationValidationError("target must be a RepairValidationTarget")
        object.__setattr__(self, "disposition", _validate_disposition(self.disposition))
        object.__setattr__(
            self,
            "required_criteria",
            _canonical_criteria(
                self.required_criteria,
                field_name="required_criteria",
                allow_empty=False,
                max_items=MAX_REPAIR_VALIDATION_REQUIRED_CRITERIA,
            ),
        )
        if not isinstance(self.evidence, tuple):
            raise RepairValidationValidationError("evidence must be a tuple")
        if len(self.evidence) > MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS:
            raise RepairValidationValidationError(
                f"evidence must contain at most {MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS} items"
            )
        for item in self.evidence:
            if not isinstance(item, RepairValidationEvidence):
                raise RepairValidationValidationError(
                    "evidence items must be RepairValidationEvidence instances"
                )
            if item.repair_reference != self.repair_reference:
                raise RepairValidationValidationError(
                    "evidence repair_reference must match the report repair_reference"
                )
            if not self.target.binds_evidence(
                procedure_id=item.procedure_id,
                procedure_revision=item.procedure_revision,
                procedure_node_id=item.procedure_node_id,
            ):
                raise RepairValidationValidationError(
                    "evidence target binding must exactly match the report target"
                )
        object.__setattr__(
            self, "evaluated_at", _validate_timestamp(self.evaluated_at, field_name="evaluated_at")
        )
        object.__setattr__(self, "detail", _validate_optional_detail(self.detail))
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise RepairValidationValidationError("schema_version must be an integer")
        if self.schema_version != REPAIR_VALIDATION_SCHEMA_VERSION:
            raise RepairValidationValidationError(
                f"schema_version must be {REPAIR_VALIDATION_SCHEMA_VERSION}"
            )

    @property
    def is_validated(self) -> bool:
        """Whether disposition is :attr:`RepairValidationDisposition.VALIDATED`."""
        return self.disposition is RepairValidationDisposition.VALIDATED

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "repair_reference": self.repair_reference,
            "target": self.target.to_dict(),
            "disposition": self.disposition.value,
            "required_criteria": [item.value for item in self.required_criteria],
            "evidence": [item.to_dict() for item in self.evidence],
            "evaluated_at": _format_timestamp(self.evaluated_at),
            "detail": self.detail,
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
    def from_dict(cls, raw: Mapping[str, object]) -> RepairValidationReport:
        if not isinstance(raw, Mapping):
            raise RepairValidationDeserializationError(
                "repair validation report must be a JSON object"
            )
        if "schema_version" not in raw:
            raise RepairValidationDeserializationError(
                "repair validation report missing required field: schema_version"
            )
        version = raw["schema_version"]
        if not isinstance(version, int) or isinstance(version, bool):
            raise RepairValidationDeserializationError("schema_version must be an integer")
        if version != REPAIR_VALIDATION_SCHEMA_VERSION:
            raise UnsupportedRepairValidationSchemaVersionError(
                f"unsupported repair validation schema version {version}; "
                f"supported version is {REPAIR_VALIDATION_SCHEMA_VERSION}"
            )
        _require_exact_fields(raw, expected=_REPORT_FIELDS, label="repair validation report")

        target_raw = raw["target"]
        if not isinstance(target_raw, Mapping):
            raise RepairValidationDeserializationError("target must be a JSON object")
        evidence_raw = raw["evidence"]
        if not isinstance(evidence_raw, list):
            raise RepairValidationDeserializationError("evidence must be a JSON array")
        if len(evidence_raw) > MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS:
            raise RepairValidationDeserializationError(
                f"evidence must contain at most {MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS} items"
            )
        evidence_items: list[RepairValidationEvidence] = []
        for item in evidence_raw:
            if not isinstance(item, Mapping):
                raise RepairValidationDeserializationError("evidence items must be JSON objects")
            evidence_items.append(RepairValidationEvidence.from_dict(item))

        detail_raw = raw["detail"]
        if detail_raw is not None and not isinstance(detail_raw, str):
            raise RepairValidationDeserializationError("detail must be a string or null")

        repair_reference = raw["repair_reference"]
        if not isinstance(repair_reference, str):
            raise RepairValidationDeserializationError("repair_reference must be a string")
        try:
            required = _parse_criteria_list(
                raw["required_criteria"], field_name="required_criteria"
            )
            return cls(
                schema_version=version,
                repair_reference=repair_reference,
                target=RepairValidationTarget.from_dict(target_raw),
                disposition=_parse_disposition(raw["disposition"]),
                required_criteria=required,
                evidence=tuple(evidence_items),
                evaluated_at=_parse_timestamp(raw["evaluated_at"], field_name="evaluated_at"),
                detail=detail_raw,
            )
        except RepairValidationDeserializationError:
            raise
        except RepairValidationValidationError as exc:
            raise RepairValidationDeserializationError(
                f"repair validation report is invalid: {exc}"
            ) from exc

    @classmethod
    def from_json(cls, text: str) -> RepairValidationReport:
        if not isinstance(text, str):
            raise RepairValidationDeserializationError("repair validation report JSON must be text")
        try:
            decoded = json.loads(text)
        except ValueError as exc:
            raise RepairValidationDeserializationError(
                "repair validation report JSON is malformed"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise RepairValidationDeserializationError(
                "repair validation report JSON root must be an object"
            )
        return cls.from_dict(decoded)


def _dedupe_evidence(
    items: Sequence[RepairValidationEvidence],
) -> tuple[RepairValidationEvidence, ...]:
    """Collapse byte-identical evidence; first occurrence wins (stable)."""
    seen: set[tuple[object, ...]] = set()
    unique: list[RepairValidationEvidence] = []
    for item in items:
        key = item.identity_key
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)


def _criterion_status(
    outcomes: set[RepairValidationOutcome],
) -> RepairValidationOutcome | None:
    """Reduce the outcome set of one criterion under fail-closed rules.

    Returns:
        ``FAILED`` if any failure or a PASSED/FAILED conflict is present;
        ``PASSED`` if PASSED is present and no FAILED;
        ``INSUFFICIENT_EVIDENCE`` if only insufficient/N/A outcomes remain;
        ``None`` if no outcomes were supplied (missing criterion).
    """
    if not outcomes:
        return None
    if RepairValidationOutcome.FAILED in outcomes:
        return RepairValidationOutcome.FAILED
    if RepairValidationOutcome.PASSED in outcomes:
        # PASSED + INSUFFICIENT or PASSED + NOT_APPLICABLE still counts as
        # explicit pass for that criterion (no contradictory failure).
        return RepairValidationOutcome.PASSED
    if outcomes <= {
        RepairValidationOutcome.INSUFFICIENT_EVIDENCE,
        RepairValidationOutcome.NOT_APPLICABLE,
    }:
        return RepairValidationOutcome.INSUFFICIENT_EVIDENCE
    return RepairValidationOutcome.INSUFFICIENT_EVIDENCE


def evaluate_repair_validation(
    *,
    repair_reference: str,
    target: RepairValidationTarget,
    evidence: Sequence[RepairValidationEvidence],
    evaluated_at: datetime,
    required_criteria: Sequence[RepairValidationCriterion] | None = None,
    detail: str | None = None,
) -> RepairValidationReport:
    """Aggregate already-produced evidence into one fail-closed validation report.

    This is a pure data transform. It never:

    - executes, shadow-runs, applies, or generates a repair;
    - reads a clock, touches the filesystem/network, or invokes a model;
    - inspects detail/text for keywords such as ``\"validated=true\"``;
    - majority-votes, averages confidence, or scores probabilistically;
    - accepts an empty required-criteria set as a success bypass;
    - lets evidence for another procedure, revision, node, or repair reference
      validate the named target.

    Aggregation rules (deterministic, fail-closed):

    1. Reject malformed inputs and empty required sets.
    2. Keep only evidence whose ``repair_reference`` and target binding exactly
       match the report target; foreign evidence is ignored (never proof).
    3. Collapse duplicate identical evidence so flooding cannot manufacture
       independent proof.
    4. For each required criterion:
       - any ``FAILED`` (including PASSED+FAILED conflict) => criterion failed;
       - else any ``PASSED`` => criterion passed;
       - else missing / only insufficient or N/A => criterion insufficient.
    5. Report disposition:
       - any required failure => ``FAILED``;
       - else any required insufficient/missing => ``INSUFFICIENT``;
       - else every required criterion passed => ``VALIDATED``.

    Optional (non-required) evidence is retained when target-bound but never
    upgrades a missing required criterion and never grants authority. An
    explicit failure on a non-required criterion does not by itself fail the
    report; only required criteria drive disposition.
    """
    if not isinstance(target, RepairValidationTarget):
        raise RepairValidationValidationError("target must be a RepairValidationTarget")
    ref = _validate_reference(repair_reference, field_name="repair_reference")
    moment = _validate_timestamp(evaluated_at, field_name="evaluated_at")
    if required_criteria is None:
        required = DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA
    else:
        required = _canonical_criteria(
            tuple(required_criteria),
            field_name="required_criteria",
            allow_empty=False,
            max_items=MAX_REPAIR_VALIDATION_REQUIRED_CRITERIA,
        )
    if not isinstance(evidence, Sequence):
        raise RepairValidationValidationError(
            "evidence must be a sequence of RepairValidationEvidence instances"
        )
    # Materialize once so a hostile str/bytes caller is rejected by the
    # per-item typed check below rather than being walked as characters.
    try:
        material = tuple(evidence)
    except TypeError as exc:
        raise RepairValidationValidationError(
            "evidence must be a sequence of RepairValidationEvidence instances"
        ) from exc
    if len(material) > MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS:
        raise RepairValidationValidationError(
            f"evidence must contain at most {MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS} items"
        )
    typed_items: list[RepairValidationEvidence] = []
    for item in material:
        if not isinstance(item, RepairValidationEvidence):
            raise RepairValidationValidationError(
                "evidence items must be RepairValidationEvidence instances; "
                "dicts, JSON text, and free-text claims are never evidence"
            )
        typed_items.append(item)

    # Exact target + repair binding only. Wrong-target / wrong-repair evidence
    # is historical noise and never validates this report.
    bound = [
        item
        for item in typed_items
        if item.repair_reference == ref
        and target.binds_evidence(
            procedure_id=item.procedure_id,
            procedure_revision=item.procedure_revision,
            procedure_node_id=item.procedure_node_id,
        )
    ]
    unique = _dedupe_evidence(bound)
    if len(unique) > MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS:
        raise RepairValidationValidationError(
            f"evidence must contain at most {MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS} items"
        )

    # Group outcomes by criterion (required criteria drive disposition).
    by_criterion: dict[RepairValidationCriterion, set[RepairValidationOutcome]] = {
        criterion: set() for criterion in required
    }
    for item in unique:
        if item.criterion in by_criterion:
            by_criterion[item.criterion].add(item.outcome)

    any_failed = False
    any_insufficient = False
    for criterion in required:
        status = _criterion_status(by_criterion[criterion])
        if status is None or status is RepairValidationOutcome.INSUFFICIENT_EVIDENCE:
            any_insufficient = True
        elif status is RepairValidationOutcome.FAILED:
            any_failed = True
        # PASSED: continue

    if any_failed:
        disposition = RepairValidationDisposition.FAILED
    elif any_insufficient:
        disposition = RepairValidationDisposition.INSUFFICIENT
    else:
        disposition = RepairValidationDisposition.VALIDATED

    # Preserve stable evidence order: required-criterion declaration order,
    # then first-seen order within each criterion for non-required leftovers.
    ordered: list[RepairValidationEvidence] = []
    remaining = list(unique)
    for criterion in CANONICAL_REPAIR_VALIDATION_CRITERIA:
        keep: list[RepairValidationEvidence] = []
        matched: list[RepairValidationEvidence] = []
        for item in remaining:
            if item.criterion is criterion:
                matched.append(item)
            else:
                keep.append(item)
        ordered.extend(matched)
        remaining = keep
    ordered.extend(remaining)

    return RepairValidationReport(
        repair_reference=ref,
        target=target,
        disposition=disposition,
        required_criteria=required,
        evidence=tuple(ordered),
        evaluated_at=moment,
        detail=detail,
    )
