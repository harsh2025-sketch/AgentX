"""Deterministic procedure applicability *matching* for AgentX (M4.04).

This module answers exactly one narrow question with no side effects:

    *Is this already-known procedure revision STRUCTURALLY COMPATIBLE with
    these explicitly requested capability/environment facts?*

It is the deterministic reuse gate in front of expensive planning. A stored
procedure must never be selected merely because its name resembles a goal,
because its payload contains similar words, because it is ``ACTIVE``, or
because a model says it probably fits. Only typed fields count.

MATCHING IS NOT ROUTING
=======================

This module is a **matching** boundary, deliberately narrower than every
neighbouring concern:

    * It does **not** choose an execution level. Nothing here names or
      influences ``L0``-``L5``; execution-level selection is owned by
      :mod:`agentx.cognition.router` (A2.07) and is never consulted from here.
    * It does **not** retrieve candidates. Candidate discovery belongs to the
      stores (``agentx.infrastructure.procedure_store``) and to future
      retrieval policy; this module holds no store, registry, Hive, database,
      filesystem, clock, network, or model reference of any kind.
    * It does **not** rank. There is deliberately no multi-candidate
      selection primitive, no score, and no ordering of candidates: those are
      planning/selection concerns and would turn this module into a selector.
      Callers that need to consider several revisions call
      :meth:`ProcedureApplicabilityMatcher.assess` once per revision and own
      their own (out-of-scope) choice.
    * It does **not** execute, activate, promote, retire, compile, or
      interpret anything, and it grants no authority of any kind.

Inputs (all caller-supplied, all typed, all bounded)
====================================================

    * :class:`ProcedureCandidate` — one candidate revision: the canonical
      :class:`~agentx.core.procedures.ProcedureRecord` plus an *explicit*
      :class:`CapabilityRequirement` when the capability the revision was
      recorded against is actually known. ``None`` means "not known", never
      "anything".
    * :class:`ProcedureRequirement` — the requested facts: the canonical
      :class:`~agentx.core.procedures.ProcedureScope` describing the
      application/environment/project/OS facts the caller asserts, plus an
      optional :class:`CapabilityRequirement`.

Both sides reuse the canonical scope vocabulary
(:class:`~agentx.core.procedures.ProcedureScopeDimension`). This module
defines **no** scope dimension vocabulary of its own and must never grow one.

Result vocabulary
=================

:class:`ProcedureMatchOutcome` has exactly four members:

    ``EXACT_MATCH``          every asserted dimension and the capability
                             identity/version (when asserted) agree, and
                             nothing is left unconfirmed.
    ``COMPATIBLE``           no contradiction exists, but something is broader
                             or unasserted: the revision is globally unscoped
                             on a dimension the request pins, or a capability
                             identity matches with no version asserted.
    ``INCOMPATIBLE``         a typed fact contradicts a typed fact (scope
                             value mismatch, capability identity mismatch, or
                             capability version mismatch).
    ``INSUFFICIENT_EVIDENCE`` the revision asserts something the caller has
                             not supplied evidence about. Absence of evidence
                             is never treated as a match.

No other outcome exists, and none of them means "may execute".

Scope rules (deterministic, per dimension)
==========================================

Both the candidate and the requirement are canonical
:class:`~agentx.core.procedures.ProcedureScope` maps. For every member of
:class:`~agentx.core.procedures.ProcedureScopeDimension`, in canonical
declaration order:

    * both sides assert a value, values are byte-equal  -> ``SCOPE_MATCH``
    * both sides assert a value, values differ          -> ``SCOPE_MISMATCH``
    * revision asserts nothing, request asserts a value
      -> ``SCOPE_UNSCOPED_ON_DIMENSION`` (the revision is globally applicable
      on that dimension, which contradicts nothing, but is not exact)
    * revision asserts a value, request asserts nothing
      -> ``MISSING_REQUIRED_EVIDENCE`` (unknown caller evidence)
    * neither asserts anything -> the dimension is not part of this match

Comparison is exact ``str`` equality on the canonical typed values. There is
no case folding, no trimming, no Unicode normalization, no prefix or
substring matching, no wildcard (``os=*``), no "any"/"latest" token, and no
similarity of any kind. ``"windows"`` and ``"Windows"`` and ``"windows "``
are three different values, and a Cyrillic small letter o (U+043E) is not a
Latin ``"o"``.

The two "missing" cases are deliberately *not* symmetric, and the asymmetry
is the whole point of this contract:

    * **Unscoped/global applicability** (revision silent) is an explicit
      recorded property of the revision — an empty scope *is* the canonical
      global scope — so it can never contradict a request.
    * **Unknown caller evidence** (request silent) is not a fact at all.
      Treating a silent request as "matches anything" would be wildcard
      authority, so it fails closed to ``INSUFFICIENT_EVIDENCE``.

Version rules
=============

Where an explicit capability version token exists, it is an opaque
exact-match token: two tokens match only when they are byte-equal. There is
no semantic-version range language, no ``>=1.2`` parsing, no caret/tilde
syntax, no ``*``, no ``latest``, and no regex. A different token is
``VERSION_MISMATCH``, never a silent match.

AgentX has no canonical capability-version type reachable from
``agentx.core`` (the ``major.minor.patch`` record lives outward in
``agentx.capabilities.abi``, which core must not import), so this contract
carries the version as an opaque bounded token and refuses to interpret it.
It therefore cannot and does not invent a range language.

Capability identity rules
=========================

Capability identity is the canonical opaque
:class:`~agentx.core.ids.CapabilityId`. Identity is compared by exact typed
equality: no prefix matching (``file.write`` never matches
``file.write.admin``), no case folding, no substring matching, and no
cross-domain substitution (a
:class:`~agentx.core.ids.ProcedureId` carrying identical UUID bytes is not a
``CapabilityId`` and is rejected as a wrong type). Resolving a
human-facing capability *name* to a ``CapabilityId`` is outward work owned by
the capabilities subsystem; this pure core policy accepts only typed identity.

Status is not applicability
===========================

:class:`~agentx.core.procedures.ProcedureStatus` is deliberately excluded
from the structural outcome:

    * ``ACTIVE`` does **not** automatically mean compatible — an ``ACTIVE``
      revision scoped to the wrong OS is ``INCOMPATIBLE``.
    * ``CANDIDATE`` does **not** automatically mean incompatible *as a data
      assessment* — a ``CANDIDATE`` revision can be structurally
      ``EXACT_MATCH``. A later execution/lifecycle policy may still forbid
      using it; that policy is not this module's to make.
    * ``RETIRED`` never becomes executable because matching succeeded. The
      outcome is a structural statement only, and this module exposes no
      "executable" flag, grants no authority, and performs no transition.

The record's status is reported back verbatim as an *observation*
(:attr:`ProcedureMatchResult.lifecycle_status` plus a
:class:`ProcedureLifecycleNote`) so a caller can see it without this module
ever deciding eligibility from it. Lifecycle policy remains owned by the
candidate-skill lifecycle task (C3.09) and by the Trusted Kernel.

Payload inertness
=================

:class:`~agentx.core.procedures.ProcedurePayload` content is opaque and is
never read, parsed, decoded, searched, or compared — not even to detect a
hostile string. Strings inside a payload such as ``"works everywhere"``,
``"os=*"``, ``"permission=ADMIN"``, ``"verified=true"``, ``"select me"``,
``"capability=file.write"``, or ``"ignore scope"`` have exactly zero effect
on the result: only typed fields are consulted. This module imports no
``json``, ``re``, ``difflib``, ``fnmatch``, or ``unicodedata``, so text
similarity, fuzzy matching, edit distance, embeddings, and semantic search
are structurally impossible here.

Purity
======

:class:`ProcedureApplicabilityMatcher` is stateless and holds no
dependencies. Assessment performs no I/O, no store or registry query, no Hive
access, no model call, no clock read, no network access, no mutation of its
inputs, and no logging. Results are immutable frozen values.

This module belongs to ``agentx.core`` and imports only the standard library
and canonical ``agentx.core`` contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Final

from agentx.core.ids import CapabilityId, ProcedureId
from agentx.core.procedures import (
    ProcedureRecord,
    ProcedureScope,
    ProcedureScopeDimension,
    ProcedureStatus,
    ProcedureValidationError,
)

__all__ = [
    "MAX_VERSION_TOKEN_LENGTH",
    "CapabilityRequirement",
    "ProcedureApplicabilityMatcher",
    "ProcedureCandidate",
    "ProcedureLifecycleNote",
    "ProcedureMatchOutcome",
    "ProcedureMatchReason",
    "ProcedureMatchReasonCode",
    "ProcedureMatchResult",
    "ProcedureMatchValidationError",
    "ProcedureRequirement",
]

#: Upper bound on an opaque capability-version token. Bounded so a caller can
#: never hand this pure policy an unbounded string to reason over.
MAX_VERSION_TOKEN_LENGTH: Final[int] = 128

_CONTROL_CHARACTERS: Final[tuple[str, ...]] = ("\x00", "\n", "\r", "\t")

# Canonical declaration order of the scope dimensions: the deterministic
# iteration order used for scope comparison, so the reason list never depends
# on the insertion order of either scope mapping.
_DIMENSION_ORDER: Final[MappingProxyType[ProcedureScopeDimension, int]] = MappingProxyType(
    {dimension: index for index, dimension in enumerate(ProcedureScopeDimension)}
)


class ProcedureMatchValidationError(ProcedureValidationError):
    """Raised when procedure-matching inputs violate the typed contract.

    A malformed *value* (for example an empty or control-character-bearing
    version token) is this error. A wrong *type* is a ``TypeError``: the
    contract never coerces, never case-folds, and never guesses.
    """


class ProcedureMatchOutcome(StrEnum):
    """Structural applicability verdict for one procedure revision.

    The vocabulary is deliberately closed. None of these members names an
    execution level, grants authority, or implies lifecycle eligibility.
    """

    #: Every asserted fact agrees and nothing is left unconfirmed.
    EXACT_MATCH = "exact_match"
    #: No contradiction, but something is broader or unasserted.
    COMPATIBLE = "compatible"
    #: A typed fact contradicts a typed fact.
    INCOMPATIBLE = "incompatible"
    #: The revision asserts something the caller supplied no evidence about.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ProcedureMatchReasonCode(StrEnum):
    """Structured, deterministic reason codes for one assessment."""

    #: A dimension asserted by both sides agreed byte-for-byte.
    SCOPE_MATCH = "scope_match"
    #: A dimension asserted by both sides disagreed.
    SCOPE_MISMATCH = "scope_mismatch"
    #: The revision is globally unscoped on a dimension the request pins.
    SCOPE_UNSCOPED_ON_DIMENSION = "scope_unscoped_on_dimension"
    #: Capability identity matched; optionally an exact version also matched.
    CAPABILITY_MATCH = "capability_match"
    #: Capability identity differed.
    CAPABILITY_MISMATCH = "capability_mismatch"
    #: Explicit capability versions were both present and differed.
    VERSION_MISMATCH = "version_mismatch"
    #: A fact was asserted on one side with no evidence on the other.
    MISSING_REQUIRED_EVIDENCE = "missing_required_evidence"


# Most-decisive reason first. Deterministic and independent of evaluation
# order: reasons are sorted by (precedence, canonical dimension order).
_REASON_PRECEDENCE: Final[MappingProxyType[ProcedureMatchReasonCode, int]] = MappingProxyType(
    {
        ProcedureMatchReasonCode.SCOPE_MISMATCH: 0,
        ProcedureMatchReasonCode.VERSION_MISMATCH: 1,
        ProcedureMatchReasonCode.CAPABILITY_MISMATCH: 2,
        ProcedureMatchReasonCode.MISSING_REQUIRED_EVIDENCE: 3,
        ProcedureMatchReasonCode.SCOPE_UNSCOPED_ON_DIMENSION: 4,
        ProcedureMatchReasonCode.SCOPE_MATCH: 5,
        ProcedureMatchReasonCode.CAPABILITY_MATCH: 6,
    }
)


class ProcedureLifecycleNote(StrEnum):
    """Verbatim observation of the record's storage status.

    This is an observation, never an eligibility decision and never a
    transition: it is reported so callers can see lifecycle state without
    this module folding it into the structural outcome.
    """

    STATUS_ACTIVE = "status_active"
    STATUS_CANDIDATE = "status_candidate"
    STATUS_RETIRED = "status_retired"


@dataclass(frozen=True, slots=True)
class ProcedureMatchReason:
    """One structured reason: a code, plus the dimension it applies to.

    ``dimension`` is ``None`` for capability-scoped reasons. There is no
    free-text detail field: explanations are codes, so they stay
    deterministic and unparseable as instruction.
    """

    code: ProcedureMatchReasonCode
    dimension: ProcedureScopeDimension | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, ProcedureMatchReasonCode):
            raise TypeError(
                f"code must be a ProcedureMatchReasonCode, got {type(self.code).__name__}"
            )
        if self.dimension is not None and not isinstance(self.dimension, ProcedureScopeDimension):
            raise TypeError(
                "dimension must be a ProcedureScopeDimension or None, "
                f"got {type(self.dimension).__name__}"
            )


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    """Explicit capability identity with an optional opaque version token.

    ``capability_id`` is the canonical :class:`~agentx.core.ids.CapabilityId`:
    exact typed identity, never a name, never a prefix, never a pattern.

    ``version`` is ``None`` when no version is asserted — which is *not* the
    same as "any version". A ``None`` version yields ``COMPATIBLE`` at best,
    never ``EXACT_MATCH``. When present, the token is compared byte-exactly:
    no ranges, no wildcards, no ``latest``, no parsing of any kind.
    """

    capability_id: CapabilityId
    version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.capability_id, CapabilityId):
            raise TypeError(
                f"capability_id must be a CapabilityId, got {type(self.capability_id).__name__}"
            )
        if self.version is not None:
            _validate_version_token(self.version)


@dataclass(frozen=True, slots=True)
class ProcedureRequirement:
    """Caller-supplied requested applicability facts.

    ``scope`` states the application/environment/project/OS facts the caller
    actually asserts, using the canonical
    :class:`~agentx.core.procedures.ProcedureScope` type (no new dimension
    vocabulary). An empty scope asserts nothing; it is not a wildcard and it
    is not "anywhere".

    ``capability`` states the required capability identity/version when it is
    explicitly known. ``None`` means the caller asserts nothing about
    capability, so the revision's own capability binding (if the caller
    supplied one) becomes unconfirmed evidence rather than a free match.
    """

    scope: ProcedureScope = field(default_factory=ProcedureScope)
    capability: CapabilityRequirement | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ProcedureScope):
            raise TypeError(f"scope must be a ProcedureScope, got {type(self.scope).__name__}")
        if self.capability is not None and not isinstance(self.capability, CapabilityRequirement):
            raise TypeError(
                "capability must be a CapabilityRequirement or None, got "
                f"{type(self.capability).__name__}"
            )


@dataclass(frozen=True, slots=True)
class ProcedureCandidate:
    """One candidate procedure revision plus its explicitly known binding.

    ``record`` is the canonical
    :class:`~agentx.core.procedures.ProcedureRecord`; only its typed
    ``scope``, ``status``, ``procedure_id``, and ``revision`` are read. The
    payload is never touched.

    ``capability`` is the capability the revision is known to have been
    recorded against, when (and only when) that is explicitly known.
    ``None`` means unknown, never universal.
    """

    record: ProcedureRecord
    capability: CapabilityRequirement | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.record, ProcedureRecord):
            raise TypeError(f"record must be a ProcedureRecord, got {type(self.record).__name__}")
        if self.capability is not None and not isinstance(self.capability, CapabilityRequirement):
            raise TypeError(
                "capability must be a CapabilityRequirement or None, got "
                f"{type(self.capability).__name__}"
            )


@dataclass(frozen=True, slots=True)
class ProcedureMatchResult:
    """Immutable, inert structural assessment of one procedure revision.

    The result is DATA. It cannot grant a Permission, change a RiskLevel,
    enlarge a ResourceEnvelope, bypass the Action Gate, clear an
    EmergencyStop, execute or activate a procedure, mutate a Task, or select
    an execution level. It names what was compared and how it compared —
    nothing more.
    """

    #: Identity of the assessed revision (never the payload).
    procedure_id: ProcedureId
    revision: int
    #: The structural verdict: scope and capability facts only.
    outcome: ProcedureMatchOutcome
    #: Ordered reason codes: most decisive first, then canonical dimension order.
    reasons: tuple[ProcedureMatchReason, ...]
    #: The record's storage status, reported verbatim as an observation only.
    lifecycle_status: ProcedureStatus
    #: Restatement of ``lifecycle_status`` as a note; never a decision.
    lifecycle_note: ProcedureLifecycleNote

    #: Invariant, not an outcome: matching can never grant authority.
    grants_execution_authority: ClassVar[bool] = False

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, ProcedureId):
            raise TypeError(
                f"procedure_id must be a ProcedureId, got {type(self.procedure_id).__name__}"
            )
        if not isinstance(self.revision, int) or isinstance(self.revision, bool):
            raise TypeError(f"revision must be an int, got {type(self.revision).__name__}")
        if not isinstance(self.outcome, ProcedureMatchOutcome):
            raise TypeError(
                f"outcome must be a ProcedureMatchOutcome, got {type(self.outcome).__name__}"
            )
        if not isinstance(self.reasons, tuple):
            raise TypeError(f"reasons must be a tuple, got {type(self.reasons).__name__}")
        for reason in self.reasons:
            if not isinstance(reason, ProcedureMatchReason):
                raise TypeError("reasons must contain only ProcedureMatchReason values")
        if not isinstance(self.lifecycle_status, ProcedureStatus):
            raise TypeError(
                "lifecycle_status must be a ProcedureStatus, got "
                f"{type(self.lifecycle_status).__name__}"
            )
        if not isinstance(self.lifecycle_note, ProcedureLifecycleNote):
            raise TypeError(
                "lifecycle_note must be a ProcedureLifecycleNote, got "
                f"{type(self.lifecycle_note).__name__}"
            )

    @property
    def structurally_applicable(self) -> bool:
        """Pure projection of :attr:`outcome` — NOT an execution statement.

        True only for ``EXACT_MATCH`` and ``COMPATIBLE``. It says the typed
        facts do not contradict each other; it says nothing about lifecycle
        eligibility, authority, or whether anything may run.
        """

        return self.outcome in (
            ProcedureMatchOutcome.EXACT_MATCH,
            ProcedureMatchOutcome.COMPATIBLE,
        )


class ProcedureApplicabilityMatcher:
    """Stateless deterministic applicability matcher.

    Holds no dependencies, no state, and no configuration. The only entry
    point is :meth:`assess`, which evaluates exactly one candidate against
    exactly one requirement — there is deliberately no multi-candidate,
    ranked, or "best match" primitive here.
    """

    __slots__ = ()

    def assess(
        self,
        candidate: ProcedureCandidate,
        requirement: ProcedureRequirement,
    ) -> ProcedureMatchResult:
        """Assess one procedure revision against one explicit requirement.

        The assessment is pure: it reads only the typed fields of
        ``candidate`` and ``requirement``, mutates nothing, and touches no
        store, registry, clock, model, or network. The record payload is
        never read.

        Raises:
            TypeError: if either argument is not the canonical typed input.
        """

        if not isinstance(candidate, ProcedureCandidate):
            raise TypeError(
                f"candidate must be a ProcedureCandidate, got {type(candidate).__name__}"
            )
        if not isinstance(requirement, ProcedureRequirement):
            raise TypeError(
                f"requirement must be a ProcedureRequirement, got {type(requirement).__name__}"
            )

        reasons: list[ProcedureMatchReason] = []
        incompatible = False
        insufficient = False
        scope_exact = True
        capability_exact = True

        procedure_scope = candidate.record.scope.dimensions
        request_scope = requirement.scope.dimensions

        for dimension in ProcedureScopeDimension:
            procedure_value = procedure_scope.get(dimension)
            request_value = request_scope.get(dimension)
            if procedure_value is None and request_value is None:
                continue
            if procedure_value is None:
                # The revision is globally unscoped on this dimension: an
                # explicit recorded property, so it contradicts nothing — but
                # it is broader than the request, so it is not an exact match.
                scope_exact = False
                reasons.append(
                    ProcedureMatchReason(
                        code=ProcedureMatchReasonCode.SCOPE_UNSCOPED_ON_DIMENSION,
                        dimension=dimension,
                    )
                )
            elif request_value is None:
                # The revision pins a fact the caller gave no evidence about.
                # Fail closed: silence is not a wildcard.
                insufficient = True
                scope_exact = False
                reasons.append(
                    ProcedureMatchReason(
                        code=ProcedureMatchReasonCode.MISSING_REQUIRED_EVIDENCE,
                        dimension=dimension,
                    )
                )
            elif procedure_value == request_value:
                reasons.append(
                    ProcedureMatchReason(
                        code=ProcedureMatchReasonCode.SCOPE_MATCH, dimension=dimension
                    )
                )
            else:
                incompatible = True
                scope_exact = False
                reasons.append(
                    ProcedureMatchReason(
                        code=ProcedureMatchReasonCode.SCOPE_MISMATCH, dimension=dimension
                    )
                )

        procedure_capability = candidate.capability
        request_capability = requirement.capability

        if procedure_capability is None and request_capability is None:
            # Nothing asserted on either side about capability.
            pass
        elif procedure_capability is None or request_capability is None:
            # Unknown caller evidence / unknown revision binding: absence of
            # evidence is never a match.
            insufficient = True
            capability_exact = False
            reasons.append(
                ProcedureMatchReason(code=ProcedureMatchReasonCode.MISSING_REQUIRED_EVIDENCE)
            )
        elif procedure_capability.capability_id != request_capability.capability_id:
            incompatible = True
            capability_exact = False
            reasons.append(ProcedureMatchReason(code=ProcedureMatchReasonCode.CAPABILITY_MISMATCH))
        elif (
            procedure_capability.version is not None
            and request_capability.version is not None
            and procedure_capability.version != request_capability.version
        ):
            incompatible = True
            capability_exact = False
            reasons.append(ProcedureMatchReason(code=ProcedureMatchReasonCode.VERSION_MISMATCH))
        else:
            # Identity matches exactly. An unasserted version on either side
            # is compatible but never exact.
            if procedure_capability.version is None or request_capability.version is None:
                capability_exact = False
            reasons.append(ProcedureMatchReason(code=ProcedureMatchReasonCode.CAPABILITY_MATCH))

        outcome = _resolve_outcome(
            incompatible=incompatible,
            insufficient=insufficient,
            exact=scope_exact and capability_exact,
        )

        return ProcedureMatchResult(
            procedure_id=candidate.record.procedure_id,
            revision=candidate.record.revision,
            outcome=outcome,
            reasons=_ordered_reasons(reasons),
            lifecycle_status=candidate.record.status,
            lifecycle_note=_lifecycle_note(candidate.record.status),
        )


def _validate_version_token(value: object) -> str:
    """Validate an opaque, byte-exact capability-version token."""
    if not isinstance(value, str):
        raise TypeError(f"version must be a string or None, got {type(value).__name__}")
    if value == "" or value != value.strip():
        raise ProcedureMatchValidationError("version must be non-empty and trimmed")
    if len(value) > MAX_VERSION_TOKEN_LENGTH:
        raise ProcedureMatchValidationError(
            f"version must not exceed {MAX_VERSION_TOKEN_LENGTH} characters"
        )
    if any(character in value for character in _CONTROL_CHARACTERS):
        raise ProcedureMatchValidationError("version must not contain control characters")
    return value


def _resolve_outcome(
    *, incompatible: bool, insufficient: bool, exact: bool
) -> ProcedureMatchOutcome:
    """Resolve the outcome from component verdicts, most decisive first."""
    if incompatible:
        return ProcedureMatchOutcome.INCOMPATIBLE
    if insufficient:
        return ProcedureMatchOutcome.INSUFFICIENT_EVIDENCE
    if exact:
        return ProcedureMatchOutcome.EXACT_MATCH
    return ProcedureMatchOutcome.COMPATIBLE


def _ordered_reasons(reasons: list[ProcedureMatchReason]) -> tuple[ProcedureMatchReason, ...]:
    """Order reasons by (precedence, canonical dimension order)."""

    def sort_key(reason: ProcedureMatchReason) -> tuple[int, int]:
        dimension_order = -1 if reason.dimension is None else _DIMENSION_ORDER[reason.dimension]
        return (_REASON_PRECEDENCE[reason.code], dimension_order)

    return tuple(sorted(reasons, key=sort_key))


def _lifecycle_note(status: ProcedureStatus) -> ProcedureLifecycleNote:
    """Restate a storage status as an observation (never an eligibility verdict)."""
    if status is ProcedureStatus.ACTIVE:
        return ProcedureLifecycleNote.STATUS_ACTIVE
    if status is ProcedureStatus.CANDIDATE:
        return ProcedureLifecycleNote.STATUS_CANDIDATE
    return ProcedureLifecycleNote.STATUS_RETIRED
