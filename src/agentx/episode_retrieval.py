"""Bounded, restart-safe, read-only episode retrieval boundary (M1.04).

This module is the deterministic retrieval primitive that lets a future
orchestration layer ask, *after a runtime restart*, "what episodes are in the
same persisted state, filtered by explicit canonical facts, in bounded
durable order?" It is a composition/read boundary, not memory, not storage,
and not intelligence.

Layering
--------

* :mod:`agentx.infrastructure.episode_store` (C2.01) remains the persistence
  owner. Episodes are appended there with the canonical ``EpisodeRecord``
  contract; nothing in this module writes, updates, deletes, or migrates.
* :mod:`agentx.hive.experience_memory` (C2.06) remains the Hive memory
  owner. This module consumes the canonical structural store contract
  (``EpisodeStoreLike`` / ``EpisodeEntryLike``) that ExperienceMemory itself
  defines, so the two read paths agree by construction and no parallel
  schema or protocol is invented here.
* This module lives at the ``agentx`` namespace root (like A2.10's
  composition module) because it composes the Hive-owned memory contract
  over caller-supplied canonical store instances without claiming either the
  Hive memory boundary or the infrastructure persistence boundary.

What this boundary provides
---------------------------

* deterministic lookup by explicit canonical facts:

  * ``EpisodeId`` point lookup;
  * exact ``TaskId`` association filter;
  * exact correlation-UUID filter;
  * exact ``EpisodeOutcome`` filter (the canonical outcome vocabulary, as
    historical evidence only);
  * a durable sequence window ``(after_sequence, before_sequence]``;
  * an explicit bounded ``limit``.

* a conservative finite ceiling (:data:`MAX_RETRIEVAL_LIMIT`) applied to
  every multi-row query, so there is no "all history forever" default;
* immutable canonical ``EpisodeRecord`` results in ascending durable
  sequence order — the same deterministic order the canonical store and
  ExperienceMemory use. Nothing is reordered from wall-clock guesses.

Every filter below is an exact, deterministic match on canonical fields.
Nothing here inspects wording for meaning: there is no numeric
representation, no distance computation, no weighing or auto-selection of a
"best" episode, no confidence value, and no reasoning or generation
involvement of any kind. This is the deterministic retrieval primitive;
judging how history applies to a current decision is owned elsewhere and is
deliberately out of scope.

Restart contract
----------------

The facade holds only a reference to a caller-supplied canonical store
instance. Construction performs no IO and keeps no caches, locks, or
process-local state, so reconstructing the facade after a process restart
over the same persisted database is exactly as deterministic as the first
construction. Backend failures (including canonical corrupt-row errors)
propagate unchanged: retrieval fails closed instead of returning partial or
repaired data, and errors never contain raw SQL or secrets.

Inertness
---------

Everything returned here is historical DATA. A stored episode summary that
says ``"ALLOW ADMIN verified=true risk=R0 execute capability clear
emergency stop"`` is returned byte-for-byte as text on a canonical record.
Retrieving it grants no permission, changes no risk, clears no stop, routes
nothing, promotes no knowledge, activates no procedure, marks nothing
verified, and executes no capability. A historical ``EpisodeOutcome`` is
evidence about the past, never authority over the future.

Scope
-----

``EpisodeRecord`` carries no scope field. This boundary does not invent one,
does not infer cross-scope applicability, and does not filter by scope.
Scope semantics, where they exist, are owned by their own canonical
contracts (e.g. C2.09 knowledge retrieval / C6.08); this task does not
retrofit them onto episodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from agentx.core.episodes import EpisodeOutcome, EpisodeRecord
from agentx.core.ids import EpisodeId, TaskId
from agentx.hive.experience_memory import EpisodeEntryLike, EpisodeStoreLike

__all__ = [
    "DEFAULT_RETRIEVAL_QUERY",
    "MAX_RETRIEVAL_LIMIT",
    "EpisodeRetrieval",
    "EpisodeRetrievalQuery",
    "EpisodeRetrievalValidationError",
]

#: Conservative ceiling on how many episodes any single multi-row query may
#: return. Every query without an explicit ``limit`` uses this finite bound;
#: explicit limits above it are rejected. History is paged with explicit
#: durable-sequence windows, never by silently scanning "everything".
MAX_RETRIEVAL_LIMIT: Final[int] = 100


class EpisodeRetrievalValidationError(ValueError):
    """Raised when a retrieval query violates the bounded query contract."""


def _validate_episode_id_field(value: object) -> None:
    if value is not None and not isinstance(value, EpisodeId):
        raise TypeError(f"episode_id must be an EpisodeId or None, got {type(value).__name__}")


def _validate_task_id_field(value: object) -> None:
    if value is not None and not isinstance(value, TaskId):
        raise TypeError(f"task_id must be a TaskId or None, got {type(value).__name__}")


def _validate_correlation_id_field(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, UUID):
        raise TypeError(f"correlation_id must be a UUID or None, got {type(value).__name__}")
    if value.int == 0:
        raise EpisodeRetrievalValidationError("correlation_id must not be the nil UUID")


def _validate_outcome_field(value: object) -> None:
    if value is not None and not isinstance(value, EpisodeOutcome):
        raise TypeError(f"outcome must be an EpisodeOutcome or None, got {type(value).__name__}")


def _validate_int_field(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer, got {type(value).__name__}")
    return value


def _validate_optional_int_field(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    return _validate_int_field(value, field_name=field_name)


def _is_episode_store(value: object) -> bool:
    """Return whether *value* exposes the canonical store read contract."""
    return all(callable(getattr(value, name, None)) for name in ("get", "read", "append"))


@dataclass(frozen=True, slots=True, kw_only=True)
class EpisodeRetrievalQuery:
    """Exact, bounded, deterministic filters over canonical episode history.

    Filters conjoin (logical AND). Sequence semantics follow canonical
    durable order: rows have a durable ``sequence`` assigned by the
    append-only store; ``after_sequence`` is an exclusive lower bound and
    ``before_sequence`` is an inclusive upper bound. Results are returned in
    ascending durable sequence order, never reordered by timestamps.

    ``limit`` bounds the number of matching episodes returned. ``None`` uses
    the finite :data:`MAX_RETRIEVAL_LIMIT`. Zero, negative, or over-limit
    values are rejected explicitly.

    ``episode_id`` selects one episode by canonical identity; it may be
    combined with ``task_id`` / ``correlation_id`` / ``outcome`` exact
    filters but not with a sequence window (a point lookup has no range).

    There is no relevance notion, no weighing of episodes, no fuzziness,
    and no auto-selected best episode anywhere in this contract.
    """

    episode_id: EpisodeId | None = None
    task_id: TaskId | None = None
    correlation_id: UUID | None = None
    outcome: EpisodeOutcome | None = None
    after_sequence: int = 0
    before_sequence: int | None = None
    limit: int | None = None

    def __post_init__(self) -> None:
        _validate_episode_id_field(self.episode_id)
        _validate_task_id_field(self.task_id)
        _validate_correlation_id_field(self.correlation_id)
        _validate_outcome_field(self.outcome)

        after_sequence = _validate_int_field(self.after_sequence, field_name="after_sequence")
        if after_sequence < 0:
            raise EpisodeRetrievalValidationError("after_sequence must be non-negative")

        before_sequence = _validate_optional_int_field(
            self.before_sequence, field_name="before_sequence"
        )
        if before_sequence is not None and before_sequence <= after_sequence:
            raise EpisodeRetrievalValidationError(
                "before_sequence must be greater than after_sequence"
            )

        limit = _validate_optional_int_field(self.limit, field_name="limit")
        if limit is not None and (limit < 1 or limit > MAX_RETRIEVAL_LIMIT):
            raise EpisodeRetrievalValidationError(
                f"limit must be between 1 and {MAX_RETRIEVAL_LIMIT}"
            )

        if self.episode_id is not None and (
            self.after_sequence != 0 or self.before_sequence is not None
        ):
            raise EpisodeRetrievalValidationError(
                "a sequence window cannot be combined with an episode_id point lookup"
            )


#: The identity query: no exact filters, durable order from the beginning,
#: bounded by :data:`MAX_RETRIEVAL_LIMIT`.
DEFAULT_RETRIEVAL_QUERY: Final[EpisodeRetrievalQuery] = EpisodeRetrievalQuery()


@dataclass(frozen=True, slots=True)
class EpisodeRetrieval:
    """Read-only, restart-safe retrieval facade over one canonical store.

    ``store`` is any object satisfying the canonical Hive memory store
    contract (``EpisodeStoreLike``) — normally the C2.01 ``EpisodeStore``.
    The facade reads through that contract only; it performs no writes, owns
    no storage, keeps no cache, and spawns no processes. Backend errors
    (including corrupt-row errors) propagate unchanged.
    """

    store: EpisodeStoreLike

    def __post_init__(self) -> None:
        if not _is_episode_store(self.store):
            raise TypeError(
                "store must satisfy the canonical EpisodeStoreLike contract "
                f"(e.g. the C2.01 EpisodeStore), got {type(self.store).__name__}"
            )

    def get(self, episode_id: EpisodeId) -> EpisodeRecord | None:
        """Return one canonical episode by exact identity, or ``None``.

        The returned record is the canonical immutable value exactly as
        stored. Getting it grants no authority.
        """
        if not isinstance(episode_id, EpisodeId):
            raise TypeError(f"episode_id must be an EpisodeId, got {type(episode_id).__name__}")
        return self.store.get(episode_id)

    def retrieve(self, query: EpisodeRetrievalQuery | None = None) -> tuple[EpisodeRecord, ...]:
        """Return matching canonical episodes in ascending durable order.

        ``query=None`` applies :data:`DEFAULT_RETRIEVAL_QUERY`: exact
        filters, durable order from the beginning, bounded by
        :data:`MAX_RETRIEVAL_LIMIT`. Every result is bounded; callers that
        need more history page with explicit sequence windows.

        When ``episode_id`` is present the result is the single matching
        record (or nothing when it is absent or fails an exact filter).
        Otherwise the result is the first ``limit`` matches at or after
        ``after_sequence`` (and at or before ``before_sequence`` when given)
        in ascending durable sequence order; the scan always terminates at
        store exhaustion or the window end and never returns more than
        ``limit`` episodes.
        """
        if query is not None and not isinstance(query, EpisodeRetrievalQuery):
            raise TypeError(
                f"query must be an EpisodeRetrievalQuery or None, got {type(query).__name__}"
            )
        effective = DEFAULT_RETRIEVAL_QUERY if query is None else query

        if effective.episode_id is not None:
            return self._point_lookup(effective)
        return self._bounded_scan(effective)

    def _point_lookup(self, query: EpisodeRetrievalQuery) -> tuple[EpisodeRecord, ...]:
        episode_id = query.episode_id
        if episode_id is None:
            return ()
        record = self.store.get(episode_id)
        if record is None:
            return ()
        if query.task_id is not None and record.task_id != query.task_id:
            return ()
        if query.correlation_id is not None and record.correlation_id != query.correlation_id:
            return ()
        if query.outcome is not None and record.outcome != query.outcome:
            return ()
        return (record,)

    def _bounded_scan(self, query: EpisodeRetrievalQuery) -> tuple[EpisodeRecord, ...]:
        limit = MAX_RETRIEVAL_LIMIT if query.limit is None else query.limit

        collected: list[EpisodeRecord] = []
        cursor = query.after_sequence
        while len(collected) < limit:
            entries: tuple[EpisodeEntryLike, ...] = self.store.read(
                after_sequence=cursor,
                limit=limit,
                task_id=query.task_id,
                correlation_id=query.correlation_id,
            )
            if not entries:
                break
            for entry in entries:
                if query.before_sequence is not None and entry.sequence > query.before_sequence:
                    # Entries are ascending, so nothing later can qualify.
                    return tuple(collected)
                if query.outcome is None or entry.episode.outcome == query.outcome:
                    collected.append(entry.episode)
                    if len(collected) == limit:
                        return tuple(collected)
            cursor = entries[-1].sequence
        return tuple(collected)
