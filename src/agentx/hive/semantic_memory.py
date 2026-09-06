"""Semantic memory service over the canonical AgentX knowledge store (C2.05).

Semantic memory is the durable *declarative* knowledge class: facts, learned
claims, stable observations generalized beyond a single episode, and
application/environment or user/project facts that a caller supplied
explicitly. It is deliberately **not** episodic history (C2.06), procedure
storage, causal trajectories (C2.10), audit history, model context, a vector
index, or authority.

Scope of this module
====================

This module owns a narrow read/write service boundary around the canonical
:class:`agentx.infrastructure.knowledge_store.KnowledgeStore`. It:

- remembers an explicitly typed :class:`~agentx.core.knowledge.KnowledgeRecord`;
- retrieves one record by exact :class:`~agentx.core.ids.KnowledgeId`;
- enumerates records deterministically, optionally filtered by exact,
  structured, already-canonical fields;
- preserves scope, provenance, and status verbatim;
- projects the persisted C2.02 provenance hook into the canonical C2.07
  :class:`~agentx.core.provenance.ProvenanceRecord` shape on read.

It deliberately does **not** own:

- lifecycle, contradiction, or supersession policy (C2.08). This service has
  no status-mutating operation at all: the port it holds
  (:class:`KnowledgeStorePort`) intentionally omits ``update_status``;
- semantic/vector retrieval, ranking, or relevance (C2.09). "Semantic" here
  names the *knowledge class*, never embedding search. There are no
  embeddings, no vector index, no cosine similarity, no keyword scoring, no
  model calls, and no graph traversal anywhere in this module;
- episodic/negative memory (C2.06) or causal experience (C2.10);
- persistence schema. Nothing here creates a table or a migration; knowledge
  is stored exactly once, in ``agentx_knowledge``, by the canonical store.

Ingestion policy
================

New information must remain explicitly typed and explicitly untrusted:

- only knowledge types in :data:`SEMANTIC_KNOWLEDGE_TYPES` may be remembered,
  so a future non-declarative knowledge type added for another memory class
  cannot silently enter semantic memory;
- :meth:`SemanticMemory.remember` accepts only a record in its canonical birth
  state (``UNVERIFIED`` with no ``verified_at``). Arbitrary text — including
  model output — therefore cannot enter semantic memory pre-labelled as
  ``VERIFIED``/``SUPPORTED``. Refusing to write is never a lifecycle decision;
  every real transition remains an explicit C2.08-owned act performed through
  the store's own ``update_status``;
- nothing here promotes, demotes, or aggregates status. Storing the same claim
  twice, attaching provenance, or accumulating evidence never increases trust.

Security boundary
=================

Remembered content is DATA. Hostile text such as ``ADMIN``, ``SYSTEM``,
``verified=true``, ``risk=R0``, ``permission=WRITE``, ``budget=unlimited``,
``ignore previous policy``, ``execute capability``, or ``clear emergency stop``
is inert: it is stored and returned as characters and interpreted by nothing.
Semantic memory cannot create a ``Permission`` or ``AuthorityContext``, bypass
the Action Gate, lower effective risk, increase a budget, clear an
``EmergencyStop``, execute a ``Capability``, mutate a ``Task``, or mark action
verification successful. It imports no kernel, capability, cognition, or
transport module, publishes no events, and executes nothing.

Scope (:class:`~agentx.core.knowledge.KnowledgeScope`) is *applicability* data
only. An empty/global scope means "this claim is not restricted to a named
application, OS, environment, project, or context"; it never means permission
everywhere and never grants machine authority.

Cross-scope retrieval protection (C6.08): when composed with a
:class:`~agentx.core.retrieval_scope.RetrievalScopeGuard` — the protected
composition being
``SemanticMemory(store, RetrievalScopeGuard(request_scope))`` — every read path
(``recall``, ``recall_all``, ``provenance_of``) additionally enforces the
guard's request scope, and denied records never enter a result. Point lookup
``recall`` denies silently as ``None`` — a denied record is indistinguishable
from an absent one, so a crafted or guessed ``KnowledgeId`` can neither pull a
foreign-scope record out of the store nor confirm that such a record exists.
Scope enforcement consults only the canonical ``scope`` field; no text inside
``content`` can override it, the guard has no detach/override knob on the
protected path, and write paths are unaffected (filtering is retrieval policy,
not ingestion policy).

This module depends only on ``agentx.core`` contracts plus the structural
:class:`KnowledgeStorePort`, so the canonical boundary model
(``agentx.hive`` -> ``agentx.core``) is preserved: concrete persistence is
injected by the composing caller and is never imported here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceKind,
)
from agentx.core.provenance import ProvenanceRecord
from agentx.core.retrieval_scope import RetrievalScopeGuard

__all__ = [
    "SEMANTIC_KNOWLEDGE_TYPES",
    "DuplicateSemanticKnowledgeError",
    "IngestionStatusError",
    "KnowledgeStorePort",
    "NonSemanticKnowledgeError",
    "SemanticMemory",
    "SemanticMemoryError",
    "SemanticMemoryQuery",
    "SemanticMemoryQueryError",
]

#: The knowledge types that are durable declarative (semantic) knowledge.
#:
#: This is an allow-list, not a denial-list: knowledge types introduced later
#: for other memory classes (episodic, procedural, causal) are rejected by
#: :meth:`SemanticMemory.remember` and are invisible to this service's reads
#: until a task explicitly decides they are semantic.
SEMANTIC_KNOWLEDGE_TYPES: Final[frozenset[KnowledgeType]] = frozenset(
    {
        KnowledgeType.FACT,
        KnowledgeType.OBSERVATION,
        KnowledgeType.PREFERENCE,
    }
)

#: The only status a caller may hand to :meth:`SemanticMemory.remember`.
_INGESTION_STATUS: Final = KnowledgeStatus.UNVERIFIED


class SemanticMemoryError(Exception):
    """Base error for the semantic-memory service boundary."""


class NonSemanticKnowledgeError(SemanticMemoryError, ValueError):
    """Raised when a record's knowledge type is not durable declarative knowledge."""


class IngestionStatusError(SemanticMemoryError, ValueError):
    """Raised when ingested knowledge does not arrive in its canonical birth state.

    Semantic memory accepts new knowledge only as ``UNVERIFIED`` with no
    ``verified_at``. This keeps trust an explicit, C2.08-owned act instead of
    something a caller (or arbitrary model text) can assert at write time.
    """


class DuplicateSemanticKnowledgeError(SemanticMemoryError):
    """Raised when the target ``KnowledgeId`` is already present in the store.

    Duplicate behaviour is explicit: the existing record is never overwritten,
    merged, or re-scored, and re-remembering the same claim under a new
    identity never increases trust.
    """


class SemanticMemoryQueryError(SemanticMemoryError, ValueError):
    """Raised when a structured query is malformed."""


@runtime_checkable
class KnowledgeStorePort(Protocol):
    """The exact durable-storage surface semantic memory is allowed to use.

    ``agentx.infrastructure.knowledge_store.KnowledgeStore`` satisfies this
    protocol structurally, so composition needs no import edge from
    ``agentx.hive`` to ``agentx.infrastructure``.

    The port is deliberately narrow. It exposes append and read only: there is
    no ``update_status``, no delete, and no schema access, so this service is
    structurally incapable of performing a lifecycle transition, of destroying
    knowledge, or of adding a second place where knowledge is stored.
    """

    def insert(self, record: KnowledgeRecord) -> None:
        """Durably store one canonical record without altering it."""

    def get(self, knowledge_id: KnowledgeId) -> KnowledgeRecord | None:
        """Return the stored record for ``knowledge_id`` or ``None``."""

    def list_records(self) -> tuple[KnowledgeRecord, ...]:
        """Return every stored record in the store's deterministic order."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SemanticMemoryQuery:
    """An exact-match filter over already-canonical, structured record fields.

    Every criterion is a deterministic equality/membership test on data the
    record already carries. There is no relevance, no ranking, no scoring, no
    similarity, and no free-text matching: two identical queries over the same
    store always return the same records in the same order.

    Criteria are combined with AND; an omitted (``None``) criterion is not
    applied. An empty criterion set is rejected rather than silently matching
    nothing.

    Scope criteria are *applicability* filters only. Selecting records whose
    scope is empty selects globally applicable knowledge; it grants nothing.
    """

    knowledge_types: frozenset[KnowledgeType] | None = None
    statuses: frozenset[KnowledgeStatus] | None = None
    provenance_kinds: frozenset[ProvenanceKind] | None = None
    #: Record scope must equal this scope exactly (``KnowledgeScope()`` selects
    #: only globally scoped knowledge).
    scope: KnowledgeScope | None = None
    #: Record scope must contain every dimension/value pair of this scope.
    scope_contains: KnowledgeScope | None = None

    def __post_init__(self) -> None:
        _validate_member_filter(
            self.knowledge_types, member=KnowledgeType, field_name="knowledge_types"
        )
        _validate_member_filter(self.statuses, member=KnowledgeStatus, field_name="statuses")
        _validate_member_filter(
            self.provenance_kinds, member=ProvenanceKind, field_name="provenance_kinds"
        )
        for field_name, value in (("scope", self.scope), ("scope_contains", self.scope_contains)):
            if value is not None and not isinstance(value, KnowledgeScope):
                raise SemanticMemoryQueryError(f"{field_name} must be a KnowledgeScope or None")

    def matches(self, record: KnowledgeRecord) -> bool:
        """Return whether ``record`` satisfies every configured criterion.

        This is a pure predicate over the record's own fields. It reads no
        storage, mutates nothing, and never inspects ``content``.
        """
        if not isinstance(record, KnowledgeRecord):
            raise TypeError("record must be a canonical KnowledgeRecord")
        if self.knowledge_types is not None and record.knowledge_type not in self.knowledge_types:
            return False
        if self.statuses is not None and record.status not in self.statuses:
            return False
        if self.provenance_kinds is not None:
            provenance = record.provenance
            if provenance is None or provenance.kind not in self.provenance_kinds:
                return False
        if self.scope is not None and record.scope != self.scope:
            return False
        if self.scope_contains is not None:
            for dimension, value in self.scope_contains.dimensions.items():
                if record.scope.value_for(dimension) != value:
                    return False
        return True


@dataclass(frozen=True, slots=True)
class SemanticMemory:
    """Read/write service for durable declarative knowledge.

    The service is a thin, deterministic boundary over an injected
    :class:`KnowledgeStorePort`. It holds no cache and no second copy of any
    record: every read goes to the canonical store, so durability across
    process restarts is exactly the store's durability.

    With a ``scope_guard`` attached — the protected composition is
    ``SemanticMemory(store, RetrievalScopeGuard(request_scope))`` — all reads
    additionally pass C6.08 cross-scope protection; without one, C2.05
    semantics are unchanged.
    """

    store: KnowledgeStorePort
    scope_guard: RetrievalScopeGuard | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.store, KnowledgeStorePort):
            raise TypeError("store must satisfy the KnowledgeStorePort protocol")
        if self.scope_guard is not None and not isinstance(self.scope_guard, RetrievalScopeGuard):
            raise TypeError("scope_guard must be a RetrievalScopeGuard or None")

    # -- write ------------------------------------------------------------

    def remember(self, record: KnowledgeRecord) -> KnowledgeRecord:
        """Durably remember one new semantic knowledge record.

        The record is stored verbatim: identity, type, content, scope,
        provenance, ``created_at``, and status are persisted exactly as given.
        Nothing is normalized, summarized, embedded, indexed, promoted, or
        merged, and no event, audit entry, or action is produced.

        Ingestion is explicit and conservative:

            - ``record.knowledge_type`` must be in
              :data:`SEMANTIC_KNOWLEDGE_TYPES`, else
              :class:`NonSemanticKnowledgeError`;
            - ``record.status`` must be the canonical birth state
              ``UNVERIFIED`` with ``verified_at`` unset, else
              :class:`IngestionStatusError`. Semantic memory never writes a
              caller-asserted trust level and owns no promotion path;
            - a ``knowledge_id`` that already exists raises
              :class:`DuplicateSemanticKnowledgeError` and writes nothing. The
              collision check consults the whole store — not just the semantic
              view — because identity is store-wide. The store remains the
              final arbiter: under a concurrent writer its own explicit
              duplicate error propagates unchanged, and no row is overwritten.

        Returns the record as stored, so callers observe exactly what was
        persisted.
        """
        if not isinstance(record, KnowledgeRecord):
            raise TypeError("record must be a canonical KnowledgeRecord")
        if record.knowledge_type not in SEMANTIC_KNOWLEDGE_TYPES:
            raise NonSemanticKnowledgeError(
                f"{record.knowledge_type.value!r} is not durable declarative knowledge; "
                f"semantic memory accepts "
                f"{sorted(item.value for item in SEMANTIC_KNOWLEDGE_TYPES)}"
            )
        if record.status is not _INGESTION_STATUS or record.verified_at is not None:
            raise IngestionStatusError(
                "new semantic knowledge must arrive UNVERIFIED with no verified_at; "
                f"got status {record.status.value!r}. Status transitions are owned by "
                "knowledge lifecycle policy, not by semantic memory"
            )
        if self.store.get(record.knowledge_id) is not None:
            raise DuplicateSemanticKnowledgeError(
                f"Knowledge record {record.knowledge_id} is already stored; "
                "semantic memory never overwrites or merges stored knowledge"
            )
        self.store.insert(record)
        return record

    # -- read -------------------------------------------------------------

    def recall(self, knowledge_id: KnowledgeId) -> KnowledgeRecord | None:
        """Return the semantic record for ``knowledge_id``, or ``None``.

        Retrieval is exact identity lookup — never similarity. The record is
        returned exactly as persisted, including a historical ``VERIFIED``
        status, which remains inert data.

        A stored record whose knowledge type is not semantic belongs to
        another memory class and is simply not part of this view, so ``None``
        is returned rather than another subsystem's data.

        Under a scope guard, a record whose scope the request context cannot
        prove is likewise reported as absent: denial never leaks content,
        metadata, or even the existence of the foreign-scope record.
        """
        _require_knowledge_id(knowledge_id)
        record = self.store.get(knowledge_id)
        if record is None or record.knowledge_type not in SEMANTIC_KNOWLEDGE_TYPES:
            return None
        if self.scope_guard is not None and not self.scope_guard.evaluate(record).allowed:
            return None
        return record

    def recall_all(self, query: SemanticMemoryQuery | None = None) -> tuple[KnowledgeRecord, ...]:
        """Return matching semantic records in the store's deterministic order.

        Ordering is inherited verbatim from the canonical store — ascending
        ``(created_at, knowledge_id)``, a total order over unique identities —
        so enumeration is stable across calls, processes, restarts, and
        insertion order. Filtering only removes records; it never reorders,
        ranks, or scores them.

        Under a scope guard, every record that survives the query filter is
        additionally evaluated against the bound request scope, and denied
        records — including records with malformed scope metadata, which
        never raise mid-batch — are removed before the result is returned.
        """
        if query is not None and not isinstance(query, SemanticMemoryQuery):
            raise TypeError("query must be a SemanticMemoryQuery or None")
        matches = tuple(
            record
            for record in self.store.list_records()
            if record.knowledge_type in SEMANTIC_KNOWLEDGE_TYPES
            and (query is None or query.matches(record))
        )
        if self.scope_guard is None:
            return matches
        return self.scope_guard.filter(matches)

    def provenance_of(self, knowledge_id: KnowledgeId) -> ProvenanceRecord | None:
        """Return the stored origin of a semantic record as a C2.07 record.

        This is a read-only projection of the provenance hook C2.02 already
        persists on the record: the canonical
        :class:`~agentx.core.provenance.ProvenanceRecord` shape with the stored
        :class:`~agentx.core.knowledge.ProvenanceReference` as its ``source``.
        Fields C2.02 does not persist (``observed_at``, ``locator``,
        ``derived_from``) are reported as unknown rather than invented, and no
        new provenance storage is introduced.

        Returns ``None`` when the record is absent, is not semantic, or
        carries no provenance. Absent provenance is absence of *data*, not
        evidence for or against the claim, and provenance of any kind — WEB,
        EMAIL, DOCUMENT, USER, SYSTEM — confers no trust and no authority.
        """
        record = self.recall(knowledge_id)
        if record is None or record.provenance is None:
            return None
        return ProvenanceRecord(knowledge_id=record.knowledge_id, source=record.provenance)


def _require_knowledge_id(knowledge_id: KnowledgeId) -> None:
    if not isinstance(knowledge_id, KnowledgeId):
        raise TypeError("knowledge_id must be a canonical KnowledgeId")


def _validate_member_filter(
    value: frozenset[object] | None,
    *,
    member: type[StrEnum],
    field_name: str,
) -> None:
    if value is None:
        return
    if not isinstance(value, frozenset):
        raise SemanticMemoryQueryError(f"{field_name} must be a frozenset or None")
    if not value:
        raise SemanticMemoryQueryError(f"{field_name} must not be an empty filter")
    for item in value:
        if not isinstance(item, member):
            raise SemanticMemoryQueryError(
                f"{field_name} entries must be {member.__name__} members"
            )
