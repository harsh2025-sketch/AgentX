"""Pure evidence and comparison contracts for verified reuse efficiency (M8.01).

This module records explicitly measured execution facts and deterministically
compares two runs. It performs no execution, routing, retrieval, procedure
activation, model call, persistence, clock read, benchmarking, or authority
change. Every record is inert data.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from fractions import Fraction
from typing import Final
from uuid import UUID

from agentx.core.events import EventValidationError, VerificationPayload
from agentx.core.ids import EpisodeId, ProcedureId, TaskId

REUSE_EFFICIENCY_SCHEMA_VERSION: Final[int] = 1
_MAX_COUNTER: Final[int] = (1 << 63) - 1
_MAX_TEXT_LENGTH: Final[int] = 1_024
_PROCEDURE_MODES: Final[frozenset[ReuseMode]]


class ReuseEfficiencyValidationError(ValueError):
    """Raised when reuse-efficiency evidence violates the canonical contract."""


class ReuseEfficiencyDeserializationError(ReuseEfficiencyValidationError):
    """Raised when encoded evidence cannot be reconstructed safely."""


class UnsupportedReuseEfficiencySchemaVersionError(ReuseEfficiencyDeserializationError):
    """Raised when encoded evidence uses an unsupported schema version."""


class ReuseMode(StrEnum):
    """Closed descriptive vocabulary aligned with AgentX's L0-L5 hierarchy.

    Values describe caller-supplied execution evidence only. They do not route,
    activate, authorize, or execute anything.
    """

    CACHE_REUSE = "cache_reuse"
    DETERMINISTIC_CAPABILITY = "deterministic_capability"
    PROCEDURE_REUSE = "procedure_reuse"
    GUIDED_PROCEDURE = "guided_procedure"
    NOVEL_PLAN = "novel_plan"
    EXPLORATORY = "exploratory"


_PROCEDURE_MODES = frozenset({ReuseMode.PROCEDURE_REUSE, ReuseMode.GUIDED_PROCEDURE})


class ExecutionEvidenceOutcome(StrEnum):
    """Typed truth boundary for the measured run's terminal evidence state."""

    VERIFIED_SUCCESS = "verified_success"
    FAILED = "failed"
    UNVERIFIED = "unverified"


class TaskRelationship(StrEnum):
    """Closed relationship vocabulary used to decide cross-task comparability."""

    SAME_TASK_REPLAY = "same_task_replay"
    RELATED_TASK_REUSE = "related_task_reuse"
    UNRELATED = "unrelated"


class ReuseEfficiencyDisposition(StrEnum):
    """Deterministic comparison result; no opaque aggregate score exists."""

    IMPROVED = "improved"
    NO_MATERIAL_IMPROVEMENT = "no_material_improvement"
    REGRESSED = "regressed"
    NOT_COMPARABLE = "not_comparable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class EfficiencyMetric(StrEnum):
    """Measured dimensions for which lower consumption is better."""

    MODEL_CALLS = "model_calls"
    MODEL_TOKENS = "model_tokens"
    DURATION_MICROSECONDS = "duration_microseconds"
    EXTERNAL_COST = "external_cost"
    RESEARCH_QUERIES = "research_queries"
    MACHINE_ACTIONS = "machine_actions"
    REPAIR_ATTEMPTS = "repair_attempts"


class MetricChange(StrEnum):
    """Exact direction of reuse value relative to baseline value."""

    IMPROVED = "improved"
    SAME = "same"
    REGRESSED = "regressed"


def _validate_schema_version(value: object) -> int:
    if type(value) is not int:
        raise ReuseEfficiencyValidationError("schema_version must be an integer")
    if value != REUSE_EFFICIENCY_SCHEMA_VERSION:
        raise UnsupportedReuseEfficiencySchemaVersionError(
            f"unsupported reuse-efficiency schema version {value}; "
            f"supported version is {REUSE_EFFICIENCY_SCHEMA_VERSION}"
        )
    return value


def _validate_counter(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int or None")
    if value < 0:
        raise ReuseEfficiencyValidationError(f"{field_name} must not be negative")
    if value > _MAX_COUNTER:
        raise OverflowError(f"{field_name} exceeds the supported counter range")
    return value


def _validate_decimal(value: object, *, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal or None")
    if not value.is_finite():
        raise ReuseEfficiencyValidationError(f"{field_name} must be finite")
    if value < 0:
        raise ReuseEfficiencyValidationError(f"{field_name} must not be negative")
    return value


def _validate_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ReuseEfficiencyValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > _MAX_TEXT_LENGTH:
        raise ReuseEfficiencyValidationError(
            f"{field_name} must not exceed {_MAX_TEXT_LENGTH} characters"
        )
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise ReuseEfficiencyValidationError(f"{field_name} must not contain control lines")
    return value


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID")
    if value.int == 0:
        raise ReuseEfficiencyValidationError(f"{field_name} must not be the nil UUID")
    return value


def _validate_timestamp(value: object, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReuseEfficiencyValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_duration(value: object) -> timedelta | None:
    if value is None:
        return None
    if not isinstance(value, timedelta):
        raise TypeError("elapsed must be a timedelta or None")
    if value < timedelta(0):
        raise ReuseEfficiencyValidationError("elapsed must not be negative")
    return value


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReuseEfficiencyDeserializationError(f"{field_name} must be a string or null")
    text = f"{value[:-1]}+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ReuseEfficiencyDeserializationError(
            f"{field_name} is not a valid ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReuseEfficiencyDeserializationError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _timedelta_microseconds(value: timedelta) -> int:
    return ((value.days * 86_400) + value.seconds) * 1_000_000 + value.microseconds


def _duration_from_microseconds(value: object) -> timedelta | None:
    if value is None:
        return None
    count = _validate_counter(value, field_name="elapsed_microseconds")
    if count is None:  # pragma: no cover - guarded by value is not None
        raise AssertionError("elapsed microseconds unexpectedly absent")
    return timedelta(microseconds=count)


def _parse_decimal(value: object, *, field_name: str) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReuseEfficiencyDeserializationError(f"{field_name} must be a string or null")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ReuseEfficiencyDeserializationError(f"{field_name} is not a valid Decimal") from exc
    return _validate_decimal(parsed, field_name=field_name)


def _parse_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, str):
        raise ReuseEfficiencyDeserializationError(f"{field_name} must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise ReuseEfficiencyDeserializationError(f"{field_name} is not a valid UUID") from exc
    return _validate_uuid(parsed, field_name=field_name)


def _parse_task_id(value: object) -> TaskId:
    if not isinstance(value, str):
        raise ReuseEfficiencyDeserializationError("task_id must be a string")
    try:
        return TaskId.parse(value)
    except ValueError as exc:
        raise ReuseEfficiencyDeserializationError("task_id is not a valid TaskId") from exc


def _parse_episode_id(value: object) -> EpisodeId:
    if not isinstance(value, str):
        raise ReuseEfficiencyDeserializationError("episode_id must be a string")
    try:
        return EpisodeId.parse(value)
    except ValueError as exc:
        raise ReuseEfficiencyDeserializationError("episode_id is not a valid EpisodeId") from exc


def _require_exact_fields(
    raw: Mapping[str, object], *, expected: frozenset[str], context: str
) -> None:
    actual = set(raw)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise ReuseEfficiencyDeserializationError(
            f"{context} missing required fields: {sorted(missing)}"
        )
    if unknown:
        raise ReuseEfficiencyDeserializationError(
            f"{context} contains unknown fields: {sorted(unknown)}"
        )


def _parse_enum[_EnumT: StrEnum](
    enum_type: type[_EnumT], value: object, *, field_name: str
) -> _EnumT:
    if not isinstance(value, str):
        raise ReuseEfficiencyDeserializationError(f"{field_name} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ReuseEfficiencyDeserializationError(
            f"{field_name} is not a supported {enum_type.__name__}"
        ) from exc


_PROCEDURE_FIELDS: Final[frozenset[str]] = frozenset({"procedure_id", "revision"})


@dataclass(frozen=True, slots=True)
class ProcedureRevisionRef:
    """Exact immutable procedure identity; there is no latest/wildcard revision."""

    procedure_id: ProcedureId
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, ProcedureId):
            raise TypeError("procedure_id must be a ProcedureId")
        revision = _validate_counter(self.revision, field_name="procedure revision")
        if revision is None or revision < 1:
            raise ReuseEfficiencyValidationError("procedure revision must be a positive integer")

    def to_dict(self) -> dict[str, object]:
        return {"procedure_id": self.procedure_id.to_str(), "revision": self.revision}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ProcedureRevisionRef:
        _require_exact_fields(raw, expected=_PROCEDURE_FIELDS, context="procedure")
        procedure_id_raw = raw["procedure_id"]
        if not isinstance(procedure_id_raw, str):
            raise ReuseEfficiencyDeserializationError("procedure_id must be a string")
        try:
            procedure_id = ProcedureId.parse(procedure_id_raw)
        except ValueError as exc:
            raise ReuseEfficiencyDeserializationError("procedure_id is not valid") from exc
        revision = _validate_counter(raw["revision"], field_name="procedure revision")
        if revision is None or revision < 1:
            raise ReuseEfficiencyDeserializationError("procedure revision must be positive")
        return cls(procedure_id=procedure_id, revision=revision)


_RELATIONSHIP_FIELDS: Final[frozenset[str]] = frozenset(
    {"relationship", "source", "reference"}
)


@dataclass(frozen=True, slots=True)
class TaskRelationshipEvidence:
    """Explicit caller-supplied relationship evidence for cross-task comparisons."""

    relationship: TaskRelationship
    source: str
    reference: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.relationship, TaskRelationship):
            raise TypeError("relationship must be a TaskRelationship")
        object.__setattr__(self, "source", _validate_text(self.source, field_name="source"))
        object.__setattr__(
            self,
            "reference",
            _validate_uuid(self.reference, field_name="relationship reference"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "relationship": self.relationship.value,
            "source": self.source,
            "reference": str(self.reference),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> TaskRelationshipEvidence:
        _require_exact_fields(raw, expected=_RELATIONSHIP_FIELDS, context="relationship")
        return cls(
            relationship=_parse_enum(
                TaskRelationship, raw["relationship"], field_name="relationship"
            ),
            source=_validate_text(raw["source"], field_name="source"),
            reference=_parse_uuid(raw["reference"], field_name="relationship reference"),
        )


_EVIDENCE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "task_id",
        "episode_id",
        "correlation_id",
        "mode",
        "outcome",
        "evidence_source",
        "evidence_reference",
        "started_at",
        "ended_at",
        "elapsed_microseconds",
        "model_calls",
        "model_input_tokens",
        "model_output_tokens",
        "model_tokens",
        "research_queries",
        "machine_actions",
        "repair_attempts",
        "external_cost",
        "cost_unit",
        "procedure",
        "verification",
        "verification_source",
        "verification_reference",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionEfficiencyEvidence:
    """Immutable measured facts for one execution run.

    Unknown measurements are ``None`` rather than fabricated zeros. A verified
    success requires a typed canonical :class:`VerificationPayload` with
    ``passed=True`` plus an explicit verification source/reference.
    """

    task_id: TaskId
    episode_id: EpisodeId
    correlation_id: UUID
    mode: ReuseMode
    outcome: ExecutionEvidenceOutcome
    evidence_source: str
    evidence_reference: UUID
    started_at: datetime | None = None
    ended_at: datetime | None = None
    elapsed: timedelta | None = None
    model_calls: int | None = None
    model_input_tokens: int | None = None
    model_output_tokens: int | None = None
    model_tokens: int | None = None
    research_queries: int | None = None
    machine_actions: int | None = None
    repair_attempts: int | None = None
    external_cost: Decimal | None = None
    cost_unit: str | None = None
    procedure: ProcedureRevisionRef | None = None
    verification: VerificationPayload | None = None
    verification_source: str | None = None
    verification_reference: UUID | None = None
    schema_version: int = REUSE_EFFICIENCY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be a TaskId")
        if not isinstance(self.episode_id, EpisodeId):
            raise TypeError("episode_id must be an EpisodeId")
        object.__setattr__(
            self, "correlation_id", _validate_uuid(self.correlation_id, field_name="correlation_id")
        )
        if not isinstance(self.mode, ReuseMode):
            raise TypeError("mode must be a ReuseMode")
        if not isinstance(self.outcome, ExecutionEvidenceOutcome):
            raise TypeError("outcome must be an ExecutionEvidenceOutcome")
        object.__setattr__(
            self,
            "evidence_source",
            _validate_text(self.evidence_source, field_name="evidence_source"),
        )
        object.__setattr__(
            self,
            "evidence_reference",
            _validate_uuid(self.evidence_reference, field_name="evidence_reference"),
        )
        object.__setattr__(self, "schema_version", _validate_schema_version(self.schema_version))

        started_at = _validate_timestamp(self.started_at, field_name="started_at")
        ended_at = _validate_timestamp(self.ended_at, field_name="ended_at")
        if (started_at is None) != (ended_at is None):
            raise ReuseEfficiencyValidationError(
                "started_at and ended_at must either both be present or both be absent"
            )
        if started_at is not None and ended_at is not None and ended_at < started_at:
            raise ReuseEfficiencyValidationError("ended_at must not be earlier than started_at")
        elapsed = _validate_duration(self.elapsed)
        if started_at is not None and ended_at is not None:
            measured_elapsed = ended_at - started_at
            if elapsed is not None and elapsed != measured_elapsed:
                raise ReuseEfficiencyValidationError(
                    "elapsed must exactly match ended_at - started_at when both are supplied"
                )
            elapsed = measured_elapsed
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "ended_at", ended_at)
        object.__setattr__(self, "elapsed", elapsed)

        for field_name in (
            "model_calls",
            "model_input_tokens",
            "model_output_tokens",
            "model_tokens",
            "research_queries",
            "machine_actions",
            "repair_attempts",
        ):
            object.__setattr__(
                self,
                field_name,
                _validate_counter(getattr(self, field_name), field_name=field_name),
            )

        if self.model_input_tokens is not None and self.model_output_tokens is not None:
            total = self.model_input_tokens + self.model_output_tokens
            if total > _MAX_COUNTER:
                raise OverflowError(
                    "model input/output token total exceeds supported counter range"
                )
            if self.model_tokens is None:
                object.__setattr__(self, "model_tokens", total)
            elif self.model_tokens != total:
                raise ReuseEfficiencyValidationError(
                    "model_tokens must equal model_input_tokens + model_output_tokens"
                )

        external_cost = _validate_decimal(self.external_cost, field_name="external_cost")
        object.__setattr__(self, "external_cost", external_cost)
        if external_cost is None:
            if self.cost_unit is not None:
                raise ReuseEfficiencyValidationError("cost_unit requires external_cost")
        else:
            if self.cost_unit is None:
                raise ReuseEfficiencyValidationError("external_cost requires cost_unit")
            object.__setattr__(
                self, "cost_unit", _validate_text(self.cost_unit, field_name="cost_unit")
            )

        if self.procedure is not None and not isinstance(self.procedure, ProcedureRevisionRef):
            raise TypeError("procedure must be a ProcedureRevisionRef or None")
        if self.mode in _PROCEDURE_MODES and self.procedure is None:
            raise ReuseEfficiencyValidationError(
                f"{self.mode.value} requires exact procedure_id + revision evidence"
            )
        if self.mode not in _PROCEDURE_MODES and self.procedure is not None:
            raise ReuseEfficiencyValidationError(
                f"{self.mode.value} must not claim a procedure was used"
            )

        if self.verification is not None and not isinstance(self.verification, VerificationPayload):
            raise TypeError("verification must be a VerificationPayload or None")
        verification_pair_present = (
            self.verification_source is not None and self.verification_reference is not None
        )
        if self.verification is None:
            if self.verification_source is not None or self.verification_reference is not None:
                raise ReuseEfficiencyValidationError(
                    "verification source/reference require typed verification evidence"
                )
        else:
            if not verification_pair_present:
                raise ReuseEfficiencyValidationError(
                    "typed verification evidence requires source and reference"
                )
            object.__setattr__(
                self,
                "verification_source",
                _validate_text(self.verification_source, field_name="verification_source"),
            )
            if self.verification_reference is None:  # pragma: no cover - paired guard above
                raise AssertionError("verification reference unexpectedly absent")
            object.__setattr__(
                self,
                "verification_reference",
                _validate_uuid(
                    self.verification_reference,
                    field_name="verification_reference",
                ),
            )

        if self.outcome is ExecutionEvidenceOutcome.VERIFIED_SUCCESS:
            if self.verification is None or not self.verification.passed:
                raise ReuseEfficiencyValidationError(
                    "verified_success requires typed verification evidence with passed=True"
                )
        elif self.verification is not None and self.verification.passed:
            raise ReuseEfficiencyValidationError(
                "passed verification is inconsistent with a non-verified-success outcome"
            )
        if self.outcome is ExecutionEvidenceOutcome.UNVERIFIED and self.verification is not None:
            raise ReuseEfficiencyValidationError(
                "unverified outcome must not carry completed verification evidence"
            )

    @property
    def verified_success(self) -> bool:
        """Whether typed evidence satisfies the verified-success truth boundary."""
        return (
            self.outcome is ExecutionEvidenceOutcome.VERIFIED_SUCCESS
            and self.verification is not None
            and self.verification.passed
        )

    def to_dict(self) -> dict[str, object]:
        """Return the exact deterministic JSON-compatible schema."""
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id.to_str(),
            "episode_id": self.episode_id.to_str(),
            "correlation_id": str(self.correlation_id),
            "mode": self.mode.value,
            "outcome": self.outcome.value,
            "evidence_source": self.evidence_source,
            "evidence_reference": str(self.evidence_reference),
            "started_at": None if self.started_at is None else _format_timestamp(self.started_at),
            "ended_at": None if self.ended_at is None else _format_timestamp(self.ended_at),
            "elapsed_microseconds": (
                None if self.elapsed is None else _timedelta_microseconds(self.elapsed)
            ),
            "model_calls": self.model_calls,
            "model_input_tokens": self.model_input_tokens,
            "model_output_tokens": self.model_output_tokens,
            "model_tokens": self.model_tokens,
            "research_queries": self.research_queries,
            "machine_actions": self.machine_actions,
            "repair_attempts": self.repair_attempts,
            "external_cost": None if self.external_cost is None else str(self.external_cost),
            "cost_unit": self.cost_unit,
            "procedure": None if self.procedure is None else self.procedure.to_dict(),
            "verification": None if self.verification is None else self.verification.to_dict(),
            "verification_source": self.verification_source,
            "verification_reference": (
                None if self.verification_reference is None else str(self.verification_reference)
            ),
        }

    def to_json(self) -> str:
        """Serialize deterministically; no pickle/object hook/dynamic execution is used."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ExecutionEfficiencyEvidence:
        """Reconstruct one evidence record and reject missing/unknown fields."""
        _require_exact_fields(raw, expected=_EVIDENCE_FIELDS, context="execution evidence")
        _validate_schema_version(raw["schema_version"])

        procedure_raw = raw["procedure"]
        if procedure_raw is not None and not isinstance(procedure_raw, Mapping):
            raise ReuseEfficiencyDeserializationError("procedure must be an object or null")
        procedure = (
            None if procedure_raw is None else ProcedureRevisionRef.from_dict(procedure_raw)
        )

        verification_raw = raw["verification"]
        if verification_raw is not None and not isinstance(verification_raw, Mapping):
            raise ReuseEfficiencyDeserializationError("verification must be an object or null")
        try:
            verification = (
                None
                if verification_raw is None
                else VerificationPayload.from_dict(verification_raw)
            )
        except EventValidationError as exc:
            raise ReuseEfficiencyDeserializationError("verification payload is invalid") from exc

        verification_source = raw["verification_source"]
        if verification_source is not None and not isinstance(verification_source, str):
            raise ReuseEfficiencyDeserializationError(
                "verification_source must be a string or null"
            )
        verification_reference_raw = raw["verification_reference"]
        verification_reference = (
            None
            if verification_reference_raw is None
            else _parse_uuid(verification_reference_raw, field_name="verification_reference")
        )
        cost_unit = raw["cost_unit"]
        if cost_unit is not None and not isinstance(cost_unit, str):
            raise ReuseEfficiencyDeserializationError("cost_unit must be a string or null")

        return cls(
            task_id=_parse_task_id(raw["task_id"]),
            episode_id=_parse_episode_id(raw["episode_id"]),
            correlation_id=_parse_uuid(raw["correlation_id"], field_name="correlation_id"),
            mode=_parse_enum(ReuseMode, raw["mode"], field_name="mode"),
            outcome=_parse_enum(
                ExecutionEvidenceOutcome, raw["outcome"], field_name="outcome"
            ),
            evidence_source=_validate_text(raw["evidence_source"], field_name="evidence_source"),
            evidence_reference=_parse_uuid(
                raw["evidence_reference"], field_name="evidence_reference"
            ),
            started_at=_parse_timestamp(raw["started_at"], field_name="started_at"),
            ended_at=_parse_timestamp(raw["ended_at"], field_name="ended_at"),
            elapsed=_duration_from_microseconds(raw["elapsed_microseconds"]),
            model_calls=_validate_counter(raw["model_calls"], field_name="model_calls"),
            model_input_tokens=_validate_counter(
                raw["model_input_tokens"], field_name="model_input_tokens"
            ),
            model_output_tokens=_validate_counter(
                raw["model_output_tokens"], field_name="model_output_tokens"
            ),
            model_tokens=_validate_counter(raw["model_tokens"], field_name="model_tokens"),
            research_queries=_validate_counter(
                raw["research_queries"], field_name="research_queries"
            ),
            machine_actions=_validate_counter(
                raw["machine_actions"], field_name="machine_actions"
            ),
            repair_attempts=_validate_counter(
                raw["repair_attempts"], field_name="repair_attempts"
            ),
            external_cost=_parse_decimal(raw["external_cost"], field_name="external_cost"),
            cost_unit=cost_unit,
            procedure=procedure,
            verification=verification,
            verification_source=verification_source,
            verification_reference=verification_reference,
            schema_version=REUSE_EFFICIENCY_SCHEMA_VERSION,
        )

    @classmethod
    def from_json(cls, text: str) -> ExecutionEfficiencyEvidence:
        if not isinstance(text, str):
            raise TypeError("reuse-efficiency JSON must be a string")
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise ReuseEfficiencyDeserializationError("reuse-efficiency JSON is malformed") from exc
        if not isinstance(raw, Mapping):
            raise ReuseEfficiencyDeserializationError(
                "reuse-efficiency JSON root must be an object"
            )
        return cls.from_dict(raw)


_METRIC_FIELDS: Final[frozenset[str]] = frozenset(
    {"metric", "baseline", "reuse", "delta", "ratio", "change"}
)
_RATIO_FIELDS: Final[frozenset[str]] = frozenset({"numerator", "denominator"})


@dataclass(frozen=True, slots=True)
class MetricComparison:
    """Exact comparison of one common measured dimension.

    ``delta`` is ``reuse - baseline``. ``ratio`` is ``reuse / baseline`` and is
    unavailable (``None``) when the baseline denominator is zero.
    """

    metric: EfficiencyMetric
    baseline: Decimal
    reuse: Decimal
    delta: Decimal = field(init=False)
    ratio: Fraction | None = field(init=False)
    change: MetricChange = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.metric, EfficiencyMetric):
            raise TypeError("metric must be an EfficiencyMetric")
        baseline = _validate_decimal(self.baseline, field_name="baseline")
        reuse = _validate_decimal(self.reuse, field_name="reuse")
        if baseline is None or reuse is None:  # pragma: no cover - non-optional fields
            raise AssertionError("metric values unexpectedly absent")
        object.__setattr__(self, "baseline", baseline)
        object.__setattr__(self, "reuse", reuse)
        delta = reuse - baseline
        object.__setattr__(self, "delta", delta)
        object.__setattr__(
            self,
            "ratio",
            None if baseline == 0 else Fraction(reuse) / Fraction(baseline),
        )
        if delta < 0:
            change = MetricChange.IMPROVED
        elif delta > 0:
            change = MetricChange.REGRESSED
        else:
            change = MetricChange.SAME
        object.__setattr__(self, "change", change)

    def to_dict(self) -> dict[str, object]:
        ratio: dict[str, int] | None = None
        if self.ratio is not None:
            ratio = {
                "numerator": self.ratio.numerator,
                "denominator": self.ratio.denominator,
            }
        return {
            "metric": self.metric.value,
            "baseline": str(self.baseline),
            "reuse": str(self.reuse),
            "delta": str(self.delta),
            "ratio": ratio,
            "change": self.change.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> MetricComparison:
        _require_exact_fields(raw, expected=_METRIC_FIELDS, context="metric comparison")
        baseline = _parse_decimal(raw["baseline"], field_name="metric baseline")
        reuse = _parse_decimal(raw["reuse"], field_name="metric reuse")
        if baseline is None or reuse is None:
            raise ReuseEfficiencyDeserializationError("metric values must not be null")
        result = cls(
            metric=_parse_enum(EfficiencyMetric, raw["metric"], field_name="metric"),
            baseline=baseline,
            reuse=reuse,
        )
        if raw["delta"] != str(result.delta) or raw["change"] != result.change.value:
            raise ReuseEfficiencyDeserializationError(
                "serialized metric derived fields do not match measured values"
            )
        ratio_raw = raw["ratio"]
        if ratio_raw is None:
            if result.ratio is not None:
                raise ReuseEfficiencyDeserializationError("serialized ratio is unexpectedly null")
        else:
            if not isinstance(ratio_raw, Mapping):
                raise ReuseEfficiencyDeserializationError("ratio must be an object or null")
            _require_exact_fields(ratio_raw, expected=_RATIO_FIELDS, context="metric ratio")
            numerator = _validate_counter(ratio_raw["numerator"], field_name="ratio numerator")
            denominator = _validate_counter(
                ratio_raw["denominator"], field_name="ratio denominator"
            )
            if numerator is None or denominator is None or denominator == 0:
                raise ReuseEfficiencyDeserializationError("ratio must be finite and non-zero-based")
            if result.ratio != Fraction(numerator, denominator):
                raise ReuseEfficiencyDeserializationError(
                    "serialized ratio does not match measured values"
                )
        return result


_COMPARISON_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "baseline",
        "reuse",
        "relationship",
        "metrics",
        "disposition",
        "reasons",
    }
)


@dataclass(frozen=True, slots=True)
class ReuseComparison:
    """Immutable deterministic comparison between two distinct execution records."""

    baseline: ExecutionEfficiencyEvidence
    reuse: ExecutionEfficiencyEvidence
    relationship: TaskRelationshipEvidence | None
    metrics: tuple[MetricComparison, ...]
    disposition: ReuseEfficiencyDisposition
    reasons: tuple[str, ...]
    schema_version: int = REUSE_EFFICIENCY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.baseline, ExecutionEfficiencyEvidence):
            raise TypeError("baseline must be ExecutionEfficiencyEvidence")
        if not isinstance(self.reuse, ExecutionEfficiencyEvidence):
            raise TypeError("reuse must be ExecutionEfficiencyEvidence")
        if self.relationship is not None and not isinstance(
            self.relationship, TaskRelationshipEvidence
        ):
            raise TypeError("relationship must be TaskRelationshipEvidence or None")
        if not isinstance(self.metrics, tuple) or not all(
            isinstance(metric, MetricComparison) for metric in self.metrics
        ):
            raise TypeError("metrics must be a tuple of MetricComparison values")
        if len({metric.metric for metric in self.metrics}) != len(self.metrics):
            raise ReuseEfficiencyValidationError("metrics must not contain duplicates")
        if not isinstance(self.disposition, ReuseEfficiencyDisposition):
            raise TypeError("disposition must be a ReuseEfficiencyDisposition")
        if not isinstance(self.reasons, tuple) or not self.reasons:
            raise ReuseEfficiencyValidationError("reasons must be a non-empty tuple")
        for reason in self.reasons:
            _validate_text(reason, field_name="comparison reason")
        object.__setattr__(self, "schema_version", _validate_schema_version(self.schema_version))

        expected_metrics, expected_disposition, expected_reasons = _comparison_components(
            self.baseline,
            self.reuse,
            self.relationship,
        )
        if self.metrics != expected_metrics:
            raise ReuseEfficiencyValidationError(
                "metrics do not match the supplied execution evidence"
            )
        if self.disposition is not expected_disposition:
            raise ReuseEfficiencyValidationError(
                "disposition does not match the supplied typed execution evidence"
            )
        if self.reasons != expected_reasons:
            raise ReuseEfficiencyValidationError(
                "reasons do not match the deterministic comparison"
            )

    def metric(self, name: EfficiencyMetric) -> MetricComparison | None:
        """Return one metric comparison by typed name, or ``None`` when unavailable."""
        if not isinstance(name, EfficiencyMetric):
            raise TypeError("name must be an EfficiencyMetric")
        return next((metric for metric in self.metrics if metric.metric is name), None)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "baseline": self.baseline.to_dict(),
            "reuse": self.reuse.to_dict(),
            "relationship": None if self.relationship is None else self.relationship.to_dict(),
            "metrics": [metric.to_dict() for metric in self.metrics],
            "disposition": self.disposition.value,
            "reasons": list(self.reasons),
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
    def from_dict(cls, raw: Mapping[str, object]) -> ReuseComparison:
        _require_exact_fields(raw, expected=_COMPARISON_FIELDS, context="reuse comparison")
        _validate_schema_version(raw["schema_version"])
        baseline_raw = raw["baseline"]
        reuse_raw = raw["reuse"]
        if not isinstance(baseline_raw, Mapping) or not isinstance(reuse_raw, Mapping):
            raise ReuseEfficiencyDeserializationError("baseline and reuse must be objects")
        relationship_raw = raw["relationship"]
        if relationship_raw is not None and not isinstance(relationship_raw, Mapping):
            raise ReuseEfficiencyDeserializationError("relationship must be an object or null")
        relationship = (
            None
            if relationship_raw is None
            else TaskRelationshipEvidence.from_dict(relationship_raw)
        )
        comparison = compare_execution_efficiency(
            ExecutionEfficiencyEvidence.from_dict(baseline_raw),
            ExecutionEfficiencyEvidence.from_dict(reuse_raw),
            relationship=relationship,
        )
        if raw["disposition"] != comparison.disposition.value:
            raise ReuseEfficiencyDeserializationError(
                "serialized disposition does not match typed evidence"
            )
        reasons_raw = raw["reasons"]
        if not isinstance(reasons_raw, list) or reasons_raw != list(comparison.reasons):
            raise ReuseEfficiencyDeserializationError(
                "serialized reasons do not match deterministic comparison"
            )
        metrics_raw = raw["metrics"]
        if not isinstance(metrics_raw, list):
            raise ReuseEfficiencyDeserializationError("metrics must be an array")
        parsed_metrics: list[MetricComparison] = []
        for item in metrics_raw:
            if not isinstance(item, Mapping):
                raise ReuseEfficiencyDeserializationError("metric entry must be an object")
            parsed_metrics.append(MetricComparison.from_dict(item))
        if tuple(parsed_metrics) != comparison.metrics:
            raise ReuseEfficiencyDeserializationError(
                "serialized metrics do not match deterministic comparison"
            )
        return comparison

    @classmethod
    def from_json(cls, text: str) -> ReuseComparison:
        if not isinstance(text, str):
            raise TypeError("reuse comparison JSON must be a string")
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise ReuseEfficiencyDeserializationError("reuse comparison JSON is malformed") from exc
        if not isinstance(raw, Mapping):
            raise ReuseEfficiencyDeserializationError(
                "reuse comparison JSON root must be an object"
            )
        return cls.from_dict(raw)


def _metric_value(value: int | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(value)


def _metric_pair(
    metric: EfficiencyMetric,
    baseline: int | Decimal | None,
    reuse: int | Decimal | None,
) -> MetricComparison | None:
    baseline_value = _metric_value(baseline)
    reuse_value = _metric_value(reuse)
    if baseline_value is None or reuse_value is None:
        return None
    return MetricComparison(metric=metric, baseline=baseline_value, reuse=reuse_value)


def _duration_metric(
    baseline: timedelta | None, reuse: timedelta | None
) -> MetricComparison | None:
    if baseline is None or reuse is None:
        return None
    return MetricComparison(
        metric=EfficiencyMetric.DURATION_MICROSECONDS,
        baseline=Decimal(_timedelta_microseconds(baseline)),
        reuse=Decimal(_timedelta_microseconds(reuse)),
    )


def _collect_metrics(
    baseline: ExecutionEfficiencyEvidence,
    reuse: ExecutionEfficiencyEvidence,
) -> tuple[MetricComparison, ...]:
    metrics: list[MetricComparison] = []
    candidates = (
        _metric_pair(EfficiencyMetric.MODEL_CALLS, baseline.model_calls, reuse.model_calls),
        _metric_pair(EfficiencyMetric.MODEL_TOKENS, baseline.model_tokens, reuse.model_tokens),
        _duration_metric(baseline.elapsed, reuse.elapsed),
        (
            _metric_pair(
                EfficiencyMetric.EXTERNAL_COST, baseline.external_cost, reuse.external_cost
            )
            if baseline.cost_unit == reuse.cost_unit
            else None
        ),
        _metric_pair(
            EfficiencyMetric.RESEARCH_QUERIES,
            baseline.research_queries,
            reuse.research_queries,
        ),
        _metric_pair(
            EfficiencyMetric.MACHINE_ACTIONS,
            baseline.machine_actions,
            reuse.machine_actions,
        ),
        _metric_pair(
            EfficiencyMetric.REPAIR_ATTEMPTS,
            baseline.repair_attempts,
            reuse.repair_attempts,
        ),
    )
    metrics.extend(candidate for candidate in candidates if candidate is not None)
    return tuple(metrics)


def _metric_reason(metric: MetricComparison) -> str:
    return (
        f"{metric.metric.value} {metric.change.value}: "
        f"{metric.baseline} -> {metric.reuse} (delta {metric.delta})"
    )


def _comparison_components(
    baseline: ExecutionEfficiencyEvidence,
    reuse: ExecutionEfficiencyEvidence,
    relationship: TaskRelationshipEvidence | None,
) -> tuple[tuple[MetricComparison, ...], ReuseEfficiencyDisposition, tuple[str, ...]]:
    if baseline.evidence_reference == reuse.evidence_reference or (
        baseline.episode_id == reuse.episode_id and baseline.correlation_id == reuse.correlation_id
    ):
        raise ReuseEfficiencyValidationError(
            "baseline and reuse must be distinct measured execution evidence"
        )

    metrics = _collect_metrics(baseline, reuse)
    reasons: list[str] = []

    if baseline.task_id == reuse.task_id:
        if (
            relationship is not None
            and relationship.relationship is not TaskRelationship.SAME_TASK_REPLAY
        ):
            return (
                metrics,
                ReuseEfficiencyDisposition.NOT_COMPARABLE,
                ("relationship evidence contradicts identical TaskId values",),
            )
        reasons.append("same TaskId establishes same-task replay")
    else:
        if relationship is None:
            return (
                metrics,
                ReuseEfficiencyDisposition.NOT_COMPARABLE,
                ("different TaskId values require explicit relationship evidence",),
            )
        if relationship.relationship is TaskRelationship.SAME_TASK_REPLAY:
            return (
                metrics,
                ReuseEfficiencyDisposition.NOT_COMPARABLE,
                ("same-task relationship evidence contradicts different TaskId values",),
            )
        if relationship.relationship is TaskRelationship.UNRELATED:
            return (
                metrics,
                ReuseEfficiencyDisposition.NOT_COMPARABLE,
                ("typed relationship evidence marks the executions as unrelated",),
            )
        reasons.append("typed relationship evidence establishes related-task reuse")

    if (
        baseline.external_cost is not None
        and reuse.external_cost is not None
        and baseline.cost_unit != reuse.cost_unit
    ):
        reasons.append("external_cost omitted because accounting units differ")

    if baseline.verified_success and not reuse.verified_success:
        reasons.append("reuse lost verified success; cheaper execution cannot be an efficiency win")
        reasons.extend(_metric_reason(metric) for metric in metrics)
        return metrics, ReuseEfficiencyDisposition.REGRESSED, tuple(reasons)

    if not baseline.verified_success:
        reasons.append("baseline lacks typed verified-success evidence")
        reasons.extend(_metric_reason(metric) for metric in metrics)
        return metrics, ReuseEfficiencyDisposition.INSUFFICIENT_EVIDENCE, tuple(reasons)

    if not reuse.verified_success:
        reasons.append("reuse lacks typed verified-success evidence")
        reasons.extend(_metric_reason(metric) for metric in metrics)
        return metrics, ReuseEfficiencyDisposition.REGRESSED, tuple(reasons)

    if not metrics:
        reasons.append("no common measured efficiency dimension is available")
        return metrics, ReuseEfficiencyDisposition.INSUFFICIENT_EVIDENCE, tuple(reasons)

    reasons.extend(_metric_reason(metric) for metric in metrics)
    if any(metric.change is MetricChange.REGRESSED for metric in metrics):
        return metrics, ReuseEfficiencyDisposition.REGRESSED, tuple(reasons)
    if any(metric.change is MetricChange.IMPROVED for metric in metrics):
        return metrics, ReuseEfficiencyDisposition.IMPROVED, tuple(reasons)
    return metrics, ReuseEfficiencyDisposition.NO_MATERIAL_IMPROVEMENT, tuple(reasons)


def compare_execution_efficiency(
    baseline: ExecutionEfficiencyEvidence,
    reuse: ExecutionEfficiencyEvidence,
    *,
    relationship: TaskRelationshipEvidence | None = None,
) -> ReuseComparison:
    """Compare two measured runs without executing, timing, routing, or inferring text.

    Lower measured resource consumption is considered improvement only when
    verified success is preserved and task comparability is established. Any
    measured regression yields ``REGRESSED``; at least one improvement with no
    measured regression yields ``IMPROVED``; equality yields
    ``NO_MATERIAL_IMPROVEMENT``. Missing metrics remain unavailable.
    """

    if not isinstance(baseline, ExecutionEfficiencyEvidence):
        raise TypeError("baseline must be ExecutionEfficiencyEvidence")
    if not isinstance(reuse, ExecutionEfficiencyEvidence):
        raise TypeError("reuse must be ExecutionEfficiencyEvidence")
    if relationship is not None and not isinstance(relationship, TaskRelationshipEvidence):
        raise TypeError("relationship must be TaskRelationshipEvidence or None")

    metrics, disposition, reasons = _comparison_components(baseline, reuse, relationship)
    return ReuseComparison(
        baseline=baseline,
        reuse=reuse,
        relationship=relationship,
        metrics=metrics,
        disposition=disposition,
        reasons=reasons,
    )


__all__ = [
    "REUSE_EFFICIENCY_SCHEMA_VERSION",
    "EfficiencyMetric",
    "ExecutionEfficiencyEvidence",
    "ExecutionEvidenceOutcome",
    "MetricChange",
    "MetricComparison",
    "ProcedureRevisionRef",
    "ReuseComparison",
    "ReuseEfficiencyDeserializationError",
    "ReuseEfficiencyDisposition",
    "ReuseEfficiencyValidationError",
    "ReuseMode",
    "TaskRelationship",
    "TaskRelationshipEvidence",
    "UnsupportedReuseEfficiencySchemaVersionError",
    "compare_execution_efficiency",
]
