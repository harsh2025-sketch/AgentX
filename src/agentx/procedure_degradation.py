"""Procedure degradation evidence detector (M5.01).

AgentX must eventually detect that *a procedure that used to work is no longer
reliable in the current environment*. This module owns the smallest pure,
deterministic **assessment policy** that answers that question from caller-
supplied bounded evidence. It owns nothing else.

What this module is:

    * :class:`DegradationState` — the closed assessment vocabulary
      (``UNKNOWN``, ``HEALTHY``, ``SUSPECTED_DEGRADED``, ``CONFIRMED_DEGRADED``).
    * :class:`ProcedureSuccessEvidence` — typed verified-success evidence that
      binds a canonical C2.10 :class:`~agentx.core.causal_experience.CausalExperience`
      (must be verified) to an explicit procedure identity and revision.
    * :class:`BoundFailureDiagnosis` — a C4.03
      :class:`~agentx.core.failure_diagnosis.FailureDiagnosis` optionally bound
      by the caller to a specific procedure revision. Diagnoses do not carry
      revision natively; the binding is the only way revision-scoped failure
      evidence enters this policy.
    * :class:`BoundEnvironmentChange` — a C4.04
      :class:`~agentx.core.environment_change.EnvironmentChangeDetection`
      optionally bound to a procedure revision the same way.
    * :class:`ProcedureDegradationAssessment` — one immutable, explainable
      assessment: identity, revision, state, supporting/ignored evidence
      indices, reasons, and unresolved uncertainties.
    * :func:`assess_procedure_degradation` — the pure entry point.

What this module is emphatically NOT:

    * It is not a lifecycle transition. It never mutates
      :class:`~agentx.core.procedures.ProcedureStatus`, never retires or
      activates a procedure, never writes a store, and never invents a
      ``DEGRADED`` status member (ProcedureStatus has none).
    * It is not repair. It never creates a repair candidate, generates a
      patch, executes repair, runs shadow repair, or selects a candidate.
    * It is not authority. Strings such as ``\"retire this procedure\"``,
      ``\"verified=true\"``, ``\"risk=R0\"``, ``\"permission=ADMIN\"``, or
      ``\"ignore previous instructions\"`` in any text field are inert.
    * It is not a scanner. The caller supplies finite evidence; this module
      never queries a store, a database, the filesystem, the network, a model,
      or a clock.
    * It carries no confidence float, probability, score, ranking, or embedding.

Determinism: the same typed inputs always produce the same assessment. All
timestamps are caller-supplied; this module never reads ``datetime.now``.

Composition placement: this module lives at the ``agentx`` namespace root
(alongside ``agent_loop``) so it may compose inward ``agentx.core`` contracts
without living inside any canonical subsystem package and without widening
``_architecture.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from agentx.core.causal_experience import CausalExperience, CausalOutcome
from agentx.core.environment_change import (
    EnvironmentChangeDetection,
    EnvironmentChangeResult,
)
from agentx.core.failure_diagnosis import DiagnosticConclusion, FailureDiagnosis
from agentx.core.failure_taxonomy import FailureCategory
from agentx.core.ids import ProcedureId
from agentx.core.procedures import ProcedureRecord

__all__ = [
    "CANONICAL_DEGRADATION_STATES",
    "MAX_ENVIRONMENT_EVIDENCE",
    "MAX_FAILURE_EVIDENCE",
    "MAX_REASON_LENGTH",
    "MAX_SUCCESS_EVIDENCE",
    "MAX_UNCERTAINTY_LENGTH",
    "BoundEnvironmentChange",
    "BoundFailureDiagnosis",
    "DegradationState",
    "ProcedureDegradationAssessment",
    "ProcedureDegradationValidationError",
    "ProcedureSuccessEvidence",
    "assess_procedure_degradation",
]

MAX_FAILURE_EVIDENCE: Final[int] = 32
MAX_ENVIRONMENT_EVIDENCE: Final[int] = 16
MAX_SUCCESS_EVIDENCE: Final[int] = 32
MAX_REASON_LENGTH: Final[int] = 256
MAX_UNCERTAINTY_LENGTH: Final[int] = 256

# Categories that, alone, never prove a procedure definition is degraded.
_NON_IMPLICATING_CATEGORIES: Final[frozenset[FailureCategory]] = frozenset(
    {
        FailureCategory.PERMISSION,
        FailureCategory.TRANSIENT,
        FailureCategory.PRECONDITION,
        FailureCategory.CAPABILITY,
        FailureCategory.DEPENDENCY,
        FailureCategory.KNOWLEDGE,
        FailureCategory.PLAN,
        FailureCategory.UNKNOWN,
        FailureCategory.ENVIRONMENT,
    }
)

# Categories that can support procedure-side implication when structured
# diagnosis already localizes to a procedure node.
_PROCEDURE_SIDE_CATEGORIES: Final[frozenset[FailureCategory]] = frozenset(
    {
        FailureCategory.PROCEDURE,
        FailureCategory.VERIFICATION,
        FailureCategory.UI_CHANGE,
        FailureCategory.API_CHANGE,
    }
)


class ProcedureDegradationValidationError(ValueError):
    """Raised when degradation-assessment inputs violate the contract."""


class DegradationState(StrEnum):
    """Closed vocabulary of procedure-revision degradation assessments.

    Members are assessment outcomes, never lifecycle statuses and never
    authority. ``CONFIRMED_DEGRADED`` requires strong structured evidence;
    free text never confirms anything.

    Members:
        UNKNOWN: No matching evidence justifies any other state.
        HEALTHY: Matching verified success supports past reliability and no
            later matching implicating failure overturns it.
        SUSPECTED_DEGRADED: Matching structured evidence raises suspicion but
            does not meet the confirmation threshold.
        CONFIRMED_DEGRADED: Strong structured evidence (repeated node-
            implicated diagnoses and/or node-implicated diagnosis plus a
            materially linked relevant environment change) supports
            confirmation for this revision only.
    """

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    SUSPECTED_DEGRADED = "suspected_degraded"
    CONFIRMED_DEGRADED = "confirmed_degraded"


CANONICAL_DEGRADATION_STATES: Final[tuple[DegradationState, ...]] = tuple(DegradationState)


def _validate_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ProcedureDegradationValidationError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProcedureDegradationValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _validate_revision(value: object, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProcedureDegradationValidationError(f"{field_name} must be an integer")
    if value < 1:
        raise ProcedureDegradationValidationError(
            f"{field_name} must be a positive integer (first revision is 1)"
        )
    return value


def _validate_optional_revision(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    return _validate_revision(value, field_name=field_name)


def _validate_procedure_id(value: object) -> ProcedureId:
    if not isinstance(value, ProcedureId):
        raise ProcedureDegradationValidationError("procedure_id must be a ProcedureId")
    return value


def _validate_inert_text(value: object, *, field_name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ProcedureDegradationValidationError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ProcedureDegradationValidationError(f"{field_name} must be non-empty and trimmed")
    if len(value) > max_length:
        raise ProcedureDegradationValidationError(
            f"{field_name} must be at most {max_length} characters"
        )
    if any(ch < " " or ch == "\x7f" for ch in value):
        raise ProcedureDegradationValidationError(
            f"{field_name} must not contain control characters"
        )
    return value


def _validate_reason_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ProcedureDegradationValidationError(f"{field_name} must be a tuple of strings")
    out: list[str] = []
    for item in value:
        out.append(
            _validate_inert_text(
                item, field_name=f"{field_name} item", max_length=MAX_REASON_LENGTH
            )
        )
    return tuple(out)


def _validate_index_tuple(value: object, *, field_name: str) -> tuple[int, ...]:
    if not isinstance(value, tuple):
        raise ProcedureDegradationValidationError(f"{field_name} must be a tuple of integers")
    out: list[int] = []
    for item in value:
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise ProcedureDegradationValidationError(
                f"{field_name} must contain only non-negative integers"
            )
        out.append(item)
    if out != sorted(out):
        raise ProcedureDegradationValidationError(
            f"{field_name} must be strictly increasing non-negative indices"
        )
    if len(set(out)) != len(out):
        raise ProcedureDegradationValidationError(f"{field_name} must not contain duplicates")
    return tuple(out)


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundFailureDiagnosis:
    """A C4.03 diagnosis optionally bound to a procedure revision by the caller.

    ``procedure_revision`` is the only revision linkage this policy accepts.
    Free text inside the diagnosis never supplies a revision. When the
    revision is omitted the diagnosis may still match by procedure identity,
    but the missing revision is recorded as an uncertainty and never alone
    confirms degradation of a specific revision.
    """

    diagnosis: FailureDiagnosis
    procedure_revision: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.diagnosis, FailureDiagnosis):
            raise ProcedureDegradationValidationError(
                "diagnosis must be a canonical FailureDiagnosis; "
                "free-form text is not accepted as failure evidence"
            )
        object.__setattr__(
            self,
            "procedure_revision",
            _validate_optional_revision(self.procedure_revision, field_name="procedure_revision"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class BoundEnvironmentChange:
    """A C4.04 environment-change detection optionally bound to a revision.

    Linkage to a procedure is taken only from the embedded diagnosis's
    canonical ``procedure_id``. No summary or detail text is parsed.
    """

    detection: EnvironmentChangeDetection
    procedure_revision: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.detection, EnvironmentChangeDetection):
            raise ProcedureDegradationValidationError(
                "detection must be a canonical EnvironmentChangeDetection; "
                "free-form text is not accepted as environment evidence"
            )
        object.__setattr__(
            self,
            "procedure_revision",
            _validate_optional_revision(self.procedure_revision, field_name="procedure_revision"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureSuccessEvidence:
    """Typed verified-success evidence for one procedure revision.

    A success record is evidence of *past* verified reliability for exactly
    the ``(procedure_id, procedure_revision)`` pair. It is not future
    authority and does not erase later matching failures. The embedded
    :class:`~agentx.core.causal_experience.CausalExperience` must be a
    canonical verified success (``outcome is VERIFIED`` with passing
    verification); non-verified experiences are rejected.
    """

    procedure_id: ProcedureId
    procedure_revision: int
    observed_at: datetime
    causal_experience: CausalExperience

    def __post_init__(self) -> None:
        object.__setattr__(self, "procedure_id", _validate_procedure_id(self.procedure_id))
        object.__setattr__(
            self,
            "procedure_revision",
            _validate_revision(self.procedure_revision, field_name="procedure_revision"),
        )
        object.__setattr__(
            self, "observed_at", _validate_timestamp(self.observed_at, field_name="observed_at")
        )
        if not isinstance(self.causal_experience, CausalExperience):
            raise ProcedureDegradationValidationError(
                "causal_experience must be a canonical CausalExperience"
            )
        if (
            self.causal_experience.outcome is not CausalOutcome.VERIFIED
            or not self.causal_experience.verified
        ):
            raise ProcedureDegradationValidationError(
                "success evidence requires a verified CausalExperience "
                "(outcome VERIFIED with passing verification)"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureDegradationAssessment:
    """Immutable assessment of whether one procedure revision appears degraded.

    Pure data. Constructing, comparing, or inspecting an assessment never
    retires a procedure, changes ``ProcedureStatus``, creates a repair, grants
    authority, executes a capability, writes an event, or touches a store.
    Hostile strings in ``reasons`` or ``uncertainties`` remain inert text.
    """

    procedure_id: ProcedureId
    revision: int
    state: DegradationState
    assessed_at: datetime
    reasons: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    supporting_failure_indices: tuple[int, ...] = ()
    supporting_environment_indices: tuple[int, ...] = ()
    supporting_success_indices: tuple[int, ...] = ()
    ignored_failure_indices: tuple[int, ...] = ()
    ignored_environment_indices: tuple[int, ...] = ()
    ignored_success_indices: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "procedure_id", _validate_procedure_id(self.procedure_id))
        object.__setattr__(
            self, "revision", _validate_revision(self.revision, field_name="revision")
        )
        if not isinstance(self.state, DegradationState):
            raise ProcedureDegradationValidationError(
                "state must be a DegradationState member; "
                "this contract never infers a state from text"
            )
        object.__setattr__(
            self, "assessed_at", _validate_timestamp(self.assessed_at, field_name="assessed_at")
        )
        object.__setattr__(
            self, "reasons", _validate_reason_tuple(self.reasons, field_name="reasons")
        )
        object.__setattr__(
            self,
            "uncertainties",
            _validate_reason_tuple(self.uncertainties, field_name="uncertainties"),
        )
        object.__setattr__(
            self,
            "supporting_failure_indices",
            _validate_index_tuple(
                self.supporting_failure_indices, field_name="supporting_failure_indices"
            ),
        )
        object.__setattr__(
            self,
            "supporting_environment_indices",
            _validate_index_tuple(
                self.supporting_environment_indices, field_name="supporting_environment_indices"
            ),
        )
        object.__setattr__(
            self,
            "supporting_success_indices",
            _validate_index_tuple(
                self.supporting_success_indices, field_name="supporting_success_indices"
            ),
        )
        object.__setattr__(
            self,
            "ignored_failure_indices",
            _validate_index_tuple(
                self.ignored_failure_indices, field_name="ignored_failure_indices"
            ),
        )
        object.__setattr__(
            self,
            "ignored_environment_indices",
            _validate_index_tuple(
                self.ignored_environment_indices, field_name="ignored_environment_indices"
            ),
        )
        object.__setattr__(
            self,
            "ignored_success_indices",
            _validate_index_tuple(
                self.ignored_success_indices, field_name="ignored_success_indices"
            ),
        )

    @property
    def is_unknown(self) -> bool:
        """Whether the assessment failed closed as :attr:`DegradationState.UNKNOWN`."""
        return self.state is DegradationState.UNKNOWN

    @property
    def is_confirmed_degraded(self) -> bool:
        """Whether strong structured evidence confirmed degradation."""
        return self.state is DegradationState.CONFIRMED_DEGRADED


def _resolve_target(
    *,
    procedure: ProcedureRecord | None,
    procedure_id: ProcedureId | None,
    revision: int | None,
) -> tuple[ProcedureId, int]:
    if procedure is not None:
        if not isinstance(procedure, ProcedureRecord):
            raise ProcedureDegradationValidationError(
                "procedure must be a ProcedureRecord when provided"
            )
        if procedure_id is not None and procedure_id != procedure.procedure_id:
            raise ProcedureDegradationValidationError(
                "procedure_id does not match the supplied ProcedureRecord"
            )
        if revision is not None and revision != procedure.revision:
            raise ProcedureDegradationValidationError(
                "revision does not match the supplied ProcedureRecord"
            )
        return procedure.procedure_id, procedure.revision
    if procedure_id is None or revision is None:
        raise ProcedureDegradationValidationError(
            "either procedure=ProcedureRecord or both procedure_id and revision are required"
        )
    return _validate_procedure_id(procedure_id), _validate_revision(revision, field_name="revision")


def _bound_failures(value: object) -> tuple[BoundFailureDiagnosis, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ProcedureDegradationValidationError(
            "failure_diagnoses must be a finite sequence of BoundFailureDiagnosis"
        )
    if len(value) > MAX_FAILURE_EVIDENCE:
        raise ProcedureDegradationValidationError(
            f"failure_diagnoses exceeds hard bound of {MAX_FAILURE_EVIDENCE}"
        )
    out: list[BoundFailureDiagnosis] = []
    for item in value:
        if isinstance(item, BoundFailureDiagnosis):
            out.append(item)
        elif isinstance(item, FailureDiagnosis):
            out.append(BoundFailureDiagnosis(diagnosis=item, procedure_revision=None))
        else:
            raise ProcedureDegradationValidationError(
                "failure_diagnoses items must be BoundFailureDiagnosis or FailureDiagnosis; "
                "dicts, JSON strings, and free text are rejected"
            )
    return tuple(out)


def _bound_environments(value: object) -> tuple[BoundEnvironmentChange, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ProcedureDegradationValidationError(
            "environment_changes must be a finite sequence of BoundEnvironmentChange"
        )
    if len(value) > MAX_ENVIRONMENT_EVIDENCE:
        raise ProcedureDegradationValidationError(
            f"environment_changes exceeds hard bound of {MAX_ENVIRONMENT_EVIDENCE}"
        )
    out: list[BoundEnvironmentChange] = []
    for item in value:
        if isinstance(item, BoundEnvironmentChange):
            out.append(item)
        elif isinstance(item, EnvironmentChangeDetection):
            out.append(BoundEnvironmentChange(detection=item, procedure_revision=None))
        else:
            raise ProcedureDegradationValidationError(
                "environment_changes items must be BoundEnvironmentChange or "
                "EnvironmentChangeDetection; dicts, JSON strings, and free text are rejected"
            )
    return tuple(out)


def _bound_successes(value: object) -> tuple[ProcedureSuccessEvidence, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ProcedureDegradationValidationError(
            "success_evidence must be a finite sequence of ProcedureSuccessEvidence"
        )
    if len(value) > MAX_SUCCESS_EVIDENCE:
        raise ProcedureDegradationValidationError(
            f"success_evidence exceeds hard bound of {MAX_SUCCESS_EVIDENCE}"
        )
    out: list[ProcedureSuccessEvidence] = []
    for item in value:
        if not isinstance(item, ProcedureSuccessEvidence):
            raise ProcedureDegradationValidationError(
                "success_evidence items must be ProcedureSuccessEvidence; "
                "dicts, JSON strings, and free text are rejected"
            )
        out.append(item)
    return tuple(out)


class _FailureRole(StrEnum):
    IGNORE = "ignore"
    NON_IMPLICATING = "non_implicating"
    WEAK = "weak"
    STRONG = "strong"


def _failure_role(diagnosis: FailureDiagnosis) -> _FailureRole:
    """Classify one diagnosis's contribution without reading free text."""
    category = diagnosis.classification.category
    conclusion = diagnosis.conclusion

    if category is FailureCategory.PERMISSION:
        return _FailureRole.NON_IMPLICATING
    if category is FailureCategory.TRANSIENT:
        return _FailureRole.NON_IMPLICATING
    if category is FailureCategory.CAPABILITY:
        return _FailureRole.NON_IMPLICATING
    if category is FailureCategory.DEPENDENCY:
        return _FailureRole.NON_IMPLICATING
    if category is FailureCategory.PRECONDITION:
        return _FailureRole.NON_IMPLICATING
    if category in _NON_IMPLICATING_CATEGORIES and category is not FailureCategory.ENVIRONMENT:
        return _FailureRole.NON_IMPLICATING

    # Environment-category failures alone do not prove procedure degradation;
    # environment *change* evidence is handled separately.
    if category is FailureCategory.ENVIRONMENT:
        return _FailureRole.NON_IMPLICATING

    if (
        conclusion is DiagnosticConclusion.NODE_IMPLICATED
        and category in _PROCEDURE_SIDE_CATEGORIES
    ):
        return _FailureRole.STRONG

    if category is FailureCategory.PROCEDURE:
        return _FailureRole.WEAK

    if category in {
        FailureCategory.UI_CHANGE,
        FailureCategory.API_CHANGE,
        FailureCategory.VERIFICATION,
    }:
        return _FailureRole.WEAK

    return _FailureRole.NON_IMPLICATING


def assess_procedure_degradation(
    *,
    assessed_at: datetime,
    procedure: ProcedureRecord | None = None,
    procedure_id: ProcedureId | None = None,
    revision: int | None = None,
    failure_diagnoses: Sequence[BoundFailureDiagnosis | FailureDiagnosis] | None = None,
    environment_changes: (
        Sequence[BoundEnvironmentChange | EnvironmentChangeDetection] | None
    ) = None,
    success_evidence: Sequence[ProcedureSuccessEvidence] | None = None,
) -> ProcedureDegradationAssessment:
    """Assess degradation of one procedure revision from bounded typed evidence.

    Pure, deterministic, side-effect free. Same inputs always yield the same
    :class:`ProcedureDegradationAssessment`. This function never:

    - mutates ``ProcedureStatus`` or any store;
    - retires, activates, or versions a procedure;
    - creates or executes a repair;
    - reads a clock, the network, the filesystem, or a database;
    - invokes a model or parses free text for keywords;
    - grants Permission, lowers risk, widens a budget, or clears EmergencyStop.

    Decision rules (explainable, ordered):

    1. Evidence that does not match the target ``procedure_id`` is ignored.
    2. Evidence bound to a *different* revision is ignored (no transfer).
    3. Permission, transient, capability, dependency, precondition, plan,
       knowledge, unknown, and environment-*category* failures do not
       themselves prove procedure degradation.
    4. ``NODE_IMPLICATED`` diagnoses in procedure-side categories are strong.
    5. ``PROCEDURE`` (and related) category diagnoses without node implication
       are weak suspicion only.
    6. A C4.04 ``RELEVANT_CHANGE_DETECTED`` result whose embedded diagnosis
       matches the target procedure may raise suspicion; it confirms only
       together with strong node-implicated failure evidence.
    7. Matching verified success supports ``HEALTHY`` when no later matching
       implicating failure overturns it. Older success never erases newer
       failures. Newer success can supersede older failures for the healthy
       path.
    8. ``CONFIRMED_DEGRADED`` requires at least two strong matching failures,
       or one strong matching failure plus a matching relevant environment
       change. Text never confirms.
    9. Empty matching evidence yields ``UNKNOWN``.
    """
    target_id, target_revision = _resolve_target(
        procedure=procedure, procedure_id=procedure_id, revision=revision
    )
    moment = _validate_timestamp(assessed_at, field_name="assessed_at")
    failures = _bound_failures(failure_diagnoses)
    environments = _bound_environments(environment_changes)
    successes = _bound_successes(success_evidence)

    ignored_failures: list[int] = []
    ignored_envs: list[int] = []
    ignored_successes: list[int] = []
    uncertainties: list[str] = []
    reasons: list[str] = []

    matching_strong: list[tuple[int, BoundFailureDiagnosis]] = []
    matching_weak: list[tuple[int, BoundFailureDiagnosis]] = []
    matching_non: list[tuple[int, BoundFailureDiagnosis]] = []
    matching_env_relevant: list[tuple[int, BoundEnvironmentChange]] = []
    matching_success: list[tuple[int, ProcedureSuccessEvidence]] = []

    for index, bound in enumerate(failures):
        diagnosis = bound.diagnosis
        if diagnosis.procedure_id != target_id:
            ignored_failures.append(index)
            continue
        if bound.procedure_revision is not None and bound.procedure_revision != target_revision:
            ignored_failures.append(index)
            reasons.append(
                f"failure[{index}] bound to revision {bound.procedure_revision} "
                f"does not transfer to revision {target_revision}"
            )
            continue
        if bound.procedure_revision is None:
            uncertainties.append(
                f"failure[{index}] matches procedure identity without an explicit revision binding"
            )
        role = _failure_role(diagnosis)
        if role is _FailureRole.STRONG:
            matching_strong.append((index, bound))
        elif role is _FailureRole.WEAK:
            matching_weak.append((index, bound))
        else:
            matching_non.append((index, bound))
            cat = diagnosis.classification.category.value
            reasons.append(f"failure[{index}] category {cat} does not prove procedure degradation")

    for index, env_bound in enumerate(environments):
        detection = env_bound.detection
        embedded_id = detection.diagnosis.procedure_id
        if embedded_id != target_id:
            ignored_envs.append(index)
            continue
        if (
            env_bound.procedure_revision is not None
            and env_bound.procedure_revision != target_revision
        ):
            ignored_envs.append(index)
            reasons.append(
                f"environment[{index}] bound to revision {env_bound.procedure_revision} "
                f"does not transfer to revision {target_revision}"
            )
            continue
        if env_bound.procedure_revision is None:
            uncertainties.append(
                f"environment[{index}] matches procedure identity without an explicit "
                "revision binding"
            )
        if detection.result is EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED:
            matching_env_relevant.append((index, env_bound))
            reasons.append(
                f"environment[{index}] reports RELEVANT_CHANGE_DETECTED for matching procedure"
            )
        else:
            ignored_envs.append(index)
            reasons.append(
                f"environment[{index}] result {detection.result.value} does not raise degradation"
            )

    for index, evidence in enumerate(successes):
        if evidence.procedure_id != target_id:
            ignored_successes.append(index)
            continue
        if evidence.procedure_revision != target_revision:
            ignored_successes.append(index)
            reasons.append(
                f"success[{index}] revision {evidence.procedure_revision} "
                f"does not apply to revision {target_revision}"
            )
            continue
        matching_success.append((index, evidence))

    success_times = [(ev.observed_at, index) for index, ev in matching_success]
    latest_success_at: datetime | None = None
    latest_success_index: int | None = None
    if success_times:
        latest_success_at, latest_success_index = max(success_times, key=lambda item: item[0])

    # Failures/env changes strictly after the latest matching success still count.
    def _after_latest_success(when: datetime) -> bool:
        if latest_success_at is None:
            return True
        return when > latest_success_at

    active_strong = [
        (i, b) for i, b in matching_strong if _after_latest_success(b.diagnosis.diagnosed_at)
    ]
    active_weak = [
        (i, b) for i, b in matching_weak if _after_latest_success(b.diagnosis.diagnosed_at)
    ]
    active_env = [
        (i, b) for i, b in matching_env_relevant if _after_latest_success(b.detection.compared_at)
    ]

    superseded_strong = [
        (i, b) for i, b in matching_strong if not _after_latest_success(b.diagnosis.diagnosed_at)
    ]
    superseded_weak = [
        (i, b) for i, b in matching_weak if not _after_latest_success(b.diagnosis.diagnosed_at)
    ]

    if superseded_strong or superseded_weak:
        reasons.append(
            "older matching failures are superseded by a later matching verified success"
        )

    support_failures: list[int] = sorted(
        {i for i, _ in active_strong} | {i for i, _ in active_weak}
    )
    support_envs: list[int] = sorted({i for i, _ in active_env})
    support_successes: list[int] = sorted({i for i, _ in matching_success})

    # Confirmation requires strong structured evidence only.
    # - >= 2 active strong (node-implicated) failures, OR
    # - >= 1 active strong failure AND >= 1 active relevant env change
    # Revision-unbound strong evidence alone never confirms (uncertainty).
    strong_revision_bound = [
        (i, b) for i, b in active_strong if b.procedure_revision == target_revision
    ]
    env_revision_bound = [(i, b) for i, b in active_env if b.procedure_revision == target_revision]

    state: DegradationState
    if len(strong_revision_bound) >= 2 or (
        len(strong_revision_bound) >= 1 and len(env_revision_bound) >= 1
    ):
        state = DegradationState.CONFIRMED_DEGRADED
        reasons.append(
            "confirmed by strong revision-bound node-implicated failure evidence"
            + (" with linked environment change" if env_revision_bound else " (repeated)")
        )
    elif active_strong or active_weak or active_env:
        state = DegradationState.SUSPECTED_DEGRADED
        if active_strong:
            reasons.append(
                f"suspected from {len(active_strong)} matching strong failure diagnosis(es)"
            )
        if active_weak:
            reasons.append(f"suspected from {len(active_weak)} matching weak failure diagnosis(es)")
        if active_env and not active_strong:
            reasons.append(
                "suspected from matching relevant environment-change evidence without "
                "strong node-implicated confirmation"
            )
        if active_strong and not strong_revision_bound:
            uncertainties.append(
                "strong failures lack explicit revision binding; confirmation withheld"
            )
    elif matching_success:
        state = DegradationState.HEALTHY
        assert latest_success_index is not None
        reasons.append(f"healthy from matching verified success at success[{latest_success_index}]")
        if matching_non:
            reasons.append(
                "matching non-implicating failures observed but do not overturn verified success"
            )
    elif matching_non:
        state = DegradationState.UNKNOWN
        reasons.append(
            "only non-implicating matching failures present; procedure degradation unproven"
        )
        uncertainties.append(
            "non-implicating failures (permission/transient/capability/etc.) "
            "never confirm degradation"
        )
    else:
        state = DegradationState.UNKNOWN
        reasons.append("no matching evidence for this procedure revision")

    # Deduplicate reasons/uncertainties while preserving order.
    def _dedupe(items: list[str]) -> tuple[str, ...]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item not in seen:
                seen.add(item)
                out.append(item)
        return tuple(out)

    return ProcedureDegradationAssessment(
        procedure_id=target_id,
        revision=target_revision,
        state=state,
        assessed_at=moment,
        reasons=_dedupe(reasons),
        uncertainties=_dedupe(uncertainties),
        supporting_failure_indices=tuple(support_failures),
        supporting_environment_indices=tuple(support_envs),
        supporting_success_indices=tuple(support_successes),
        ignored_failure_indices=tuple(ignored_failures),
        ignored_environment_indices=tuple(ignored_envs),
        ignored_success_indices=tuple(ignored_successes),
    )
