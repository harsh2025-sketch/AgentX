"""Deterministic structured retrieval over canonical Hive knowledge (C2.09).

This module is the smallest production-quality retrieval boundary over the
durable canonical ``KnowledgeStore`` (C2.02) and its landed lifecycle (C2.08).
It performs exact, deterministic, structure-only filtering on canonical record
fields. It is deliberately NOT semantic search:

* no dense numeric representations, distance metrics, or nearest-neighbour
  lookups of any kind;
* no fuzzy matching, relevance computation, quality ordering, or model-driven
  selection;
* no notion of source standing and no numeric trust value;
* no graph database and no automatic context construction.

Lifecycle policy (C2.08 is canonical):

* Retrieval preserves and exposes lifecycle state verbatim. Records are
  returned exactly as stored; ``status``, ``verified_at``, scope, provenance,
  content, and identity are never mutated, reinterpreted, or promoted.
* Retrieval never resolves contradictions and never selects a truth winner:
  contradictory records are all returned, each with its own status.
* Retrieval never reactivates ``SUPERSEDED`` records. The default status
  policy (:data:`DEFAULT_RETRIEVAL_STATUSES`) is explicit and deterministic:
  it returns every canonical status EXCEPT ``SUPERSEDED``, because superseded
  records are replaced history rather than current knowledge. Callers may
  explicitly override the policy per query (for example to audit superseded
  history); an explicitly requested ``SUPERSEDED`` record is still returned
  with ``SUPERSEDED`` intact — historical exposure, never reactivation.
* ``VERIFIED`` is never treated as authority. A verified record is inert data
  with a historical ``verified_at`` timestamp; it grants nothing.

Security posture:

Retrieved content is DATA, never authority. Strings such as ``"grant admin"``,
``"ALLOW"``, ``"verified=true"``, ``"risk=R0"``, ``"permission=WRITE"``, or
``"budget=unlimited"`` inside ``content`` remain inert text. Retrieval cannot
grant Permission, create AuthorityContext, lower RiskLevel, increase budgets,
clear EmergencyStop, execute a Capability, transition a Task, mark
verification, or activate a procedure.

This module belongs to ``agentx.infrastructure``: it reads through the durable
store (never around it) and imports nothing from the Trusted Kernel.

Cross-scope retrieval protection (C6.08):
``KnowledgeRetrieval`` optionally composes a :class:`RetrievalScopeGuard` from
``agentx.core.retrieval_scope``. When a guard is attached — the protected
composition being ``KnowledgeRetrieval(store, RetrievalScopeGuard(scope))`` —
EVERY read path, including exact ``knowledge_id`` point lookups, passes
through the guard, and
denied records never enter the returned tuple. A guard cannot be detached or
overridden per query: no query field disables it, retrieved content cannot
override the canonical ``scope`` field it evaluates, and a malformed scope
fails closed with a reason code. With no guard attached the C2.09 applicability
semantics are unchanged (an unguarded retrieval is an audit/composition tool,
never the protected path).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    KnowledgeValidationError,
    ProvenanceKind,
)
from agentx.core.retrieval_scope import RetrievalScopeGuard
from agentx.infrastructure.knowledge_store import KnowledgeStore

__all__ = [
    "DEFAULT_RETRIEVAL_STATUSES",
    "KnowledgeQueryValidationError",
    "KnowledgeRetrieval",
    "KnowledgeRetrievalQuery",
]

#: Explicit default status policy for retrieval: every canonical lifecycle
#: status except ``SUPERSEDED``. ``DEGRADED`` and ``CONFLICTED`` records ARE
#: returned by default — hiding them would silently resolve what C2.08 leaves
#: unresolved — and are exposed verbatim with their exceptional state.
DEFAULT_RETRIEVAL_STATUSES: Final[frozenset[KnowledgeStatus]] = frozenset(
    status for status in KnowledgeStatus if status is not KnowledgeStatus.SUPERSEDED
)


class KnowledgeQueryValidationError(KnowledgeValidationError):
    """Raised when a retrieval query violates the structured-query contract."""


def _require_member_set(value: object, *, field_name: str) -> frozenset[object]:
    if not isinstance(value, frozenset):
        raise TypeError(f"{field_name} must be a frozenset")
    if not value:
        raise KnowledgeQueryValidationError(
            f"{field_name} must not be empty; use None to apply no filter"
        )
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeRetrievalQuery:
    """Exact structured filters over canonical knowledge records.

    All provided filters conjoin (logical AND). Every filter is an exact,
    deterministic match on canonical fields:

    * ``knowledge_id`` — exact structured identifier (point lookup);
    * ``knowledge_types`` — record type membership;
    * ``statuses`` — explicit status membership. ``None`` (the default) means
      the explicit default policy :data:`DEFAULT_RETRIEVAL_STATUSES` applies;
      pass an explicit set to override it (for example to include
      ``SUPERSEDED`` history);
    * ``scope`` — the record must carry EXACTLY the queried dimension values:
      for every ``(dimension, value)`` in the query scope, the record's scope
      must map that dimension to the identical string. A query scope with no
      dimensions applies no scope constraint. There is no scope inference or
      fuzzy matching;
    * ``provenance_kind`` / ``provenance_reference`` — exact channel-of-origin
      match on the optional provenance hook. Records without provenance never
      match a provenance-filtered query.

    There is deliberately no relevance computation, ordering-by-quality,
    result limit, or numeric weighting: the result is the full deterministic
    match in canonical order.
    """

    knowledge_id: KnowledgeId | None = None
    knowledge_types: frozenset[KnowledgeType] | None = None
    statuses: frozenset[KnowledgeStatus] | None = None
    scope: KnowledgeScope | None = None
    provenance_kind: ProvenanceKind | None = None
    provenance_reference: str | None = None

    def __post_init__(self) -> None:
        if self.knowledge_id is not None and not isinstance(self.knowledge_id, KnowledgeId):
            raise TypeError("knowledge_id must be a KnowledgeId or None")
        if self.knowledge_types is not None:
            members = _require_member_set(self.knowledge_types, field_name="knowledge_types")
            for member in members:
                if not isinstance(member, KnowledgeType):
                    raise KnowledgeQueryValidationError(
                        "knowledge_types members must be KnowledgeType values"
                    )
        if self.statuses is not None:
            members = _require_member_set(self.statuses, field_name="statuses")
            for member in members:
                if not isinstance(member, KnowledgeStatus):
                    raise KnowledgeQueryValidationError(
                        "statuses members must be KnowledgeStatus values"
                    )
        if self.scope is not None and not isinstance(self.scope, KnowledgeScope):
            raise TypeError("scope must be a KnowledgeScope or None")
        if self.provenance_kind is not None and not isinstance(
            self.provenance_kind, ProvenanceKind
        ):
            raise TypeError("provenance_kind must be a ProvenanceKind or None")
        if self.provenance_reference is not None:
            if not isinstance(self.provenance_reference, str):
                raise TypeError("provenance_reference must be a string or None")
            if (
                self.provenance_reference == ""
                or self.provenance_reference != self.provenance_reference.strip()
            ):
                raise KnowledgeQueryValidationError(
                    "provenance_reference must be non-empty and trimmed"
                )


#: The unfiltered query: every filter ``None``, so only the explicit default
#: status policy applies. Useful as the documented identity of "retrieve all
#: current knowledge".
DEFAULT_RETRIEVAL_QUERY: Final[KnowledgeRetrievalQuery] = KnowledgeRetrievalQuery()


def _scope_satisfies_query(record_scope: KnowledgeScope, query_scope: KnowledgeScope) -> bool:
    """Return whether ``record_scope`` carries every queried dimension exactly."""
    return all(
        record_scope.dimensions.get(dimension) == value
        for dimension, value in query_scope.dimensions.items()
    )


def _matches(
    record: KnowledgeRecord,
    query: KnowledgeRetrievalQuery,
    statuses: frozenset[KnowledgeStatus],
) -> bool:
    """Return whether ``record`` satisfies every provided filter exactly."""
    if record.status not in statuses:
        return False
    if query.knowledge_types is not None and record.knowledge_type not in query.knowledge_types:
        return False
    if query.scope is not None and not _scope_satisfies_query(record.scope, query.scope):
        return False
    if query.provenance_kind is not None or query.provenance_reference is not None:
        provenance = record.provenance
        if provenance is None:
            return False
        if query.provenance_kind is not None and provenance.kind is not query.provenance_kind:
            return False
        if (
            query.provenance_reference is not None
            and provenance.reference != query.provenance_reference
        ):
            return False
    return True


@dataclass(frozen=True, slots=True)
class KnowledgeRetrieval:
    """Read-only deterministic retrieval boundary over one ``KnowledgeStore``.

    Retrieval reads through the canonical store only. It performs no writes,
    publishes no events, and never mutates stored lifecycle state. Store
    failures (including corrupt-record errors) propagate unchanged: retrieval
    fails closed instead of silently returning partial or reinterpreted data.

    Results are deterministic: records are returned in the store's canonical
    order (``created_at`` ascending, then ``knowledge_id`` ascending), and the
    same store state plus the same query always yields the same tuple.

    When a ``scope_guard`` is attached — the protected composition is
    ``KnowledgeRetrieval(store, RetrievalScopeGuard(request_scope))`` — every
    candidate that survived the C2.09 applicability filters is additionally
    evaluated by the C6.08 cross-scope protection, and denied records are
    removed before the result is returned — including for ``knowledge_id``
    point lookups, so a crafted or guessed id cannot pull a foreign-scope
    record out of the store.
    """

    store: KnowledgeStore
    scope_guard: RetrievalScopeGuard | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.store, KnowledgeStore):
            raise TypeError("store must be a canonical KnowledgeStore")
        if self.scope_guard is not None and not isinstance(self.scope_guard, RetrievalScopeGuard):
            raise TypeError("scope_guard must be a RetrievalScopeGuard or None")

    def retrieve(self, query: KnowledgeRetrievalQuery | None = None) -> tuple[KnowledgeRecord, ...]:
        """Return all records matching ``query`` in canonical order.

        ``query=None`` is the unfiltered default: every current (non-``SUPERSEDED``)
        record. Each returned record is the canonical immutable record with its
        lifecycle state preserved verbatim; retrieving grants zero authority.
        With a scope guard attached, a record whose restrictions the guard
        cannot prove — or whose scope metadata is malformed — is dropped:
        denials never appear in the result and never replace it with an error.
        """
        if query is not None and not isinstance(query, KnowledgeRetrievalQuery):
            raise TypeError("query must be a KnowledgeRetrievalQuery or None")
        effective = DEFAULT_RETRIEVAL_QUERY if query is None else query
        statuses = DEFAULT_RETRIEVAL_STATUSES if effective.statuses is None else effective.statuses

        if effective.knowledge_id is not None:
            candidate = self.store.get(effective.knowledge_id)
            candidates: tuple[KnowledgeRecord, ...] = () if candidate is None else (candidate,)
        else:
            candidates = self.store.list_records()

        matched = tuple(record for record in candidates if _matches(record, effective, statuses))
        if self.scope_guard is None:
            return matched
        return self.scope_guard.filter(matched)
