"""Canonical procedure revision replacement / rollback eligibility policy (M5.06 / C4.08).

AgentX stores procedure revisions as immutable, append-only history
(``agentx.core.procedures``). Storage mechanics deliberately decide nothing
about *which* revision may become the live one. This module owns exactly that
missing boundary: a **pure, deterministic eligibility policy** that says
whether a requested revision replacement or rollback is *structurally legal*
and *sufficiently evidenced*. It answers one question and no others:

    "May the designated ACTIVE revision be replaced by this target revision,
    for this declared reason, on this declared evidence?"

Replacement vs rollback are two distinct operations and are never conflated:

    - ``FORWARD_REPLACEMENT`` moves the live pointer to a LATER revision
      (``active N -> N+1``): a repaired or improved revision takes over.
    - ``ROLLBACK`` moves the live pointer to an EARLIER, already-known
      revision (``active N -> M``, ``M < N``): the newer revision is abandoned
      while remaining stored history.

What this module emphatically is NOT
------------------------------------

* **It is not a mutation.** It never writes to any store, creates a revision,
  copies a payload, deletes or renumbers history, rewrites a stored record,
  executes or shadow-runs a procedure, or validates a repair. It reads the
  caller-supplied records and returns a decision. Nothing else happens.
* **It does not set ``ProcedureStatus``.** It never promotes, retires, or
  otherwise transitions a record. It assesses whether a *future* controlled
  transaction could retire the current ACTIVE revision and activate the
  target; performing that transaction belongs to storage/lifecycle code, not
  here. No new status vocabulary is invented, and ``RETIRED`` records are
  never resurrected as a side effect of an assessment.
* **It is not authority.** Eligibility is not execution permission. Even an
  ``ELIGIBLE`` decision grants nothing: it cannot grant a Permission, create
  an AuthorityContext, bypass an ActionGate, lower a RiskLevel, widen a
  ResourceEnvelope, clear an EmergencyStop, transition a Task, publish an
  Event, or execute a Capability. An eligible replacement must still be
  carried out through the Trusted Kernel and its own approval machinery.
  Authority belongs exclusively to ``agentx.kernel``.
* **It is not a lifecycle engine.** Candidate-skill lifecycle and trust are
  owned by C3.09; the Procedure Graph IR by A3.01. This module neither
  pre-enacts nor constrains them beyond the structural rules below.

No silent replacement
---------------------

A revision never becomes the live one because of anything incidental. This
policy deliberately reads **only** ``procedure_id``, ``revision``, ``status``,
and ``scope`` from the caller-supplied records. It never reads a record's
``payload``, ``created_at``, or ``updated_at``, so none of the following can
ever influence a decision:

    - a higher revision number, or "latest == best";
    - a newer timestamp;
    - payload text such as ``"fixed"``, ``"verified=true"``,
      ``"activate me"``, ``"permission=ADMIN"``, or ``"risk=R0"``;
    - the mere existence of a CANDIDATE record;
    - a model's opinion that a revision is better.

Replacement is always an explicit, assessed act requested by a caller with
typed facts. A free-text claim of validation is not a fact and cannot
qualify: eligibility requires closed typed evidence (see below).

Revision rules
--------------

Forward replacement requires ALL of:

    1. the same ``ProcedureId`` on both records;
    2. ``target.revision > active.revision`` (strictly later);
    3. ``target.revision == active.revision + 1`` — the canonical store keeps
       revisions append-only and contiguous starting at 1, so arbitrary
       revision jumps are refused rather than silently accepted;
    4. if the target revision is not yet part of the caller's known history,
       it must be exactly ``max(known_revisions) + 1`` — the only revision
       number an append-only store can accept next;
    5. a target whose status is CANDIDATE (never ACTIVE, never RETIRED).

Rollback requires ALL of:

    1. the same ``ProcedureId`` on both records;
    2. ``target.revision < active.revision`` (strictly earlier);
    3. ``target.revision`` present in the caller's known history — a rollback
       can only select a revision that actually exists;
    4. an explicitly declared rollback reason;
    5. an intact target (see evidence).

A rollback never clones an old record into a new revision number and never
touches historical revision identity: the target keeps its own immutable
``(procedure_id, revision)`` pair.

Status semantics
----------------

The designated "active" record must actually carry ``ProcedureStatus.ACTIVE``;
otherwise there is nothing to replace and the request is refused. A target that
is already ACTIVE is refused: canonical policy must never leave two
contradictory active revisions for one procedure.

The two kinds then treat ``RETIRED`` differently, deliberately:

* A **forward** target must be CANDIDATE. A ``RETIRED`` record is withdrawn
  history and is never eligible to be advanced onto; the architecturally
  correct path is to derive a NEW revision from that historical payload and
  have it assessed as its own forward replacement. This keeps history
  monotonic and keeps retired revisions retired.
* A **rollback** to a ``RETIRED`` historical revision is *architecturally*
  permitted here, but only as an explicitly attested controlled lifecycle act
  (``RetiredTargetReactivation.CONTROLLED_LIFECYCLE_ACT``) backed by the same
  evidence requirements as any other replacement. Absent that attestation the
  decision is ``INSUFFICIENT_EVIDENCE``. Nothing is reactivated by assessing:
  the attestation is a typed fact supplied by the caller, and a downstream
  lifecycle owner (C3.09) remains free to forbid RETIRED -> ACTIVE outright and
  require a *new* revision derived from the historical payload instead. This
  module states that alternative explicitly rather than silently choosing it.

Evidence requirements
---------------------

Eligibility requires closed typed facts, never prose:

    - at least one explicit evidence reference (an opaque identifier the
      caller's own audit trail can resolve — this module resolves nothing);
    - ``validation_evidence`` PRESENT for both kinds;
    - ``shadow_evidence`` PRESENT for forward replacement (a new revision
      takes over live behaviour and must have been compared against it);
    - ``target_integrity`` INTACT (CORRUPT and UNKNOWN both fail closed).

Every evidence field defaults to its fail-closed member, so an omitted fact is
an absent fact. A reason never substitutes for evidence: a reason explains
WHY a replacement is requested and grants no authority on its own.

History preservation
--------------------

A rollback selects future live behaviour; it does not rewrite the past. The
failed revision N stays stored, keeps its revision number, keeps its payload,
and keeps its own status history. This module has no delete, renumber, or
rewrite concept at all, and a decision never references or mutates the failed
revision beyond reporting its number.

Atomicity a future transaction must satisfy
-------------------------------------------

This module implements no transaction, but the decision it returns is written
so a future storage transaction can honour it atomically. Such a transaction
would need to perform, as one indivisible unit:

    1. retire the current ACTIVE revision (an explicit status act);
    2. register the target revision if it is new (append-only insert), and
       activate it (an explicit status act).

It must not be observable in a half-applied state: either the previous
revision is still the single ACTIVE one, or the target is. Because canonical
policy forbids two contradictory ACTIVE revisions for one procedure, a
transaction that cannot complete both steps must apply neither.

Purity and determinism
----------------------

The assessment touches no database, filesystem, clock, model, network,
research service, subprocess, or source of randomness. The caller supplies the
records and every fact. Identical inputs therefore always produce a
byte-identical decision, and every returned decision is an immutable snapshot
that later assessments can never alter.

This module belongs to ``agentx.core`` and imports nothing from other AgentX
subsystems except the canonical procedure contracts in
``agentx.core.procedures`` and the identifiers in ``agentx.core.ids``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from agentx.core.ids import ProcedureId
from agentx.core.procedures import ProcedureRecord, ProcedureScope, ProcedureStatus

__all__ = [
    "CURRENT_PROCEDURE_REPLACEMENT_SCHEMA_VERSION",
    "EvidencePresence",
    "ProcedureReplacementDecision",
    "ProcedureReplacementEvidence",
    "ProcedureReplacementFinding",
    "ProcedureReplacementKind",
    "ProcedureReplacementOutcome",
    "ProcedureReplacementPolicyError",
    "ProcedureReplacementReason",
    "ProcedureReplacementRequest",
    "ProcedureReplacementRequestError",
    "RetiredTargetReactivation",
    "TargetIntegrityState",
    "assess_procedure_replacement",
]

CURRENT_PROCEDURE_REPLACEMENT_SCHEMA_VERSION: Final[int] = 1


class ProcedureReplacementPolicyError(ValueError):
    """Raised when a replacement/rollback request violates the canonical contract."""


class ProcedureReplacementRequestError(ProcedureReplacementPolicyError):
    """Raised when a request, its evidence, or its history facts are malformed."""


class ProcedureReplacementKind(StrEnum):
    """The two structurally distinct revision-selection operations.

    They are deliberately separate members with separate rules: a forward
    replacement advances to a later revision, while a rollback returns to an
    earlier already-known one. Nothing here treats them as interchangeable,
    and no third kind exists.
    """

    #: Active revision N is superseded by a later revision (N -> N+1).
    FORWARD_REPLACEMENT = "forward_replacement"
    #: Active revision N is abandoned for an earlier known revision (N -> M<N).
    ROLLBACK = "rollback"


class ProcedureReplacementReason(StrEnum):
    """Closed vocabulary of declared motives for a replacement or rollback.

    A reason is an explanation, never a grant: on its own it confers no
    eligibility, no permission, and no authority. Every member is justified by
    a landed AgentX boundary — validated repair (C4.01-C4.04 diagnosis and
    repair candidates), environment incompatibility (the canonical
    ``ProcedureScopeDimension`` vocabulary), and the three rollback motives
    (human approval protocol, regression evidence, and kernel safety stop).

    Members not evidencable by the architecture are deliberately absent: there
    is no ``MODEL_PREFERRED``, ``NEWER_IS_BETTER``, or ``UNSPECIFIED``.
    """

    #: A repair validated against the failure it was raised for. Forward only.
    VALIDATED_REPAIR = "validated_repair"
    #: The live revision does not fit the current environment. Either kind.
    ENVIRONMENT_INCOMPATIBILITY = "environment_incompatibility"
    #: An explicit human decision to return to an earlier revision.
    MANUAL_ROLLBACK = "manual_rollback"
    #: The live revision demonstrably regressed against an earlier one.
    REGRESSION_ROLLBACK = "regression_rollback"
    #: The live revision tripped a safety stop and must be abandoned.
    SAFETY_ROLLBACK = "safety_rollback"


class ProcedureReplacementOutcome(StrEnum):
    """Closed decision vocabulary. There is deliberately no boolean anywhere.

    ``INELIGIBLE`` and ``INSUFFICIENT_EVIDENCE`` are kept apart on purpose: the
    first means the request is structurally wrong and no amount of evidence
    would rescue it, while the second means the structure is sound but the
    caller has not yet supplied the facts required to act.
    """

    #: Structurally legal and sufficiently evidenced. Still not permission.
    ELIGIBLE = "eligible"
    #: Structurally illegal; more evidence cannot make it eligible.
    INELIGIBLE = "ineligible"
    #: Structure is sound but required typed evidence is missing.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    #: The revision relation does not satisfy the canonical sequencing rules.
    INVALID_REVISION_RELATION = "invalid_revision_relation"
    #: The two records do not belong to the same procedure.
    WRONG_PROCEDURE = "wrong_procedure"


class ProcedureReplacementFinding(StrEnum):
    """Structured, closed findings that explain a non-eligible outcome.

    Members are declared in the exact order the assessment evaluates them, so
    a decision's ``findings`` tuple is always in this canonical order. That
    makes two decisions over identical inputs byte-identical.
    """

    # -- identity (outcome WRONG_PROCEDURE) --
    #: The two records carry different ``ProcedureId`` values.
    PROCEDURE_IDENTITY_MISMATCH = "procedure_identity_mismatch"

    # -- revision relation (outcome INVALID_REVISION_RELATION) --
    #: Forward replacement whose target is not strictly later than the active.
    REVISION_NOT_STRICTLY_LATER = "revision_not_strictly_later"
    #: Forward replacement whose target skips the contiguous next revision.
    REVISION_NOT_CONTIGUOUS = "revision_not_contiguous"
    #: A not-yet-stored forward target is not the next append-only revision.
    NEW_REVISION_BREAKS_APPEND_ONLY_HISTORY = "new_revision_breaks_append_only_history"
    #: Rollback whose target is not strictly earlier than the active revision.
    REVISION_NOT_STRICTLY_EARLIER = "revision_not_strictly_earlier"
    #: Rollback whose target is not part of the caller's known history.
    TARGET_NOT_IN_KNOWN_HISTORY = "target_not_in_known_history"

    # -- structural ineligibility (outcome INELIGIBLE) --
    #: The record designated as active does not carry ProcedureStatus.ACTIVE.
    DESIGNATED_ACTIVE_NOT_ACTIVE = "designated_active_not_active"
    #: The target is already ACTIVE; two active revisions are never legal.
    TARGET_ALREADY_ACTIVE = "target_already_active"
    #: A forward target is RETIRED history; a new derived revision is required.
    FORWARD_TARGET_IS_RETIRED = "forward_target_is_retired"
    #: The declared reason is not permitted for the requested kind.
    REASON_NOT_PERMITTED_FOR_KIND = "reason_not_permitted_for_kind"
    #: The target would silently widen or alter the active scope.
    SCOPE_NOT_COMPATIBLE = "scope_not_compatible"

    # -- evidence (outcome INSUFFICIENT_EVIDENCE) --
    #: No explicit evidence reference was supplied.
    EVIDENCE_REFERENCE_ABSENT = "evidence_reference_absent"
    #: Validation evidence is not PRESENT.
    VALIDATION_EVIDENCE_ABSENT = "validation_evidence_absent"
    #: Shadow evidence is not PRESENT (required for forward replacement).
    SHADOW_EVIDENCE_ABSENT = "shadow_evidence_absent"
    #: Target integrity is CORRUPT or UNKNOWN; both fail closed.
    TARGET_INTEGRITY_NOT_INTACT = "target_integrity_not_intact"
    #: A RETIRED target was not backed by a controlled-lifecycle attestation.
    CONTROLLED_REACTIVATION_NOT_ATTESTED = "controlled_reactivation_not_attested"


class EvidencePresence(StrEnum):
    """Closed presence vocabulary, used instead of booleans where possible.

    ``ABSENT`` is the fail-closed default: an evidence fact that was not
    supplied is absent, never presumed present.
    """

    PRESENT = "present"
    ABSENT = "absent"


class TargetIntegrityState(StrEnum):
    """Closed structural-integrity state of the target revision.

    ``UNKNOWN`` is distinct from ``CORRUPT`` and both are refused: a policy
    that cannot establish that a historical revision is structurally sound
    must not select it.
    """

    INTACT = "intact"
    CORRUPT = "corrupt"
    UNKNOWN = "unknown"


class RetiredTargetReactivation(StrEnum):
    """Attestation vocabulary for selecting a RETIRED historical revision.

    ``NOT_ATTESTED`` is the fail-closed default. A controlled lifecycle act is
    an explicit downstream commitment recorded here as a typed fact; asserting
    it never performs, authorizes, or implies any status transition.
    """

    NOT_ATTESTED = "not_attested"
    CONTROLLED_LIFECYCLE_ACT = "controlled_lifecycle_act"


#: Which declared reasons may accompany which replacement kind. A reason
#: outside this table is refused for that kind — reasons are not fungible
#: between advancing history and retreating through it.
_REASONS_BY_KIND: Final[
    Mapping[ProcedureReplacementKind, frozenset[ProcedureReplacementReason]]
] = MappingProxyType(
    {
        ProcedureReplacementKind.FORWARD_REPLACEMENT: frozenset(
            {
                ProcedureReplacementReason.VALIDATED_REPAIR,
                ProcedureReplacementReason.ENVIRONMENT_INCOMPATIBILITY,
            }
        ),
        ProcedureReplacementKind.ROLLBACK: frozenset(
            {
                ProcedureReplacementReason.MANUAL_ROLLBACK,
                ProcedureReplacementReason.REGRESSION_ROLLBACK,
                ProcedureReplacementReason.SAFETY_ROLLBACK,
                ProcedureReplacementReason.ENVIRONMENT_INCOMPATIBILITY,
            }
        ),
    }
)


def _validate_revision(value: object, *, field_name: str) -> int:
    """Validate one caller-supplied revision number, rejecting bools as ints."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ProcedureReplacementRequestError(f"{field_name} must be an integer")
    if value < 1:
        raise ProcedureReplacementRequestError(
            f"{field_name} must be a positive integer (first revision is 1)"
        )
    return value


def _validate_record(value: object, *, field_name: str) -> ProcedureRecord:
    if not isinstance(value, ProcedureRecord):
        raise ProcedureReplacementRequestError(f"{field_name} must be a canonical ProcedureRecord")
    return value


def _validate_enum[T: StrEnum](
    value: object,
    expected: type[T],
    *,
    field_name: str,
) -> T:
    if not isinstance(value, expected):
        raise ProcedureReplacementRequestError(f"{field_name} must be a {expected.__name__}")
    return value


def _freeze_known_revisions(value: object) -> tuple[int, ...]:
    """Freeze the caller's authoritative view of stored revision numbers."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ProcedureReplacementRequestError("known_revisions must be a sequence of integers")
    frozen = tuple(_validate_revision(item, field_name="known_revisions item") for item in value)
    if not frozen:
        raise ProcedureReplacementRequestError(
            "known_revisions must be non-empty: the policy needs the caller's history facts"
        )
    if len(set(frozen)) != len(frozen):
        raise ProcedureReplacementRequestError("known_revisions must not contain duplicates")
    return frozen


def _freeze_findings(value: object) -> tuple[ProcedureReplacementFinding, ...]:
    """Freeze decision findings, rejecting anything that is not a finding sequence."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ProcedureReplacementRequestError("findings must be a sequence of findings")
    return tuple(
        _validate_enum(item, ProcedureReplacementFinding, field_name="findings item")
        for item in value
    )


def _freeze_evidence_references(value: object) -> tuple[str, ...]:
    """Freeze explicit evidence references; they are opaque and never resolved."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ProcedureReplacementRequestError("evidence_references must be a sequence of strings")
    frozen: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ProcedureReplacementRequestError("evidence_references must contain strings")
        if item == "" or item != item.strip():
            raise ProcedureReplacementRequestError(
                "evidence_references must be non-empty and trimmed"
            )
        frozen.append(item)
    return tuple(frozen)


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureReplacementEvidence:
    """Closed, typed eligibility facts supporting one replacement request.

    Every field defaults to its fail-closed member, so a fact the caller did
    not supply counts as absent. References are opaque identifiers owned by the
    caller's audit trail; this module never resolves, reads, opens, fetches, or
    interprets them, and their text content is never inspected — a reference
    reading ``"validated"`` is no more meaningful than any other string.
    """

    validation_evidence: EvidencePresence = EvidencePresence.ABSENT
    shadow_evidence: EvidencePresence = EvidencePresence.ABSENT
    target_integrity: TargetIntegrityState = TargetIntegrityState.UNKNOWN
    evidence_references: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "validation_evidence",
            _validate_enum(
                self.validation_evidence,
                EvidencePresence,
                field_name="validation_evidence",
            ),
        )
        object.__setattr__(
            self,
            "shadow_evidence",
            _validate_enum(self.shadow_evidence, EvidencePresence, field_name="shadow_evidence"),
        )
        object.__setattr__(
            self,
            "target_integrity",
            _validate_enum(
                self.target_integrity,
                TargetIntegrityState,
                field_name="target_integrity",
            ),
        )
        object.__setattr__(
            self,
            "evidence_references",
            _freeze_evidence_references(self.evidence_references),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "validation_evidence": self.validation_evidence.value,
            "shadow_evidence": self.shadow_evidence.value,
            "target_integrity": self.target_integrity.value,
            "evidence_references": list(self.evidence_references),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureReplacementRequest:
    """One explicit, immutable request to replace or roll back a live revision.

    The caller supplies both records and every fact the policy needs,
    including ``known_revisions`` — the caller's authoritative view of which
    revision numbers are stored for this procedure. The policy queries nothing,
    so those facts are the only history it can reason about.

    ``known_revisions`` must be non-empty, duplicate-free, and must contain the
    designated active revision: a request whose own history facts contradict
    the record it calls active is malformed and is rejected rather than
    guessed at.
    """

    kind: ProcedureReplacementKind
    reason: ProcedureReplacementReason
    active_revision: ProcedureRecord
    target_revision: ProcedureRecord
    known_revisions: tuple[int, ...]
    evidence: ProcedureReplacementEvidence
    retired_target_reactivation: RetiredTargetReactivation = RetiredTargetReactivation.NOT_ATTESTED

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            _validate_enum(self.kind, ProcedureReplacementKind, field_name="kind"),
        )
        object.__setattr__(
            self,
            "reason",
            _validate_enum(self.reason, ProcedureReplacementReason, field_name="reason"),
        )
        object.__setattr__(
            self,
            "active_revision",
            _validate_record(self.active_revision, field_name="active_revision"),
        )
        object.__setattr__(
            self,
            "target_revision",
            _validate_record(self.target_revision, field_name="target_revision"),
        )
        object.__setattr__(
            self,
            "known_revisions",
            _freeze_known_revisions(self.known_revisions),
        )
        if not isinstance(self.evidence, ProcedureReplacementEvidence):
            raise ProcedureReplacementRequestError(
                "evidence must be a ProcedureReplacementEvidence"
            )
        object.__setattr__(
            self,
            "retired_target_reactivation",
            _validate_enum(
                self.retired_target_reactivation,
                RetiredTargetReactivation,
                field_name="retired_target_reactivation",
            ),
        )
        if self.active_revision.revision not in self.known_revisions:
            raise ProcedureReplacementRequestError(
                "known_revisions must contain the designated active revision "
                f"{self.active_revision.revision}"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "reason": self.reason.value,
            "procedure_id": self.active_revision.procedure_id.to_str(),
            "active_revision": self.active_revision.revision,
            "target_revision": self.target_revision.revision,
            "known_revisions": list(self.known_revisions),
            "evidence": self.evidence.to_dict(),
            "retired_target_reactivation": self.retired_target_reactivation.value,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureReplacementDecision:
    """Immutable, deterministic outcome of one assessed replacement request.

    The decision binds to the *exact* revisions it assessed by number, so it
    can never be silently reinterpreted as applying to some other revision.
    It is a frozen snapshot: a later assessment cannot alter it, and it grants
    nothing. ``outcome is ELIGIBLE`` means "structurally legal and sufficiently
    evidenced", never "authorized" or "executed".
    """

    outcome: ProcedureReplacementOutcome
    kind: ProcedureReplacementKind
    reason: ProcedureReplacementReason
    findings: tuple[ProcedureReplacementFinding, ...]
    procedure_id: ProcedureId
    active_revision: int
    target_revision: int
    schema_version: int = CURRENT_PROCEDURE_REPLACEMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "outcome",
            _validate_enum(self.outcome, ProcedureReplacementOutcome, field_name="outcome"),
        )
        object.__setattr__(
            self,
            "kind",
            _validate_enum(self.kind, ProcedureReplacementKind, field_name="kind"),
        )
        object.__setattr__(
            self,
            "reason",
            _validate_enum(self.reason, ProcedureReplacementReason, field_name="reason"),
        )
        object.__setattr__(self, "findings", _freeze_findings(self.findings))
        if not isinstance(self.procedure_id, ProcedureId):
            raise ProcedureReplacementRequestError("procedure_id must be a ProcedureId")
        object.__setattr__(
            self,
            "active_revision",
            _validate_revision(self.active_revision, field_name="active_revision"),
        )
        object.__setattr__(
            self,
            "target_revision",
            _validate_revision(self.target_revision, field_name="target_revision"),
        )
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise ProcedureReplacementRequestError("schema_version must be an integer")
        if self.schema_version != CURRENT_PROCEDURE_REPLACEMENT_SCHEMA_VERSION:
            raise ProcedureReplacementRequestError(
                f"unsupported procedure replacement schema version {self.schema_version}"
            )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible representation."""
        return {
            "schema_version": self.schema_version,
            "outcome": self.outcome.value,
            "kind": self.kind.value,
            "reason": self.reason.value,
            "findings": [finding.value for finding in self.findings],
            "procedure_id": self.procedure_id.to_str(),
            "active_revision": self.active_revision,
            "target_revision": self.target_revision,
        }

    def to_json(self) -> str:
        """Serialize to deterministic UTF-8-safe JSON text."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _scope_is_compatible(active: ProcedureScope, target: ProcedureScope) -> bool:
    """Return whether the target preserves the active scope's applicability.

    Every dimension the active revision is scoped on must be present on the
    target with an identical value. The target may ADD dimensions, which only
    narrows where it applies. Dropping or altering an active dimension is
    refused: that would silently widen or redirect applicability as a side
    effect of a replacement.
    """
    for dimension, value in active.dimensions.items():
        if target.dimensions.get(dimension) != value:
            return False
    return True


def _assess_revision_relation(
    request: ProcedureReplacementRequest,
) -> tuple[ProcedureReplacementFinding, ...]:
    """Assess the revision relation alone, in canonical finding order."""
    active = request.active_revision
    target = request.target_revision
    highest_known = max(request.known_revisions)

    if request.kind is ProcedureReplacementKind.FORWARD_REPLACEMENT:
        if target.revision <= active.revision:
            return (ProcedureReplacementFinding.REVISION_NOT_STRICTLY_LATER,)
        if target.revision != active.revision + 1:
            return (ProcedureReplacementFinding.REVISION_NOT_CONTIGUOUS,)
        if target.revision not in request.known_revisions and target.revision != highest_known + 1:
            return (ProcedureReplacementFinding.NEW_REVISION_BREAKS_APPEND_ONLY_HISTORY,)
        return ()

    if target.revision >= active.revision:
        return (ProcedureReplacementFinding.REVISION_NOT_STRICTLY_EARLIER,)
    if target.revision not in request.known_revisions:
        return (ProcedureReplacementFinding.TARGET_NOT_IN_KNOWN_HISTORY,)
    return ()


def _assess_structure(
    request: ProcedureReplacementRequest,
) -> tuple[ProcedureReplacementFinding, ...]:
    """Assess status, reason, and scope compatibility, in canonical order."""
    findings: list[ProcedureReplacementFinding] = []

    if request.active_revision.status is not ProcedureStatus.ACTIVE:
        findings.append(ProcedureReplacementFinding.DESIGNATED_ACTIVE_NOT_ACTIVE)
    if request.target_revision.status is ProcedureStatus.ACTIVE:
        findings.append(ProcedureReplacementFinding.TARGET_ALREADY_ACTIVE)
    if (
        request.kind is ProcedureReplacementKind.FORWARD_REPLACEMENT
        and request.target_revision.status is ProcedureStatus.RETIRED
    ):
        findings.append(ProcedureReplacementFinding.FORWARD_TARGET_IS_RETIRED)
    if request.reason not in _REASONS_BY_KIND[request.kind]:
        findings.append(ProcedureReplacementFinding.REASON_NOT_PERMITTED_FOR_KIND)
    if not _scope_is_compatible(request.active_revision.scope, request.target_revision.scope):
        findings.append(ProcedureReplacementFinding.SCOPE_NOT_COMPATIBLE)

    return tuple(findings)


def _assess_evidence(
    request: ProcedureReplacementRequest,
) -> tuple[ProcedureReplacementFinding, ...]:
    """Assess the typed evidence facts, in canonical order."""
    evidence = request.evidence
    findings: list[ProcedureReplacementFinding] = []

    if not evidence.evidence_references:
        findings.append(ProcedureReplacementFinding.EVIDENCE_REFERENCE_ABSENT)
    if evidence.validation_evidence is not EvidencePresence.PRESENT:
        findings.append(ProcedureReplacementFinding.VALIDATION_EVIDENCE_ABSENT)
    if (
        request.kind is ProcedureReplacementKind.FORWARD_REPLACEMENT
        and evidence.shadow_evidence is not EvidencePresence.PRESENT
    ):
        findings.append(ProcedureReplacementFinding.SHADOW_EVIDENCE_ABSENT)
    if evidence.target_integrity is not TargetIntegrityState.INTACT:
        findings.append(ProcedureReplacementFinding.TARGET_INTEGRITY_NOT_INTACT)
    if (
        request.target_revision.status is ProcedureStatus.RETIRED
        and request.retired_target_reactivation
        is not RetiredTargetReactivation.CONTROLLED_LIFECYCLE_ACT
    ):
        findings.append(ProcedureReplacementFinding.CONTROLLED_REACTIVATION_NOT_ATTESTED)

    return tuple(findings)


def _decision(
    request: ProcedureReplacementRequest,
    outcome: ProcedureReplacementOutcome,
    findings: tuple[ProcedureReplacementFinding, ...],
) -> ProcedureReplacementDecision:
    return ProcedureReplacementDecision(
        outcome=outcome,
        kind=request.kind,
        reason=request.reason,
        findings=findings,
        procedure_id=request.active_revision.procedure_id,
        active_revision=request.active_revision.revision,
        target_revision=request.target_revision.revision,
    )


def assess_procedure_replacement(
    request: ProcedureReplacementRequest,
) -> ProcedureReplacementDecision:
    """Assess one replacement/rollback request and return a structured decision.

    Pure and deterministic: the same request always yields a byte-identical
    decision, and no store, filesystem, clock, model, or network is consulted.

    Precedence is fixed and documented so outcomes are predictable:

        1. identity — different procedures short-circuit to ``WRONG_PROCEDURE``;
        2. revision relation — a violation short-circuits to
           ``INVALID_REVISION_RELATION``, because evidence is meaningless once
           the revision relation is wrong;
        3. structure — status, reason, and scope violations give ``INELIGIBLE``;
        4. evidence — missing typed facts give ``INSUFFICIENT_EVIDENCE``;
        5. otherwise ``ELIGIBLE``.

    An ``ELIGIBLE`` result is not permission. Performing the replacement still
    requires a controlled storage/lifecycle transaction and, for execution, the
    Trusted Kernel.
    """
    if not isinstance(request, ProcedureReplacementRequest):
        raise ProcedureReplacementRequestError(
            "request must be a canonical ProcedureReplacementRequest"
        )

    if request.active_revision.procedure_id != request.target_revision.procedure_id:
        return _decision(
            request,
            ProcedureReplacementOutcome.WRONG_PROCEDURE,
            (ProcedureReplacementFinding.PROCEDURE_IDENTITY_MISMATCH,),
        )

    relation = _assess_revision_relation(request)
    if relation:
        return _decision(
            request,
            ProcedureReplacementOutcome.INVALID_REVISION_RELATION,
            relation,
        )

    structure = _assess_structure(request)
    if structure:
        return _decision(request, ProcedureReplacementOutcome.INELIGIBLE, structure)

    evidence = _assess_evidence(request)
    if evidence:
        return _decision(request, ProcedureReplacementOutcome.INSUFFICIENT_EVIDENCE, evidence)

    return _decision(request, ProcedureReplacementOutcome.ELIGIBLE, ())
