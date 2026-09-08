"""Deterministic bounded repair-attempt policy (C4.09 / M5.03).

Repair in AgentX must be conservative. A failure must never be allowed to
spawn an autonomous mutation loop of the shape

    failure -> repair -> failed repair -> repair -> failed repair -> ...

This module owns exactly one narrow rule inside that boundary:

    Given explicit caller-supplied historical repair-attempt evidence for one
    repair target, may *another* repair attempt be CONSIDERED under finite,
    explicit, repair-specific attempt limits?

The answer is a typed, inert loop-policy opinion — nothing more:

* ``ALLOW_CONSIDERATION`` is **not** authorization. It means only that the
  repair-specific loop policy has not exhausted its finite allowance. A future
  repair executor still needs kernel authority, the Action Gate, risk checks,
  the canonical C1.08 ``ResourceBudget``, verification, and every other gate
  independently. repair policy ALLOW + kernel budget DENY => NO REPAIR.
* any ``STOP_*`` decision means no further repair attempt may be considered
  for that target/proposal even if kernel resource budget remains.
  repair policy STOP => NO further repair, regardless of remaining budget.

Neither decision grants execution authority of any kind.

What this module deliberately is NOT:

* It is not a second resource-accounting system. The canonical Trusted Kernel
  ``agentx.kernel.resource_budget`` (``ResourceEnvelope`` / ``ResourceUsage`` /
  ``ResourceBudget``) remains the sole owner of global resource accounting:
  model tokens, model calls, money, machine actions, research queries, and
  wall-clock time. This module tracks none of those, defines no envelope, no
  usage snapshot, and no consumption; it owns only repair-loop attempt
  structure. Both policies must eventually pass for a repair to happen.
* It is not a replacement for the canonical generic cognition anti-loop
  (``agentx.cognition.anti_loop``, A2.09), which bounds generic agent-attempt
  repetition. This contract is the narrower repair-domain rule: it is target
  scoped, proposal-fingerprint scoped, and repair-lifecycle aware.
* It is not an executor. It does not generate, select, apply, verify, or roll
  back patches, does not execute or shadow-execute anything, does not mutate
  procedures, Tasks, Hive memory, or any store, does not persist repair
  attempts, and adds no migration.
* It is not authority. It grants no ``Permission``, constructs no
  ``AuthorityContext``, invokes no ``ActionGate``, changes no ``RiskLevel``,
  consumes no ``ResourceBudget``, clears no ``EmergencyStop``, transitions no
  ``Task``, activates no ``Procedure``, publishes no events or audit records.
  Hostile strings remain inert data.

Determinism and purity:

* Evaluating history is pure and stateless. The module reads no clock, uses no
  randomness, touches no database, filesystem, network, model, research,
  subprocess, or thread, and sleeps never. Callers supply all evidence; the
  same inputs always produce the same decision.
* Evidence order is the caller-supplied tuple position. Records carry no
  timestamp field at all, so "just later" or "new UUID" games are structurally
  impossible: identical typed fields are identical evidence.
* Repair proposals and progress markers are opaque stable tokens with a narrow
  grammar. This module never hashes, embeds, fuzzy-matches, or interprets
  natural-language patch content, error text, or model output; the caller
  computes any content-derived fingerprint before calling.
* Progress is recognized only from an explicit, previously unseen (for that
  target) typed progress marker, or from an outcome that records completed
  validation. Replaying an old marker resets nothing. Progress is never
  inferred from different wording, a longer patch, model confidence, a changed
  timestamp, a new UUID, or "looks better".

Fail-closed policy: structurally invalid historical evidence yields the typed
``INVALID_HISTORY`` decision — never an allowance. Unrecognized outcome values
are rejected rather than coerced to ``UNKNOWN``. Every limit is a finite
positive integer; there is no ``None``, zero, negative, or sentinel unlimited
mode.

Identity reuse: this module introduces no competing identifier types. It
reuses the canonical ``ProcedureId`` from ``agentx.core.ids``. A procedure
repair target is identified by exactly the C2.03 record identity — canonical
``ProcedureId`` plus explicit positive revision — so the same procedure at two
different revisions (for example, a pre-rollback and a post-replacement
revision) always remain distinguishable repair targets. A caller that needs
finer-than-revision scoping (one node of a procedure graph) represents it
explicitly with an opaque target fingerprint; this contract never guesses
such equivalence. It imports nothing from any subsystem outside
``agentx.core``.

Deliberate non-goals owned by other tasks: the repair patch contract, repair
generation, repair validation, shadow execution, rollback, procedure
replacement, degradation detection, generic cognition anti-loop policy, kernel
budget accounting, repair persistence, and any migration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.core.ids import ProcedureId

__all__ = [
    "CANONICAL_REPAIR_ATTEMPT_OUTCOMES",
    "ProcedureRepairTarget",
    "RepairAttemptEvidence",
    "RepairAttemptOutcome",
    "RepairBudgetAssessment",
    "RepairBudgetDecision",
    "RepairBudgetLimits",
    "RepairBudgetScope",
    "RepairBudgetValidationError",
    "RepairProgressMarker",
    "RepairProposalFingerprint",
    "RepairTarget",
    "RepairTargetFingerprint",
    "assess_repair_attempt",
]

_MAX_TOKEN_LENGTH: Final[int] = 256
_MAX_LIMIT: Final[int] = (1 << 63) - 1
_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+=@-]{0,255}")


class RepairBudgetValidationError(ValueError):
    """Raised when repair-budget data violates the canonical contract."""


def _validate_token(value: object, *, field_name: str) -> str:
    """Validate one caller-supplied stable opaque token.

    Tokens intentionally use a narrow grammar instead of free natural
    language, so no fingerprint or marker can smuggle an instruction, and no
    two tokens are ever "equivalent enough". Callers that need to represent
    rich content must first supply their own stable ID or digest; this module
    never guesses equivalence.
    """
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be non-empty and trimmed")
    if len(value) > _MAX_TOKEN_LENGTH:
        raise ValueError(f"{field_name} must not exceed {_MAX_TOKEN_LENGTH} characters")
    if _TOKEN_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{field_name} must be an opaque stable token containing only "
            "ASCII letters, digits, '.', '_', ':', '/', '+', '=', '@', or '-'"
        )
    return value


def _validate_limit(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int")
    if value < 1:
        raise ValueError(f"{field_name} must be at least 1")
    if value > _MAX_LIMIT:
        raise OverflowError(f"{field_name} exceeds the supported counter range")
    return value


def _validate_count(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int")
    if value < 0:
        raise ValueError(f"{field_name} must not be negative")
    if value > _MAX_LIMIT:
        raise OverflowError(f"{field_name} exceeds the supported counter range")
    return value


def _validate_revision(value: object, *, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int")
    if value < 1:
        raise ValueError(f"{field_name} must be a positive integer (first revision is 1)")
    if value > _MAX_LIMIT:
        raise OverflowError(f"{field_name} exceeds the supported counter range")
    return value


@dataclass(frozen=True, slots=True)
class RepairProposalFingerprint:
    """Stable opaque identity of the exact repair proposal one attempt tried.

    The value is caller-supplied. Two attempts tried the same repair exactly
    when their proposal fingerprints are equal; this module never derives,
    hashes, embeds, or fuzzy-matches patch content to decide that.
    """

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _validate_token(self.value, field_name="proposal fingerprint")
        )


@dataclass(frozen=True, slots=True)
class RepairProgressMarker:
    """Explicit stable marker for a meaningfully new repair-progress state.

    Only the first occurrence of a previously unseen marker (for one target)
    demonstrates progress. Reusing an old marker is not progress and resets
    nothing.
    """

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _validate_token(self.value, field_name="progress marker"))


@dataclass(frozen=True, slots=True)
class RepairTargetFingerprint:
    """Stable opaque identity of a non-procedure repair target.

    Used when a repair target is not a procedure revision. The token is the
    caller's canonical stable identity for that target; this module never
    infers that two different tokens are "the same really".
    """

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "value", _validate_token(self.value, field_name="target fingerprint")
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ProcedureRepairTarget:
    """Canonical identity of a procedure-revision repair target.

    Identity is exactly the C2.03 procedure-record identity: the pair
    ``(procedure_id, revision)``. Two targets are equal only when the
    canonical ``ProcedureId`` and the exact revision are both equal, so the
    same procedure at two different revisions (for example, a pre-rollback
    and a post-replacement revision) always remain distinguishable repair
    targets, and evidence for one never consumes the other's budget. A
    caller needing finer-than-revision scoping (one node of a graph) must
    represent that explicitly with an opaque target fingerprint instead;
    this contract never infers such equivalence.
    """

    procedure_id: ProcedureId
    revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, ProcedureId):
            raise RepairBudgetValidationError("procedure_id must be a ProcedureId")
        object.__setattr__(
            self, "revision", _validate_revision(self.revision, field_name="revision")
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairTarget:
    """Stable identity of what one repair attempt targets.

    Exactly one identification must be supplied: either the canonical
    ``procedure`` identity (a ``ProcedureRepairTarget``) or an ``opaque``
    target fingerprint for non-procedure targets. A procedure target never
    equals an opaque target, and target equality is exact typed equality —
    never string inference, prefix matching, or "similar" identity.
    """

    procedure: ProcedureRepairTarget | None = None
    opaque: RepairTargetFingerprint | None = None

    def __post_init__(self) -> None:
        if self.procedure is None and self.opaque is None:
            raise RepairBudgetValidationError(
                "a repair target requires exactly one explicit identification: "
                "a ProcedureRepairTarget or an opaque target fingerprint"
            )
        if self.procedure is not None and self.opaque is not None:
            raise RepairBudgetValidationError(
                "a repair target must carry exactly one identification; carrying both "
                "a procedure identity and an opaque fingerprint would be ambiguous"
            )
        if self.procedure is not None and not isinstance(self.procedure, ProcedureRepairTarget):
            raise RepairBudgetValidationError("procedure must be a ProcedureRepairTarget")
        if self.opaque is not None and not isinstance(self.opaque, RepairTargetFingerprint):
            raise RepairBudgetValidationError("opaque must be a RepairTargetFingerprint")


class RepairAttemptOutcome(StrEnum):
    """Closed vocabulary of one *historical* repair-attempt outcome.

    Each member names the observed endpoint of one past repair attempt across
    the repair lifecycle the C4.01-C4.04 contracts already anticipate (patch
    generation C4.05, repair validation C4.06, shadow repair C4.07, procedure
    version replacement/rollback C4.08). An outcome is DATA:

    * a ``FAILED_*`` member records that a past attempt failed; it does not
      trigger, schedule, or justify another attempt;
    * ``VALIDATED`` records that a past attempt's result passed its explicit
      validation; it does not apply, approve, or verify anything now;
    * no member asserts that any repair is correct, safe, selected,
      authorized, executed, or applied.

    Members:

        VALIDATED — the attempt's result passed its explicit validation.
        FAILED_VALIDATION — the proposed repair failed validation (C4.06).
        FAILED_SHADOW — the proposed repair failed shadow execution (C4.07).
        FAILED_APPLICATION — applying/replacing the target failed (C4.08).
        ABORTED — the attempt ended before reaching any endpoint above.
        UNKNOWN — fail-closed default: the recorded outcome is not
            representable; it demonstrates nothing.
    """

    VALIDATED = "validated"
    FAILED_VALIDATION = "failed_validation"
    FAILED_SHADOW = "failed_shadow"
    FAILED_APPLICATION = "failed_application"
    ABORTED = "aborted"
    UNKNOWN = "unknown"


#: The canonical outcome vocabulary in its declared order. Exported so callers
#: may enumerate the closed vocabulary without re-declaring it.
CANONICAL_REPAIR_ATTEMPT_OUTCOMES: Final[tuple[RepairAttemptOutcome, ...]] = tuple(
    RepairAttemptOutcome
)


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairAttemptEvidence:
    """One completed repair attempt's explicit historical evidence.

    There is deliberately no metadata bag, correlation ID, free-text error,
    retry instruction, timestamp, authority, status mutation, or escalation
    field: none of those can become progress, reset a counter, or widen a
    limit. The record is inert data about the past.
    """

    target: RepairTarget
    proposal: RepairProposalFingerprint
    outcome: RepairAttemptOutcome
    progress: RepairProgressMarker | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, RepairTarget):
            raise TypeError("target must be a RepairTarget")
        if not isinstance(self.proposal, RepairProposalFingerprint):
            raise TypeError("proposal must be a RepairProposalFingerprint")
        if not isinstance(self.outcome, RepairAttemptOutcome):
            raise TypeError("outcome must be a RepairAttemptOutcome member")
        if self.progress is not None and not isinstance(self.progress, RepairProgressMarker):
            raise TypeError("progress must be a RepairProgressMarker or None")


class RepairBudgetScope(StrEnum):
    """What the total-attempt ceiling counts.

    ``TARGET`` counts recorded attempts for the assessed target only, so
    evidence from an unrelated target never consumes this target's total
    allowance. ``SESSION`` counts every attempt in the supplied history —
    an explicit, opt-in, caller-wide session ceiling.
    """

    TARGET = "target"
    SESSION = "session"


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairBudgetLimits:
    """Explicit finite ceilings owned only by this repair-loop policy.

    All fields are required positive integers; there is no unlimited mode,
    no ``None``, no zero, no negative, and no sentinel value.

    ``max_total_attempts`` bounds recorded repair attempts counted according
    to ``total_attempt_scope`` and never resets — not by progress, not by a
    validated outcome, not by anything.
    ``max_attempts_per_proposal`` bounds exact occurrences of one repair
    proposal fingerprint for the assessed target. It also never resets: a
    progress marker may justify a *different* proposal, but never revives an
    already-exhausted identical one.
    ``max_consecutive_failures_without_progress`` bounds the trailing run of
    target attempts that demonstrated neither a first-seen progress marker
    nor a ``VALIDATED`` outcome. Only genuinely new evidence resets this run.

    Reaching a ceiling exactly is terminal for the next attempt.
    """

    max_total_attempts: int
    max_attempts_per_proposal: int
    max_consecutive_failures_without_progress: int
    total_attempt_scope: RepairBudgetScope

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_total_attempts",
            _validate_limit(self.max_total_attempts, field_name="max_total_attempts"),
        )
        object.__setattr__(
            self,
            "max_attempts_per_proposal",
            _validate_limit(self.max_attempts_per_proposal, field_name="max_attempts_per_proposal"),
        )
        object.__setattr__(
            self,
            "max_consecutive_failures_without_progress",
            _validate_limit(
                self.max_consecutive_failures_without_progress,
                field_name="max_consecutive_failures_without_progress",
            ),
        )
        if not isinstance(self.total_attempt_scope, RepairBudgetScope):
            raise TypeError("total_attempt_scope must be a RepairBudgetScope member")


class RepairBudgetDecision(StrEnum):
    """Bounded repair-attempt loop-policy decision.

    ``ALLOW_CONSIDERATION`` means only that the finite repair-specific
    allowance is not exhausted; it is not authorization to repair. Every
    ``STOP_*`` member is terminal for the assessed target/proposal: no
    further repair attempt may be considered, even if kernel resource budget
    remains. ``INVALID_HISTORY`` is the fail-closed answer for structurally
    invalid historical evidence.
    """

    ALLOW_CONSIDERATION = "allow_consideration"
    STOP_TOTAL_LIMIT = "stop_total_limit"
    STOP_REPEAT_LIMIT = "stop_repeat_limit"
    STOP_NO_PROGRESS = "stop_no_progress"
    INVALID_HISTORY = "invalid_history"


@dataclass(frozen=True, slots=True, kw_only=True)
class RepairBudgetAssessment:
    """Inert repair-loop decision plus the exact counters that produced it.

    The counters are data about the supplied history only. For an
    ``INVALID_HISTORY`` decision every counter is zero because no counter of
    corrupt history may be trusted.
    """

    decision: RepairBudgetDecision
    reason: str
    history_attempt_count: int
    target_attempt_count: int
    proposal_attempt_count: int
    consecutive_no_progress_count: int
    distinct_progress_markers: int

    def __post_init__(self) -> None:
        if not isinstance(self.decision, RepairBudgetDecision):
            raise TypeError("decision must be a RepairBudgetDecision")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")
        if not self.reason or self.reason != self.reason.strip():
            raise ValueError("reason must be non-empty and trimmed")
        object.__setattr__(
            self,
            "history_attempt_count",
            _validate_count(self.history_attempt_count, field_name="history_attempt_count"),
        )
        object.__setattr__(
            self,
            "target_attempt_count",
            _validate_count(self.target_attempt_count, field_name="target_attempt_count"),
        )
        object.__setattr__(
            self,
            "proposal_attempt_count",
            _validate_count(self.proposal_attempt_count, field_name="proposal_attempt_count"),
        )
        object.__setattr__(
            self,
            "consecutive_no_progress_count",
            _validate_count(
                self.consecutive_no_progress_count,
                field_name="consecutive_no_progress_count",
            ),
        )
        object.__setattr__(
            self,
            "distinct_progress_markers",
            _validate_count(self.distinct_progress_markers, field_name="distinct_progress_markers"),
        )
        if self.target_attempt_count > self.history_attempt_count:
            raise ValueError("target_attempt_count must not exceed history_attempt_count")
        if self.proposal_attempt_count > self.target_attempt_count:
            raise ValueError("proposal_attempt_count must not exceed target_attempt_count")
        if self.consecutive_no_progress_count > self.target_attempt_count:
            raise ValueError("consecutive_no_progress_count must not exceed target_attempt_count")
        if self.distinct_progress_markers > self.target_attempt_count:
            raise ValueError("distinct_progress_markers must not exceed target_attempt_count")
        if self.decision is RepairBudgetDecision.INVALID_HISTORY and (
            self.history_attempt_count,
            self.target_attempt_count,
            self.proposal_attempt_count,
            self.consecutive_no_progress_count,
            self.distinct_progress_markers,
        ) != (0, 0, 0, 0, 0):
            raise ValueError("INVALID_HISTORY must carry no counters")


def _invalid_history(reason: str) -> RepairBudgetAssessment:
    """Build the fail-closed zero-counter assessment for corrupt history."""
    return RepairBudgetAssessment(
        decision=RepairBudgetDecision.INVALID_HISTORY,
        reason=f"INVALID_HISTORY: {reason}; no repair attempt may be considered.",
        history_attempt_count=0,
        target_attempt_count=0,
        proposal_attempt_count=0,
        consecutive_no_progress_count=0,
        distinct_progress_markers=0,
    )


def _history_violation(history: object) -> str | None:
    """Return why ``history`` is structurally invalid, or ``None`` if valid.

    The parameter is deliberately typed ``object``: hostile callers are not
    bound by annotations, so this boundary re-checks the runtime shape and
    the fail-closed path stays reachable for every possible input.
    """
    if not isinstance(history, tuple):
        return f"history must be a tuple of RepairAttemptEvidence, got {type(history).__name__}"
    for index, item in enumerate(history):
        if not isinstance(item, RepairAttemptEvidence):
            return (
                f"history item at index {index} must be a RepairAttemptEvidence, "
                f"got {type(item).__name__}"
            )
    return None


def _demonstrated_progress(
    history: tuple[RepairAttemptEvidence, ...],
) -> tuple[list[bool], int]:
    """Flag, per target-scoped attempt, whether it demonstrated progress.

    An attempt demonstrates progress exactly when it carries the first
    occurrence of a progress marker (for this target's evidence) or records a
    ``VALIDATED`` outcome. A replayed old marker demonstrates nothing. The
    second return value is the count of distinct markers seen.
    """
    seen: set[RepairProgressMarker] = set()
    demonstrated: list[bool] = []
    for item in history:
        marker = item.progress
        if marker is not None and marker not in seen:
            seen.add(marker)
            demonstrated.append(True)
            continue
        demonstrated.append(item.outcome is RepairAttemptOutcome.VALIDATED)
    return demonstrated, len(seen)


def assess_repair_attempt(
    *,
    target: RepairTarget,
    proposal: RepairProposalFingerprint,
    history: tuple[RepairAttemptEvidence, ...],
    limits: RepairBudgetLimits,
) -> RepairBudgetAssessment:
    """Evaluate whether another repair attempt may be *considered*.

    This is a pure, stateless, deterministic function over explicit
    caller-supplied evidence. It executes nothing, repairs nothing, grants
    nothing, consumes no kernel budget, and mutates no state anywhere.

    Fail-closed history handling: a ``history`` that is not a tuple of
    :class:`RepairAttemptEvidence` yields the typed
    :attr:`RepairBudgetDecision.INVALID_HISTORY` decision with zero counters —
    never an allowance. (Malformed request objects — wrong ``target``,
    ``proposal``, or ``limits`` types — raise immediately, matching every
    other core construction boundary.)

    Trigger priority is deterministic when one history reaches several
    ceilings: the total-attempt ceiling first, then the identical-proposal
    ceiling, then the no-progress ceiling.
    """
    if not isinstance(target, RepairTarget):
        raise TypeError("target must be a RepairTarget")
    if not isinstance(proposal, RepairProposalFingerprint):
        raise TypeError("proposal must be a RepairProposalFingerprint")
    if not isinstance(limits, RepairBudgetLimits):
        raise TypeError("limits must be RepairBudgetLimits")
    violation = _history_violation(history)
    if violation is not None:
        return _invalid_history(violation)
    if len(history) > _MAX_LIMIT:
        raise OverflowError("history exceeds the supported counter range")

    target_history = tuple(item for item in history if item.target == target)
    demonstrated, distinct_progress_markers = _demonstrated_progress(target_history)

    consecutive_no_progress = 0
    for progressed in reversed(demonstrated):
        if progressed:
            break
        consecutive_no_progress += 1

    history_attempt_count = len(history)
    target_attempt_count = len(target_history)
    proposal_attempt_count = sum(1 for item in target_history if item.proposal == proposal)

    scope = limits.total_attempt_scope
    total_count = (
        target_attempt_count if scope is RepairBudgetScope.TARGET else history_attempt_count
    )

    if total_count >= limits.max_total_attempts:
        decision = RepairBudgetDecision.STOP_TOTAL_LIMIT
        reason = (
            f"STOP_TOTAL_LIMIT: {total_count} recorded repair attempts "
            f"({scope.value} scope) already meet the explicit maximum of "
            f"{limits.max_total_attempts}; no further repair attempt may be "
            f"considered."
        )
    elif proposal_attempt_count >= limits.max_attempts_per_proposal:
        decision = RepairBudgetDecision.STOP_REPEAT_LIMIT
        reason = (
            f"STOP_REPEAT_LIMIT: this exact repair proposal fingerprint has "
            f"already been attempted {proposal_attempt_count} times for this "
            f"target, meeting the explicit maximum of "
            f"{limits.max_attempts_per_proposal}; the identical proposal may "
            f"not be considered again."
        )
    elif consecutive_no_progress >= limits.max_consecutive_failures_without_progress:
        decision = RepairBudgetDecision.STOP_NO_PROGRESS
        reason = (
            f"STOP_NO_PROGRESS: the last {consecutive_no_progress} repair "
            f"attempts for this target demonstrated neither a new progress "
            f"marker nor a validated outcome, meeting the explicit maximum of "
            f"{limits.max_consecutive_failures_without_progress}; no further "
            f"repair attempt may be considered."
        )
    else:
        decision = RepairBudgetDecision.ALLOW_CONSIDERATION
        reason = (
            "ALLOW_CONSIDERATION: the finite repair-attempt allowance for this "
            "target and proposal is not exhausted; this is a loop-policy "
            "opinion only and grants no kernel authority, Action Gate "
            "approval, risk acceptance, or resource budget."
        )

    return RepairBudgetAssessment(
        decision=decision,
        reason=reason,
        history_attempt_count=history_attempt_count,
        target_attempt_count=target_attempt_count,
        proposal_attempt_count=proposal_attempt_count,
        consecutive_no_progress_count=consecutive_no_progress,
        distinct_progress_markers=distinct_progress_markers,
    )
