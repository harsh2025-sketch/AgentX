"""Canonical composition boundary for recording verified execution episodes.

M1.03 packages already-existing canonical execution evidence into the existing
:class:`agentx.core.episodes.EpisodeRecord` and records it through the existing
:class:`agentx.hive.experience_memory.ExperienceMemory` service. It creates no
new execution, verification, authority, causal, or persistence semantics.

Truth comes only from the canonical ``CausalExperience.outcome`` and its
validated typed evidence. Free text is preserved as inert historical context
and is never interpreted as success, permission, policy, or authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from agentx.core.causal_experience import CausalExperience, CausalOutcome
from agentx.core.episodes import EpisodeOutcome, EpisodeRecord
from agentx.core.execution import ExecutionContext
from agentx.core.ids import EpisodeId
from agentx.core.tasks import Task, TaskStatus
from agentx.hive.experience_memory import ExperienceMemory

__all__ = [
    "ExecutionEpisodeCapture",
    "ExecutionEpisodeCaptureError",
    "ExecutionEpisodeRequest",
    "RecordedExecutionEpisode",
    "package_execution_episode",
]


class ExecutionEpisodeCaptureError(ValueError):
    """Raised when canonical evidence is inconsistent and must not be recorded."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionEpisodeRequest:
    """Canonical inputs required to package one historical execution episode.

    ``task`` is the terminal canonical Task after the execution/orchestration
    attempt. Task identity and objective are immutable across canonical state
    transitions, so it also supplies the original task identity/objective
    context without introducing a second task snapshot schema.
    """

    task: Task
    context: ExecutionContext
    experience: CausalExperience
    episode_id: EpisodeId
    recorded_at: datetime
    supporting_event_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.task, Task):
            raise TypeError(f"task must be a Task, got {type(self.task).__name__}")
        if not isinstance(self.context, ExecutionContext):
            raise TypeError(
                f"context must be an ExecutionContext, got {type(self.context).__name__}"
            )
        if not isinstance(self.experience, CausalExperience):
            raise TypeError(
                "experience must be a canonical CausalExperience, "
                f"got {type(self.experience).__name__}"
            )
        if not isinstance(self.episode_id, EpisodeId):
            raise TypeError(
                f"episode_id must be an EpisodeId, got {type(self.episode_id).__name__}"
            )
        if not isinstance(self.recorded_at, datetime):
            raise TypeError(
                f"recorded_at must be a datetime, got {type(self.recorded_at).__name__}"
            )
        if not isinstance(self.supporting_event_ids, tuple):
            raise TypeError("supporting_event_ids must be a tuple of UUID values")


@dataclass(frozen=True, slots=True)
class RecordedExecutionEpisode:
    """One canonical episode paired with its durable EpisodeStore sequence."""

    sequence: int
    episode: EpisodeRecord

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence <= 0:
            raise ValueError("sequence must be a positive integer")
        if not isinstance(self.episode, EpisodeRecord):
            raise TypeError("episode must be a canonical EpisodeRecord")


def _episode_outcome(outcome: CausalOutcome) -> EpisodeOutcome:
    """Conservatively project the richer causal outcome into EpisodeOutcome."""
    if outcome is CausalOutcome.VERIFIED:
        return EpisodeOutcome.SUCCEEDED
    if outcome in (
        CausalOutcome.VERIFICATION_FAILED,
        CausalOutcome.EXECUTION_FAILED,
        CausalOutcome.DENIED,
    ):
        return EpisodeOutcome.FAILED
    if outcome in (CausalOutcome.CANCELLED, CausalOutcome.TIMED_OUT):
        return EpisodeOutcome.CANCELLED
    raise ExecutionEpisodeCaptureError(f"unsupported canonical causal outcome: {outcome!r}")


def _expected_task_status(outcome: CausalOutcome) -> TaskStatus:
    """Return the canonical terminal Task status compatible with ``outcome``."""
    if outcome is CausalOutcome.VERIFIED:
        return TaskStatus.SUCCEEDED
    if outcome in (
        CausalOutcome.VERIFICATION_FAILED,
        CausalOutcome.EXECUTION_FAILED,
        CausalOutcome.DENIED,
    ):
        return TaskStatus.FAILED
    if outcome in (CausalOutcome.CANCELLED, CausalOutcome.TIMED_OUT):
        return TaskStatus.CANCELLED
    raise ExecutionEpisodeCaptureError(f"unsupported canonical causal outcome: {outcome!r}")


def _validate_identity_and_truth(request: ExecutionEpisodeRequest) -> None:
    task_id = request.task.task_id
    context_task_id = request.context.task_id
    experience_task_id = request.experience.task_id

    if context_task_id is None:
        raise ExecutionEpisodeCaptureError(
            "execution context must carry the task identity for episode capture"
        )
    if experience_task_id is None:
        raise ExecutionEpisodeCaptureError(
            "causal experience must carry the task identity for episode capture"
        )
    if context_task_id != task_id:
        raise ExecutionEpisodeCaptureError(
            "execution context task identity does not match the canonical Task"
        )
    if experience_task_id != task_id:
        raise ExecutionEpisodeCaptureError(
            "causal experience task identity does not match the canonical Task"
        )
    if request.context.correlation_id != request.experience.correlation_id:
        raise ExecutionEpisodeCaptureError(
            "execution context correlation identity does not match causal experience"
        )

    experience_episode_id = request.experience.episode_id
    if experience_episode_id is not None and experience_episode_id != request.episode_id:
        raise ExecutionEpisodeCaptureError(
            "causal experience episode identity does not match the requested EpisodeId"
        )

    expected_status = _expected_task_status(request.experience.outcome)
    if request.task.status is not expected_status:
        raise ExecutionEpisodeCaptureError(
            "canonical Task status is inconsistent with causal outcome: "
            f"expected {expected_status.value}, got {request.task.status.value}"
        )


def _summary(task: Task, experience: CausalExperience) -> str:
    """Preserve objective context plus the exact controlled causal outcome label."""
    return f"{experience.outcome.value}: {task.objective}"


def package_execution_episode(request: ExecutionEpisodeRequest) -> EpisodeRecord:
    """Validate canonical evidence and deterministically package one EpisodeRecord.

    No persistence occurs here. The function never executes a capability,
    performs verification, reads text for truth, mutates Task state, or changes
    authority. Every identity/outcome consistency check completes before a
    record can be handed to persistence.
    """
    if not isinstance(request, ExecutionEpisodeRequest):
        raise TypeError(f"request must be an ExecutionEpisodeRequest, got {type(request).__name__}")

    _validate_identity_and_truth(request)
    episode = EpisodeRecord(
        episode_id=request.episode_id,
        outcome=_episode_outcome(request.experience.outcome),
        summary=_summary(request.task, request.experience),
        created_at=request.recorded_at,
        task_id=request.task.task_id,
        correlation_id=request.context.correlation_id,
        started_at=request.experience.state_before.captured_at,
        ended_at=request.experience.outcome_at,
        supporting_event_ids=request.supporting_event_ids,
    )
    if episode.created_at < request.experience.outcome_at:
        raise ExecutionEpisodeCaptureError(
            "recorded_at must not be earlier than the canonical causal outcome timestamp"
        )
    return episode


class ExecutionEpisodeCapture:
    """Explicit persistence adapter from canonical execution evidence to history."""

    __slots__ = ("_memory",)

    def __init__(self, *, memory: ExperienceMemory) -> None:
        if not isinstance(memory, ExperienceMemory):
            raise TypeError(f"memory must be an ExperienceMemory, got {type(memory).__name__}")
        self._memory = memory

    def record(self, request: ExecutionEpisodeRequest) -> RecordedExecutionEpisode:
        """Package, validate, then append one canonical episode through Hive memory."""
        episode = package_execution_episode(request)
        sequence = self._memory.record_episode(episode)
        return RecordedExecutionEpisode(sequence=sequence, episode=episode)
