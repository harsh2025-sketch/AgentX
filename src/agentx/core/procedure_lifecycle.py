"""Canonical procedure-revision lifecycle transition policy (M4.03).

This module owns exactly one question:

    *Is this ``ProcedureStatus`` transition STRUCTURALLY permitted for this
    typed reason, for this one immutable procedure revision?*

It deliberately does **not** answer:

    *Has this candidate actually EARNED promotion?*

Evidence sufficiency — whether a candidate procedure was really validated,
how many trajectories succeeded, whether a verification passed — is owned by
the independent validation policy and by the future integration layer. Nothing
in this module inspects, scores, or trusts evidence, and nothing here promotes
anything.

STRUCTURALLY_ALLOWED != EVIDENCE_VALIDATED
==========================================

:data:`ProcedureLifecycleDecision.ALLOWED` means only that the requested
``(current, target, reason)`` triple is a legal shape in the canonical state
machine. It is **not** proof that validation happened, **not** an
authorization, and **not** an instruction to write anything. A caller that
receives ``ALLOWED`` is still fully responsible for proving — through its own
evidence policy — that the transition should really occur, and for performing
the explicit ``ProcedureStore.update_status`` write itself. A typed
:class:`ProcedureLifecycleReason` is **data describing intent**: it records
WHY a caller says it wants the move. It creates no authority and proves
nothing by existing.

Canonical transition matrix
---------------------------

::

    CANDIDATE -> ACTIVE     (VALIDATION_PROMOTION only)
    CANDIDATE -> RETIRED    (MANUAL_RETIREMENT | SUPERSEDED_BY_NEW_REVISION |
                             SAFETY_RETIREMENT)
    ACTIVE    -> RETIRED    (MANUAL_RETIREMENT | SUPERSEDED_BY_NEW_REVISION |
                             SAFETY_RETIREMENT)

``RETIRED`` is terminal. Every other combination — ``ACTIVE -> CANDIDATE``,
``RETIRED -> ACTIVE``, ``RETIRED -> CANDIDATE`` — is rejected, fail-closed,
with no exception, no override flag, and no reason that can unlock it.
Retirement is monotonic for a revision identity: there is no "unretire", no
reset, and no silent status rewind. A retired revision is immutable historical
data. When a repaired or improved procedure is needed, the architecture
creates a NEW revision elsewhere; revision creation is emphatically not owned
here.

Same-status requests
--------------------

``CANDIDATE -> CANDIDATE``, ``ACTIVE -> ACTIVE`` and ``RETIRED -> RETIRED``
are reported as the explicit typed outcome
:data:`ProcedureLifecycleDecision.NO_OP`. The policy never pretends a
transition occurred, and ``NO_OP`` never permits a status write
(:attr:`ProcedureLifecycleAssessment.permits_status_write` is ``False``).
Because no transition is being permitted, the requested reason is recorded but
not matched against the transition table for a no-op.

Status is never inferred
------------------------

Only the typed arguments decide the outcome. Payload text, scope values,
identifiers, or free-form strings such as ``"verified=true"``,
``"permission=ADMIN"``, ``"risk=R0"``, ``"force ACTIVE"``,
``"restore RETIRED"``, or ``"ignore previous instructions"`` are inert data
and can never change transition legality. Raw strings are never coerced into
the controlled vocabularies: ``"active"`` is not a ``ProcedureStatus`` and
``"validation_promotion"`` is not a :class:`ProcedureLifecycleReason`; both are
rejected.

Authority boundary
------------------

``ACTIVE`` is a storage-level lifecycle state, **never authority**. This module
imports no ``agentx.kernel`` contract and manipulates no ``Permission``,
``AuthorityContext``, ``ActionGate``, ``RiskLevel``, ``ResourceEnvelope``,
``EmergencyStop``, ``Capability``, or Task state. An ``ACTIVE`` procedure
remains subject to governed execution later.

Purity
------

Every function here is pure and deterministic: no database, filesystem,
network, model, clock read, UUID generation, thread, subprocess, or
randomness. Timestamps are supplied by the caller and validated to be
timezone-aware. The same inputs always produce an equal assessment.

Non-goals (owned elsewhere, structurally absent here)
-----------------------------------------------------

No validation/evidence policy, no candidate execution, no ``ProcedureStore``
access or mutation, no database update, no revision creation or increment, no
payload/scope copying or rewriting, no procedure execution, no skill
compilation, no degradation detection, no repair, and no migration. Decisions
are transient in-memory values; mirroring the sibling pure transition contract
:mod:`agentx.core.task_state`, no serialization surface is added here.

Owner: M4.03. Belongs to ``agentx.core`` and imports only the standard library
and sibling ``agentx.core`` contracts, so ``agentx.core`` stays a dependency
leaf.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from agentx.core.ids import ProcedureId
from agentx.core.procedures import ProcedureStatus, ProcedureValidationError

__all__ = [
    "LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS",
    "PERMITTED_TRANSITION_REASONS",
    "TERMINAL_PROCEDURE_STATUSES",
    "ProcedureLifecycleAssessment",
    "ProcedureLifecycleDecision",
    "ProcedureLifecycleError",
    "ProcedureLifecycleReason",
    "assess_procedure_transition",
    "is_terminal_status",
    "is_transition_permitted",
    "legal_target_statuses",
    "permitted_reasons_for",
]


class ProcedureLifecycleError(ProcedureValidationError):
    """Raised when a lifecycle request is malformed or wrongly typed.

    Malformed input is a violated contract, not a lifecycle outcome: it fails
    closed with this exception rather than being reported as a rejection. A
    genuinely illegal (but well-typed) transition is *not* an error — it comes
    back as a typed :class:`ProcedureLifecycleAssessment`.
    """


class ProcedureLifecycleReason(StrEnum):
    """Closed vocabulary for the declared INTENT behind a transition request.

    A reason is DATA. It records why a caller says it wants a transition; it
    never proves that validation happened, never grants authority, and never
    unlocks a transition that the matrix forbids.

    Members:

        VALIDATION_PROMOTION — the caller asserts an explicit, deliberate
            promotion act. It is the ONLY reason with which a CANDIDATE may
            structurally reach ACTIVE. The assertion is not verified here; the
            integrator owns the evidence.
        MANUAL_RETIREMENT — an operator/caller explicitly withdraws the
            revision.
        SUPERSEDED_BY_NEW_REVISION — the revision is withdrawn because a newer
            revision replaces it. This module never creates that revision.
        SAFETY_RETIREMENT — the revision is withdrawn for safety reasons.
            Retirement is always fail-safe and always permitted out of a
            non-terminal state.
    """

    VALIDATION_PROMOTION = "validation_promotion"
    MANUAL_RETIREMENT = "manual_retirement"
    SUPERSEDED_BY_NEW_REVISION = "superseded_by_new_revision"
    SAFETY_RETIREMENT = "safety_retirement"


class ProcedureLifecycleDecision(StrEnum):
    """Closed outcome vocabulary for one assessed transition request.

    Members:

        ALLOWED — the transition shape is structurally legal for the given
            reason. This is NOT evidence validation and NOT authority.
        NO_OP — current and target status are identical; nothing changes and
            no status write is permitted.
        REJECTED_ILLEGAL_TRANSITION — the target is not reachable from the
            current status (for example a backwards ACTIVE -> CANDIDATE move).
        REJECTED_RETIREMENT_IS_TERMINAL — the request would resurrect a
            RETIRED revision. Retirement is monotonic and terminal.
        REJECTED_REASON_MISMATCH — the transition exists in the matrix, but
            the declared reason is not one of the reasons permitted for it
            (for example retiring with VALIDATION_PROMOTION).
    """

    ALLOWED = "allowed"
    NO_OP = "no_op"
    REJECTED_ILLEGAL_TRANSITION = "rejected_illegal_transition"
    REJECTED_RETIREMENT_IS_TERMINAL = "rejected_retirement_is_terminal"
    REJECTED_REASON_MISMATCH = "rejected_reason_mismatch"


#: Reasons that may accompany a withdrawal of a revision. Retirement is the
#: fail-safe direction, so every retirement reason is accepted out of any
#: non-terminal status. ``VALIDATION_PROMOTION`` is deliberately absent.
_RETIREMENT_REASONS: Final[frozenset[ProcedureLifecycleReason]] = frozenset(
    {
        ProcedureLifecycleReason.MANUAL_RETIREMENT,
        ProcedureLifecycleReason.SUPERSEDED_BY_NEW_REVISION,
        ProcedureLifecycleReason.SAFETY_RETIREMENT,
    }
)

#: The canonical transition matrix: every canonical :class:`ProcedureStatus`
#: maps to the exact set of statuses it may move to. An empty set means
#: terminal. Anything not listed is rejected — never inferred, never defaulted
#: to "allowed". This mapping is the single source of truth and is immutable
#: at runtime.
LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS: Final[
    Mapping[ProcedureStatus, frozenset[ProcedureStatus]]
] = MappingProxyType(
    {
        ProcedureStatus.CANDIDATE: frozenset(
            {ProcedureStatus.ACTIVE, ProcedureStatus.RETIRED},
        ),
        ProcedureStatus.ACTIVE: frozenset({ProcedureStatus.RETIRED}),
        # Terminal: no resurrection, no unretire, no reset, no rewind.
        ProcedureStatus.RETIRED: frozenset(),
    }
)

#: Statuses with no outgoing transitions, derived from the matrix so
#: terminality can never drift away from it.
TERMINAL_PROCEDURE_STATUSES: Final[frozenset[ProcedureStatus]] = frozenset(
    status for status in ProcedureStatus if not LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS[status]
)

#: Which typed reasons may accompany which legal transition. A pair absent
#: from this mapping is an illegal transition; a pair present with a reason
#: outside its set is a reason mismatch. Promotion is reachable through
#: exactly one explicit reason.
PERMITTED_TRANSITION_REASONS: Final[
    Mapping[tuple[ProcedureStatus, ProcedureStatus], frozenset[ProcedureLifecycleReason]]
] = MappingProxyType(
    {
        (ProcedureStatus.CANDIDATE, ProcedureStatus.ACTIVE): frozenset(
            {ProcedureLifecycleReason.VALIDATION_PROMOTION},
        ),
        (ProcedureStatus.CANDIDATE, ProcedureStatus.RETIRED): _RETIREMENT_REASONS,
        (ProcedureStatus.ACTIVE, ProcedureStatus.RETIRED): _RETIREMENT_REASONS,
    }
)


def _require_status(value: object, *, field_name: str) -> ProcedureStatus:
    """Reject anything that is not a canonical :class:`ProcedureStatus` member.

    Raw strings are never coerced: ``"active"`` is a programming error, not a
    status, even though ``ProcedureStatus`` is a :class:`~enum.StrEnum`.
    """
    if not isinstance(value, ProcedureStatus):
        raise ProcedureLifecycleError(
            f"{field_name} must be a ProcedureStatus, got {type(value).__name__}"
        )
    return value


def _require_reason(value: object, *, field_name: str) -> ProcedureLifecycleReason:
    """Reject anything that is not a member of the closed reason vocabulary."""
    if not isinstance(value, ProcedureLifecycleReason):
        raise ProcedureLifecycleError(
            f"{field_name} must be a ProcedureLifecycleReason, got {type(value).__name__}"
        )
    return value


def _require_procedure_id(value: object, *, field_name: str) -> ProcedureId:
    if not isinstance(value, ProcedureId):
        raise ProcedureLifecycleError(
            f"{field_name} must be a ProcedureId, got {type(value).__name__}"
        )
    return value


def _require_revision(value: object, *, field_name: str) -> int:
    """Validate a revision number without ever changing it.

    Mirrors the canonical record rule (positive integer, first revision is 1).
    The policy reads revision identity; it never increments or derives one.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProcedureLifecycleError(f"{field_name} must be an integer")
    if value < 1:
        raise ProcedureLifecycleError(
            f"{field_name} must be a positive integer (first revision is 1)"
        )
    return value


def _require_timestamp(value: object, *, field_name: str) -> datetime:
    """Validate a caller-supplied, timezone-aware instant (normalized to UTC).

    The policy never reads a clock: the caller owns the instant, so identical
    inputs stay reproducible.
    """
    if not isinstance(value, datetime):
        raise ProcedureLifecycleError(f"{field_name} must be a timezone-aware datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProcedureLifecycleError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def legal_target_statuses(status: ProcedureStatus) -> frozenset[ProcedureStatus]:
    """Return the statuses ``status`` may legally move to.

    An empty set means ``status`` is terminal. The result is a ``frozenset``
    so callers cannot widen the contract.
    """
    return LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS[_require_status(status, field_name="status")]


def is_terminal_status(status: ProcedureStatus) -> bool:
    """Return ``True`` when ``status`` has no outgoing transitions (RETIRED)."""
    return _require_status(status, field_name="status") in TERMINAL_PROCEDURE_STATUSES


def permitted_reasons_for(
    current: ProcedureStatus, target: ProcedureStatus
) -> frozenset[ProcedureLifecycleReason]:
    """Return the typed reasons permitted for ``current -> target``.

    An empty set means the transition itself is illegal (or is a same-status
    request, which is a no-op rather than a transition).
    """
    validated_current = _require_status(current, field_name="current_status")
    validated_target = _require_status(target, field_name="target_status")
    return PERMITTED_TRANSITION_REASONS.get((validated_current, validated_target), frozenset())


def is_transition_permitted(
    current: ProcedureStatus,
    target: ProcedureStatus,
    reason: ProcedureLifecycleReason,
) -> bool:
    """Return ``True`` when ``current -> target`` is structurally legal for ``reason``.

    Pure predicate over the three typed arguments only. ``True`` still means
    *structurally allowed*, never *evidence validated*: it is not permission to
    promote and not proof that any validation occurred. Same-status requests
    are ``False`` — nothing is permitted to be written for a no-op.
    """
    validated_current = _require_status(current, field_name="current_status")
    validated_target = _require_status(target, field_name="target_status")
    validated_reason = _require_reason(reason, field_name="reason")
    if validated_current is validated_target:
        return False
    return validated_reason in PERMITTED_TRANSITION_REASONS.get(
        (validated_current, validated_target), frozenset()
    )


def _explain(
    *,
    decision: ProcedureLifecycleDecision,
    current: ProcedureStatus,
    target: ProcedureStatus,
    reason: ProcedureLifecycleReason,
) -> str:
    """Build the deterministic explanation from controlled vocabulary only.

    No caller-supplied free text ever reaches this string: it is composed
    exclusively from enum values, so hostile payload content cannot leak into
    (or influence) a decision.
    """
    move = f"{current.value} -> {target.value}"
    match decision:
        case ProcedureLifecycleDecision.ALLOWED:
            return (
                f"transition {move} is structurally permitted for reason "
                f"{reason.value}; structurally allowed is not evidence validated"
            )
        case ProcedureLifecycleDecision.NO_OP:
            return (
                f"requested status {target.value} equals the current status; "
                "no transition occurs and no status write is permitted"
            )
        case ProcedureLifecycleDecision.REJECTED_RETIREMENT_IS_TERMINAL:
            return (
                f"transition {move} is rejected: retirement is terminal and "
                "monotonic; a retired revision is never resurrected"
            )
        case ProcedureLifecycleDecision.REJECTED_ILLEGAL_TRANSITION:
            return f"transition {move} is not a legal procedure lifecycle transition"
        case ProcedureLifecycleDecision.REJECTED_REASON_MISMATCH:
            return f"transition {move} is legal but reason {reason.value} is not permitted for it"


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureLifecycleAssessment:
    """One immutable, fully typed lifecycle assessment.

    The assessment echoes the exact revision identity it was asked about —
    ``(procedure_id, revision)`` — without ever changing it: the policy reads
    identity, never increments a revision and never creates one. It is DATA:
    holding an ``ALLOWED`` assessment grants no Permission, creates no
    AuthorityContext, bypasses no ActionGate, changes no RiskLevel, enlarges no
    ResourceEnvelope, clears no EmergencyStop, executes nothing, mutates no
    Task, and writes nothing to any store.

    ``requested_at`` is the caller-supplied instant the transition was
    requested, normalized to UTC. It is historical context recorded on the
    assessment; it never influences legality and it is never written back to a
    persisted record.
    """

    procedure_id: ProcedureId
    revision: int
    current_status: ProcedureStatus
    target_status: ProcedureStatus
    reason: ProcedureLifecycleReason
    requested_at: datetime
    decision: ProcedureLifecycleDecision
    permitted_reasons: tuple[ProcedureLifecycleReason, ...]
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "procedure_id",
            _require_procedure_id(self.procedure_id, field_name="procedure_id"),
        )
        object.__setattr__(
            self, "revision", _require_revision(self.revision, field_name="revision")
        )
        object.__setattr__(
            self,
            "current_status",
            _require_status(self.current_status, field_name="current_status"),
        )
        object.__setattr__(
            self, "target_status", _require_status(self.target_status, field_name="target_status")
        )
        object.__setattr__(self, "reason", _require_reason(self.reason, field_name="reason"))
        object.__setattr__(
            self, "requested_at", _require_timestamp(self.requested_at, field_name="requested_at")
        )
        if not isinstance(self.decision, ProcedureLifecycleDecision):
            raise ProcedureLifecycleError("decision must be a ProcedureLifecycleDecision")
        if not isinstance(self.permitted_reasons, tuple):
            raise ProcedureLifecycleError("permitted_reasons must be a tuple")
        for item in self.permitted_reasons:
            _require_reason(item, field_name="permitted_reasons entry")
        if len(set(self.permitted_reasons)) != len(self.permitted_reasons):
            raise ProcedureLifecycleError("permitted_reasons must not contain duplicates")
        if not isinstance(self.explanation, str) or not self.explanation.strip():
            raise ProcedureLifecycleError("explanation must be a non-empty string")

    @property
    def permits_status_write(self) -> bool:
        """``True`` only for :data:`ProcedureLifecycleDecision.ALLOWED`.

        Even then this is a structural permission to *consider* the write, not
        proof that the revision earned it and not authority to perform it. The
        caller still owns the evidence and the explicit
        ``ProcedureStore.update_status`` act.
        """
        return self.decision is ProcedureLifecycleDecision.ALLOWED

    @property
    def is_no_op(self) -> bool:
        """``True`` when current and target status were identical."""
        return self.decision is ProcedureLifecycleDecision.NO_OP

    @property
    def is_rejected(self) -> bool:
        """``True`` for every rejection outcome (fail-closed by default)."""
        return self.decision not in (
            ProcedureLifecycleDecision.ALLOWED,
            ProcedureLifecycleDecision.NO_OP,
        )


def assess_procedure_transition(
    *,
    procedure_id: ProcedureId,
    revision: int,
    current_status: ProcedureStatus,
    target_status: ProcedureStatus,
    reason: ProcedureLifecycleReason,
    requested_at: datetime,
) -> ProcedureLifecycleAssessment:
    """Assess one lifecycle transition request for one immutable revision.

    Deterministic decision order (fixed):

        1. Every argument is type-validated; malformed input raises
           :class:`ProcedureLifecycleError` and yields no assessment.
        2. ``current_status is target_status`` → ``NO_OP``. No transition is
           claimed and no status write is permitted, so the reason is recorded
           but not matched.
        3. ``target_status`` unreachable from ``current_status`` →
           ``REJECTED_RETIREMENT_IS_TERMINAL`` when the current status is the
           terminal ``RETIRED`` state (no resurrection), otherwise
           ``REJECTED_ILLEGAL_TRANSITION``.
        4. ``reason`` not permitted for that legal pair →
           ``REJECTED_REASON_MISMATCH``.
        5. Otherwise ``ALLOWED``.

    Nothing is read from a payload, a scope, a store, a clock, or an
    environment: only the typed arguments decide. The function mutates
    nothing, persists nothing, promotes nothing, and creates no revision. An
    ``ALLOWED`` result is a structural verdict only — the caller must still
    prove validation evidence before performing the explicit status write.

    Raises:
        ProcedureLifecycleError: if any argument is missing, wrongly typed, a
            raw string instead of a controlled vocabulary member, a
            non-positive revision, or a naive datetime.
    """
    validated_id = _require_procedure_id(procedure_id, field_name="procedure_id")
    validated_revision = _require_revision(revision, field_name="revision")
    validated_current = _require_status(current_status, field_name="current_status")
    validated_target = _require_status(target_status, field_name="target_status")
    validated_reason = _require_reason(reason, field_name="reason")
    validated_requested_at = _require_timestamp(requested_at, field_name="requested_at")

    allowed_reasons = PERMITTED_TRANSITION_REASONS.get(
        (validated_current, validated_target), frozenset()
    )

    if validated_current is validated_target:
        decision = ProcedureLifecycleDecision.NO_OP
    elif validated_target not in LEGAL_PROCEDURE_LIFECYCLE_TRANSITIONS[validated_current]:
        decision = (
            ProcedureLifecycleDecision.REJECTED_RETIREMENT_IS_TERMINAL
            if validated_current is ProcedureStatus.RETIRED
            else ProcedureLifecycleDecision.REJECTED_ILLEGAL_TRANSITION
        )
    elif validated_reason not in allowed_reasons:
        decision = ProcedureLifecycleDecision.REJECTED_REASON_MISMATCH
    else:
        decision = ProcedureLifecycleDecision.ALLOWED

    return ProcedureLifecycleAssessment(
        procedure_id=validated_id,
        revision=validated_revision,
        current_status=validated_current,
        target_status=validated_target,
        reason=validated_reason,
        requested_at=validated_requested_at,
        decision=decision,
        permitted_reasons=tuple(sorted(allowed_reasons, key=lambda item: item.value)),
        explanation=_explain(
            decision=decision,
            current=validated_current,
            target=validated_target,
            reason=validated_reason,
        ),
    )
