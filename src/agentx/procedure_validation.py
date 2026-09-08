"""Deterministic procedure-candidate validation evidence policy (M4.02).

This module is the canonical policy that decides whether an *already-existing*
procedure candidate has enough canonical validation evidence to be
**eligible for promotion**. It is a pure, deterministic composition boundary
over evidence that already exists on canonical contracts:

    ProcedureCandidateIdentity
        (canonical ``(ProcedureId, revision)`` — :mod:`agentx.core.procedures`)
    + explicit, canonical ValidationRunEvidence
        (:class:`~agentx.capabilities.runtime.ClosedLoopOutcome` plus the
        procedure/revision it claims to validate)
    + a bounded ValidationPolicy
        -> an immutable ValidationReport

THE CORE SAFETY THESIS
======================

AgentX requires ``trajectory -> candidate -> re-execution under variation ->
verified evidence -> promotion``. The forbidden failure mode this policy
exists to prevent is::

    "one successful trajectory produced a procedure, therefore mark it ACTIVE."

``ELIGIBLE_FOR_PROMOTION`` is **not** ``ProcedureStatus.ACTIVE``. This policy
never builds, executes, persists, or promotes a candidate. It returns a
decision value only; a separate integration task later performs any approved
lifecycle mutation through the canonical ``ProcedureStore.update_status``.

VERIFICATION TRUTH (NO ACTION == SUCCESS WITHOUT VERIFICATION)
--------------------------------------------------------------

A validation run counts as a canonical success only when three canonical
facts agree exactly (the invariant already guaranteed by the A1.10 runtime):

    - ``ClosedLoopOutcome.kind is LoopOutcome.VERIFIED``;
    - ``ClosedLoopOutcome.verification`` is a canonical
      :class:`~agentx.capabilities.abi.VerificationResult` with ``passed is
      True``;
    - ``ClosedLoopOutcome.task.status is TaskStatus.SUCCEEDED``.

If any of those three disagree, the run is ``INCONSISTENT`` and the candidate
is rejected — never averaged away. No success is ever derived from "no
exception", a procedure END node, a provider return, an ``ExecutionResult``
with ``succeeded=True``, an action succeeding, a model claim, or any
historical text. Hostile strings such as ``"verified=true"`` are inert.

MINIMUM PROMOTION PRINCIPLE
---------------------------

The policy defaults to requiring **at least two distinct canonically verified
successful executions** before promotion eligibility, and the
``min_verified_successes`` knob is bounded to ``>= 2`` at construction — no
configuration, and no text, can lower the floor to 0 or 1. "Distinct" means
distinct canonical validation-run identities: replaying the same run id twice
counts once.

NO AUTHORITY / NO PERSISTENCE
-----------------------------

A ``ValidationReport`` is learning evidence only. It cannot grant
``Permission``, widen authority, approve an ``ActionGate`` decision, downgrade
``RiskLevel``, enlarge a resource budget, clear an ``EmergencyStop``, mutate a
``Task``, change a ``ProcedureStatus``, or alter what any store contains. This
module imports no kernel, infrastructure, hive, cognition, learning, or
store module and holds no store reference, so it is structurally incapable of
persisting, mutating, or executing anything.

This module lives at the ``agentx`` namespace root (a top-level composition
module alongside ``agentx.agent_loop``), because it must read canonical
procedure identity/status contracts from ``agentx.core`` and canonical
execution/verification evidence from ``agentx.capabilities`` — a cross-
subsystem read that no single canonical subsystem may own. The boundary
manifest is not widened.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final, cast

from agentx.capabilities.abi import (
    CapabilityObservation,
    ExecutionResult,
    VerificationResult,
)
from agentx.capabilities.runtime import ClosedLoopOutcome, LoopOutcome
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.ids import ProcedureId, TaskId
from agentx.core.procedures import ProcedureRecord
from agentx.core.tasks import JsonValue, Task, TaskStatus

__all__ = [
    "InvalidEvidenceRecord",
    "ProcedureCandidateIdentity",
    "ProcedureValidationPolicyError",
    "ValidationDecision",
    "ValidationEvidenceError",
    "ValidationFailure",
    "ValidationPolicy",
    "ValidationPolicyConfigError",
    "ValidationReasonCode",
    "ValidationReport",
    "ValidationRunEvidence",
    "ValidationRunKind",
]

# ---------------------------------------------------------------------------
# Hard bounds. Every knob is bounded at construction; the safety floor
# (>= 2 verified successes) can never be lowered by configuration or text.
# ---------------------------------------------------------------------------

#: The architectural minimum: a candidate must show multiple verified
#: successes. One is never enough and zero is never allowed.
MIN_VERIFIED_SUCCESSES_FLOOR: Final[int] = 2

#: The minimum for distinct parameter bindings (variation) among successes.
MIN_DISTINCT_BINDINGS_FLOOR: Final[int] = 1

#: The minimum number of validation records the policy is willing to consider.
MIN_RECORDS_CONSIDERED_FLOOR: Final[int] = 1

#: Upper cap for ``min_verified_successes`` / ``min_distinct_parameter_bindings``.
_MAX_MIN_THRESHOLD: Final[int] = 10_000

#: Upper cap for the number of validation records considered.
_MAX_RECORDS_CONSIDERED: Final[int] = 1_000_000

#: Upper cap for tolerated current-revision failures.
_MAX_TOLERATED_FAILURES: Final[int] = 10_000

#: Bounds on optional environment identity and observed parameter bindings.
_MAX_ENVIRONMENT_LABEL_LENGTH: Final[int] = 128
_MAX_ALLOWED_ENVIRONMENTS: Final[int] = 128
_MAX_BINDING_ENTRIES: Final[int] = 256
_MAX_BINDING_KEY_LENGTH: Final[int] = 128

_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class ProcedureValidationPolicyError(ValueError):
    """Base error for the procedure validation evidence policy."""


class ValidationPolicyConfigError(ProcedureValidationPolicyError):
    """Raised when a :class:`ValidationPolicy` configuration violates hard bounds."""


class ValidationEvidenceError(ProcedureValidationPolicyError):
    """Raised when caller-supplied validation evidence violates the contract."""


class ValidationDecision(StrEnum):
    """The complete decision vocabulary of the policy.

    ``ELIGIBLE_FOR_PROMOTION`` is the only positive outcome and is explicitly
    NOT ``ProcedureStatus.ACTIVE``. Every other member fails closed:

    - ``INSUFFICIENT_EVIDENCE`` — usable current-revision evidence exists but
      does not yet satisfy the policy thresholds (including zero evidence).
    - ``REJECTED`` — a current-revision *canonical* failure (verification
      failure, execution failure, denial, timeout/cancel, environment
      mismatch) or inconsistent verification truth blocks clean eligibility.
    - ``DEGRADED`` — the evidence set is contaminated (forged/non-canonical
      records, or a conflicting duplicate run identity), so clean eligibility
      is withheld even though no canonical failure was found.
    """

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    ELIGIBLE_FOR_PROMOTION = "eligible_for_promotion"
    REJECTED = "rejected"
    DEGRADED = "degraded"


class ValidationRunKind(StrEnum):
    """Controlled classification of one current-revision validation record.

    ``VERIFIED_SUCCESS`` is the only success classification. ``FORGED`` marks
    records whose canonical fields are not the canonical types (for example a
    lookalike ``VerificationResult``), and ``INCONSISTENT`` marks records whose
    canonical verification truth disagrees with itself. ``ENVIRONMENT_MISMATCH``
    marks an otherwise-successful run whose declared environment identity is
    not among the policy's permitted environments.
    """

    VERIFIED_SUCCESS = "verified_success"
    VERIFICATION_FAILURE = "verification_failure"
    EXECUTION_FAILURE = "execution_failure"
    DENIED = "denied"
    TIMEOUT_OR_CANCEL = "timeout_or_cancel"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    INCONSISTENT = "inconsistent"
    FORGED = "forged"


class ValidationReasonCode(StrEnum):
    """Deterministic reason vocabulary for report contents.

    These never echo evidence content: hostile text inside a forged record or
    an observation cannot leak through a reason code.
    """

    ELIGIBLE = "eligible"
    NO_EVIDENCE = "no_evidence"
    INSUFFICIENT_VERIFIED_SUCCESSES = "insufficient_verified_successes"
    EXACT_REPEAT_ONLY = "exact_repeat_only"
    INSUFFICIENT_PARAMETER_VARIATION = "insufficient_parameter_variation"
    VERIFICATION_FAILURE = "verification_failure"
    EXECUTION_FAILURE = "execution_failure"
    DENIED_RUN = "denied_run"
    TIMEOUT_OR_CANCEL = "timeout_or_cancel"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    INCONSISTENT_EVIDENCE = "inconsistent_evidence"
    FORGED_EVIDENCE = "forged_evidence"
    CONFLICTING_DUPLICATE_IDENTITY = "conflicting_duplicate_identity"
    STALE_REVISION = "stale_revision"
    FOREIGN_IDENTITY = "foreign_identity"
    REPLAYED_DUPLICATE = "replayed_duplicate"


#: Canonical run classification -> the reason code it contributes when it is a
#: failure (or an integrity problem) rather than a success.
_FAILURE_REASON_FOR_KIND: Final[Mapping[ValidationRunKind, ValidationReasonCode]] = (
    MappingProxyType(
        {
            ValidationRunKind.VERIFICATION_FAILURE: ValidationReasonCode.VERIFICATION_FAILURE,
            ValidationRunKind.EXECUTION_FAILURE: ValidationReasonCode.EXECUTION_FAILURE,
            ValidationRunKind.DENIED: ValidationReasonCode.DENIED_RUN,
            ValidationRunKind.TIMEOUT_OR_CANCEL: ValidationReasonCode.TIMEOUT_OR_CANCEL,
            ValidationRunKind.ENVIRONMENT_MISMATCH: ValidationReasonCode.ENVIRONMENT_MISMATCH,
            ValidationRunKind.INCONSISTENT: ValidationReasonCode.INCONSISTENT_EVIDENCE,
        }
    )
)

#: Canonical failure kinds, in the fixed order they are reported.
_FAILURE_KINDS_IN_ORDER: Final[tuple[ValidationRunKind, ...]] = (
    ValidationRunKind.INCONSISTENT,
    ValidationRunKind.VERIFICATION_FAILURE,
    ValidationRunKind.EXECUTION_FAILURE,
    ValidationRunKind.DENIED,
    ValidationRunKind.TIMEOUT_OR_CANCEL,
    ValidationRunKind.ENVIRONMENT_MISMATCH,
)

#: Failure kinds that count against ``max_current_revision_failures``.
_BLOCKING_FAILURE_KINDS: Final[frozenset[ValidationRunKind]] = frozenset(
    {
        ValidationRunKind.VERIFICATION_FAILURE,
        ValidationRunKind.EXECUTION_FAILURE,
        ValidationRunKind.DENIED,
        ValidationRunKind.TIMEOUT_OR_CANCEL,
        ValidationRunKind.ENVIRONMENT_MISMATCH,
    }
)


# ---------------------------------------------------------------------------
# Validation helpers.
# ---------------------------------------------------------------------------


def _validate_revision(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if value < 1:
        raise ValidationEvidenceError(
            f"{field_name} must be a positive integer (first revision is 1)"
        )
    return value


def _validate_bounded_int(
    value: object,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int, got {type(value).__name__}")
    if not minimum <= value <= maximum:
        raise ValidationPolicyConfigError(
            f"{field_name} must be between {minimum} and {maximum}; got {value}"
        )
    return value


def _freeze_json_value(value: object, *, path: str) -> object:
    """Validate JSON compatibility and return an immutable defensive copy."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationEvidenceError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationEvidenceError(f"{path} contains a non-string object key")
            frozen[key] = _freeze_json_value(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(
            _freeze_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        )
    raise ValidationEvidenceError(
        f"{path} contains a non-JSON-compatible value of type {type(value).__name__}"
    )


def _validate_binding_key(key: str) -> str:
    if not key or key != key.strip():
        raise ValidationEvidenceError("parameter binding keys must be non-empty and trimmed")
    if any(character in key for character in _CONTROL_CHARACTERS):
        raise ValidationEvidenceError("parameter binding keys must not contain control characters")
    if len(key) > _MAX_BINDING_KEY_LENGTH:
        raise ValidationEvidenceError(
            f"parameter binding keys must not exceed {_MAX_BINDING_KEY_LENGTH} characters"
        )
    return key


def _freeze_parameter_binding(value: object) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise TypeError(
            "parameter_binding must be a mapping of parameter name to JSON-compatible "
            f"value, got {type(value).__name__}"
        )
    if len(value) > _MAX_BINDING_ENTRIES:
        raise ValidationEvidenceError(
            f"parameter_binding must not exceed {_MAX_BINDING_ENTRIES} entries"
        )
    frozen: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValidationEvidenceError("parameter binding keys must be strings")
        _validate_binding_key(key)
        frozen[key] = _freeze_json_value(item, path=f"parameter_binding.{key}")
    return MappingProxyType(cast("dict[str, JsonValue]", frozen))


def _validate_environment(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"environment must be a string or None, got {type(value).__name__}")
    if not value or value != value.strip():
        raise ValidationEvidenceError("environment must be non-empty and trimmed")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise ValidationEvidenceError("environment must not contain control characters")
    if len(value) > _MAX_ENVIRONMENT_LABEL_LENGTH:
        raise ValidationEvidenceError(
            f"environment must not exceed {_MAX_ENVIRONMENT_LABEL_LENGTH} characters"
        )
    return value


def _freeze_allowed_environments(value: object) -> frozenset[str] | None:
    if value is None:
        return None
    if not isinstance(value, frozenset):
        raise TypeError(
            "allowed_environments must be a frozenset of strings or None, "
            f"got {type(value).__name__}"
        )
    if len(value) > _MAX_ALLOWED_ENVIRONMENTS:
        raise ValidationPolicyConfigError(
            f"allowed_environments must not exceed {_MAX_ALLOWED_ENVIRONMENTS} entries"
        )
    validated: set[str] = set()
    for entry in value:
        environment = _validate_environment(entry)
        if environment is None:
            raise ValidationEvidenceError("allowed_environments entries must not be None")
        validated.add(environment)
    return frozenset(validated)


def _validate_optional_timestamp(value: object, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a timezone-aware datetime or None")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationEvidenceError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


# ---------------------------------------------------------------------------
# Candidate identity.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProcedureCandidateIdentity:
    """Canonical identity of the procedure candidate under validation.

    Identity is the canonical ``(procedure_id, revision)`` pair owned by
    :class:`agentx.core.procedures.ProcedureRecord`. It carries no status: the
    policy computes promotion eligibility evidence only and never reads or
    mutates ``ProcedureStatus``.
    """

    procedure_id: ProcedureId
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, ProcedureId):
            raise TypeError(
                f"procedure_id must be a ProcedureId, got {type(self.procedure_id).__name__}"
            )
        object.__setattr__(
            self, "revision", _validate_revision(self.revision, field_name="revision")
        )

    @classmethod
    def from_record(cls, record: ProcedureRecord) -> ProcedureCandidateIdentity:
        """Derive candidate identity from a canonical procedure record."""
        if not isinstance(record, ProcedureRecord):
            raise TypeError(f"record must be a ProcedureRecord, got {type(record).__name__}")
        return cls(procedure_id=record.procedure_id, revision=record.revision)


# ---------------------------------------------------------------------------
# Validation run evidence.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValidationRunEvidence:
    """One explicit, canonical validation run for a procedure revision.

    Every fact is canonical or explicitly typed:

    - ``run_id`` — canonical validation-run identity (a ``TaskId``; the
      governed unit of work that produced the run). Duplicate counting and
      conflicting-duplicate detection key on this identity.
    - ``procedure_id`` / ``revision`` — the procedure revision this evidence
      claims to validate.
    - ``outcome`` — the canonical
      :class:`~agentx.capabilities.runtime.ClosedLoopOutcome` carrying
      execution result, verification result, and terminal task state.
    - ``parameter_binding`` — the observed parameter binding for the run,
      frozen and JSON-compatible only. This (not timestamps) is the source of
      variation distinctness.
    - ``environment`` — optional opaque environment identity label. Never
      interpreted, never grants authority.
    - ``recorded_at`` — optional UTC timestamp. Never used for variation and
      never used for replay identity.
    """

    run_id: TaskId
    procedure_id: ProcedureId
    revision: int
    outcome: ClosedLoopOutcome
    parameter_binding: Mapping[str, JsonValue] = field(default_factory=dict)
    environment: str | None = None
    recorded_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, TaskId):
            raise TypeError(f"run_id must be a TaskId, got {type(self.run_id).__name__}")
        if not isinstance(self.procedure_id, ProcedureId):
            raise TypeError(
                f"procedure_id must be a ProcedureId, got {type(self.procedure_id).__name__}"
            )
        object.__setattr__(
            self, "revision", _validate_revision(self.revision, field_name="revision")
        )
        if not isinstance(self.outcome, ClosedLoopOutcome):
            raise TypeError(
                f"outcome must be a ClosedLoopOutcome, got {type(self.outcome).__name__}"
            )
        object.__setattr__(
            self, "parameter_binding", _freeze_parameter_binding(self.parameter_binding)
        )
        object.__setattr__(self, "environment", _validate_environment(self.environment))
        object.__setattr__(
            self,
            "recorded_at",
            _validate_optional_timestamp(self.recorded_at, field_name="recorded_at"),
        )


# ---------------------------------------------------------------------------
# Report structures.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValidationFailure:
    """One current-revision failure that the policy must not average away."""

    run_id: TaskId
    kind: ValidationRunKind
    reason: ValidationReasonCode


@dataclass(frozen=True, slots=True)
class InvalidEvidenceRecord:
    """One record excluded for failing canonical evidence identity/integrity."""

    run_id: TaskId
    reason: ValidationReasonCode


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Immutable structured decision report. Never a naked bool.

    Every field is a frozen value: the report is immune to mutation after it is
    returned, so a caller cannot rewrite a decision in place.
    """

    candidate: ProcedureCandidateIdentity
    decision: ValidationDecision
    verified_successes: int
    counted_success_run_ids: tuple[TaskId, ...]
    distinct_parameter_bindings: int
    distinct_environments: int
    exact_repeat_only: bool
    failures: tuple[ValidationFailure, ...]
    invalid_evidence: tuple[InvalidEvidenceRecord, ...]
    stale_revision_records: tuple[TaskId, ...]
    foreign_identity_records: tuple[TaskId, ...]
    replayed_duplicates: tuple[TaskId, ...]
    considered_record_count: int
    truncated_record_count: int
    requirements: tuple[str, ...]
    unmet_requirements: tuple[str, ...]
    reasons: tuple[ValidationReasonCode, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, ProcedureCandidateIdentity):
            raise TypeError("candidate must be a ProcedureCandidateIdentity")
        if not isinstance(self.decision, ValidationDecision):
            raise TypeError("decision must be a ValidationDecision")
        if type(self.verified_successes) is not int:
            raise TypeError("verified_successes must be an int")

    @property
    def eligible(self) -> bool:
        """True only for the single positive outcome.

        A convenience projection of ``decision``; the authoritative value is
        always the ``decision`` enum, never this bool.
        """
        return self.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION


# ---------------------------------------------------------------------------
# Policy.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    """Bounded, configurable promotion-eligibility policy.

    ``min_verified_successes`` is floored at :data:`MIN_VERIFIED_SUCCESSES_FLOOR`
    (2) and capped at :data:`_MAX_MIN_THRESHOLD`; no caller and no text can
    relax the floor to 0 or 1. All knobs reject zero/negative/unlimited/absurd
    values at construction.

    ``allowed_environments``, when set, restricts which declared environment
    identities a success may run in; evidence without an environment identity
    (``None``) is not presumed mismatched, because environment identity is
    optional and not universally canonical.
    """

    min_verified_successes: int = 2
    min_distinct_parameter_bindings: int = 1
    max_validation_records_considered: int = 10_000
    max_current_revision_failures: int = 0
    allowed_environments: frozenset[str] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "min_verified_successes",
            _validate_bounded_int(
                self.min_verified_successes,
                field_name="min_verified_successes",
                minimum=MIN_VERIFIED_SUCCESSES_FLOOR,
                maximum=_MAX_MIN_THRESHOLD,
            ),
        )
        object.__setattr__(
            self,
            "min_distinct_parameter_bindings",
            _validate_bounded_int(
                self.min_distinct_parameter_bindings,
                field_name="min_distinct_parameter_bindings",
                minimum=MIN_DISTINCT_BINDINGS_FLOOR,
                maximum=_MAX_MIN_THRESHOLD,
            ),
        )
        object.__setattr__(
            self,
            "max_validation_records_considered",
            _validate_bounded_int(
                self.max_validation_records_considered,
                field_name="max_validation_records_considered",
                minimum=MIN_RECORDS_CONSIDERED_FLOOR,
                maximum=_MAX_RECORDS_CONSIDERED,
            ),
        )
        object.__setattr__(
            self,
            "max_current_revision_failures",
            _validate_bounded_int(
                self.max_current_revision_failures,
                field_name="max_current_revision_failures",
                minimum=0,
                maximum=_MAX_TOLERATED_FAILURES,
            ),
        )
        object.__setattr__(
            self,
            "allowed_environments",
            _freeze_allowed_environments(self.allowed_environments),
        )

    # -- decision -----------------------------------------------------------

    def evaluate(
        self,
        candidate: ProcedureCandidateIdentity,
        evidence: Iterable[ValidationRunEvidence],
    ) -> ValidationReport:
        """Evaluate one candidate against explicit canonical validation evidence.

        Deterministic and side-effect free: the same candidate, evidence
        sequence, and policy always produce an equal report. The report is
        derived entirely from canonical typed fields; no text, no string, and
        no callable can influence the decision.

        Raises :class:`TypeError` for arguments of the wrong Python type (a
        programming error). Evidence problems that are part of the policy's
        domain — stale revisions, foreign identities, replayed duplicates,
        forged records, canonical failures — are captured in the report and
        never raised.
        """
        if not isinstance(candidate, ProcedureCandidateIdentity):
            raise TypeError(
                f"candidate must be a ProcedureCandidateIdentity, got {type(candidate).__name__}"
            )
        if not isinstance(evidence, Iterable):
            raise TypeError(
                "evidence must be an iterable of ValidationRunEvidence, "
                f"got {type(evidence).__name__}"
            )
        records = list(evidence)
        for record in records:
            if not isinstance(record, ValidationRunEvidence):
                raise TypeError(
                    f"evidence must contain only ValidationRunEvidence, got {type(record).__name__}"
                )

        truncated_record_count = max(0, len(records) - self.max_validation_records_considered)
        considered = records[: self.max_validation_records_considered]

        # First-encounter map keyed by canonical run identity.
        seen_by_run_id: dict[str, ValidationRunEvidence] = {}
        successes: list[ValidationRunEvidence] = []
        failures: list[ValidationFailure] = []
        invalid_evidence: list[InvalidEvidenceRecord] = []
        stale_revision_records: set[TaskId] = set()
        foreign_identity_records: set[TaskId] = set()
        replayed_duplicates: set[TaskId] = set()
        conflicting_duplicates: set[TaskId] = set()

        for record in considered:
            if record.procedure_id != candidate.procedure_id:
                foreign_identity_records.add(record.run_id)
                continue
            if record.revision != candidate.revision:
                stale_revision_records.add(record.run_id)
                continue

            run_key = record.run_id.to_str()
            prior = seen_by_run_id.get(run_key)
            if prior is not None:
                if _same_run_content(prior, record):
                    replayed_duplicates.add(record.run_id)
                else:
                    conflicting_duplicates.add(record.run_id)
                    invalid_evidence.append(
                        InvalidEvidenceRecord(
                            record.run_id, ValidationReasonCode.CONFLICTING_DUPLICATE_IDENTITY
                        )
                    )
                continue
            seen_by_run_id[run_key] = record

            kind = _classify_outcome(record.outcome)
            if kind is ValidationRunKind.FORGED:
                invalid_evidence.append(
                    InvalidEvidenceRecord(record.run_id, ValidationReasonCode.FORGED_EVIDENCE)
                )
                continue

            if kind is ValidationRunKind.VERIFIED_SUCCESS and not self._environment_permitted(
                record.environment
            ):
                kind = ValidationRunKind.ENVIRONMENT_MISMATCH

            if kind is ValidationRunKind.VERIFIED_SUCCESS:
                successes.append(record)
            else:
                failures.append(
                    ValidationFailure(record.run_id, kind, _FAILURE_REASON_FOR_KIND[kind])
                )

        successes.sort(key=lambda record: record.run_id.to_str())

        verified_successes = len(successes)
        distinct_parameter_bindings = _count_distinct_bindings(
            record.parameter_binding for record in successes
        )
        distinct_environments = len(
            {record.environment for record in successes if record.environment is not None}
        )
        exact_repeat_only = _is_exact_repeat_only(successes)

        blocking_failures = [
            failure for failure in failures if failure.kind in _BLOCKING_FAILURE_KINDS
        ]
        inconsistent_present = any(
            failure.kind is ValidationRunKind.INCONSISTENT for failure in failures
        )
        forged_present = any(
            record.reason is ValidationReasonCode.FORGED_EVIDENCE for record in invalid_evidence
        )
        conflicting_present = bool(conflicting_duplicates)

        decision = self._decide(
            verified_successes=verified_successes,
            distinct_parameter_bindings=distinct_parameter_bindings,
            blocking_failure_count=len(blocking_failures),
            inconsistent_present=inconsistent_present,
            forged_present=forged_present,
            conflicting_present=conflicting_present,
            no_usable_evidence=(not successes and not failures and not invalid_evidence),
        )

        requirements = _requirement_texts(self)
        unmet_requirements = _unmet_requirement_texts(
            self,
            verified_successes=verified_successes,
            distinct_parameter_bindings=distinct_parameter_bindings,
            blocking_failure_count=len(blocking_failures),
            inconsistent_present=inconsistent_present,
            forged_present=forged_present,
            conflicting_present=conflicting_present,
        )

        reasons = _build_reasons(
            decision,
            self,
            verified_successes=verified_successes,
            distinct_parameter_bindings=distinct_parameter_bindings,
            exact_repeat_only=exact_repeat_only,
            blocking_failures=blocking_failures,
            inconsistent_present=inconsistent_present,
            forged_present=forged_present,
            conflicting_present=conflicting_present,
            no_usable_evidence=(not successes and not failures and not invalid_evidence),
        )

        return ValidationReport(
            candidate=candidate,
            decision=decision,
            verified_successes=verified_successes,
            counted_success_run_ids=tuple(record.run_id for record in successes),
            distinct_parameter_bindings=distinct_parameter_bindings,
            distinct_environments=distinct_environments,
            exact_repeat_only=exact_repeat_only,
            failures=tuple(
                sorted(failures, key=lambda failure: (failure.kind.value, failure.run_id.to_str()))
            ),
            invalid_evidence=tuple(
                sorted(
                    invalid_evidence,
                    key=lambda record: (record.reason.value, record.run_id.to_str()),
                )
            ),
            stale_revision_records=_sorted_ids(stale_revision_records),
            foreign_identity_records=_sorted_ids(foreign_identity_records),
            replayed_duplicates=_sorted_ids(replayed_duplicates),
            considered_record_count=len(considered),
            truncated_record_count=truncated_record_count,
            requirements=requirements,
            unmet_requirements=unmet_requirements,
            reasons=reasons,
        )

    # -- helpers ------------------------------------------------------------

    def _environment_permitted(self, environment: str | None) -> bool:
        allowed = self.allowed_environments
        if allowed is None:
            return True
        if environment is None:
            # Environment identity is optional; absence is not a mismatch.
            return True
        return environment in allowed

    def _decide(
        self,
        *,
        verified_successes: int,
        distinct_parameter_bindings: int,
        blocking_failure_count: int,
        inconsistent_present: bool,
        forged_present: bool,
        conflicting_present: bool,
        no_usable_evidence: bool,
    ) -> ValidationDecision:
        # Fail closed first: integrity problems and canonical failures are
        # never "averaged away" by the presence of successes.
        if inconsistent_present:
            return ValidationDecision.REJECTED
        if blocking_failure_count > self.max_current_revision_failures:
            return ValidationDecision.REJECTED
        if conflicting_present or forged_present:
            return ValidationDecision.DEGRADED

        met_successes = verified_successes >= self.min_verified_successes
        met_variation = distinct_parameter_bindings >= self.min_distinct_parameter_bindings
        if met_successes and met_variation and not no_usable_evidence:
            return ValidationDecision.ELIGIBLE_FOR_PROMOTION
        return ValidationDecision.INSUFFICIENT_EVIDENCE


# ---------------------------------------------------------------------------
# Deterministic classification and comparison helpers.
# ---------------------------------------------------------------------------


def _classify_outcome(outcome: ClosedLoopOutcome) -> ValidationRunKind:
    """Classify one canonical closed-loop outcome into the run-kind vocabulary.

    Structural checks first: any non-canonical inner object (a lookalike
    ``VerificationResult``, a non-``Task``, a non-``LoopOutcome`` kind, etc.)
    is forged evidence — never a success and never a canonical failure.
    """
    # Typed as ``object`` so the defensive isinstance checks below stay live
    # runtime guards: the frozen dataclass does not validate inner field types,
    # so a forged lookalike can still be smuggled in at runtime.
    kind: object = outcome.kind
    task: object = outcome.task
    verification: object = outcome.verification
    execution: object = outcome.execution
    observation: object = outcome.observation
    error: object = outcome.error

    if not isinstance(kind, LoopOutcome):
        return ValidationRunKind.FORGED
    if not isinstance(task, Task):
        return ValidationRunKind.FORGED
    if verification is not None and not isinstance(verification, VerificationResult):
        return ValidationRunKind.FORGED
    if execution is not None and not isinstance(execution, ExecutionResult):
        return ValidationRunKind.FORGED
    if observation is not None and not isinstance(observation, CapabilityObservation):
        return ValidationRunKind.FORGED
    if error is not None and not isinstance(error, AgentXError):
        return ValidationRunKind.FORGED

    if kind is LoopOutcome.VERIFIED:
        # Canonical success requires the three facts to agree exactly.
        verified_consistent = (
            isinstance(verification, VerificationResult)
            and verification.passed is True
            and task.status is TaskStatus.SUCCEEDED
        )
        if verified_consistent:
            return ValidationRunKind.VERIFIED_SUCCESS
        return ValidationRunKind.INCONSISTENT

    # A non-VERIFIED kind that nevertheless carries a passing verdict and a
    # SUCCEEDED task is contradictory: success must flow through VERIFIED.
    if (
        isinstance(verification, VerificationResult)
        and verification.passed is True
        and task.status is TaskStatus.SUCCEEDED
    ):
        return ValidationRunKind.INCONSISTENT

    if task.status is TaskStatus.CANCELLED:
        return ValidationRunKind.TIMEOUT_OR_CANCEL
    if kind is LoopOutcome.VERIFICATION_FAILED:
        return ValidationRunKind.VERIFICATION_FAILURE
    if kind is LoopOutcome.EXECUTION_FAILED:
        return ValidationRunKind.EXECUTION_FAILURE

    # The only remaining canonical member is DENIED; the cast keeps the final
    # fail-closed FORGED return reachable for any future, unknown member.
    remaining: LoopOutcome = cast("LoopOutcome", kind)
    if remaining is LoopOutcome.DENIED:
        if error is not None and error.category in {ErrorCategory.TIMEOUT, ErrorCategory.CANCELLED}:
            return ValidationRunKind.TIMEOUT_OR_CANCEL
        return ValidationRunKind.DENIED
    return ValidationRunKind.FORGED


def _same_run_content(first: ValidationRunEvidence, second: ValidationRunEvidence) -> bool:
    """True when two records with the same run id carry identical canonical content.

    ``recorded_at`` is deliberately excluded: timestamps are not identity, so a
    re-logged run with a different timestamp is still a replay, never a
    distinct run and never a conflict.
    """
    return (
        first.procedure_id == second.procedure_id
        and first.revision == second.revision
        and first.outcome == second.outcome
        and first.parameter_binding == second.parameter_binding
        and first.environment == second.environment
    )


def _count_distinct_bindings(bindings: Iterable[Mapping[str, JsonValue]]) -> int:
    distinct: list[Mapping[str, JsonValue]] = []
    for binding in bindings:
        if not any(existing == binding for existing in distinct):
            distinct.append(binding)
    return len(distinct)


def _is_exact_repeat_only(successes: list[ValidationRunEvidence]) -> bool:
    if not successes:
        return False
    first = successes[0]
    return all(
        record.parameter_binding == first.parameter_binding
        and record.environment == first.environment
        for record in successes
    )


def _sorted_ids(ids: Iterable[TaskId]) -> tuple[TaskId, ...]:
    return tuple(sorted(ids, key=lambda task_id: task_id.to_str()))


# ---------------------------------------------------------------------------
# Requirement text and reason assembly (deterministic, never echoes evidence).
# ---------------------------------------------------------------------------


def _requirement_texts(policy: ValidationPolicy) -> tuple[str, ...]:
    return (
        (
            f"at least {policy.min_verified_successes} distinct canonically verified "
            "successful validation run(s) on the candidate revision"
        ),
        (
            f"at least {policy.min_distinct_parameter_bindings} distinct observed "
            "parameter binding(s) across the counted successes"
        ),
        (
            f"no more than {policy.max_current_revision_failures} current-revision "
            "validation failure(s)"
        ),
        "no inconsistent verification truth, forged evidence, or conflicting "
        "duplicate run identity",
    )


def _unmet_requirement_texts(
    policy: ValidationPolicy,
    *,
    verified_successes: int,
    distinct_parameter_bindings: int,
    blocking_failure_count: int,
    inconsistent_present: bool,
    forged_present: bool,
    conflicting_present: bool,
) -> tuple[str, ...]:
    unmet: list[str] = []
    if verified_successes < policy.min_verified_successes:
        unmet.append(
            f"verified successes {verified_successes} < required {policy.min_verified_successes}"
        )
    if distinct_parameter_bindings < policy.min_distinct_parameter_bindings:
        unmet.append(
            f"distinct parameter bindings {distinct_parameter_bindings} < required "
            f"{policy.min_distinct_parameter_bindings}"
        )
    if blocking_failure_count > policy.max_current_revision_failures:
        unmet.append(
            f"current-revision failures {blocking_failure_count} > tolerated "
            f"{policy.max_current_revision_failures}"
        )
    if inconsistent_present:
        unmet.append("inconsistent verification truth present")
    if forged_present:
        unmet.append("forged (non-canonical) evidence present")
    if conflicting_present:
        unmet.append("conflicting duplicate run identity present")
    return tuple(unmet)


def _build_reasons(
    decision: ValidationDecision,
    policy: ValidationPolicy,
    *,
    verified_successes: int,
    distinct_parameter_bindings: int,
    exact_repeat_only: bool,
    blocking_failures: list[ValidationFailure],
    inconsistent_present: bool,
    forged_present: bool,
    conflicting_present: bool,
    no_usable_evidence: bool,
) -> tuple[ValidationReasonCode, ...]:
    reasons: list[ValidationReasonCode] = []
    if decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION:
        return ()
    if decision is ValidationDecision.REJECTED:
        if inconsistent_present:
            reasons.append(ValidationReasonCode.INCONSISTENT_EVIDENCE)
        present = {failure.kind for failure in blocking_failures}
        for kind in _FAILURE_KINDS_IN_ORDER:
            if kind in present:
                reasons.append(_FAILURE_REASON_FOR_KIND[kind])
        return tuple(dict.fromkeys(reasons))
    if decision is ValidationDecision.DEGRADED:
        if conflicting_present:
            reasons.append(ValidationReasonCode.CONFLICTING_DUPLICATE_IDENTITY)
        if forged_present:
            reasons.append(ValidationReasonCode.FORGED_EVIDENCE)
        return tuple(reasons)

    # INSUFFICIENT_EVIDENCE
    if no_usable_evidence:
        return (ValidationReasonCode.NO_EVIDENCE,)
    if verified_successes < policy.min_verified_successes:
        reasons.append(ValidationReasonCode.INSUFFICIENT_VERIFIED_SUCCESSES)
    if distinct_parameter_bindings < policy.min_distinct_parameter_bindings:
        reasons.append(
            ValidationReasonCode.EXACT_REPEAT_ONLY
            if exact_repeat_only
            else ValidationReasonCode.INSUFFICIENT_PARAMETER_VARIATION
        )
    return tuple(dict.fromkeys(reasons))
