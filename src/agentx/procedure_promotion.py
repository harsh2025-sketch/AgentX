"""Explicit reviewed procedure-promotion transaction (N2.10).

This module owns exactly one narrow act: atomically applying an *already
canonical* promotion decision to canonical persistence. It is the integration
boundary the M4.02 validation policy and the M4.03 lifecycle policy
deliberately left open::

    CANDIDATE --(eligible evidence + ALLOWED lifecycle decision + THIS
                 transaction)--> ACTIVE

Everything that DECIDES is canonical and lands elsewhere:

    - :class:`agentx.procedure_validation.ValidationReport` (M4.02) decides
      whether the candidate EARNED promotion
      (:data:`ValidationDecision.ELIGIBLE_FOR_PROMOTION`). Eligibility
      evidence is data and never mutates persistence by itself.
    - :func:`agentx.core.procedure_lifecycle.assess_procedure_transition`
      (M4.03) decides whether the ``CANDIDATE -> ACTIVE`` move is structurally
      legal for :attr:`ProcedureLifecycleReason.VALIDATION_PROMOTION`.
    - :class:`agentx.core.procedures.ProcedureRecord` /
      :class:`agentx.core.procedures.ProcedureStatus` (C2.03) define identity
      ``(ProcedureId, revision)`` and the closed status vocabulary.
    - :class:`agentx.infrastructure.procedure_store.ProcedureStore` (C2.03)
      owns durable, append-only revision storage.

N2.10 adds none of those decisions. It re-verifies every one of them against
the exact stored record, then performs the single explicit status write —
fail-closed, all-or-nothing, and race-free.

THE CORE INVARIANT (unchanged, enforced here)
=============================================

``CANDIDATE != ELIGIBLE != ACTIVE``. A validated report is evidence, not
state. This transaction is the ONLY step at which a revision's persisted
status may become ``ACTIVE``, and it will do so only when ALL of the
following hold simultaneously, inside one atomic compare-and-set write:

    1. the request names one exact ``ProcedureId`` and one exact revision;
    2. the caller supplies the exact stored ``ProcedureRecord`` it validated
       (stale requests are rejected, never "refreshed");
    3. the stored record is still byte-for-byte the same canonical record at
       write time (compare-and-set inside the store's ``BEGIN IMMEDIATE``
       transaction closes the stale-check race);
    4. a canonical ``ValidationReport`` with decision
       ``ELIGIBLE_FOR_PROMOTION`` is bound to exactly that identity;
    5. a canonical ``ProcedureLifecycleAssessment`` with decision ``ALLOWED``
       describes exactly ``CANDIDATE -> ACTIVE`` for reason
       ``VALIDATION_PROMOTION`` on that identity (and the assessment is
       re-derived from the canonical policy as a cross-check).

Any other situation is a typed, deterministic rejection with an explicit
reason. Nothing is ever deleted: revisions, history, and evidence survive
promotion and rejection alike.

No raw booleans, no parsed text
-------------------------------

There is no ``approved=`` flag and no free-text approval anywhere in this
boundary. The only accepted evidence types are the canonical frozen reports;
strings such as ``"verified=true"``, ``"activate_candidate=true"``, or
``"permission=ADMIN"`` are inert data that cannot construct them and cannot
influence any decision.

Authority boundary
------------------

``ACTIVE`` is a lifecycle status, never authority. A successful promotion does
not grant Permission, authorize any run, bypass the ActionGate, reduce
RiskLevel, enlarge a ResourceBudget, clear an EmergencyStop, satisfy
environment applicability, mark a Task successful, or provide verification.
The module imports no kernel contract and executes nothing; execution still
goes through the normal governed paths.

Purity and determinism
----------------------

The transaction reads a clock only through the caller-supplied
``requested_at`` instant (which also becomes the persisted ``updated_at``),
performs no model call, no research, no capability execution, and emits one
bounded, deterministic result value whose explanation is composed exclusively
from canonical enum values and identities — never from payload or evidence
text. Storage errors that are infrastructure failures (not decisions) are
raised, not swallowed; every deterministic outcome is a typed result.

Owner: N2.10. Belongs to the ``agentx`` namespace root as a top-level
composition module (alongside ``agentx.procedure_validation``), because it
must compose ``agentx.core`` lifecycle/record contracts, the M4.02 evidence
report, and the C2.03 store without widening the subsystem boundary manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from agentx.core.ids import ProcedureId
from agentx.core.procedure_lifecycle import (
    ProcedureLifecycleAssessment,
    ProcedureLifecycleDecision,
    ProcedureLifecycleReason,
    assess_procedure_transition,
)
from agentx.core.procedures import (
    ProcedureRecord,
    ProcedureStatus,
    ProcedureValidationError,
)
from agentx.infrastructure.procedure_store import (
    ProcedureNotFoundError,
    ProcedureStaleRecordError,
    ProcedureStore,
)
from agentx.procedure_validation import ValidationDecision, ValidationReport

__all__ = [
    "ProcedurePromotionError",
    "ProcedurePromotionOutcome",
    "ProcedurePromotionRequest",
    "ProcedurePromotionResult",
    "promote_procedure_candidate",
]


class ProcedurePromotionError(ProcedureValidationError):
    """Raised when a promotion request is malformed or wrongly typed.

    Malformed input is a violated contract, not a review outcome: it fails
    closed with this exception and nothing is read, decided, or written. A
    well-typed but ineligible request is *not* an error — it comes back as a
    typed :class:`ProcedurePromotionResult` with an explicit rejection
    outcome.
    """


class ProcedurePromotionOutcome(StrEnum):
    """Closed, bounded outcome vocabulary of one promotion transaction.

    ``PROMOTED`` is the single positive outcome: the revision's persisted
    status is now ``ACTIVE`` — inert data with zero execution authority.
    Every ``REJECTED_*`` member is fail-closed: nothing was written.

    Members:

        PROMOTED — the compare-and-set status write committed; the revision
            is now ``ACTIVE``.
        REJECTED_PROCEDURE_ABSENT — no stored revision for the requested
            ``(procedure_id, revision)`` pair.
        REJECTED_IDENTITY_MISMATCH — the request's ``ProcedureId`` differs
            from the supplied expected-current record's identity.
        REJECTED_REVISION_MISMATCH — the request's revision differs from the
            supplied expected-current record's revision.
        REJECTED_VALIDATION_EVIDENCE_FOREIGN — the validation report is bound
            to another procedure identity or another revision.
        REJECTED_VALIDATION_NOT_ELIGIBLE — the canonical validation decision
            is negative, insufficient, or degraded; it is never
            ``ELIGIBLE_FOR_PROMOTION``.
        REJECTED_LIFECYCLE_DECISION_INVALID — the supplied lifecycle
            assessment does not describe ``CANDIDATE -> ACTIVE`` for reason
            ``VALIDATION_PROMOTION`` on this identity, or is not ``ALLOWED``.
        REJECTED_REPEATED_PROMOTION — the stored revision is already
            ``ACTIVE``; the canonical policy reports ``NO_OP``, and a no-op
            never permits a status write.
        REJECTED_RETIRED_TERMINAL — the stored revision is ``RETIRED``;
            retirement is terminal and monotonic, never resurrected.
        REJECTED_LIFECYCLE_REJECTED — the canonical lifecycle policy rejected
            the transition for the stored status (fail-closed catch-all that
            cannot fire for the current closed vocabulary).
        REJECTED_STALE_CURRENT_RECORD — the stored record no longer equals
            the record the request was validated against (stale request, or
            a concurrent writer won the race); the compare-and-set write was
            refused and nothing was persisted.
    """

    PROMOTED = "promoted"
    REJECTED_PROCEDURE_ABSENT = "rejected_procedure_absent"
    REJECTED_IDENTITY_MISMATCH = "rejected_identity_mismatch"
    REJECTED_REVISION_MISMATCH = "rejected_revision_mismatch"
    REJECTED_VALIDATION_EVIDENCE_FOREIGN = "rejected_validation_evidence_foreign"
    REJECTED_VALIDATION_NOT_ELIGIBLE = "rejected_validation_not_eligible"
    REJECTED_LIFECYCLE_DECISION_INVALID = "rejected_lifecycle_decision_invalid"
    REJECTED_REPEATED_PROMOTION = "rejected_repeated_promotion"
    REJECTED_RETIRED_TERMINAL = "rejected_retired_terminal"
    REJECTED_LIFECYCLE_REJECTED = "rejected_lifecycle_rejected"
    REJECTED_STALE_CURRENT_RECORD = "rejected_stale_current_record"


@dataclass(frozen=True, slots=True)
class ProcedurePromotionRequest:
    """The complete, canonical input set of one promotion transaction.

    Every field is a canonical typed value; there is deliberately no boolean
    and no free-text approval channel. ``expected_current`` is the exact
    stored record the caller validated — the transaction refuses to act if
    persistence no longer holds exactly that record. ``requested_at`` is the
    explicit, caller-supplied instant of the reviewed promotion request; it
    is normalized to UTC, recorded on the result, and persisted as the
    revision's ``updated_at``. It is historical data, never authority.
    """

    procedure_id: ProcedureId
    revision: int
    expected_current: ProcedureRecord
    validation_report: ValidationReport
    lifecycle_assessment: ProcedureLifecycleAssessment
    requested_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, ProcedureId):
            raise ProcedurePromotionError("procedure_id must be a canonical ProcedureId")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool):
            raise ProcedurePromotionError("revision must be an integer")
        if self.revision < 1:
            raise ProcedurePromotionError(
                "revision must be a positive integer (first revision is 1)"
            )
        if not isinstance(self.expected_current, ProcedureRecord):
            raise ProcedurePromotionError("expected_current must be a canonical ProcedureRecord")
        if not isinstance(self.validation_report, ValidationReport):
            raise ProcedurePromotionError(
                "validation_report must be a canonical ValidationReport; raw booleans "
                "and free-text approvals are not promotion evidence"
            )
        if not isinstance(self.lifecycle_assessment, ProcedureLifecycleAssessment):
            raise ProcedurePromotionError(
                "lifecycle_assessment must be a canonical ProcedureLifecycleAssessment"
            )
        object.__setattr__(
            self,
            "requested_at",
            _require_timestamp(self.requested_at),
        )


@dataclass(frozen=True, slots=True)
class ProcedurePromotionResult:
    """Immutable, fully typed outcome of one promotion transaction.

    The result makes the transaction explicit: whether promotion occurred,
    for exactly which ``(procedure_id, revision)``, the prior and resulting
    lifecycle states, and — for every rejection — which canonical decision
    caused it, with a bounded explanation composed only from canonical enum
    values and identities. It is data, never authority: a ``PROMOTED`` result
    grants no Permission and executes nothing. The same request against the
    same persisted state always yields an equal result.
    """

    outcome: ProcedurePromotionOutcome
    procedure_id: ProcedureId
    revision: int
    prior_status: ProcedureStatus | None
    resulting_status: ProcedureStatus | None
    promoted_record: ProcedureRecord | None
    validation_decision: ValidationDecision | None
    lifecycle_decision: ProcedureLifecycleDecision | None
    explanation: str

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ProcedurePromotionOutcome):
            raise ProcedurePromotionError("outcome must be a ProcedurePromotionOutcome")
        if not isinstance(self.procedure_id, ProcedureId):
            raise ProcedurePromotionError("procedure_id must be a canonical ProcedureId")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool):
            raise ProcedurePromotionError("revision must be an integer")
        if self.revision < 1:
            raise ProcedurePromotionError(
                "revision must be a positive integer (first revision is 1)"
            )
        for field_name in ("prior_status", "resulting_status"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, ProcedureStatus):
                raise ProcedurePromotionError(f"{field_name} must be a ProcedureStatus or None")
        if self.promoted_record is not None and not isinstance(
            self.promoted_record, ProcedureRecord
        ):
            raise ProcedurePromotionError("promoted_record must be a ProcedureRecord or None")
        if self.validation_decision is not None and not isinstance(
            self.validation_decision, ValidationDecision
        ):
            raise ProcedurePromotionError(
                "validation_decision must be a ValidationDecision or None"
            )
        if self.lifecycle_decision is not None and not isinstance(
            self.lifecycle_decision, ProcedureLifecycleDecision
        ):
            raise ProcedurePromotionError(
                "lifecycle_decision must be a ProcedureLifecycleDecision or None"
            )
        if not isinstance(self.explanation, str) or not self.explanation.strip():
            raise ProcedurePromotionError("explanation must be a non-empty string")
        if self.outcome is ProcedurePromotionOutcome.PROMOTED:
            if self.prior_status is not ProcedureStatus.CANDIDATE:
                raise ProcedurePromotionError("a promoted result's prior status is CANDIDATE")
            if self.resulting_status is not ProcedureStatus.ACTIVE:
                raise ProcedurePromotionError("a promoted result's resulting status is ACTIVE")
            if (
                self.promoted_record is None
                or self.promoted_record.procedure_id != self.procedure_id
                or self.promoted_record.revision != self.revision
                or self.promoted_record.status is not ProcedureStatus.ACTIVE
            ):
                raise ProcedurePromotionError(
                    "a promoted result carries the exact persisted ACTIVE record"
                )
        else:
            if self.resulting_status is not None or self.promoted_record is not None:
                raise ProcedurePromotionError(
                    "only a promoted result carries a resulting status or record"
                )

    @property
    def promoted(self) -> bool:
        """``True`` only for :attr:`ProcedurePromotionOutcome.PROMOTED`.

        A convenience projection; the authoritative value is always the
        ``outcome`` enum, never this bool.
        """
        return self.outcome is ProcedurePromotionOutcome.PROMOTED


def promote_procedure_candidate(
    store: ProcedureStore,
    request: ProcedurePromotionRequest,
) -> ProcedurePromotionResult:
    """Apply one reviewed promotion to canonical persistence, or fail closed.

    Deterministic decision order (fixed; the first failure wins and nothing
    is written):

        1. The store and the request are type-validated; malformed input
           raises :class:`ProcedurePromotionError`.
        2. Request-internal identity coherence: ``procedure_id`` and
           ``revision`` must match the supplied expected-current record.
        3. The validation report must be bound to exactly the requested
           ``(procedure_id, revision)``.
        4. The validation report's canonical decision must be
           ``ELIGIBLE_FOR_PROMOTION``.
        5. The lifecycle assessment must describe exactly
           ``CANDIDATE -> ACTIVE`` for reason ``VALIDATION_PROMOTION`` on
           this identity with decision ``ALLOWED``, and the canonical policy
           must independently re-derive that same verdict.
        6. The stored record must exist and must equal the expected-current
           record exactly (stale requests are rejected, never refreshed).
        7. The canonical lifecycle policy must permit the stored status's
           move to ``ACTIVE`` for ``VALIDATION_PROMOTION`` (an already-ACTIVE
           revision is a typed repeated-promotion rejection, a RETIRED
           revision is terminal).
        8. The status write commits through the store's compare-and-set
           (``update_status_if_current``) inside one ``BEGIN IMMEDIATE``
           transaction: if any concurrent writer changed the row after step
           6, the write is refused with
           :data:`ProcedurePromotionOutcome.REJECTED_STALE_CURRENT_RECORD`
           and nothing is persisted.

    Returns a typed :class:`ProcedurePromotionResult` in every decided case.
    Infrastructure storage failures are raised (the store has already rolled
    back, so they leave persistence untouched). The function never deletes a
    revision, never rewrites history, never touches validation evidence,
    never executes anything, and never changes any authority state.

    Raises:
        ProcedurePromotionError: if ``store`` or any request field has the
            wrong type.
    """
    if not isinstance(store, ProcedureStore):
        raise ProcedurePromotionError("store must be a canonical ProcedureStore")
    _require_request(request)

    identity = request.procedure_id.to_str()

    # 2. Request-internal identity coherence.
    if request.expected_current.procedure_id != request.procedure_id:
        return _rejection(
            ProcedurePromotionOutcome.REJECTED_IDENTITY_MISMATCH,
            request,
            prior_status=request.expected_current.status,
            explanation=(
                f"request procedure_id {identity} does not match the expected current "
                f"record procedure_id {request.expected_current.procedure_id.to_str()}; "
                "nothing written"
            ),
        )
    if request.expected_current.revision != request.revision:
        return _rejection(
            ProcedurePromotionOutcome.REJECTED_REVISION_MISMATCH,
            request,
            prior_status=request.expected_current.status,
            explanation=(
                f"request revision {request.revision} does not match the expected "
                f"current record revision {request.expected_current.revision}; "
                "nothing written"
            ),
        )

    # 3. The eligibility evidence must be bound to exactly this revision.
    report = request.validation_report
    if (
        report.candidate.procedure_id != request.procedure_id
        or report.candidate.revision != request.revision
    ):
        return _rejection(
            ProcedurePromotionOutcome.REJECTED_VALIDATION_EVIDENCE_FOREIGN,
            request,
            prior_status=request.expected_current.status,
            validation_decision=report.decision,
            explanation=(
                "validation report is bound to procedure "
                f"{report.candidate.procedure_id.to_str()} revision "
                f"{report.candidate.revision}, not the requested procedure "
                f"{identity} revision {request.revision}; nothing written"
            ),
        )

    # 4. The eligibility decision must be the single positive outcome.
    if report.decision is not ValidationDecision.ELIGIBLE_FOR_PROMOTION:
        return _rejection(
            ProcedurePromotionOutcome.REJECTED_VALIDATION_NOT_ELIGIBLE,
            request,
            prior_status=request.expected_current.status,
            validation_decision=report.decision,
            explanation=(
                f"validation decision {report.decision.value} is not "
                f"{ValidationDecision.ELIGIBLE_FOR_PROMOTION.value}; eligibility "
                "evidence never mutates persistence; nothing written"
            ),
        )

    # 5. The lifecycle assessment must be the canonical ALLOWED promotion.
    assessment = request.lifecycle_assessment
    describes_this_promotion = (
        assessment.procedure_id == request.procedure_id
        and assessment.revision == request.revision
        and assessment.current_status is ProcedureStatus.CANDIDATE
        and assessment.target_status is ProcedureStatus.ACTIVE
        and assessment.reason is ProcedureLifecycleReason.VALIDATION_PROMOTION
    )
    canonical_assessment = assess_procedure_transition(
        procedure_id=request.procedure_id,
        revision=request.revision,
        current_status=ProcedureStatus.CANDIDATE,
        target_status=ProcedureStatus.ACTIVE,
        reason=ProcedureLifecycleReason.VALIDATION_PROMOTION,
        requested_at=request.requested_at,
    )
    if (
        not describes_this_promotion
        or assessment.decision is not ProcedureLifecycleDecision.ALLOWED
        or canonical_assessment.decision is not ProcedureLifecycleDecision.ALLOWED
    ):
        return _rejection(
            ProcedurePromotionOutcome.REJECTED_LIFECYCLE_DECISION_INVALID,
            request,
            prior_status=request.expected_current.status,
            lifecycle_decision=assessment.decision,
            explanation=(
                f"lifecycle assessment {assessment.decision.value} is not "
                f"{ProcedureLifecycleDecision.ALLOWED.value} candidate->active for "
                f"reason {ProcedureLifecycleReason.VALIDATION_PROMOTION.value} on "
                f"procedure {identity} revision {request.revision}; nothing written"
            ),
        )

    # 6. The stored record must still be exactly the validated record.
    stored = store.get(request.procedure_id, request.revision)
    if stored is None:
        return _rejection(
            ProcedurePromotionOutcome.REJECTED_PROCEDURE_ABSENT,
            request,
            prior_status=None,
            explanation=(
                f"no stored procedure {identity} revision {request.revision}; nothing written"
            ),
        )
    if stored != request.expected_current:
        return _rejection(
            ProcedurePromotionOutcome.REJECTED_STALE_CURRENT_RECORD,
            request,
            prior_status=stored.status,
            explanation=(
                f"stored procedure {identity} revision {request.revision} changed since "
                "the request was validated; stale requests are rejected, never "
                "refreshed; nothing written"
            ),
        )

    # 7. The canonical policy must permit the stored status's move to ACTIVE.
    stored_assessment = assess_procedure_transition(
        procedure_id=request.procedure_id,
        revision=request.revision,
        current_status=stored.status,
        target_status=ProcedureStatus.ACTIVE,
        reason=ProcedureLifecycleReason.VALIDATION_PROMOTION,
        requested_at=request.requested_at,
    )
    if stored_assessment.decision is ProcedureLifecycleDecision.NO_OP:
        return _rejection(
            ProcedurePromotionOutcome.REJECTED_REPEATED_PROMOTION,
            request,
            prior_status=stored.status,
            lifecycle_decision=stored_assessment.decision,
            explanation=(
                f"stored status {ProcedureStatus.ACTIVE.value} equals the requested "
                f"target {ProcedureStatus.ACTIVE.value}; the canonical policy reports "
                "no_op and a no-op never permits a status write; nothing written"
            ),
        )
    if stored_assessment.decision is ProcedureLifecycleDecision.REJECTED_RETIREMENT_IS_TERMINAL:
        return _rejection(
            ProcedurePromotionOutcome.REJECTED_RETIRED_TERMINAL,
            request,
            prior_status=stored.status,
            lifecycle_decision=stored_assessment.decision,
            explanation=(
                f"stored status {stored.status.value} is terminal; retirement is "
                "monotonic and a retired revision is never resurrected; nothing written"
            ),
        )
    if stored_assessment.decision is not ProcedureLifecycleDecision.ALLOWED:
        return _rejection(
            ProcedurePromotionOutcome.REJECTED_LIFECYCLE_REJECTED,
            request,
            prior_status=stored.status,
            lifecycle_decision=stored_assessment.decision,
            explanation=(
                f"lifecycle policy rejected {stored.status.value}->"
                f"{ProcedureStatus.ACTIVE.value} for reason "
                f"{ProcedureLifecycleReason.VALIDATION_PROMOTION.value}; nothing written"
            ),
        )

    # 8. Atomic compare-and-set commit.
    try:
        promoted_record = store.update_status_if_current(
            request.procedure_id,
            request.revision,
            ProcedureStatus.ACTIVE,
            expected_current=stored,
            updated_at=request.requested_at,
        )
    except ProcedureStaleRecordError:
        return _rejection(
            ProcedurePromotionOutcome.REJECTED_STALE_CURRENT_RECORD,
            request,
            prior_status=stored.status,
            explanation=(
                f"stored procedure {identity} revision {request.revision} changed "
                "between the read and the write; the compare-and-set write was "
                "refused; re-read and re-validate; nothing written"
            ),
        )
    except ProcedureNotFoundError:
        return _rejection(
            ProcedurePromotionOutcome.REJECTED_PROCEDURE_ABSENT,
            request,
            prior_status=None,
            explanation=(
                f"no stored procedure {identity} revision {request.revision} at write "
                "time; nothing written"
            ),
        )

    return ProcedurePromotionResult(
        outcome=ProcedurePromotionOutcome.PROMOTED,
        procedure_id=request.procedure_id,
        revision=request.revision,
        prior_status=ProcedureStatus.CANDIDATE,
        resulting_status=ProcedureStatus.ACTIVE,
        promoted_record=promoted_record,
        validation_decision=report.decision,
        lifecycle_decision=ProcedureLifecycleDecision.ALLOWED,
        explanation=(
            f"promotion committed: procedure {identity} revision {request.revision} "
            f"moved {ProcedureStatus.CANDIDATE.value} -> {ProcedureStatus.ACTIVE.value} "
            f"for reason {ProcedureLifecycleReason.VALIDATION_PROMOTION.value}; the "
            "active record remains inert data with zero execution authority"
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------


def _require_request(request: ProcedurePromotionRequest) -> None:
    if not isinstance(request, ProcedurePromotionRequest):
        raise ProcedurePromotionError("request must be a canonical ProcedurePromotionRequest")


def _require_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ProcedurePromotionError("requested_at must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProcedurePromotionError("requested_at must be timezone-aware")
    return value.astimezone(UTC)


def _rejection(
    outcome: ProcedurePromotionOutcome,
    request: ProcedurePromotionRequest,
    *,
    prior_status: ProcedureStatus | None,
    explanation: str,
    validation_decision: ValidationDecision | None = None,
    lifecycle_decision: ProcedureLifecycleDecision | None = None,
) -> ProcedurePromotionResult:
    """Assemble one deterministic, bounded rejection result."""
    return ProcedurePromotionResult(
        outcome=outcome,
        procedure_id=request.procedure_id,
        revision=request.revision,
        prior_status=prior_status,
        resulting_status=None,
        promoted_record=None,
        validation_decision=validation_decision,
        lifecycle_decision=lifecycle_decision,
        explanation=explanation,
    )
