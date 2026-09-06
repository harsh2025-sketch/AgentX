"""Conservative deterministic consolidation contracts for Hive memory (C6.04).

This module provides pure, deterministic, model-free consolidation logic for
Hive memory. It reduces redundant representation while retaining evidence and
auditability. Nothing here grants authority, executes capabilities, or invokes
models.

Rules enforced
--------------

* Never turn repetition into verification: consolidating N ``UNVERIFIED``
  duplicates remains ``UNVERIFIED``. Status is preserved verbatim; counting
  duplicates never promotes ``UNVERIFIED`` -> ``PROVISIONAL``/``SUPPORTED``/
  ``VERIFIED``.

* Never discard contradictory evidence: two records with the same type and
  scope but different content are *not* equivalent and are never merged. They
  remain separate and can be linked as ``KnowledgeContradiction`` elsewhere
  without deletion.

* Never erase negative experience merely because success later occurred:
  negative-experience consolidation is identity-preserving and append-only.
  This module never deletes a ``NegativeExperienceRecord``.

* Never lose provenance: the provenance union of a duplicate group is the
  distinct set of ``ProvenanceReference`` values carried by the group. Source
  records remain durably stored; the union is computed deterministically.

* Never destroy source history needed for audit: consolidation prefers
  linking/superseding over destructive deletion. For canonical knowledge,
  duplicates are superseded via ``KnowledgeSupersession``; the historical rows
  remain retrievable with ``SUPERSEDED`` status.

Implemented responsibilities
----------------------------

* candidate selection (grouping by equivalence key)
* compatibility/equivalence checks (exact type+content+scope+status, filtered)
* consolidated representation/result (canonical + superseded + source refs)
* source references
* provenance union
* status handling (verbatim preservation, no inflation)
* supersession/archive linkage where canonical (knowledge)
* idempotence (existing supersessions are recognised)
* bounded batch behavior (caller-controlled limit; deterministic truncation)

Out of scope
------------

* C6.05 salience policy
* C6.06 revalidation
* model invocation (deterministic consolidation is preferred)

This module belongs to ``agentx.core`` and imports nothing from other AgentX
subsystems. It is pure data logic: it never touches storage, never mutates
records, and never performs I/O.

The Hive service layer ``agentx.hive.memory_consolidation`` composes this
core logic over injected store ports and performs the durable supersession
edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentx.core.ids import KnowledgeId
from agentx.core.knowledge import (
    KnowledgeRecord,
    KnowledgeScope,
    KnowledgeStatus,
    KnowledgeType,
    ProvenanceReference,
    ScopeDimension,
)

__all__ = [
    "ConsolidatedKnowledgeGroup",
    "ConsolidationConfig",
    "ConsolidationResult",
    "KnowledgeConsolidationKey",
    "MemoryConsolidation",
    "MemoryConsolidationConfig",
    "MemoryConsolidationResult",
    "are_knowledge_records_equivalent",
    "build_consolidated_knowledge_groups",
    "canonical_for_knowledge_group",
    "check_knowledge_compatibility",
    "knowledge_consolidation_key",
    "provenance_union_for_knowledge_group",
    "select_knowledge_candidates",
]


# ---------------------------------------------------------------------------
# Config / result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ConsolidationConfig:
    """Bounded, deterministic execution policy for consolidation."""

    batch_size: int | None = None
    """Maximum number of duplicate groups to consolidate per invocation.

    ``None`` means no limit (process all groups). When set, groups are
    selected deterministically and truncated; the caller may invoke
    consolidation repeatedly to drain remaining groups (bounded batch).
    """

    def __post_init__(self) -> None:
        if self.batch_size is not None:
            if not isinstance(self.batch_size, int) or isinstance(self.batch_size, bool):
                raise TypeError("batch_size must be an int or None")
            if self.batch_size <= 0:
                raise ValueError("batch_size must be positive when set")


# Backwards-compatibility alias demanded by some callers.
MemoryConsolidationConfig = ConsolidationConfig


@dataclass(frozen=True, slots=True, kw_only=True)
class ConsolidatedKnowledgeGroup:
    """Deterministic description of one duplicate group and its resolution."""

    canonical: KnowledgeRecord
    """The surviving record for the group (earliest created_at, tie-break id)."""

    sources: tuple[KnowledgeRecord, ...]
    """All records in the group in deterministic order (canonical first)."""

    superseded: tuple[KnowledgeRecord, ...]
    """Sources that would be superseded (all except canonical)."""

    provenance_union: frozenset[ProvenanceReference]
    """Distinct provenance references carried by the group's sources."""

    source_ids: frozenset[KnowledgeId]
    """Identity set of all sources in the group."""

    superseded_ids: frozenset[KnowledgeId]
    """Identity set of the superseded members."""

    @property
    def knowledge_type(self) -> KnowledgeType:
        return self.canonical.knowledge_type

    @property
    def content(self) -> str:
        return self.canonical.content

    @property
    def scope(self) -> KnowledgeScope:
        return self.canonical.scope

    @property
    def status(self) -> KnowledgeStatus:
        return self.canonical.status


@dataclass(frozen=True, slots=True, kw_only=True)
class ConsolidationResult:
    """Result of one deterministic consolidation pass over knowledge."""

    groups: tuple[ConsolidatedKnowledgeGroup, ...]
    """Duplicate groups selected (already bounded, deterministic order)."""

    total_records_examined: int
    """Number of input records considered (including singletons)."""

    total_groups_found: int
    """Number of duplicate groups before batch truncation."""

    bounded: bool
    """Whether ``batch_size`` truncated the result."""

    @property
    def superseded_ids(self) -> frozenset[KnowledgeId]:
        ids: set[KnowledgeId] = set()
        for group in self.groups:
            ids.update(group.superseded_ids)
        return frozenset(ids)

    @property
    def canonical_ids(self) -> frozenset[KnowledgeId]:
        return frozenset(group.canonical.knowledge_id for group in self.groups)

    @property
    def source_ids(self) -> frozenset[KnowledgeId]:
        ids: set[KnowledgeId] = set()
        for group in self.groups:
            ids.update(group.source_ids)
        return frozenset(ids)

    @property
    def provenance_union(self) -> frozenset[ProvenanceReference]:
        union: set[ProvenanceReference] = set()
        for group in self.groups:
            union.update(group.provenance_union)
        return frozenset(union)


MemoryConsolidationResult = ConsolidationResult


# ---------------------------------------------------------------------------
# Equivalence key / compatibility
# ---------------------------------------------------------------------------


# ScopeDimension is StrEnum, values are stable. Using frozenset of
# (dimension, value) pairs gives exact equality: empty scope == global,
# and any additional dimension or value change makes the key distinct.
# This deterministically separates "different scopes" and "different
# environments" (ENVIRONMENT is one dimension).
KnowledgeConsolidationKey = tuple[
    KnowledgeType, str, frozenset[tuple[ScopeDimension, str]], KnowledgeStatus
]


def _scope_key(scope: KnowledgeScope) -> frozenset[tuple[ScopeDimension, str]]:
    # MappingProxyType is not hashable; convert to frozenset of pairs.
    # Sorting is not needed for set equality, but we normalise via frozenset.
    return frozenset(scope.dimensions.items())


def knowledge_consolidation_key(record: KnowledgeRecord) -> KnowledgeConsolidationKey:
    """Deterministic equivalence key for knowledge consolidation.

    Two records share a key exactly when they carry the same
    ``knowledge_type``, ``content``, ``scope`` (exact dimension/value equality),
    and ``status``. ``provenance``, ``knowledge_id``, ``created_at``,
    ``verified_at`` and ``schema_version`` are intentionally not part of the
    key:

    * ``provenance`` differs for "same claim different provenance" and must
      still group so the union can be computed;
    * ``knowledge_id`` is identity, never equality;
    * ``status`` is included so ``VERIFIED`` vs ``UNVERIFIED`` are not
      automatically merged (conservative; see module docstring).
    """
    if not isinstance(record, KnowledgeRecord):
        raise TypeError("record must be a KnowledgeRecord")
    return (
        record.knowledge_type,
        record.content,
        _scope_key(record.scope),
        record.status,
    )


def are_knowledge_records_equivalent(a: KnowledgeRecord, b: KnowledgeRecord) -> bool:
    """Return whether two records are equivalent for consolidation.

    Deterministic exact equality on type, content, scope, and status.
    Hostile content confers no authority and is compared as characters.
    """
    if not isinstance(a, KnowledgeRecord) or not isinstance(b, KnowledgeRecord):
        raise TypeError("both arguments must be KnowledgeRecord")
    return knowledge_consolidation_key(a) == knowledge_consolidation_key(b)


def check_knowledge_compatibility(group: tuple[KnowledgeRecord, ...]) -> bool:
    """Return whether a candidate group is compatible for consolidation.

    A group is compatible exactly when:

    * it contains at least two records;
    * every record shares the same equivalence key (type+content+scope+status);
    * no member is already ``SUPERSEDED`` or ``CONFLICTED`` (those are
      exceptional lifecycle states that must not be auto-merged);
    * no hostile string is interpreted (all fields are inert data).

    This is a pure predicate; it never mutates, never scores, and never
    invokes a model.
    """
    if not isinstance(group, tuple):
        raise TypeError("group must be a tuple of KnowledgeRecord")
    if len(group) < 2:
        return False
    # All members must be KnowledgeRecord.
    for record in group:
        if not isinstance(record, KnowledgeRecord):
            raise TypeError("group members must be KnowledgeRecord")
        if record.status is KnowledgeStatus.SUPERSEDED:
            return False
        if record.status is KnowledgeStatus.CONFLICTED:
            return False
    first_key = knowledge_consolidation_key(group[0])
    return all(knowledge_consolidation_key(record) == first_key for record in group[1:])


def provenance_union_for_knowledge_group(
    group: tuple[KnowledgeRecord, ...],
) -> frozenset[ProvenanceReference]:
    """Return the distinct provenance union for a group.

    Records without provenance contribute nothing. Two distinct
    ``ProvenanceReference`` values with identical ``kind`` and ``reference``
    are the same entry. Order is irrelevant; the result is a set.
    """
    if not isinstance(group, tuple):
        raise TypeError("group must be a tuple")
    union: set[ProvenanceReference] = set()
    for record in group:
        if not isinstance(record, KnowledgeRecord):
            raise TypeError("group members must be KnowledgeRecord")
        if record.provenance is not None:
            union.add(record.provenance)
    return frozenset(union)


def canonical_for_knowledge_group(group: tuple[KnowledgeRecord, ...]) -> KnowledgeRecord:
    """Deterministically pick the canonical survivor for a compatible group.

    Ordering is ``(created_at, knowledge_id string)`` ascending, a total
    deterministic order. The earliest observation is the canonical survivor;
    later duplicates are superseded. This is stable across processes and
    restarts.
    """
    if not isinstance(group, tuple) or not group:
        raise TypeError("group must be a non-empty tuple")
    if not check_knowledge_compatibility(group):
        raise ValueError("group is not compatible for consolidation")
    return min(group, key=lambda r: (r.created_at, r.knowledge_id.to_str()))


def _deterministic_group_order_key(group: tuple[KnowledgeRecord, ...]) -> tuple[str, str]:
    canonical = canonical_for_knowledge_group(group)
    return (canonical.created_at.isoformat(), canonical.knowledge_id.to_str())


# ---------------------------------------------------------------------------
# Candidate selection / group building
# ---------------------------------------------------------------------------


def select_knowledge_candidates(
    records: tuple[KnowledgeRecord, ...],
    *,
    config: ConsolidationConfig | None = None,
) -> tuple[tuple[KnowledgeRecord, ...], ...]:
    """Select candidate duplicate groups from records.

    Steps (deterministic):

    1. Filter out records whose status is ``SUPERSEDED`` or ``CONFLICTED``:
       they are already exceptional history and are not auto-merged.
    2. Group remaining records by ``knowledge_consolidation_key``.
    3. Keep only groups with size >= 2 that satisfy
       :func:`check_knowledge_compatibility`.
    4. Sort groups deterministically by their canonical's
       ``(created_at, knowledge_id)``.
    5. If ``config.batch_size`` is set, truncate to that many groups
       (bounded batch behavior). The order before truncation is deterministic
       so repeated bounded calls are stable.

    No status is promoted, no provenance is invented, and no content is
    compared fuzzily. Hostile strings remain inert.
    """
    if not isinstance(records, tuple):
        raise TypeError("records must be a tuple of KnowledgeRecord")
    for record in records:
        if not isinstance(record, KnowledgeRecord):
            raise TypeError("records must contain only KnowledgeRecord")

    if config is not None and not isinstance(config, ConsolidationConfig):
        raise TypeError("config must be a ConsolidationConfig or None")

    # Step 1: filter exceptional states.
    filtered = tuple(
        record
        for record in records
        if record.status is not KnowledgeStatus.SUPERSEDED
        and record.status is not KnowledgeStatus.CONFLICTED
    )

    # Step 2: group by key.
    buckets: dict[KnowledgeConsolidationKey, list[KnowledgeRecord]] = {}
    for record in filtered:
        key = knowledge_consolidation_key(record)
        buckets.setdefault(key, []).append(record)

    # Step 3: keep compatible groups >=2, with deterministic internal order.
    groups: list[tuple[KnowledgeRecord, ...]] = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        # Sort members inside group deterministically: canonical first is min,
        # but keep full group sorted for stable source ordering.
        ordered = tuple(sorted(members, key=lambda r: (r.created_at, r.knowledge_id.to_str())))
        if check_knowledge_compatibility(ordered):
            groups.append(ordered)

    # Step 4: deterministic group order.
    groups.sort(key=_deterministic_group_order_key)

    # Step 5: bounded truncation (external caller may also truncate result;
    # we do it here for candidate selection symmetry).
    if config is not None and config.batch_size is not None:
        truncated = tuple(groups[: config.batch_size])
        return truncated
    return tuple(groups)


def build_consolidated_knowledge_groups(
    records: tuple[KnowledgeRecord, ...],
    *,
    config: ConsolidationConfig | None = None,
) -> ConsolidationResult:
    """Build the consolidated representation for duplicate knowledge.

    This is the pure-data counterpart of :func:`select_knowledge_candidates`:
    it groups, picks canonicals, computes provenance unions, and packages
    source references. It never mutates records, never writes storage, and
    never inflates status.

    The returned :class:`ConsolidationResult` is inert data: it describes what
    *would* be superseded, where provenance unions come from, and which
    identities are sources. The Hive service layer turns the description into
    durable ``KnowledgeSupersession`` edges where canonical.
    """
    if not isinstance(records, tuple):
        raise TypeError("records must be a tuple of KnowledgeRecord")
    if config is not None and not isinstance(config, ConsolidationConfig):
        raise TypeError("config must be a ConsolidationConfig or None")

    # Count total groups before truncation for audit.
    all_groups = select_knowledge_candidates(records, config=None)
    total_groups = len(all_groups)

    bounded_groups = select_knowledge_candidates(records, config=config)
    bounded = (
        config is not None and config.batch_size is not None and total_groups > len(bounded_groups)
    )

    consolidated: list[ConsolidatedKnowledgeGroup] = []
    for group in bounded_groups:
        canonical = canonical_for_knowledge_group(group)
        # Deterministic source order: canonical first, then rest by order.
        # ``group`` is already sorted canonical-first.
        sources = group
        superseded = tuple(r for r in sources if r.knowledge_id != canonical.knowledge_id)
        union = provenance_union_for_knowledge_group(sources)
        source_ids = frozenset(r.knowledge_id for r in sources)
        superseded_ids = frozenset(r.knowledge_id for r in superseded)
        consolidated.append(
            ConsolidatedKnowledgeGroup(
                canonical=canonical,
                sources=sources,
                superseded=superseded,
                provenance_union=union,
                source_ids=source_ids,
                superseded_ids=superseded_ids,
            )
        )

    return ConsolidationResult(
        groups=tuple(consolidated),
        total_records_examined=len(records),
        total_groups_found=total_groups,
        bounded=bounded,
    )


# ---------------------------------------------------------------------------
# Top-level engine (pure) for callers that prefer an object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MemoryConsolidation:
    """Pure deterministic engine for Hive memory consolidation.

    This object holds no store and performs no I/O. Call
    :meth:`consolidate_knowledge` with a tuple of records to obtain a
    deterministic, bounded, idempotent description of what would be
    consolidated. The Hive service ``agentx.hive.memory_consolidation``
    drives the durable supersession edges.

    The engine is deliberately model-free: no embeddings, no ranking, no
    graph traversal, and no host/origin allow-lists are consulted.
    """

    config: ConsolidationConfig = field(default_factory=ConsolidationConfig)

    def __post_init__(self) -> None:
        if not isinstance(self.config, ConsolidationConfig):
            raise TypeError("config must be a ConsolidationConfig")

    def select_candidates(
        self, records: tuple[KnowledgeRecord, ...]
    ) -> tuple[tuple[KnowledgeRecord, ...], ...]:
        """Select duplicate candidate groups (pure)."""
        return select_knowledge_candidates(records, config=self.config)

    def check_compatibility(self, group: tuple[KnowledgeRecord, ...]) -> bool:
        """Check whether a group is compatible (pure)."""
        return check_knowledge_compatibility(group)

    def consolidate_knowledge(self, records: tuple[KnowledgeRecord, ...]) -> ConsolidationResult:
        """Return the consolidated representation for knowledge records.

        Never inflates status, never discards contradictory content, never
        loses provenance, and never destroys source history. The result is
        inert data; callers may persist supersessions separately.
        """
        return build_consolidated_knowledge_groups(records, config=self.config)

    # Compatibility aliases for callers that use generic names.
    def consolidate(self, records: tuple[KnowledgeRecord, ...]) -> ConsolidationResult:
        return self.consolidate_knowledge(records)

    def candidates(
        self, records: tuple[KnowledgeRecord, ...]
    ) -> tuple[tuple[KnowledgeRecord, ...], ...]:
        return self.select_candidates(records)


# ---------------------------------------------------------------------------
# Minimal stubs for other memory classes (conservative no-op)
# ---------------------------------------------------------------------------
# The other Hive memory classes are out of scope for destructive
# consolidation in C6.04. These stubs exist so callers can import a single
# engine without branching, and so audit tests can verify that consolidation
# does not silently discard negative experience, episodic, procedural,
# causal, or environmental data.
#
# They are pure and always return empty/bounded results unless overridden
# by the Hive service layer which explicitly preserves those stores.


@dataclass(frozen=True, slots=True, kw_only=True)
class NegativeExperienceConsolidationResult:
    """Inert result for negative-experience consolidation (always preserved)."""

    total_examined: int = 0
    groups: tuple[object, ...] = ()
    preserved: bool = True
    bounded: bool = False


def consolidate_negative_experience_candidates(
    records: tuple[object, ...],
    *,
    config: ConsolidationConfig | None = None,
) -> NegativeExperienceConsolidationResult:
    """Return a preserved (no-op) result for negative experiences.

    Negative experiences are never erased merely because a later success
    occurred. This function therefore never groups for deletion and always
    reports ``preserved=True``.
    """
    if not isinstance(records, tuple):
        raise TypeError("records must be a tuple")
    if config is not None and not isinstance(config, ConsolidationConfig):
        raise TypeError("config must be a ConsolidationConfig or None")
    return NegativeExperienceConsolidationResult(total_examined=len(records))
