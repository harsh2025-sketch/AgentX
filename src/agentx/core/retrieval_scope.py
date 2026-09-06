"""Deterministic cross-scope retrieval protection for canonical records (C6.08).

This module is the explicit scope-compatibility boundary that keeps Hive
retrieval from leaking records across incompatible scopes. It decides, for one
candidate record and one caller-declared request scope, whether the record may
enter the retrieval result. It is a pure-data predicate: it reads no storage,
executes no code, and interprets no text.

Relationship to the existing scope contract (C2.02 / C2.07 / C2.09)
====================================================================

``KnowledgeScope`` and ``ScopeDimension`` stay owned by
``agentx.core.knowledge`` unchanged. No hierarchy, wildcard, inheritance,
prefix, or fuzzy scope semantics are introduced here — none exist in the
current architecture, and inventing them is explicitly out of scope. This
boundary uses exactly the current per-dimension exact-string equality model.

The existing C2.09 retrieval filter answers an *applicability* question ("does
this record carry the queried dimension values?"). This module answers the
orthogonal *isolation* question ("may this record be shown to this request
context at all?"). The two compose: a guarded retrieval applies the store
filter first and this protection second, and a denial here removes the record
no matter how well it matched the applicability filter.

Compatibility rule (deterministic, conservative)
=================================================

A record is visible exactly when EVERY restriction the record asserts is
proven by the request context. Formally, for every ``(dimension, value)`` in
the record's scope, the request scope must contain the same dimension with the
identical string value:

* record scope equals request scope (including both empty)  -> allowed
  (:data:`ScopeAccessReason.ALLOW_SAME_SCOPE`);
* record scope empty while the request scope is not          -> allowed
  (:data:`ScopeAccessReason.ALLOW_GLOBAL_RECORD`) because a global record
  asserts no restriction to leak — mirroring the documented meaning of an
  empty scope in C2.02/C2.05;
* every record restriction matched while the request names extra dimensions
  the record does not mention -> allowed
  (:data:`ScopeAccessReason.ALLOW_RESTRICTIONS_SATISFIED`);
* a dimension the record restricts holds a different value in the request
  -> denied (:data:`ScopeAccessReason.DENY_SCOPE_MISMATCH`) — this is the
  cross-project / cross-environment / cross-application leak;
* the record restricts a dimension the request does not state -> denied
  (:data:`ScopeAccessReason.DENY_RESTRICTION_UNPROVEN`) — the caller cannot
  prove membership in a scope it never names, so retrieval fails closed.

Malformed data fails closed
===========================

* A request scope that is not a canonical, well-formed ``KnowledgeScope``
  cannot build a guard at all (:class:`RetrievalScopeError`; omitting the
  required field is a ``TypeError``): there is no "unrestricted" sentinel and
  ``None`` is never accepted.
* A candidate that is not one of the canonical scoped record types
  (:class:`agentx.core.knowledge.KnowledgeRecord` or
  :class:`agentx.core.negative_experience.NegativeExperienceRecord`) is denied
  with :data:`ScopeAccessReason.DENY_INVALID_RECORD` instead of raising, so a
  corrupt or duck-typed store entry never crashes a batch and never leaks.
* A scoped-looking record whose ``scope`` attribute was bypassed or corrupted
  (not an exact ``KnowledgeScope``, unknown dimension keys, non-string, empty,
  or untrimmed values) is denied with
  :data:`ScopeAccessReason.DENY_MALFORMED_RECORD_SCOPE`. The boundary never
  trusts ``KnowledgeScope``'s construction-time guarantees; the scope mapping
  is fully re-validated on every decision.

No caller-controlled bypass
===========================

The guard exposes no "include everything" flag, no per-record scope override,
and no fallback for unparseable scopes. Request scope is a constructor
requirement validated once and snapshotted immutably. A caller may only *narrow*
what a guard can prove, never widen it: weakening the request scope can only
turn more records into denials (an unstated dimension can never prove a
record's restriction), so "forgetting" the scope is never a bypass.

Scope metadata cannot be overridden by retrieved content
=========================================================

Visibility depends on exactly one input per record: the canonical, typed
``scope`` field of the stored record. ``content``, provenance references,
locators, evidence text, or any other string are never consulted — a stored
record claiming ``scope: project=prod`` or "this record is global" in its text
changes nothing, because text is DATA, never authority. Retrieval also never
writes: this module mutates no record and promotes no status, and authority
remains exclusively owned by ``agentx.kernel``, which this module never
imports or references.

This module belongs to ``agentx.core``: it imports nothing outside
``agentx.core`` and the standard library, so both ``agentx.hive`` and
``agentx.infrastructure`` can compose it along existing dependency edges.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    ScopeDimension,
)
from agentx.core.negative_experience import NegativeExperienceRecord

__all__ = [
    "ProtectedRecord",
    "RetrievalScopeError",
    "RetrievalScopeGuard",
    "ScopeAccessReason",
    "ScopeDecision",
    "ScopeDenial",
    "ScopePartition",
    "evaluate_record_scope",
]

#: The canonical record types that carry a ``KnowledgeScope`` and are therefore
#: subject to cross-scope protection. The list is closed: a duck-typed or
#: subclassed look-alike is denied, not "best-effort" interpreted.
_CANONICAL_SCOPED_RECORD_TYPES: Final[
    tuple[type[KnowledgeRecord] | type[NegativeExperienceRecord], ...]
] = (KnowledgeRecord, NegativeExperienceRecord)


class ScopeAccessReason(StrEnum):
    """Closed reason-code vocabulary for one scope-compatibility decision.

    Every decision — allow or deny — carries exactly one code. Codes are data
    for logging and tests only; they never alter a record and grant no
    authority. A denial may name the offending :class:`ScopeDimension` but
    never carries any part of the denied record (no id, content, scope value,
    or provenance), so denial reports cannot leak details of a record the
    caller's scope is not allowed to see.
    """

    ALLOW_SAME_SCOPE = "allow_same_scope"
    ALLOW_GLOBAL_RECORD = "allow_global_record"
    ALLOW_RESTRICTIONS_SATISFIED = "allow_restrictions_satisfied"
    DENY_SCOPE_MISMATCH = "deny_scope_mismatch"
    DENY_RESTRICTION_UNPROVEN = "deny_restriction_unproven"
    DENY_MALFORMED_RECORD_SCOPE = "deny_malformed_record_scope"
    DENY_INVALID_RECORD = "deny_invalid_record"


_ALLOWED_REASONS: Final[frozenset[ScopeAccessReason]] = frozenset(
    {
        ScopeAccessReason.ALLOW_SAME_SCOPE,
        ScopeAccessReason.ALLOW_GLOBAL_RECORD,
        ScopeAccessReason.ALLOW_RESTRICTIONS_SATISFIED,
    }
)


class RetrievalScopeError(ValueError):
    """Raised when a request scope is too malformed to build any decision."""


def _snapshot_scope(scope: object) -> dict[ScopeDimension, str] | None:
    """Return a validated plain-dict snapshot of ``scope``, or ``None``.

    ``None`` means "malformed — deny". Validation never trusts the mapping the
    holder hands over: the exact runtime type is required, and every key and
    value is re-validated from the live mapping exactly once into a private
    snapshot, so a mutable look-alike cannot change a decision after it was
    made (time-of-check/time-of-use).
    """
    if type(scope) is not KnowledgeScope:
        return None
    # An explicit ``object`` annotation keeps the runtime shape check
    # meaningful: a tampered frozen dataclass can put anything in
    # ``dimensions``, and this boundary re-validates instead of trusting.
    dimensions: object = scope.dimensions
    if not isinstance(dimensions, Mapping):
        return None
    snapshot: dict[ScopeDimension, str] = {}
    try:
        entries = list(dimensions.items())
    except Exception:
        # A hostile mapping that raises while being iterated is malformed
        # data, not a crash signal: deny, never propagate out of a batch.
        return None
    for key, value in entries:
        # Enum members are singletons; the exact-type guard rejects string
        # look-alikes (StrEnum compares equal to str on purpose).
        if type(key) is not ScopeDimension:
            return None
        if key in snapshot:
            return None
        if type(value) is not str or value == "" or value != value.strip():
            return None
        snapshot[key] = value
    return snapshot


@dataclass(frozen=True, slots=True)
class ScopeDecision:
    """The outcome of one scope-compatibility check.

    ``dimension`` names the constrained :class:`ScopeDimension` that caused a
    denial, for logging only; it is ``None`` for every allow and for denials
    that are not attributable to a single dimension. The decision carries no
    part of the record itself.
    """

    reason: ScopeAccessReason
    dimension: ScopeDimension | None = None

    @property
    def allowed(self) -> bool:
        """Return whether the record may enter the result set."""
        return self.reason in _ALLOWED_REASONS


def _decide(
    record: object,
    request_dimensions: Mapping[ScopeDimension, str],
) -> ScopeDecision:
    """Evaluate one candidate against a validated request-scope snapshot."""
    if type(record) not in _CANONICAL_SCOPED_RECORD_TYPES:
        return ScopeDecision(ScopeAccessReason.DENY_INVALID_RECORD)
    record_dimensions = _snapshot_scope(getattr(record, "scope", None))
    if record_dimensions is None:
        return ScopeDecision(ScopeAccessReason.DENY_MALFORMED_RECORD_SCOPE)
    if record_dimensions == dict(request_dimensions):
        return ScopeDecision(ScopeAccessReason.ALLOW_SAME_SCOPE)
    if not record_dimensions:
        return ScopeDecision(ScopeAccessReason.ALLOW_GLOBAL_RECORD)
    for dimension in sorted(record_dimensions, key=lambda item: item.value):
        if dimension not in request_dimensions:
            return ScopeDecision(ScopeAccessReason.DENY_RESTRICTION_UNPROVEN, dimension)
        if request_dimensions[dimension] != record_dimensions[dimension]:
            return ScopeDecision(ScopeAccessReason.DENY_SCOPE_MISMATCH, dimension)
    return ScopeDecision(ScopeAccessReason.ALLOW_RESTRICTIONS_SATISFIED)


def evaluate_record_scope(record: object, request_scope: KnowledgeScope) -> ScopeDecision:
    """Return the deterministic visibility decision for ``record``.

    A pure predicate over caller-supplied values: it performs no retrieval,
    so evaluating a hypothetical record can never confirm that such a record
    exists in any store. ``request_scope`` must be a well-formed canonical
    ``KnowledgeScope``; anything else raises instead of defaulting open.
    """
    request_dimensions = _snapshot_scope(request_scope)
    if request_dimensions is None:
        raise RetrievalScopeError("request_scope is not a well-formed canonical KnowledgeScope")
    return _decide(record, MappingProxyType(request_dimensions))


@dataclass(frozen=True, slots=True)
class ScopeDenial:
    """One denied candidate inside a batch, identified by position only.

    ``index`` is the zero-based position in the iterable that was handed to
    :meth:`RetrievalScopeGuard.partition`; the denial carries no id, content,
    or scope value of the denied record.
    """

    index: int
    reason: ScopeAccessReason
    dimension: ScopeDimension | None = None


#: One record that passed cross-scope protection: a canonical scoped record.
ProtectedRecord = KnowledgeRecord | NegativeExperienceRecord


@dataclass(frozen=True, slots=True)
class ScopePartition:
    """Result of one batch evaluation: the allowed records and the denials.

    ``allowed`` preserves the input order exactly (filtering never reorders)
    and may be used by callers; ``denials`` explains what was removed. A
    denied record never appears in ``allowed``.
    """

    allowed: tuple[ProtectedRecord, ...]
    denials: tuple[ScopeDenial, ...]

    @property
    def denied_count(self) -> int:
        """Number of candidates removed by scope protection."""
        return len(self.denials)


@dataclass(frozen=True, slots=True)
class RetrievalScopeGuard:
    """Immutable cross-scope filter for one caller-declared request context.

    The guard binds exactly one ``request_scope`` at construction, validates
    it strictly, and snapshots it, so:

    * ``request_scope`` is a required field: a guard cannot exist without a
      declared context, and "unknown scope" fails at construction instead of
      defaulting open;
    * there is no per-call "scope" parameter that a caller could pick to widen
      access beyond its own context;
    * there is no bypass flag, allow-list, or "global" sentinel; a malformed
      scope raises;
    * a malformed request scope fails closed at construction: a broken guard
      cannot serve a batch at all.

    Because denials can only grow as the request scope loses information, a
    caller cannot fabricate visibility by omitting dimensions: an unstated
    dimension can never prove a record's restriction. Values are opaque
    strings compared exactly; no case folding, re-trimming, or Unicode
    normalization is applied by this module.
    """

    request_scope: KnowledgeScope
    _request_dimensions: Mapping[ScopeDimension, str] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        request_dimensions = _snapshot_scope(self.request_scope)
        if request_dimensions is None:
            raise RetrievalScopeError(
                "request_scope must be a well-formed canonical KnowledgeScope; "
                "scope protection fails closed instead of guessing"
            )
        object.__setattr__(self, "_request_dimensions", MappingProxyType(request_dimensions))

    def evaluate(self, record: object) -> ScopeDecision:
        """Return the visibility decision for one candidate record."""
        return _decide(record, self._request_dimensions)

    def partition(self, records: Iterable[object]) -> ScopePartition:
        """Split a candidate batch into allowed records and positional denials.

        Evaluation is per-candidate and never aborts the batch: one malformed
        record denies itself while the rest of the batch is decided normally.
        Input order is preserved in ``allowed``; denials are ordered by index.
        """
        if isinstance(records, (str, bytes)):
            raise TypeError("records must be an iterable of candidate records")
        allowed: list[ProtectedRecord] = []
        denials: list[ScopeDenial] = []
        for index, record in enumerate(records):
            decision = self.evaluate(record)
            if decision.allowed:
                # The isinstance test re-establishes the canonical type for
                # the result tuple; a non-canonical candidate is always denied
                # (DENY_INVALID_RECORD precedes every allow), so the allowed
                # branch can only ever see canonical records.
                if isinstance(record, (KnowledgeRecord, NegativeExperienceRecord)):
                    allowed.append(record)
                else:  # pragma: no cover - defensive, unreachable by policy
                    denials.append(
                        ScopeDenial(
                            index=index,
                            reason=ScopeAccessReason.DENY_INVALID_RECORD,
                            dimension=None,
                        )
                    )
            else:
                denials.append(
                    ScopeDenial(index=index, reason=decision.reason, dimension=decision.dimension)
                )
        return ScopePartition(allowed=tuple(allowed), denials=tuple(denials))

    def filter[T](self, records: Iterable[T]) -> tuple[T, ...]:
        """Return only the records the guard's request scope proves visible.

        Convenience over :meth:`partition` for callers that do not need denial
        detail; denied records never enter the returned tuple, and the
        candidate element type is preserved exactly (no reinterpretation).
        """
        if isinstance(records, (str, bytes)):
            raise TypeError("records must be an iterable of candidate records")
        return tuple(record for record in records if self.evaluate(record).allowed)
