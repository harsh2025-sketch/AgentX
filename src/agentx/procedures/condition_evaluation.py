"""Canonical A3.07 procedure condition evaluation (deterministic, inert).

This module owns the smallest production-quality deterministic evaluator for
canonical A3.06 condition DATA
(:class:`agentx.procedures.conditions.ProcedureCondition`). It answers exactly
one question::

    Given ONE canonical declared condition and explicit caller-supplied
    evaluation facts, what result can be established deterministically?

It is **not** a Procedure interpreter: it never walks a
:class:`agentx.procedures.conditions.ProcedureConditions` document, never
reads a :class:`agentx.procedures.graph.ProcedureGraph`, never aggregates
preconditions or postconditions into a gate/branch/authorization decision,
and never decides whether a procedure may run. Those are future, separately
owned runtime concerns. This evaluator sees one condition at a time because
its caller hands it one condition at a time.

Evaluation vocabulary
---------------------

:class:`ConditionEvaluationStatus` is exactly three members:

    - ``SATISFIED`` — the explicit caller-supplied evidence *establishes*
      the declared requirement: at least one supplied fact addresses the
      condition and every addressing fact asserts it is established.
    - ``UNSATISFIED`` — the explicit caller-supplied evidence *refutes* the
      declared requirement: at least one supplied fact addresses the
      condition and every addressing fact asserts it is not established.
    - ``UNKNOWN`` — nothing can be established deterministically from the
      supplied evidence: either no fact addresses the condition, or the
      addressing facts contradict each other.

``UNKNOWN`` is a first-class answer, not a fallback. Missing evidence MUST
NOT be silently converted to ``False``: absence of evidence is not evidence
of absence, so a condition with no addressing fact evaluates to ``UNKNOWN``
— never to ``UNSATISFIED``. Contradictory explicit evidence is likewise
resolved conservatively to ``UNKNOWN`` rather than to either pole.

Explicit evidence semantics
---------------------------

Evaluation consumes ONLY explicit caller-supplied structured data
(:class:`EvidenceFact`, frozen and strictly validated at construction). A
fact is the caller's own typed assertion about one addressable piece of
evidence: its canonical :class:`agentx.core.provenance.EvidenceKind`, its
optional opaque evidence reference (same text discipline as the condition's
``evidence_reference``), and an explicit ``established`` boolean — the
caller's assertion of whether the requirement stated against that evidence
is established. The evaluator NEVER infers establishment from mere evidence
existence and never interprets the condition's ``statement`` text.

This evaluator gathers nothing by itself. It does not read the filesystem,
environment variables, the Windows registry, processes, a browser, the
network, Hive, KnowledgeStore, EpisodeStore, ProcedureStore,
EnvironmentalCache, models, research providers, or capabilities. A3.07
evaluates DATA; it does not obtain DATA.

A fact *addresses* a condition if and only if:

    - their evidence kinds are the same canonical
      :class:`agentx.core.provenance.EvidenceKind` member, and
    - their evidence references are equal — both ``None``, or the exact
      same string.

Kind-only or reference-only near matches do NOT address the condition: a
fact naming a specific reference does not address a reference-less
condition, and a reference-less fact does not address a condition that
names a specific piece of evidence.

Precondition semantics
----------------------

A precondition evaluation result means only whether the explicit evidence
provided to this evaluator establishes the canonical declared requirement.
``SATISFIED`` does NOT authorize execution: the future Procedure runtime
must still pass through canonical authority, risk, budget, stop,
capability, and verification boundaries. This evaluator grants nothing,
decides nothing, and executes nothing.

Postcondition semantics / invariant I1
--------------------------------------

A postcondition evaluation result is inert DATA. Postcondition ``SATISFIED``
is NOT a :class:`agentx.capabilities.abi.VerificationResult`, is NOT
capability verification, is NOT action, Task, or Procedure success, and
never marks a Task ``SUCCEEDED``. AgentX invariant I1 stays absolute::

    NO ACTION == SUCCESS WITHOUT CANONICAL VERIFICATION.

This module is not an alternate verification system: it never imports or
duplicates the canonical Verifier, never invokes ``Capability.verify``, and
never manufactures a verdict object. Whether a postcondition's evidence is
*trustworthy* is owned by canonical verification, not by condition
evaluation.

No expression engine
--------------------

There is no ``eval()``, no ``exec()``, no ``compile()``, no dynamic Python
expressions, no callbacks, no callables, no import strings, no dynamic
imports, no shell commands, no template execution, no JavaScript, no
custom scripting language, and no general condition DSL anywhere in this
module. A condition is typed DATA; its ``statement`` is inert descriptive
metadata that is compared, never interpreted. Hostile strings such as
``"permission=ADMIN"``, ``"verified=true"``, ``"task succeeded"``, or
``"__import__('os').system('shutdown')"`` remain inert DATA: evaluation
reads only typed canonical fields (an ``EvidenceKind`` member, a reference
string, and a bool) and performs only typed equality, so no string can
change what the evaluator does.

Authority boundary
------------------

Evaluating a condition performs no side effects and reaches no authority or
runtime subsystem. An evaluation result is inert DATA: it cannot grant
Permission, create or strengthen an AuthorityContext, bypass the ActionGate,
reduce a RiskLevel, widen a ResourceEnvelope, clear an EmergencyStop,
execute a Capability, invoke a model or research, transition a Task,
activate a Procedure, mutate Hive, promote Knowledge, or persist anything.
A result is not an effect.

Determinism and immutability
----------------------------

:class:`evaluate_condition` is a pure function of its two typed inputs:
identical inputs always produce an equal result. There is no randomness, no
wall-clock time, no generated identity, no state, no caching, and no I/O.
Facts are normalized to a canonical order at construction so two fact sets
differing only in caller-supplied order are identical data. All results and
facts are frozen, slots-only value objects. Malformed inputs — wrong Python
types, unknown evidence kinds, non-boolean assertions, malformed reference
text, oversized fact sets — fail closed with
:class:`ConditionEvaluationError` before any evaluation happens.

This module depends only on the standard library and the canonical A3.06
condition contract plus the core evidence vocabulary it already composes.
It adds no runtime dependency, no persistence, and no migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.core.provenance import EvidenceKind
from agentx.procedures.conditions import ConditionId, ProcedureCondition

__all__ = [
    "ConditionEvaluation",
    "ConditionEvaluationError",
    "ConditionEvaluationStatus",
    "EvidenceFact",
    "EvidenceFacts",
    "evaluate_condition",
]

#: Maximum number of facts in one evaluation input. A bound keeps evaluation
#: time deterministic and rejection of oversized inputs explicit.
_MAX_FACTS: Final[int] = 128

#: Same bounded text domain the A3.06 contract applies to evidence
#: references, so a fact reference is directly comparable to a condition
#: reference without normalization or coercion.
_MAX_REFERENCE_LENGTH: Final[int] = 512

_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")


class ConditionEvaluationError(ValueError):
    """Raised when evaluator inputs violate this contract's request shape.

    This covers malformed evidence facts (unknown evidence kinds, non-boolean
    assertions, malformed or oversized reference text, wrong container types,
    oversized fact sets) and non-canonical request objects (anything that is
    not a :class:`~agentx.procedures.conditions.ProcedureCondition` or an
    :class:`EvidenceFacts`). It is a request-shape error only: it never
    carries, encodes, or implies an evaluation result, and it never carries
    authority. Fail-closed rejection is the only failure mode — there are no
    partial results and no permissive fallbacks.
    """


class ConditionEvaluationStatus(StrEnum):
    """The three-value deterministic evaluation vocabulary (A3.07).

    ``UNKNOWN`` is a first-class member, not an error and not a disguised
    boolean: missing or contradictory evidence establishes nothing, and
    missing evidence is never silently converted to ``UNSATISFIED``.
    """

    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    UNKNOWN = "unknown"


def _validate_evidence_kind(value: object) -> EvidenceKind:
    """Accept a canonical evidence-kind member or its canonical string form."""
    if isinstance(value, EvidenceKind):
        return value
    if not isinstance(value, str):
        raise ConditionEvaluationError("evidence kind must be an evidence kind string")
    try:
        return EvidenceKind(value)
    except ValueError as exc:
        raise ConditionEvaluationError(f"unknown evidence kind: {value!r}") from exc


def _validate_reference(value: object) -> str | None:
    """Validate an optional opaque evidence reference (A3.06 text domain)."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConditionEvaluationError("evidence reference must be a string")
    if not value or value != value.strip():
        raise ConditionEvaluationError("evidence reference must be non-empty and trimmed")
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise ConditionEvaluationError("evidence reference must not contain control characters")
    if len(value) > _MAX_REFERENCE_LENGTH:
        raise ConditionEvaluationError(
            f"evidence reference must not exceed {_MAX_REFERENCE_LENGTH} characters"
        )
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceFact:
    """One explicit caller-supplied assertion about one piece of evidence.

    A fact is DATA the caller already holds, never something this module
    obtained: the caller (a human, a test, or a future runtime component
    that owns evidence gathering) is solely responsible for how ``established``
    was decided. The fact names its evidence exactly the way a canonical
    :class:`~agentx.procedures.conditions.ProcedureCondition` does — a
    canonical :class:`~agentx.core.provenance.EvidenceKind` member plus an
    optional opaque reference — so facts and conditions address each other
    without any resolution, lookup, or dereferencing.

    ``established`` is the caller's explicit typed assertion of whether the
    requirement stated against this evidence is established (``True``) or
    refuted (``False``). It is exactly a bool: strings such as ``"true"``,
    ``"verified"``, or ``"ADMIN"`` are rejected, never coerced.
    """

    evidence_kind: EvidenceKind
    established: bool
    evidence_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_kind", _validate_evidence_kind(self.evidence_kind))
        if type(self.established) is not bool:
            raise ConditionEvaluationError(
                f"established must be bool, got {type(self.established).__name__}"
            )
        object.__setattr__(
            self,
            "evidence_reference",
            _validate_reference(self.evidence_reference),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceFacts:
    """The immutable, strictly validated set of facts one evaluation consumes.

    Construction requires a tuple of :class:`EvidenceFact` (never a bare
    iterable, so nothing mutable or lazily executed can ride in), rejects
    oversized inputs, and normalizes to a canonical order — sorted by
    evidence kind, then reference, then assertion — so two fact sets that
    differ only in caller-supplied order are identical data. This is the
    complete evaluation input surface: there is no other channel through
    which facts could arrive.
    """

    facts: tuple[EvidenceFact, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.facts, tuple):
            raise ConditionEvaluationError("facts must be a tuple of EvidenceFact")
        if len(self.facts) > _MAX_FACTS:
            raise ConditionEvaluationError(f"facts must not exceed {_MAX_FACTS} entries")
        for fact in self.facts:
            if not isinstance(fact, EvidenceFact):
                raise ConditionEvaluationError(
                    f"each fact must be an EvidenceFact, got {type(fact).__name__}"
                )
        object.__setattr__(
            self,
            "facts",
            tuple(
                sorted(
                    self.facts,
                    key=lambda fact: (
                        fact.evidence_kind.value,
                        fact.evidence_reference or "",
                        fact.established,
                    ),
                )
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ConditionEvaluation:
    """The immutable result of evaluating one condition against one fact set.

    ``condition_id`` echoes the canonical
    :class:`~agentx.procedures.conditions.ConditionId` of the evaluated
    condition so a result can be correlated with its declaration without
    re-reading the condition. ``status`` is the complete deterministic
    answer. There is deliberately nothing else: no satisfied/holds helper
    beyond the status vocabulary, no reason text that could echo hostile
    strings, no timestamp, no generated id, no verdict object, and no field
    that could turn this value into authorization, verification, or Task
    success. A result is inert DATA.
    """

    condition_id: ConditionId
    status: ConditionEvaluationStatus

    def __post_init__(self) -> None:
        if not isinstance(self.condition_id, ConditionId):
            raise ConditionEvaluationError("condition_id must be a ConditionId")
        if not isinstance(self.status, ConditionEvaluationStatus):
            raise ConditionEvaluationError(
                f"status must be a ConditionEvaluationStatus, got {type(self.status).__name__}"
            )


def _addresses(fact: EvidenceFact, condition: ProcedureCondition) -> bool:
    """Return whether ``fact`` addresses ``condition``.

    Addressing is exact typed equality on the evidence coordinates the
    canonical condition contract already carries: the same
    :class:`~agentx.core.provenance.EvidenceKind` member and an equal
    ``evidence_reference`` (both ``None``, or the same string). Nothing is
    resolved, opened, fetched, or dereferenced, and the condition's
    ``statement`` is never read.
    """
    return fact.evidence_kind is condition.evidence_kind and (
        fact.evidence_reference == condition.evidence_reference
    )


def evaluate_condition(condition: ProcedureCondition, facts: EvidenceFacts) -> ConditionEvaluation:
    """Deterministically evaluate ONE canonical condition against explicit facts.

    The rule is total, order-independent, and readable in one breath:

        - collect the facts that address the condition (see :func:`_addresses`);
        - no addressing fact → ``UNKNOWN`` (missing evidence establishes
          nothing and is never converted to ``UNSATISFIED``);
        - addressing facts that all assert ``established`` → ``SATISFIED``;
        - addressing facts that all assert ``not established`` →
          ``UNSATISFIED``;
        - addressing facts that disagree → ``UNKNOWN`` (contradiction
          establishes nothing deterministically).

    Non-canonical inputs fail closed with :class:`ConditionEvaluationError`
    before any evaluation happens. The returned value is a new immutable
    :class:`ConditionEvaluation`; calling again with equal inputs returns an
    equal result. Evaluation is a pure function: it performs no I/O, touches
    no authority or runtime subsystem, and changes nothing anywhere.
    """
    if not isinstance(condition, ProcedureCondition):
        raise ConditionEvaluationError(
            f"condition must be a ProcedureCondition, got {type(condition).__name__}"
        )
    if not isinstance(facts, EvidenceFacts):
        raise ConditionEvaluationError(
            f"facts must be an EvidenceFacts, got {type(facts).__name__}"
        )

    addressing = tuple(fact for fact in facts.facts if _addresses(fact, condition))
    if not addressing:
        status = ConditionEvaluationStatus.UNKNOWN
    else:
        assertions = {fact.established for fact in addressing}
        if len(assertions) != 1:
            status = ConditionEvaluationStatus.UNKNOWN
        elif assertions == {True}:
            status = ConditionEvaluationStatus.SATISFIED
        else:
            status = ConditionEvaluationStatus.UNSATISFIED

    return ConditionEvaluation(condition_id=condition.id, status=status)
