"""Controlled shadow-procedure validation runner (N2.16).

This module is the *controlled runner* that executes an explicitly supplied
repaired Procedure candidate in a SHADOW / TEST validation context and emits
the already-canonical shadow-repair evidence contract
(:class:`~agentx.core.shadow_repair.ShadowRepairResult`, M5.05 / C4.07).

The M5.05 / C4.07 evidence contract is canonical and is **not redesigned** here.
This runner does not create a new evidence vocabulary; it *fills* the canonical
one, one immutable ``ShadowRepairResult`` per bounded shadow case, from typed
facts returned by an injected controlled-execution harness.

CONTROLLED EXECUTION (no generic sandboxing)
============================================

This runner never sandboxes anything and never spawns a process, shell, or
subprocess, and it never talks to a store, a model, the Procedure interpreter,
a Capability, the network, or the filesystem. All candidate execution happens
through an **explicit injected execution port** (see
:class:`ShadowExecutionPort`). The port is the only code that touches the
candidate: a real controlled shadow harness in production, or a deterministic
fake harness in repository tests. The runner is pure orchestration over that
injected boundary. There is no arbitrary shell and no ``subprocess``/``os``
framework anywhere in this module.

SHADOW MEANS NON-CANONICAL
==========================

The candidate under shadow testing is **not** the live ACTIVE replacement
merely because it is run through the harness. This module:

* never calls the canonical procedure store (imports nothing from
  ``agentx.infrastructure``),
* never switches the active Procedure, retires a prior revision, marks a
  candidate ACTIVE, promotes a revision, or routes production work to the
  candidate,
* never rolls anything back (there is nothing to roll back here),
* never clears an emergency stop, widens a budget, lowers a RiskLevel, or
  grants a Permission.

Shadow execution is evidence gathering only.

VERIFICATION TRUTH
==================

``execution completed != shadow success`` and ``Procedure END !=
shadow-safe``. A candidate reaches a ``PASSED`` trial only through canonical
typed verification evidence: a ``VerificationPayload`` with ``passed == True``
plus at least one recorded step, no failed step, and no uncontained external
effect — enforced by the canonical record's own fail-closed validation. Text
such as ``shadow_safe=true``, ``verified=true``, ``repair_approved=true``,
``permission=ADMIN``, or ``activate_candidate=true`` inside candidate content,
node results, or detail strings is inert and never flips a verdict. This
module never inspects candidate text to derive anything.

The runner derives the disposition for each case from the harness's typed
output (termination signal + verification + steps) under the same fail-closed
rules the canonical contract encodes, then constructs the canonical
``ShadowRepairResult`` whose constructor re-validates the whole claim. An
impossible or contradictory combination (for example a claimed ``PASSED``
with no verification, or an ``ABORTED`` trial carrying a verification) is
rejected, never silently normalized.

BOUNDEDNESS
===========

The case list is finite and bounded (1..``MAX_SHADOW_CASES``). An empty or
oversized list is rejected. Each trial records a bounded number of steps.
There is no retry loop and no auto-repair inside this runner: each supplied
case is executed exactly once and its result is preserved, whether it passed,
failed, aborted, was refused as unsafe, or produced insufficient evidence.

IDENTITY BINDING
================

Evidence binds exactly to the source ``ProcedureId``/revision and the
candidate ``ProcedureId``/revision (or fingerprint). The harness reports which
identity it actually exercised; this runner rejects any mismatch, so evidence
gathered while evaluating candidate revision N can never be attached to a run
declared against revision N+1.

DETERMINISM
===========

Callers inject a ``clock`` and a ``run_id_factory``. With a deterministic
clock/factory and a deterministic harness, identical inputs produce identical
run-level summaries and canonical trial records (byte-identical JSON per
record).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Protocol
from uuid import UUID, uuid4

from agentx.core.events import VerificationPayload
from agentx.core.ids import ProcedureId, TaskId
from agentx.core.procedures import ProcedureScope
from agentx.core.shadow_repair import (
    ShadowRepairDisposition,
    ShadowRepairMode,
    ShadowRepairResult,
    ShadowRepairStepEvidence,
    ShadowRepairValidationError,
    ShadowRevisionOutcome,
    ShadowStepOutcome,
)

__all__ = [
    "MAX_CANDIDATE_CONTENT_LENGTH",
    "MAX_SHADOW_CASES",
    "MAX_SHADOW_HARNESS_STEPS",
    "MAX_SHADOW_OBJECTIVE_LENGTH",
    "ShadowCaseRequest",
    "ShadowExecutionPort",
    "ShadowHarnessStep",
    "ShadowHarnessTrial",
    "ShadowProcedureCandidate",
    "ShadowProcedureRunnerError",
    "ShadowRunSummary",
    "ShadowRunnerBindingError",
    "ShadowRunnerConfigError",
    "ShadowRunnerEvidenceError",
    "ShadowTermination",
    "ShadowValidationCase",
    "ShadowValidationRun",
    "run_shadow_validation",
]

# ---------------------------------------------------------------------------
# Hard bounds. The runner is a bounded orchestrator: finite cases, finite
# steps per case, bounded inert strings.
# ---------------------------------------------------------------------------

#: Maximum number of bounded shadow validation cases per run.
MAX_SHADOW_CASES: Final[int] = 64

#: Maximum number of recorded steps a single case may report to the runner.
MAX_SHADOW_HARNESS_STEPS: Final[int] = 64

#: Maximum length (characters) of an opaque candidate revision's content text.
MAX_CANDIDATE_CONTENT_LENGTH: Final[int] = 1_048_576

#: Maximum length (characters) of the inert per-case objective annotation.
MAX_SHADOW_OBJECTIVE_LENGTH: Final[int] = 4_096

#: Upper bound on the number of regression node ids a trial may report.
_MAX_REGRESSION_NODE_IDS: Final[int] = 32

#: Upper bound on a node identity string inside harness steps.
_MAX_NODE_ID_LENGTH: Final[int] = 128


class ShadowProcedureRunnerError(ValueError):
    """Base error raised by the shadow-procedure validation runner.

    Raised only for malformed runner inputs, identity binding mismatches, or
    contradictory harness evidence. It never indicates that the candidate was
    adopted, activated, or replaced — those are impossible here.
    """


class ShadowRunnerConfigError(ShadowProcedureRunnerError):
    """Raised when the runner's inputs violate hard bounds.

    For example an empty or oversized case list, or an underspecified
    candidate identity.
    """


class ShadowRunnerBindingError(ShadowProcedureRunnerError):
    """Raised when harness evidence binds to a different procedure/revision.

    Evidence produced against candidate revision N must never be attached to a
    run declared against revision N+1; mismatches are rejected.
    """


class ShadowRunnerEvidenceError(ShadowProcedureRunnerError):
    """Raised when harness evidence is contradictory or unsupported.

    For example a claimed passing verdict with no recorded step, a non-verdict
    termination carrying verification evidence, or an oversized step list.
    """


class ShadowTermination(StrEnum):
    """Closed vocabulary describing how one shadow-case execution ended.

    This is the harness's typed report of *why* evidence collection stopped.
    It is a controlled signal only; it never carries candidate text and never
    grants authority.

    Members:
        COMPLETED: The candidate execution ran to the end of the case and a
            verdict can be derived from the supplied typed evidence.
        ABORTED: Evaluation stopped before completion (budget, halt, timeout,
            cancellation) through the harness.
        UNSAFE_TO_EVALUATE: The harness refused to evaluate the candidate as
            unsafe.
        INSUFFICIENT_EVIDENCE: Execution could not produce enough typed
            evidence to support a verdict.
    """

    COMPLETED = "completed"
    ABORTED = "aborted"
    UNSAFE_TO_EVALUATE = "unsafe_to_evaluate"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True, kw_only=True)
class ShadowHarnessStep:
    """One typed stage report returned by the injected execution harness.

    This is the harness's observation vocabulary (order is assigned by the
    runner). A step with ``outcome == ShadowStepOutcome.FAILED`` is an
    explicit typed failure for that stage; a failed stage can never yield a
    passing verdict.

    Containment is a claim about an observed external effect only:
    ``effect_contained`` with ``external_effect_observed=False`` is rejected,
    mirroring the canonical evidence contract.
    """

    node_id: str | None = None
    outcome: ShadowStepOutcome = ShadowStepOutcome.OBSERVED
    external_effect_observed: bool = False
    effect_contained: bool = False

    def __post_init__(self) -> None:
        if self.node_id is not None:
            if (
                not isinstance(self.node_id, str)
                or self.node_id == ""
                or self.node_id != self.node_id.strip()
            ):
                raise ShadowRunnerEvidenceError(
                    "step node_id must be a non-empty trimmed string or None"
                )
            if len(self.node_id) > _MAX_NODE_ID_LENGTH:
                raise ShadowRunnerEvidenceError(
                    f"step node_id must be at most {_MAX_NODE_ID_LENGTH} characters"
                )
        if not isinstance(self.outcome, ShadowStepOutcome):
            raise ShadowRunnerEvidenceError("step outcome must be a ShadowStepOutcome")
        if not isinstance(self.external_effect_observed, bool):
            raise ShadowRunnerEvidenceError("step external_effect_observed must be a boolean")
        if not isinstance(self.effect_contained, bool):
            raise ShadowRunnerEvidenceError("step effect_contained must be a boolean")
        if self.effect_contained and not self.external_effect_observed:
            raise ShadowRunnerEvidenceError(
                "step effect_contained may only be claimed for an observed external effect"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class ShadowValidationCase:
    """One explicit, bounded shadow validation case.

    A case is a single test scenario under which the candidate is executed
    through the injected harness. The runner records one canonical
    ``ShadowRepairResult`` per case, binding the case's scope / task / node
    where the canonical contract supports them.
    """

    scope: ProcedureScope = field(default_factory=ProcedureScope)
    task_id: TaskId | None = None
    target_node_id: str | None = None
    objective: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ProcedureScope):
            raise ShadowRunnerConfigError("case scope must be a ProcedureScope")
        if self.task_id is not None and not isinstance(self.task_id, TaskId):
            raise ShadowRunnerConfigError("case task_id must be a TaskId or None")
        if self.target_node_id is not None:
            if not isinstance(self.target_node_id, str):
                raise ShadowRunnerConfigError("case target_node_id must be a string or None")
            if self.target_node_id == "" or self.target_node_id != self.target_node_id.strip():
                raise ShadowRunnerConfigError(
                    "case target_node_id must be non-empty and trimmed when provided"
                )
        if self.objective is not None:
            if not isinstance(self.objective, str):
                raise ShadowRunnerConfigError("case objective must be a string or None")
            if len(self.objective) > MAX_SHADOW_OBJECTIVE_LENGTH:
                raise ShadowRunnerConfigError(
                    f"case objective must be at most {MAX_SHADOW_OBJECTIVE_LENGTH} characters"
                )


@dataclass(frozen=True, slots=True, kw_only=True)
class ShadowProcedureCandidate:
    """The explicitly supplied repaired Procedure candidate to evaluate.

    Exactly one candidate identity must be present: an explicit positive
    ``candidate_revision`` OR an opaque non-empty ``candidate_fingerprint`` —
    never both, never neither. The candidate must differ from the source
    revision (enforced against ``source_revision`` at run time).

    ``content`` is the opaque repaired-procedure text the injected harness
    executes. It is inert data to this runner and is never parsed or
    interpreted here.
    """

    content: str
    candidate_revision: int | None = None
    candidate_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.content, str) or self.content.strip() == "":
            raise ShadowRunnerConfigError("candidate content must be a non-empty string")
        if len(self.content) > MAX_CANDIDATE_CONTENT_LENGTH:
            raise ShadowRunnerConfigError(
                f"candidate content must be at most {MAX_CANDIDATE_CONTENT_LENGTH} characters"
            )
        if self.candidate_revision is not None and (
            isinstance(self.candidate_revision, bool)
            or not isinstance(self.candidate_revision, int)
        ):
            raise ShadowRunnerConfigError("candidate_revision must be a positive integer")
        if self.candidate_revision is not None and self.candidate_revision < 1:
            raise ShadowRunnerConfigError("candidate_revision must be a positive integer")
        if self.candidate_fingerprint is not None:
            if not isinstance(self.candidate_fingerprint, str):
                raise ShadowRunnerConfigError("candidate_fingerprint must be a string or None")
            if (
                self.candidate_fingerprint == ""
                or self.candidate_fingerprint != self.candidate_fingerprint.strip()
            ):
                raise ShadowRunnerConfigError(
                    "candidate_fingerprint must be non-empty and trimmed when provided"
                )
        if (self.candidate_revision is None) == (self.candidate_fingerprint is None):
            raise ShadowRunnerConfigError(
                "exactly one candidate identity is required: candidate_revision "
                "or candidate_fingerprint, never both and never neither"
            )

    @property
    def identity_label(self) -> str:
        """Canonical candidate identity label used for binding checks."""
        if self.candidate_revision is not None:
            return f"revision:{self.candidate_revision}"
        assert self.candidate_fingerprint is not None
        return f"fingerprint:{self.candidate_fingerprint}"


@dataclass(frozen=True, slots=True, kw_only=True)
class ShadowCaseRequest:
    """The immutable context handed to the injected execution harness.

    The harness uses ``candidate.content`` to actually run the candidate for
    ``case`` and returns a :class:`ShadowHarnessTrial`.
    """

    case_index: int
    case: ShadowValidationCase
    candidate: ShadowProcedureCandidate
    procedure_id: ProcedureId
    source_revision: int
    correlation_id: UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class ShadowHarnessTrial:
    """Typed result of running the candidate for one shadow case.

    ``procedure_id`` / ``source_revision`` / ``candidate_revision`` /
    ``candidate_fingerprint`` name the identity the harness *actually
    exercised*; the runner rejects any that differ from the run's declared
    binding.

    Attributes:
        termination: Why this case's execution ended (controlled vocabulary).
        procedure_id: The procedure identity the harness exercised.
        source_revision: The exact source revision evaluated against.
        candidate_revision: Exact candidate revision exercised (or None when
            identified by fingerprint).
        candidate_fingerprint: Opaque candidate fingerprint exercised (or None
            when identified by revision).
        verification: Independent canonical verification the harness
            performed, when the case produced a verdict; None otherwise.
        steps: Ordered typed stage reports (runner assigns final order).
        original_outcome: The source revision's observed outcome on this case,
            when recorded; None otherwise.
        regression_node_ids: Bounded node ids observed to regress.
        detail: Optional inert annotation; never authority-bearing.
    """

    termination: ShadowTermination
    procedure_id: ProcedureId
    source_revision: int
    candidate_revision: int | None = None
    candidate_fingerprint: str | None = None
    verification: VerificationPayload | None = None
    steps: tuple[ShadowHarnessStep, ...] = ()
    original_outcome: ShadowRevisionOutcome | None = None
    regression_node_ids: tuple[str, ...] = ()
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.termination, ShadowTermination):
            raise ShadowRunnerEvidenceError("trial termination must be a ShadowTermination")
        if not isinstance(self.procedure_id, ProcedureId):
            raise ShadowRunnerEvidenceError("trial procedure_id must be a ProcedureId")
        if isinstance(self.source_revision, bool) or not isinstance(self.source_revision, int):
            raise ShadowRunnerEvidenceError("trial source_revision must be a positive integer")
        if self.source_revision < 1:
            raise ShadowRunnerEvidenceError("trial source_revision must be a positive integer")
        if self.candidate_revision is not None and (
            isinstance(self.candidate_revision, bool)
            or not isinstance(self.candidate_revision, int)
            or self.candidate_revision < 1
        ):
            raise ShadowRunnerEvidenceError(
                "trial candidate_revision must be a positive integer or None"
            )
        if (self.candidate_revision is None) == (self.candidate_fingerprint is None):
            raise ShadowRunnerEvidenceError(
                "trial must identify exactly one candidate (revision or fingerprint)"
            )
        if self.verification is not None and not isinstance(self.verification, VerificationPayload):
            raise ShadowRunnerEvidenceError(
                "trial verification must be a VerificationPayload or None"
            )
        if not isinstance(self.steps, tuple):
            raise ShadowRunnerEvidenceError("trial steps must be a tuple of ShadowHarnessStep")
        for step in self.steps:
            if not isinstance(step, ShadowHarnessStep):
                raise ShadowRunnerEvidenceError("trial steps must contain ShadowHarnessStep")
        if self.original_outcome is not None and not isinstance(
            self.original_outcome, ShadowRevisionOutcome
        ):
            raise ShadowRunnerEvidenceError(
                "trial original_outcome must be a ShadowRevisionOutcome or None"
            )
        if not isinstance(self.regression_node_ids, tuple):
            raise ShadowRunnerEvidenceError("trial regression_node_ids must be a tuple of strings")
        for node in self.regression_node_ids:
            if not isinstance(node, str) or node == "" or node != node.strip():
                raise ShadowRunnerEvidenceError(
                    "trial regression_node_ids entries must be non-empty trimmed strings"
                )
        if len(self.regression_node_ids) > _MAX_REGRESSION_NODE_IDS:
            raise ShadowRunnerEvidenceError(
                f"trial regression_node_ids must contain at most {_MAX_REGRESSION_NODE_IDS} entries"
            )
        if self.detail is not None:
            if not isinstance(self.detail, str):
                raise ShadowRunnerEvidenceError("trial detail must be a string or None")
            if self.detail == "" or self.detail != self.detail.strip():
                raise ShadowRunnerEvidenceError(
                    "trial detail must be non-empty and trimmed when provided"
                )
            if len(self.detail) > MAX_SHADOW_OBJECTIVE_LENGTH:
                raise ShadowRunnerEvidenceError(
                    f"trial detail must be at most {MAX_SHADOW_OBJECTIVE_LENGTH} characters"
                )


class ShadowExecutionPort(Protocol):
    """The injected controlled-execution boundary.

    Implementations run the candidate for one shadow case and return typed
    facts. In repository tests this is a deterministic fake harness; in
    production it is a real controlled shadow/test harness. The runner never
    performs this execution itself and never bypasses this port.
    """

    def execute_case(self, *, request: ShadowCaseRequest) -> ShadowHarnessTrial: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _derive_disposition(
    *,
    termination: ShadowTermination,
    verification: VerificationPayload | None,
    steps: Sequence[ShadowHarnessStep],
) -> tuple[ShadowRepairDisposition, VerificationPayload | None]:
    """Derive the canonical disposition from typed harness facts (fail closed).

    The derivation mirrors the rules the canonical record enforces, so an
    impossible claim is rejected here before construction rather than accepted.
    Candidate text never reaches this function.

    Returns:
        The derived disposition and the verification payload the trial may
        carry (never present for non-verdict dispositions).
    """
    failed_step = any(step.outcome is ShadowStepOutcome.FAILED for step in steps)
    uncontained_effect = any(
        step.external_effect_observed and not step.effect_contained for step in steps
    )

    if termination is ShadowTermination.ABORTED:
        if verification is not None:
            raise ShadowRunnerEvidenceError("an aborted trial must not carry verification evidence")
        return ShadowRepairDisposition.ABORTED, None

    if termination is ShadowTermination.UNSAFE_TO_EVALUATE:
        if verification is not None:
            raise ShadowRunnerEvidenceError(
                "a trial refused as unsafe to evaluate must not carry verification evidence"
            )
        return ShadowRepairDisposition.UNSAFE_TO_EVALUATE, None

    if termination is ShadowTermination.INSUFFICIENT_EVIDENCE:
        if verification is not None:
            raise ShadowRunnerEvidenceError(
                "an evidence-poor trial must not carry verification evidence"
            )
        return ShadowRepairDisposition.INSUFFICIENT_EVIDENCE, None

    # termination is COMPLETED.
    if verification is None:
        if failed_step:
            # An explicit typed failure (a failed stage) with no passing
            # verification => FAILED.
            return ShadowRepairDisposition.FAILED, None
        # Executed to completion (possibly reaching Procedure END) but produced
        # no canonical verification => no verdict. Procedure END != shadow-safe.
        return ShadowRepairDisposition.INSUFFICIENT_EVIDENCE, None

    if not verification.passed:
        # Explicit failing canonical verification => FAILED.
        return ShadowRepairDisposition.FAILED, verification

    # verification.passed is True. A passing verdict additionally requires at
    # least one recorded step and no failed / uncontained stage. Any
    # contradiction is rejected rather than normalized.
    if failed_step:
        raise ShadowRunnerEvidenceError("a passing verification is contradicted by a failed stage")
    if uncontained_effect:
        raise ShadowRunnerEvidenceError(
            "a passing verdict is contradicted by an uncontained external effect"
        )
    if not steps:
        raise ShadowRunnerEvidenceError(
            "a passing verdict requires at least one recorded step of evidence"
        )
    return ShadowRepairDisposition.PASSED, verification


def _bound_identity(*, procedure_id: ProcedureId, source_revision: int) -> None:
    if not isinstance(procedure_id, ProcedureId):
        raise ShadowRunnerConfigError("procedure_id must be a ProcedureId")
    if isinstance(source_revision, bool) or not isinstance(source_revision, int):
        raise ShadowRunnerConfigError("source_revision must be a positive integer")
    if source_revision < 1:
        raise ShadowRunnerConfigError("source_revision must be a positive integer")


def _assert_binding(
    *,
    declared_procedure_id: ProcedureId,
    declared_source_revision: int,
    declared_candidate_revision: int | None,
    declared_candidate_fingerprint: str | None,
    exercised_procedure_id: ProcedureId,
    exercised_source_revision: int,
    exercised_candidate_revision: int | None,
    exercised_candidate_fingerprint: str | None,
) -> None:
    """Reject harness evidence that does not bind exactly to the run's target.

    Evidence collected for procedure/source/candidate identity X is only valid
    inside a run declared against the very same identity. Mismatched evidence
    (for example a trial the harness exercised against candidate revision N in
    a run declared against revision N+1) is rejected, never silently reused.
    """
    if exercised_procedure_id != declared_procedure_id:
        raise ShadowRunnerBindingError(
            "harness evidence binds to a different procedure than the run declares"
        )
    if exercised_source_revision != declared_source_revision:
        raise ShadowRunnerBindingError(
            "harness evidence binds to a different source revision than the run declares"
        )
    if exercised_candidate_revision != declared_candidate_revision:
        raise ShadowRunnerBindingError(
            "harness evidence binds to a candidate revision that differs from the "
            "run's declared candidate; evidence from one candidate revision cannot "
            "validate another"
        )
    if exercised_candidate_fingerprint != declared_candidate_fingerprint:
        raise ShadowRunnerBindingError(
            "harness evidence binds to a candidate fingerprint that differs from "
            "the run's declared candidate"
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ShadowRunSummary:
    """Deterministic run-level tally of the canonical trial dispositions.

    The summary is orchestration bookkeeping over the canonical evidence; it is
    not authority and never means the candidate was adopted or activated.
    """

    total: int
    passed: int
    failed: int
    aborted: int
    unsafe_to_evaluate: int
    insufficient_evidence: int

    @property
    def all_passed(self) -> bool:
        """True only when every trial is a validated ``PASSED`` record."""
        return self.total > 0 and self.passed == self.total

    @property
    def any_failure(self) -> bool:
        """True when at least one trial carried an explicit failure."""
        return self.failed > 0


@dataclass(frozen=True, slots=True, kw_only=True)
class ShadowValidationRun:
    """The immutable result of one bounded shadow-procedure validation run.

    ``trials`` are the canonical M5.05 / C4.07 ``ShadowRepairResult`` records,
    one per supplied shadow case, in case order. The run itself is evidence
    only: it never activates a candidate, replaces a revision, or mutates any
    store.
    """

    procedure_id: ProcedureId
    source_revision: int
    candidate: ShadowProcedureCandidate
    correlation_id: UUID
    started_at: datetime
    ended_at: datetime
    trials: tuple[ShadowRepairResult, ...]
    summary: ShadowRunSummary
    detail: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, ProcedureId):
            raise ShadowRunnerConfigError("run procedure_id must be a ProcedureId")
        if isinstance(self.source_revision, bool) or not isinstance(self.source_revision, int):
            raise ShadowRunnerConfigError("run source_revision must be a positive integer")
        if self.source_revision < 1:
            raise ShadowRunnerConfigError("run source_revision must be a positive integer")
        if not isinstance(self.candidate, ShadowProcedureCandidate):
            raise ShadowRunnerConfigError("run candidate must be a ShadowProcedureCandidate")
        if (
            self.candidate.candidate_revision is not None
            and self.candidate.candidate_revision == self.source_revision
        ):
            raise ShadowRunnerConfigError(
                "candidate_revision must differ from source_revision; a shadow trial "
                "evaluates a candidate distinct from the source revision"
            )
        if not isinstance(self.correlation_id, UUID) or self.correlation_id.int == 0:
            raise ShadowRunnerConfigError("run correlation_id must be a non-nil UUID")
        if not isinstance(self.trials, tuple):
            raise ShadowRunnerConfigError("run trials must be a tuple of ShadowRepairResult")
        for trial in self.trials:
            if not isinstance(trial, ShadowRepairResult):
                raise ShadowRunnerConfigError("run trials must contain ShadowRepairResult")

    @property
    def all_passed(self) -> bool:
        """Whether every bounded shadow case produced validated evidence."""
        return self.summary.all_passed

    def to_dict(self) -> dict[str, object]:
        """Deterministic summary representation (canonical per-trial JSON)."""
        return {
            "procedure_id": self.procedure_id.to_str(),
            "source_revision": self.source_revision,
            "candidate": {
                "candidate_revision": self.candidate.candidate_revision,
                "candidate_fingerprint": self.candidate.candidate_fingerprint,
            },
            "correlation_id": str(self.correlation_id),
            "mode": ShadowRepairMode.NON_COMMITTING.value,
            "all_passed": self.all_passed,
            "summary": {
                "total": self.summary.total,
                "passed": self.summary.passed,
                "failed": self.summary.failed,
                "aborted": self.summary.aborted,
                "unsafe_to_evaluate": self.summary.unsafe_to_evaluate,
                "insufficient_evidence": self.summary.insufficient_evidence,
            },
            "trials": [trial.to_dict() for trial in self.trials],
        }


def run_shadow_validation(
    *,
    procedure_id: ProcedureId,
    source_revision: int,
    candidate: ShadowProcedureCandidate,
    cases: Sequence[ShadowValidationCase],
    harness: ShadowExecutionPort,
    correlation_id: UUID,
    clock: Callable[[], datetime] | None = None,
    run_id_factory: Callable[[], UUID] | None = None,
    detail: str | None = None,
) -> ShadowValidationRun:
    """Run every bounded shadow case against the candidate and emit M5.05 evidence.

    For each supplied case the runner invokes ``harness.execute_case`` once,
    verifies the returned typed facts bind exactly to the declared source and
    candidate, derives a canonical disposition fail-closed, and constructs one
    immutable :class:`~agentx.core.shadow_repair.ShadowRepairResult`. No retry
    loop, no auto-repair, no live mutation.

    Args:
        procedure_id: The source/current procedure identity being repaired.
        source_revision: The exact source revision the candidate repairs.
        candidate: The proposed candidate revision (content plus exact identity).
        cases: The explicit bounded shadow validation cases (1..``MAX_SHADOW_CASES``).
        harness: The injected controlled-execution port.
        correlation_id: Non-nil UUID correlating this run.
        clock: Injectable clock used for deterministic trial timestamps.
        run_id_factory: Injectable run-id generator for deterministic trials.
        detail: Optional inert run-level annotation.

    Returns:
        A :class:`ShadowValidationRun` of canonical trial evidence.

    Raises:
        ShadowRunnerConfigError: For empty/oversized case lists or malformed inputs.
        ShadowRunnerBindingError: If harness evidence binds to a different identity.
        ShadowRunnerEvidenceError: If harness evidence is contradictory.
    """
    if not isinstance(procedure_id, ProcedureId):
        raise ShadowRunnerConfigError("procedure_id must be a ProcedureId")
    _bound_identity(procedure_id=procedure_id, source_revision=source_revision)
    if not isinstance(candidate, ShadowProcedureCandidate):
        raise ShadowRunnerConfigError("candidate must be a ShadowProcedureCandidate")
    if candidate.candidate_revision is not None and candidate.candidate_revision == source_revision:
        raise ShadowRunnerConfigError(
            "candidate_revision must differ from source_revision; a shadow trial "
            "evaluates a candidate distinct from the source revision"
        )
    if not isinstance(correlation_id, UUID) or correlation_id.int == 0:
        raise ShadowRunnerConfigError("correlation_id must be a non-nil UUID")
    if not isinstance(cases, Sequence):
        raise ShadowRunnerConfigError("cases must be a sequence of ShadowValidationCase")
    material = tuple(cases)
    if not material:
        raise ShadowRunnerConfigError("at least one bounded shadow case is required")
    if len(material) > MAX_SHADOW_CASES:
        raise ShadowRunnerConfigError(f"at most {MAX_SHADOW_CASES} shadow cases are allowed")
    for case in material:
        if not isinstance(case, ShadowValidationCase):
            raise ShadowRunnerConfigError("cases must contain ShadowValidationCase")

    if clock is None:
        clock = _utc_now
    if run_id_factory is None:
        run_id_factory = uuid4

    run_started = clock()
    trials: list[ShadowRepairResult] = []
    for index, case in enumerate(material, start=1):
        request = ShadowCaseRequest(
            case_index=index,
            case=case,
            candidate=candidate,
            procedure_id=procedure_id,
            source_revision=source_revision,
            correlation_id=correlation_id,
        )
        trial_started = clock()
        harness_trial = harness.execute_case(request=request)
        trial_ended = clock()
        if not isinstance(harness_trial, ShadowHarnessTrial):
            raise ShadowRunnerEvidenceError("harness must return a ShadowHarnessTrial")
        _assert_binding(
            declared_procedure_id=procedure_id,
            declared_source_revision=source_revision,
            declared_candidate_revision=candidate.candidate_revision,
            declared_candidate_fingerprint=candidate.candidate_fingerprint,
            exercised_procedure_id=harness_trial.procedure_id,
            exercised_source_revision=harness_trial.source_revision,
            exercised_candidate_revision=harness_trial.candidate_revision,
            exercised_candidate_fingerprint=harness_trial.candidate_fingerprint,
        )
        if len(harness_trial.steps) > MAX_SHADOW_HARNESS_STEPS:
            raise ShadowRunnerEvidenceError(
                f"a case may record at most {MAX_SHADOW_HARNESS_STEPS} steps"
            )
        disposition, verification = _derive_disposition(
            termination=harness_trial.termination,
            verification=harness_trial.verification,
            steps=harness_trial.steps,
        )
        canonical_steps = tuple(
            ShadowRepairStepEvidence(
                order=order,
                node_id=step.node_id,
                outcome=step.outcome,
                external_effect_observed=step.external_effect_observed,
                effect_contained=step.effect_contained,
            )
            for order, step in enumerate(harness_trial.steps, start=1)
        )
        try:
            record = ShadowRepairResult(
                run_id=run_id_factory(),
                procedure_id=procedure_id,
                source_revision=source_revision,
                candidate_revision=candidate.candidate_revision,
                candidate_fingerprint=candidate.candidate_fingerprint,
                target_node_id=case.target_node_id,
                task_id=case.task_id,
                correlation_id=correlation_id,
                scope=case.scope,
                started_at=trial_started,
                ended_at=trial_ended,
                mode=ShadowRepairMode.NON_COMMITTING,
                disposition=disposition,
                verification=verification,
                steps=canonical_steps,
                original_outcome=harness_trial.original_outcome,
                regression_node_ids=harness_trial.regression_node_ids,
                detail=harness_trial.detail or case.objective,
            )
        except ShadowRepairValidationError as exc:
            raise ShadowRunnerEvidenceError(
                f"harness evidence cannot form a canonical shadow record: {exc}"
            ) from exc
        trials.append(record)

    run_ended = clock()
    counts = _tally(trials)
    summary = ShadowRunSummary(
        total=counts.total,
        passed=counts.passed,
        failed=counts.failed,
        aborted=counts.aborted,
        unsafe_to_evaluate=counts.unsafe_to_evaluate,
        insufficient_evidence=counts.insufficient_evidence,
    )
    return ShadowValidationRun(
        procedure_id=procedure_id,
        source_revision=source_revision,
        candidate=candidate,
        correlation_id=correlation_id,
        started_at=run_started,
        ended_at=run_ended,
        trials=tuple(trials),
        summary=summary,
        detail=detail,
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class _RunCounts:
    total: int
    passed: int
    failed: int
    aborted: int
    unsafe_to_evaluate: int
    insufficient_evidence: int


def _tally(trials: Sequence[ShadowRepairResult]) -> _RunCounts:
    passed = sum(1 for trial in trials if trial.disposition is ShadowRepairDisposition.PASSED)
    failed = sum(1 for trial in trials if trial.disposition is ShadowRepairDisposition.FAILED)
    aborted = sum(1 for trial in trials if trial.disposition is ShadowRepairDisposition.ABORTED)
    unsafe = sum(
        1 for trial in trials if trial.disposition is ShadowRepairDisposition.UNSAFE_TO_EVALUATE
    )
    insufficient = sum(
        1 for trial in trials if trial.disposition is ShadowRepairDisposition.INSUFFICIENT_EVIDENCE
    )
    return _RunCounts(
        total=len(trials),
        passed=passed,
        failed=failed,
        aborted=aborted,
        unsafe_to_evaluate=unsafe,
        insufficient_evidence=insufficient,
    )
