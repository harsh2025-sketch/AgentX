"""Varied-parameter procedure validation RUNNER (N2.09).

This module is the canonical execution-side runner that evaluates ONE explicit
procedure candidate revision against an explicit, bounded, caller-supplied
collection of validation cases and produces canonical validation evidence.

It is deliberately NOT a second validation policy. The canonical
promotion-eligibility policy already landed as M4.02 in
:mod:`agentx.procedure_validation`; this module is the controlled RUNNER
around that contract::

    ProcedureCandidateIdentity (canonical ``(procedure_id, revision)``)
    + a bounded, immutable, explicitly varied validation-case collection
    + an injected canonical execution port (``ProcedureValidationHarness``)
    + optional canonical verification requirements (A2.05)
        -> per-case canonical results
        -> a canonical M4.02 ``ValidationReport``
        -> one immutable, deterministic ``ProcedureValidationRunResult``

The runner decides NOTHING about promotion eligibility that M4.02 does not
own: it builds the canonical
:class:`~agentx.procedure_validation.ValidationRunEvidence` form, feeds it to
the canonical M4.02 :class:`~agentx.procedure_validation.ValidationPolicy`,
and composes around the report it gets back. It never re-implements the
verification-truth classifier, never creates a second decision vocabulary (its
per-case results reuse :class:`~agentx.procedure_validation.ValidationRunKind`
and :class:`~agentx.procedure_validation.ValidationReasonCode`), and never
creates a second procedure graph schema.

WHY THIS TASK EXISTS
====================

The forbidden failure mode is::

    "the procedure worked once"  =>  "the procedure is a validated reusable skill"

A runner over ONE case cannot distinguish a genuinely reusable procedure from
a procedure that happens to match one trajectory. This module therefore makes
the **case set** the unit of validation:

    - every case is explicit, bounded, and caller-supplied (no fuzzing, no
      model-generated cases, no random parameter mutation);
    - every case carries its own parameter binding, so cases are expected to
      differ in their parameters, and that difference is preserved;
    - every case is executed separately and reported separately, in the exact
      order the caller supplied them;
    - a procedure that passes case A and fails case B is reported as a
      **partial** result, never as universal validation.

Failures are never averaged into success, never dropped, and never reordered
into invisibility.

VERIFICATION TRUTH BOUNDARY
===========================

Execution return is not validation success. A procedure reaching END is not
validation success. A capability execution succeeding is not validation
success. Every case must carry explicit canonical verification evidence:

    - the injected harness must return a canonical
      :class:`~agentx.capabilities.runtime.ClosedLoopOutcome`;
    - a case may additionally declare an explicit canonical A2.05
      :class:`~agentx.capabilities.verifier.VerificationRequirement`, which the
      runner evaluates with the canonical A2.05
      :class:`~agentx.capabilities.verifier.Verifier` over the produced
      outcome. The Verifier reads evidence; it manufactures nothing. If that
      evaluation is not satisfied, the case is an explicit verification
      failure, even when the outcome's own text claims success;
    - the final per-case success classification is M4.02's, not this module's.

Data inside a case is inert. A case whose parameter binding contains
``verified=true``, ``task_success=true``, ``validation_passed=true``,
``permission=ADMIN``, or ``risk=R0`` changes nothing: those are strings, and
no field of any runner value carries permission, risk, budget, approval, or
verification meaning.

NO PROMOTION AUTHORITY (ABSOLUTE)
=================================

This runner produces EVIDENCE ONLY. It does not and cannot:

    - activate a Procedure or change a ``ProcedureStatus``;
    - mutate a ``ProcedureStore`` lifecycle state;
    - grant a ``Permission``, widen authority, or approve an ``ActionGate``
      decision;
    - downgrade a ``RiskLevel``, enlarge a ``ResourceBudget``, or clear an
      ``EmergencyStop``;
    - transition a ``Task``, execute a capability itself, call a model,
      perform research, or touch persistence.

Even a perfect result — every case a canonically verified success and the
M4.02 policy returning ``ELIGIBLE_FOR_PROMOTION`` — remains evidence.
Activation/promotion belongs to the canonical lifecycle owner, not here. This
module imports no kernel, infrastructure, hive, cognition, learning, or
procedures module and holds no store reference, so it is structurally
incapable of persisting, mutating, promoting, or executing anything.

The only external effect of a run is whatever the caller's **injected**
harness does; the runner hands it a frozen request and reads the canonical
result. When that harness is the canonical A1.10
``CapabilityExecutionLoop``, execution evidence is recorded by that canonical
machinery, through the canonical kernel, exactly as it would be without this
runner.

DETERMINISM AND BOUNDEDNESS
===========================

    - the case count is bounded (``max_cases``, hard-capped at
      :data:`MAX_VALIDATION_CASES_LIMIT`);
    - duplicate case identities and duplicate canonical run identities are
      rejected, so replay cannot inflate evidence;
    - each case is executed exactly once, in caller order, and per-case
      results are returned in that same order;
    - the runner reads no clock and generates no identifiers: canonical run
      identities are supplied by the caller, and evidence records carry no
      timestamp;
    - no randomness, no environment inspection, no dynamic loading, no
      threading, and no module-level side effects.

This is a top-level composition module (alongside ``agent_loop`` and
``procedure_validation``) because it must read canonical procedure identity
from ``agentx.core`` AND canonical execution/verification evidence from
``agentx.capabilities`` — a cross-subsystem read no single canonical subsystem
may own. The boundary manifest is not widened.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Protocol, cast, runtime_checkable

from agentx.capabilities.runtime import ClosedLoopOutcome
from agentx.capabilities.verifier import (
    VerificationRequirement,
    Verifier,
    VerifierRequest,
)
from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.ids import TaskId
from agentx.core.procedures import ProcedureRecord
from agentx.core.result import Result
from agentx.core.tasks import JsonValue
from agentx.procedure_validation import (
    ProcedureCandidateIdentity,
    ValidationDecision,
    ValidationPolicy,
    ValidationReasonCode,
    ValidationReport,
    ValidationRunEvidence,
    ValidationRunKind,
)

__all__ = [
    "DEFAULT_MAX_VALIDATION_CASES",
    "DEFAULT_MIN_DISTINCT_PARAMETER_BINDINGS",
    "MAX_BINDING_ENTRIES",
    "MAX_BINDING_KEY_LENGTH",
    "MAX_CASE_ID_LENGTH",
    "MAX_ENVIRONMENT_LABEL_LENGTH",
    "MAX_VALIDATION_CASES_LIMIT",
    "MIN_VALIDATION_CASES",
    "ProcedureValidationCase",
    "ProcedureValidationCaseResult",
    "ProcedureValidationHarness",
    "ProcedureValidationRunRequest",
    "ProcedureValidationRunResult",
    "ProcedureValidationRunner",
    "ProcedureValidationRunnerError",
    "ProcedureValidationRunnerInputError",
]

# ---------------------------------------------------------------------------
# Hard bounds. Every knob is bounded at construction; no caller and no text
# can turn a bounded validation run into an unbounded one.
# ---------------------------------------------------------------------------

#: A validation run needs at least one explicit case.
MIN_VALIDATION_CASES: Final[int] = 1

#: Default upper bound on how many cases one run may execute.
DEFAULT_MAX_VALIDATION_CASES: Final[int] = 32

#: Hard ceiling for ``max_cases``: no configuration can exceed it.
MAX_VALIDATION_CASES_LIMIT: Final[int] = 1_000

#: The runner's default variation requirement, expressed with M4.02's own
#: knob. M4.02 floors ``min_distinct_parameter_bindings`` at 1; this runner
#: asks for 2 by default because a validation set whose successes all share one
#: parameter binding is an exact repeat, and an exact repeat cannot tell
#: "worked once" from "validated under variation". Callers may pass any
#: :class:`~agentx.procedure_validation.ValidationPolicy` they own; M4.02's
#: floor cannot be lowered by anyone.
DEFAULT_MIN_DISTINCT_PARAMETER_BINDINGS: Final[int] = 2

#: Bounds on case identity, environment labels, bindings, and error codes.
MAX_CASE_ID_LENGTH: Final[int] = 128
MAX_ENVIRONMENT_LABEL_LENGTH: Final[int] = 128
MAX_BINDING_ENTRIES: Final[int] = 256
MAX_BINDING_KEY_LENGTH: Final[int] = 128
MAX_ERROR_CODE_LENGTH: Final[int] = 128

_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")

#: Error categories the canonical taxonomy reserves for timeouts and
#: cancellations. M4.02 reads the same categories when it classifies a
#: canonical outcome; the runner reads them from a ``Result.failure`` that
#: carries no outcome at all.
_TIMEOUT_CATEGORIES: Final[frozenset[ErrorCategory]] = frozenset(
    {ErrorCategory.TIMEOUT, ErrorCategory.CANCELLED}
)

#: Canonical reason code for each failure kind THIS runner can establish on its
#: own (before evidence ever reaches M4.02), plus the canonical reasons for
#: blocking kinds M4.02 may report back. The M4.02 reason vocabulary is reused
#: verbatim; no second vocabulary is created.
_REASON_FOR_KIND: Final[Mapping[ValidationRunKind, ValidationReasonCode]] = MappingProxyType(
    {
        ValidationRunKind.VERIFICATION_FAILURE: ValidationReasonCode.VERIFICATION_FAILURE,
        ValidationRunKind.EXECUTION_FAILURE: ValidationReasonCode.EXECUTION_FAILURE,
        ValidationRunKind.TIMEOUT_OR_CANCEL: ValidationReasonCode.TIMEOUT_OR_CANCEL,
        ValidationRunKind.DENIED: ValidationReasonCode.DENIED_RUN,
        ValidationRunKind.ENVIRONMENT_MISMATCH: ValidationReasonCode.ENVIRONMENT_MISMATCH,
        ValidationRunKind.INCONSISTENT: ValidationReasonCode.INCONSISTENT_EVIDENCE,
        ValidationRunKind.FORGED: ValidationReasonCode.FORGED_EVIDENCE,
    }
)

#: Per-case kinds that block a clean aggregate result.
_BLOCKING_CASE_KINDS: Final[frozenset[ValidationRunKind]] = frozenset(
    {
        ValidationRunKind.VERIFICATION_FAILURE,
        ValidationRunKind.EXECUTION_FAILURE,
        ValidationRunKind.DENIED,
        ValidationRunKind.TIMEOUT_OR_CANCEL,
        ValidationRunKind.ENVIRONMENT_MISMATCH,
        ValidationRunKind.INCONSISTENT,
    }
)

#: Deterministic order used when several blocking kinds are present at once.
_BLOCKING_KINDS_IN_ORDER: Final[tuple[ValidationRunKind, ...]] = (
    ValidationRunKind.INCONSISTENT,
    ValidationRunKind.ENVIRONMENT_MISMATCH,
    ValidationRunKind.TIMEOUT_OR_CANCEL,
    ValidationRunKind.DENIED,
    ValidationRunKind.EXECUTION_FAILURE,
    ValidationRunKind.VERIFICATION_FAILURE,
)

#: Requirements this runner adds on top of the M4.02 policy requirements.
_RUNNER_REQUIREMENTS: Final[tuple[str, ...]] = (
    "every supplied validation case must produce canonical evidence the M4.02 "
    "validation policy can consider",
    "no validation case may fail, be denied, time out, or carry non-canonical evidence",
)


class ProcedureValidationRunnerError(ValueError):
    """Base error for the varied-parameter procedure validation runner."""


class ProcedureValidationRunnerInputError(ProcedureValidationRunnerError):
    """Raised when a validation run request violates the runner's bounds.

    Bounded-input failures are rejected at the boundary, before any case is
    executed: an unbounded, empty, or ambiguous case collection never becomes
    a partially trusted run. Evidence problems discovered *during* execution
    are recorded in the run result instead of being raised.
    """


# ---------------------------------------------------------------------------
# Bounded input validation helpers.
# ---------------------------------------------------------------------------


def _validate_bounded_text(value: object, *, field_name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise ProcedureValidationRunnerInputError(f"{field_name} must be non-empty and trimmed")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise ProcedureValidationRunnerInputError(
            f"{field_name} must not contain control characters"
        )
    if len(value) > limit:
        raise ProcedureValidationRunnerInputError(
            f"{field_name} must not exceed {limit} characters"
        )
    return value


def _freeze_json_value(value: object, *, path: str) -> object:
    """Validate JSON compatibility and return an immutable defensive copy."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProcedureValidationRunnerInputError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProcedureValidationRunnerInputError(
                    f"{path} contains a non-string object key"
                )
            frozen[key] = _freeze_json_value(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(
            _freeze_json_value(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        )
    raise ProcedureValidationRunnerInputError(
        f"{path} contains a non-JSON-compatible value of type {type(value).__name__}"
    )


def _freeze_parameter_binding(value: object) -> Mapping[str, JsonValue]:
    """Freeze one case's parameter binding (the source of controlled variation).

    Bindings are inert data: hostile text such as ``permission=ADMIN`` or
    ``verified=true`` is carried verbatim and interpreted by nothing here.
    """
    if not isinstance(value, Mapping):
        raise TypeError(
            "parameter_binding must be a mapping of parameter name to JSON-compatible "
            f"value, got {type(value).__name__}"
        )
    if len(value) > MAX_BINDING_ENTRIES:
        raise ProcedureValidationRunnerInputError(
            f"parameter_binding must not exceed {MAX_BINDING_ENTRIES} entries"
        )
    frozen: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ProcedureValidationRunnerInputError("parameter binding keys must be strings")
        _validate_bounded_text(
            key, field_name="parameter binding key", limit=MAX_BINDING_KEY_LENGTH
        )
        frozen[key] = _freeze_json_value(item, path=f"parameter_binding.{key}")
    return MappingProxyType(cast("dict[str, JsonValue]", frozen))


def _validate_environment(value: object) -> str | None:
    if value is None:
        return None
    return _validate_bounded_text(
        value, field_name="environment", limit=MAX_ENVIRONMENT_LABEL_LENGTH
    )


def _validate_optional_requirement(value: object) -> VerificationRequirement | None:
    if value is None:
        return None
    if not isinstance(value, VerificationRequirement):
        raise TypeError(
            f"verification must be a VerificationRequirement or None, got {type(value).__name__}"
        )
    return value


def _error_code(error: AgentXError) -> str | None:
    """Return a bounded, inert error code for evidence (never free text)."""
    code = error.code
    if not isinstance(code, str) or not code:
        return None
    return code[:MAX_ERROR_CODE_LENGTH]


# ---------------------------------------------------------------------------
# Validation case and execution request.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProcedureValidationCase:
    """One explicit, bounded, immutable validation case.

    A case is the minimum data needed to run and verify the candidate under ONE
    controlled variation, and nothing more:

    - ``case_id`` — explicit case identity. It is opaque, bounded, and inert:
      the runner orders and reports by it and rejects duplicates, but it
      carries no authority and no verification meaning.
    - ``run_id`` — the canonical ``TaskId`` identifying the governed unit of
      work this case produces. M4.02 keys validation-run identity on it, so it
      is supplied explicitly (never generated) and must be unique within a
      run: replaying one run id cannot inflate evidence.
    - ``parameter_binding`` — the varied parameters for this case, in the
      canonical JSON-compatible value space. This is the controlled variation:
      cases are expected to differ here, and those differences are what make
      "passed once" distinguishable from "validated under variation".
    - ``environment`` — optional opaque environment label, never interpreted.
    - ``verification`` — optional explicit canonical A2.05 verification
      requirement for this case. ``None`` means the runner relies solely on the
      canonical closed-loop verification evidence in the outcome.

    A case carries no procedure graph, no status, no permission, no risk, and
    no expected-outcome flag: a case cannot declare itself passed.
    """

    case_id: str
    run_id: TaskId
    parameter_binding: Mapping[str, JsonValue]
    environment: str | None = None
    verification: VerificationRequirement | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "case_id",
            _validate_bounded_text(self.case_id, field_name="case_id", limit=MAX_CASE_ID_LENGTH),
        )
        if not isinstance(self.run_id, TaskId):
            raise TypeError(f"run_id must be a TaskId, got {type(self.run_id).__name__}")
        object.__setattr__(
            self, "parameter_binding", _freeze_parameter_binding(self.parameter_binding)
        )
        object.__setattr__(self, "environment", _validate_environment(self.environment))
        object.__setattr__(self, "verification", _validate_optional_requirement(self.verification))


@dataclass(frozen=True, slots=True)
class ProcedureValidationRunRequest:
    """The exact frozen value handed to the injected execution harness.

    It binds the candidate revision to the case so a harness cannot validate a
    different procedure revision than the one the runner reports on, and it
    carries nothing a harness could use to widen authority: no permission, no
    risk override, no budget, no gate, no store, and no expected verdict.
    """

    candidate: ProcedureCandidateIdentity
    case: ProcedureValidationCase

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, ProcedureCandidateIdentity):
            raise TypeError(
                "candidate must be a ProcedureCandidateIdentity, "
                f"got {type(self.candidate).__name__}"
            )
        if not isinstance(self.case, ProcedureValidationCase):
            raise TypeError(
                f"case must be a ProcedureValidationCase, got {type(self.case).__name__}"
            )


@runtime_checkable
class ProcedureValidationHarness(Protocol):
    """Explicitly injected canonical execution port (a port, not an authority).

    One ``run`` call executes exactly ONE validation case once and returns the
    canonical evidence the governed path produced. The expected implementation
    delegates to the canonical A1.10
    :class:`~agentx.capabilities.runtime.CapabilityExecutionLoop` (through the
    governed composition root) and returns exactly what that path produced:
    ``Result.success(ClosedLoopOutcome)`` for every deterministic terminal
    state (verified, verification failed, execution failed, denied) and
    ``Result.failure(AgentXError)`` when no canonical outcome exists at all.

    The harness must not verify, retry, escalate, promote, or widen anything.
    The runner re-checks canonical evidence for every case regardless of what
    a harness reports, and a harness that raises, returns a non-canonical
    value, or returns a forged outcome fails its case: it can never make a case
    pass by claiming success.
    """

    def run(self, request: ProcedureValidationRunRequest) -> Result[ClosedLoopOutcome, AgentXError]:
        """Execute one validation case once and return canonical evidence."""
        ...


# ---------------------------------------------------------------------------
# Per-case and aggregate results.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProcedureValidationCaseResult:
    """The preserved per-case result of one executed validation case.

    Success is the single canonical classification
    :attr:`~agentx.procedure_validation.ValidationRunKind.VERIFIED_SUCCESS`
    produced by M4.02 over canonical evidence — never a bool manufactured
    here, and never derived from execution returning normally.

    ``kind``/``reason`` reuse the M4.02 vocabularies, so this module adds no
    second status language. ``evidence_considered`` records whether the
    canonical policy actually saw evidence for this case: a case that never
    produced canonical evidence (harness error, forged outcome, or an unmet
    explicit verification requirement) still appears here, explicitly, as a
    failure. Failing cases are never erased and never reordered away.
    """

    case_id: str
    run_id: TaskId
    kind: ValidationRunKind
    reason: ValidationReasonCode | None
    evidence_considered: bool
    error_code: str | None
    unmet_conditions: tuple[str, ...]
    parameter_binding: Mapping[str, JsonValue]
    environment: str | None

    @property
    def passed(self) -> bool:
        """True only for a canonically verified success."""
        return self.kind is ValidationRunKind.VERIFIED_SUCCESS


@dataclass(frozen=True, slots=True)
class ProcedureValidationRunResult:
    """Immutable, deterministic aggregate of one varied-parameter run.

    ``cases`` preserves every case result in caller-supplied order, so a mixed
    outcome stays visible: "case A passed, case B failed" is never collapsed
    into "validated".

    ``report`` is the canonical M4.02 report over exactly the evidence this
    run produced. It describes the evidence the policy could consider; it does
    not by itself describe cases that never produced canonical evidence, which
    is why ``decision`` exists: the runner's aggregate may only ever DOWNGRADE
    the policy's decision, never upgrade it.

    A result is learning evidence only. It grants no Permission, changes no
    ``ProcedureStatus``, widens no budget, lowers no risk, bypasses no
    ``ActionGate``, clears no ``EmergencyStop``, and mutates no store. Even
    ``decision is ELIGIBLE_FOR_PROMOTION`` is evidence about evidence, not
    activation.
    """

    candidate: ProcedureCandidateIdentity
    cases: tuple[ProcedureValidationCaseResult, ...]
    report: ValidationReport
    decision: ValidationDecision
    reasons: tuple[ValidationReasonCode, ...]
    requirements: tuple[str, ...]
    unmet_requirements: tuple[str, ...]
    complete: bool

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, ProcedureCandidateIdentity):
            raise TypeError("candidate must be a ProcedureCandidateIdentity")
        if not isinstance(self.decision, ValidationDecision):
            raise TypeError("decision must be a ValidationDecision")

    @property
    def case_count(self) -> int:
        """Number of supplied validation cases."""
        return len(self.cases)

    @property
    def passed_case_count(self) -> int:
        """Number of cases that are canonically verified successes."""
        return sum(1 for case in self.cases if case.passed)

    @property
    def failed_case_count(self) -> int:
        """Number of cases that are not canonically verified successes."""
        return self.case_count - self.passed_case_count

    @property
    def all_cases_passed(self) -> bool:
        """True only when every supplied case is a canonically verified success."""
        return self.case_count > 0 and self.passed_case_count == self.case_count

    @property
    def eligible(self) -> bool:
        """True only for the single positive outcome.

        A convenience projection of ``decision``; the authoritative value is
        always the ``decision`` enum, never this bool.
        """
        return self.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION


# ---------------------------------------------------------------------------
# Internal per-case execution record.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _CaseExecution:
    """Internal record of one case after the harness returned.

    Exactly one of ``outcome`` / ``kind`` is set: ``outcome`` means canonical
    evidence exists and may be handed to M4.02; ``kind`` means the case failed
    before any canonical evidence existed and is recorded explicitly.
    """

    case: ProcedureValidationCase
    outcome: ClosedLoopOutcome | None = None
    kind: ValidationRunKind | None = None
    reason: ValidationReasonCode | None = None
    error_code: str | None = None
    unmet_conditions: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# The runner.
# ---------------------------------------------------------------------------


class ProcedureValidationRunner:
    """Runs one procedure candidate revision across bounded validation cases.

    Collaborators are bound once at construction and never re-derived during a
    run, so the execution port and the policy cannot drift between cases of the
    same run:

    - ``harness`` — the injected canonical execution port;
    - ``policy`` — the canonical M4.02 promotion-eligibility policy. It
      defaults to :data:`DEFAULT_MIN_DISTINCT_PARAMETER_BINDINGS` (2) distinct
      parameter bindings, because this runner exists to prevent an exact repeat
      from passing as validation; M4.02's own "at least two verified
      successes" floor is enforced there, never relaxed here;
    - ``max_cases`` — the bounded case ceiling for one run.

    The runner keeps no state between runs, performs no I/O, reads no clock,
    and holds no reference to any store, kernel, or provider.
    """

    __slots__ = ("_harness", "_max_cases", "_policy", "_verifier")

    def __init__(
        self,
        *,
        harness: ProcedureValidationHarness,
        policy: ValidationPolicy | None = None,
        max_cases: int = DEFAULT_MAX_VALIDATION_CASES,
    ) -> None:
        """Bind the execution port, the canonical policy, and the case bound."""
        if not isinstance(harness, ProcedureValidationHarness):
            raise TypeError(
                "harness must expose the ProcedureValidationHarness protocol, "
                f"got {type(harness).__name__}"
            )
        resolved_policy = (
            ValidationPolicy(
                min_distinct_parameter_bindings=DEFAULT_MIN_DISTINCT_PARAMETER_BINDINGS
            )
            if policy is None
            else policy
        )
        if not isinstance(resolved_policy, ValidationPolicy):
            raise TypeError(
                f"policy must be a ValidationPolicy or None, got {type(policy).__name__}"
            )
        self._harness = harness
        self._policy = resolved_policy
        self._max_cases = _validate_max_cases(max_cases)
        self._verifier = Verifier()

    @property
    def harness(self) -> ProcedureValidationHarness:
        """The injected execution port bound to this runner."""
        return self._harness

    @property
    def policy(self) -> ValidationPolicy:
        """The canonical M4.02 policy this runner composes around."""
        return self._policy

    @property
    def max_cases(self) -> int:
        """The bounded maximum number of cases one run may execute."""
        return self._max_cases

    # -- public entry point ------------------------------------------------

    def run(
        self,
        candidate: ProcedureCandidateIdentity | ProcedureRecord,
        cases: Iterable[ProcedureValidationCase],
    ) -> ProcedureValidationRunResult:
        """Execute every supplied case and return canonical validation evidence.

        Deterministic and evidence-only. The runner:

        1. validates the bounded input (candidate identity, case count, case
           and run identity uniqueness, policy capacity) — malformed input is
           rejected here, before anything executes;
        2. executes each case exactly once, in caller order, through the
           injected harness;
        3. collects canonical execution/verification evidence, applying each
           case's optional canonical A2.05 verification requirement through the
           canonical Verifier;
        4. preserves case identity and binds every evidence record to the exact
           candidate revision;
        5. feeds the canonical M4.02 evidence form to the canonical M4.02
           policy; and
        6. returns per-case results in caller order plus an aggregate decision
           that may only downgrade the policy's decision.

        Raises :class:`TypeError` for arguments of the wrong Python type and
        :class:`ProcedureValidationRunnerInputError` for input that violates
        the runner's bounds. Evidence problems discovered during execution
        (harness error, denial, timeout, verification failure, forged
        evidence) are recorded in the result, never raised.

        This method never activates, promotes, persists, or mutates anything.
        """
        identity = _resolve_candidate(candidate)
        ordered = _validate_cases(cases, max_cases=self._max_cases, policy=self._policy)

        executions = tuple(self._execute_case(identity, case) for case in ordered)
        evidence = tuple(
            _build_evidence(identity, execution.case, execution.outcome)
            for execution in executions
            if execution.outcome is not None
        )
        report = self._policy.evaluate(identity, evidence)
        classification = _classify_report(report)

        case_results: list[ProcedureValidationCaseResult] = []
        for execution in executions:
            if execution.outcome is not None:
                kind, reason = classification[execution.case.run_id.to_str()]
                case_results.append(
                    _case_result(
                        execution.case,
                        kind=kind,
                        reason=reason,
                        evidence_considered=True,
                        error_code=None,
                        unmet_conditions=(),
                    )
                )
                continue
            case_results.append(
                _case_result(
                    execution.case,
                    kind=cast("ValidationRunKind", execution.kind),
                    reason=execution.reason,
                    evidence_considered=False,
                    error_code=execution.error_code,
                    unmet_conditions=execution.unmet_conditions,
                )
            )

        return _compose(identity, tuple(case_results), report, len(evidence))

    # -- internals ---------------------------------------------------------

    def _execute_case(
        self, identity: ProcedureCandidateIdentity, case: ProcedureValidationCase
    ) -> _CaseExecution:
        """Execute exactly one case once through the injected harness."""
        request = ProcedureValidationRunRequest(candidate=identity, case=case)
        try:
            # Typed as ``object``: the harness is an injected port, so its
            # return value is untrusted until it is proven canonical. The
            # isinstance guards below are live runtime checks, not casts.
            produced: object = self._harness.run(request)
        except Exception:
            # Fail closed: a raising harness produced no canonical evidence,
            # and an exception is never success.
            return _CaseExecution(
                case=case,
                kind=ValidationRunKind.EXECUTION_FAILURE,
                reason=ValidationReasonCode.EXECUTION_FAILURE,
            )

        if not isinstance(produced, Result):
            # A harness that does not speak the canonical Result contract has
            # produced no canonical evidence at all.
            return _CaseExecution(
                case=case,
                kind=ValidationRunKind.FORGED,
                reason=ValidationReasonCode.FORGED_EVIDENCE,
            )

        if produced.is_failure:
            error = produced.unwrap_error()
            if isinstance(error, AgentXError) and error.category in _TIMEOUT_CATEGORIES:
                kind = ValidationRunKind.TIMEOUT_OR_CANCEL
            else:
                kind = ValidationRunKind.EXECUTION_FAILURE
            return _CaseExecution(
                case=case,
                kind=kind,
                reason=_REASON_FOR_KIND[kind],
                error_code=_error_code(error) if isinstance(error, AgentXError) else None,
            )

        outcome = produced.unwrap()
        if not isinstance(outcome, ClosedLoopOutcome):
            # A lookalike outcome object is forged evidence, never success.
            return _CaseExecution(
                case=case,
                kind=ValidationRunKind.FORGED,
                reason=ValidationReasonCode.FORGED_EVIDENCE,
            )

        requirement = case.verification
        if requirement is not None:
            evaluation = self._verifier.evaluate(
                VerifierRequest(outcome=outcome, requirement=requirement)
            )
            if not evaluation.satisfied:
                return _CaseExecution(
                    case=case,
                    kind=ValidationRunKind.VERIFICATION_FAILURE,
                    reason=ValidationReasonCode.VERIFICATION_FAILURE,
                    unmet_conditions=evaluation.unmet_conditions,
                )

        return _CaseExecution(case=case, outcome=outcome)


# ---------------------------------------------------------------------------
# Deterministic composition helpers.
# ---------------------------------------------------------------------------


def _validate_max_cases(value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"max_cases must be an int, got {type(value).__name__}")
    if not MIN_VALIDATION_CASES <= value <= MAX_VALIDATION_CASES_LIMIT:
        raise ProcedureValidationRunnerInputError(
            f"max_cases must be between {MIN_VALIDATION_CASES} and "
            f"{MAX_VALIDATION_CASES_LIMIT}; got {value}"
        )
    return value


def _resolve_candidate(candidate: object) -> ProcedureCandidateIdentity:
    if isinstance(candidate, ProcedureCandidateIdentity):
        return candidate
    if isinstance(candidate, ProcedureRecord):
        # Derives the canonical (procedure_id, revision) pair and nothing else:
        # no status is read, copied, carried, or changed.
        return ProcedureCandidateIdentity.from_record(candidate)
    raise TypeError(
        "candidate must be a ProcedureCandidateIdentity or a ProcedureRecord, "
        f"got {type(candidate).__name__}"
    )


def _validate_cases(
    cases: object,
    *,
    max_cases: int,
    policy: ValidationPolicy,
) -> tuple[ProcedureValidationCase, ...]:
    if isinstance(cases, str | bytes) or not isinstance(cases, Iterable):
        raise TypeError(
            f"cases must be an iterable of ProcedureValidationCase, got {type(cases).__name__}"
        )
    ordered = tuple(cases)
    for case in ordered:
        if not isinstance(case, ProcedureValidationCase):
            raise TypeError(
                f"cases must contain only ProcedureValidationCase, got {type(case).__name__}"
            )
    if not ordered:
        raise ProcedureValidationRunnerInputError(
            "a validation run requires at least one explicit validation case: an empty "
            "case collection is rejected, never treated as evidence"
        )
    if len(ordered) > max_cases:
        raise ProcedureValidationRunnerInputError(
            f"a validation run must not exceed {max_cases} cases; got {len(ordered)}"
        )
    if len(ordered) > policy.max_validation_records_considered:
        raise ProcedureValidationRunnerInputError(
            "a validation run must not exceed the M4.02 policy's "
            f"max_validation_records_considered ({policy.max_validation_records_considered}); "
            f"got {len(ordered)} cases"
        )
    seen_case_ids: set[str] = set()
    seen_run_ids: set[str] = set()
    for case in ordered:
        if case.case_id in seen_case_ids:
            raise ProcedureValidationRunnerInputError(
                f"duplicate validation case identity: {case.case_id!r}"
            )
        seen_case_ids.add(case.case_id)
        run_key = case.run_id.to_str()
        if run_key in seen_run_ids:
            raise ProcedureValidationRunnerInputError(
                f"duplicate validation run identity: {run_key}"
            )
        seen_run_ids.add(run_key)
    return ordered


def _build_evidence(
    identity: ProcedureCandidateIdentity,
    case: ProcedureValidationCase,
    outcome: ClosedLoopOutcome,
) -> ValidationRunEvidence:
    """Bind canonical outcome evidence to the exact candidate revision.

    No timestamp is attached: the runner reads no clock, and M4.02 never uses
    timestamps for variation or replay identity.
    """
    return ValidationRunEvidence(
        run_id=case.run_id,
        procedure_id=identity.procedure_id,
        revision=identity.revision,
        outcome=outcome,
        parameter_binding=case.parameter_binding,
        environment=case.environment,
        recorded_at=None,
    )


def _classify_report(
    report: ValidationReport,
) -> dict[str, tuple[ValidationRunKind, ValidationReasonCode | None]]:
    """Map canonical run identity to the M4.02 classification of its evidence.

    The runner does not classify evidence itself: M4.02's classification is the
    single truth, and this mapping only lets the runner report it per case, in
    caller order.
    """
    classification: dict[str, tuple[ValidationRunKind, ValidationReasonCode | None]] = {}
    for run_id in report.counted_success_run_ids:
        classification[run_id.to_str()] = (ValidationRunKind.VERIFIED_SUCCESS, None)
    for failure in report.failures:
        classification[failure.run_id.to_str()] = (failure.kind, failure.reason)
    for invalid in report.invalid_evidence:
        kind = (
            ValidationRunKind.FORGED
            if invalid.reason is ValidationReasonCode.FORGED_EVIDENCE
            else ValidationRunKind.INCONSISTENT
        )
        classification[invalid.run_id.to_str()] = (kind, invalid.reason)
    for run_id in (*report.stale_revision_records, *report.foreign_identity_records):
        # Unreachable for evidence this runner builds (identity is bound here),
        # and fail-closed if it ever were reached.
        classification.setdefault(
            run_id.to_str(),
            (ValidationRunKind.INCONSISTENT, ValidationReasonCode.STALE_REVISION),
        )
    return classification


def _case_result(
    case: ProcedureValidationCase,
    *,
    kind: ValidationRunKind,
    reason: ValidationReasonCode | None,
    evidence_considered: bool,
    error_code: str | None,
    unmet_conditions: tuple[str, ...],
) -> ProcedureValidationCaseResult:
    return ProcedureValidationCaseResult(
        case_id=case.case_id,
        run_id=case.run_id,
        kind=kind,
        reason=reason,
        evidence_considered=evidence_considered,
        error_code=error_code,
        unmet_conditions=unmet_conditions,
        parameter_binding=case.parameter_binding,
        environment=case.environment,
    )


def _compose(
    identity: ProcedureCandidateIdentity,
    cases: tuple[ProcedureValidationCaseResult, ...],
    report: ValidationReport,
    evidence_count: int,
) -> ProcedureValidationRunResult:
    """Build the aggregate result, only ever downgrading the M4.02 decision."""
    complete = evidence_count == len(cases)
    kinds = [case.kind for case in cases]
    blocking_kinds = [kind for kind in _BLOCKING_KINDS_IN_ORDER if kind in set(kinds)]
    blocking_count = sum(1 for kind in kinds if kind in _BLOCKING_CASE_KINDS)
    forged_present = any(kind is ValidationRunKind.FORGED for kind in kinds)

    decision, reasons = _aggregate_decision(
        report,
        complete=complete,
        blocking_kinds=blocking_kinds,
        blocking_count=blocking_count,
        forged_present=forged_present,
    )
    unmet = _unmet_requirements(
        report,
        case_count=len(cases),
        evidence_count=evidence_count,
        blocking_count=blocking_count,
        complete=complete,
    )
    return ProcedureValidationRunResult(
        candidate=identity,
        cases=cases,
        report=report,
        decision=decision,
        reasons=reasons,
        requirements=(*report.requirements, *_RUNNER_REQUIREMENTS),
        unmet_requirements=unmet,
        complete=complete,
    )


def _aggregate_decision(
    report: ValidationReport,
    *,
    complete: bool,
    blocking_kinds: list[ValidationRunKind],
    blocking_count: int,
    forged_present: bool,
) -> tuple[ValidationDecision, tuple[ValidationReasonCode, ...]]:
    """Compose the aggregate decision: never upgrade the M4.02 decision.

    Fail closed first, in a fixed order, so a partial or contaminated run can
    never be reported as universal validation:

    1. forged (non-canonical) case evidence -> ``DEGRADED``;
    2. any blocking per-case failure — including a case that never produced
       evidence the policy could consider -> ``REJECTED``;
    3. a complete run whose policy decision is eligible -> eligible;
    4. otherwise the canonical policy decision, unchanged.
    """
    if forged_present:
        return ValidationDecision.DEGRADED, (ValidationReasonCode.FORGED_EVIDENCE,)
    if blocking_count:
        reasons: list[ValidationReasonCode] = []
        for kind in blocking_kinds:
            reason = _REASON_FOR_KIND[kind]
            if reason not in reasons:
                reasons.append(reason)
        return ValidationDecision.REJECTED, tuple(reasons)
    if complete and report.decision is ValidationDecision.ELIGIBLE_FOR_PROMOTION:
        return ValidationDecision.ELIGIBLE_FOR_PROMOTION, ()
    return report.decision, report.reasons


def _unmet_requirements(
    report: ValidationReport,
    *,
    case_count: int,
    evidence_count: int,
    blocking_count: int,
    complete: bool,
) -> tuple[str, ...]:
    unmet: list[str] = []
    if not complete:
        unmet.append(
            f"{case_count - evidence_count} of {case_count} validation case(s) did not "
            "produce evidence the M4.02 policy could consider"
        )
    if blocking_count:
        unmet.append(f"{blocking_count} validation case(s) did not pass")
    if not unmet:
        return report.unmet_requirements
    for text in report.unmet_requirements:
        if text not in unmet:
            unmet.append(text)
    return tuple(unmet)
