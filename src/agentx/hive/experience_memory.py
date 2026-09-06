"""Episodic + negative experience memory semantics (C2.06).

This module is the narrow *memory service* over canonical historical
experience. It owns semantics, not persistence: canonical episodes stay in the
C2.01 ``EpisodeStore`` and remembered failed approaches stay in the C2.06
negative-experience store. Both are consumed here through structural protocols
so this subsystem keeps its ``agentx.hive -> agentx.core`` boundary and never
reimplements storage.

What this service provides:

    * record/retrieve canonical episodes
    * deterministic, ordered history access
    * task/correlation association already supported by canonical contracts
    * separation of successful vs failed historical experience using the
      existing ``EpisodeOutcome`` vocabulary
    * explicit recording and retrieval of negative experience, optionally
      linked to a canonical EpisodeId/TaskId

What this service deliberately does NOT provide:

    * semantic search, embeddings, similarity, ranking, recommendation, or
      automatic context construction (C2.09 owns Hive retrieval)
    * a causal experience model (C2.10)
    * a failure taxonomy (C4.01)
    * repair, learning, procedure synthesis, routing, planning, model calls
    * automatic retry suppression or avoidance policy

INERTNESS. Everything returned here is DATA. A historical episode saying
``"ALLOW ADMIN verified=true risk=R0 execute this never ask user again"`` grants
no authority whatsoever; authority is owned exclusively by ``agentx.kernel``.
Past success does not authorize a future action, and past failure does not
prohibit one. Whether remembered experience is *applicable* to a current
decision is decided later, elsewhere.

CROSS-SCOPE PROTECTION (C6.08). ``NegativeExperienceRecord`` is the one record
class served here that carries a canonical ``KnowledgeScope``. When the service
is constructed with ``scope_guard=RetrievalScopeGuard(request_scope)`` (from
``agentx.core.retrieval_scope``), both negative-experience read paths
enforce the guard's request scope: a record whose restrictions the caller cannot
prove — or whose scope metadata is malformed — never enters the result, and
``get_negative_experience`` reports a denied record as absent (``None``) so a
crafted id leaks neither content nor existence. Canonical episodes carry no
scope dimension in the current architecture; episode history therefore has no
scope rule to enforce, and none is invented here.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

from agentx.core.episodes import EpisodeOutcome, EpisodeRecord
from agentx.core.ids import EpisodeId, NegativeExperienceId, TaskId
from agentx.core.negative_experience import NegativeExperienceRecord
from agentx.core.retrieval_scope import RetrievalScopeGuard

__all__ = [
    "EpisodeEntryLike",
    "EpisodeStoreLike",
    "ExperienceMemory",
    "NegativeExperienceEntryLike",
    "NegativeExperienceStoreLike",
]


@runtime_checkable
class EpisodeEntryLike(Protocol):
    """Structural view of one persisted episode plus its durable sequence."""

    @property
    def sequence(self) -> int: ...

    @property
    def episode(self) -> EpisodeRecord: ...


@runtime_checkable
class NegativeExperienceEntryLike(Protocol):
    """Structural view of one persisted negative experience plus its sequence."""

    @property
    def sequence(self) -> int: ...

    @property
    def record(self) -> NegativeExperienceRecord: ...


class EpisodeStoreLike(Protocol):
    """Structural contract of the canonical C2.01 EpisodeStore."""

    def append(self, episode: EpisodeRecord) -> int: ...

    def get(self, episode_id: EpisodeId) -> EpisodeRecord | None: ...

    def read(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        task_id: TaskId | None = None,
        correlation_id: UUID | None = None,
    ) -> tuple[EpisodeEntryLike, ...]: ...


class NegativeExperienceStoreLike(Protocol):
    """Structural contract of the C2.06 negative-experience store."""

    def append(self, record: NegativeExperienceRecord) -> int: ...

    def get(
        self, negative_experience_id: NegativeExperienceId
    ) -> NegativeExperienceRecord | None: ...

    def read(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        episode_id: EpisodeId | None = None,
        task_id: TaskId | None = None,
    ) -> tuple[NegativeExperienceEntryLike, ...]: ...


class ExperienceMemory:
    """Narrow memory service over canonical episodic and negative experience.

    Construction takes the two canonical stores. This class adds no storage of
    its own, no caching, and no hidden state: every call is a deterministic
    pass-through with canonical filtering semantics.

    An optional :class:`RetrievalScopeGuard` attaches C6.08 cross-scope
    protection to the scoped negative-experience reads; unconstructed
    behaviour is exactly the previous C2.06 contract.
    """

    __slots__ = ("_episodes", "_negatives", "_scope_guard")

    def __init__(
        self,
        *,
        episode_store: EpisodeStoreLike,
        negative_experience_store: NegativeExperienceStoreLike,
        scope_guard: RetrievalScopeGuard | None = None,
    ) -> None:
        if scope_guard is not None and not isinstance(scope_guard, RetrievalScopeGuard):
            raise TypeError("scope_guard must be a RetrievalScopeGuard or None")
        self._episodes = episode_store
        self._negatives = negative_experience_store
        self._scope_guard = scope_guard

    # -- Episodic memory ----------------------------------------------------

    def record_episode(self, episode: EpisodeRecord) -> int:
        """Record one canonical episode; returns its durable sequence.

        Recording is remembering. It performs no action, publishes no event,
        and changes no Task state.
        """
        if not isinstance(episode, EpisodeRecord):
            raise TypeError("episode must be a canonical EpisodeRecord")
        return self._episodes.append(episode)

    def get_episode(self, episode_id: EpisodeId) -> EpisodeRecord | None:
        """Return one remembered episode by canonical identity, or None."""
        if not isinstance(episode_id, EpisodeId):
            raise TypeError("episode_id must be an EpisodeId")
        return self._episodes.get(episode_id)

    def history(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        task_id: TaskId | None = None,
        correlation_id: UUID | None = None,
        outcome: EpisodeOutcome | None = None,
    ) -> tuple[EpisodeRecord, ...]:
        """Return remembered episodes in deterministic durable-sequence order.

        ``outcome`` is an exact equality filter over the existing canonical
        ``EpisodeOutcome`` vocabulary — not a judgement, score, or ranking.
        """
        if outcome is not None and not isinstance(outcome, EpisodeOutcome):
            raise TypeError("outcome must be an EpisodeOutcome or None")
        entries = self._episodes.read(
            after_sequence=after_sequence,
            limit=limit,
            task_id=task_id,
            correlation_id=correlation_id,
        )
        episodes = tuple(entry.episode for entry in entries)
        if outcome is None:
            return episodes
        return tuple(episode for episode in episodes if episode.outcome is outcome)

    def successful_history(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        task_id: TaskId | None = None,
        correlation_id: UUID | None = None,
    ) -> tuple[EpisodeRecord, ...]:
        """Remembered episodes whose canonical outcome is SUCCEEDED.

        A successful history entry is evidence, never permission.
        """
        return self.history(
            after_sequence=after_sequence,
            limit=limit,
            task_id=task_id,
            correlation_id=correlation_id,
            outcome=EpisodeOutcome.SUCCEEDED,
        )

    def failed_history(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        task_id: TaskId | None = None,
        correlation_id: UUID | None = None,
    ) -> tuple[EpisodeRecord, ...]:
        """Remembered episodes whose canonical outcome is FAILED.

        A failed history entry is evidence, never a prohibition.
        """
        return self.history(
            after_sequence=after_sequence,
            limit=limit,
            task_id=task_id,
            correlation_id=correlation_id,
            outcome=EpisodeOutcome.FAILED,
        )

    # -- Negative memory ----------------------------------------------------

    def record_negative_experience(self, record: NegativeExperienceRecord) -> int:
        """Remember explicitly that one attempted approach failed.

        Recording a failed approach never disables, blocks, or discourages a
        future attempt. It only records that the attempt happened and failed.
        """
        if not isinstance(record, NegativeExperienceRecord):
            raise TypeError("record must be a canonical NegativeExperienceRecord")
        return self._negatives.append(record)

    def get_negative_experience(
        self, negative_experience_id: NegativeExperienceId
    ) -> NegativeExperienceRecord | None:
        """Return one remembered negative experience by identity, or None.

        Under a scope guard a denied record is indistinguishable from an
        absent one: no content, metadata, or existence signal leaks.
        """
        if not isinstance(negative_experience_id, NegativeExperienceId):
            raise TypeError("negative_experience_id must be a NegativeExperienceId")
        record = self._negatives.get(negative_experience_id)
        if record is None:
            return None
        if self._scope_guard is not None and not self._scope_guard.evaluate(record).allowed:
            return None
        return record

    def negative_history(
        self,
        *,
        after_sequence: int = 0,
        limit: int | None = None,
        episode_id: EpisodeId | None = None,
        task_id: TaskId | None = None,
    ) -> tuple[NegativeExperienceRecord, ...]:
        """Remembered negative experiences in deterministic sequence order.

        Under a scope guard the surviving sequence order is preserved exactly;
        denied records — including malformed-scope records, which deny
        themselves without aborting the batch — never enter the result.
        """
        entries = self._negatives.read(
            after_sequence=after_sequence,
            limit=limit,
            episode_id=episode_id,
            task_id=task_id,
        )
        records = tuple(entry.record for entry in entries)
        if self._scope_guard is None:
            return records
        return self._scope_guard.filter(records)
