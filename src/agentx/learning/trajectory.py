"""Deterministic normalization of historical AgentX execution trajectories.

C3.01 is a pure transformation boundary. It orders and groups canonical
``CausalExperience`` records into one analysis-friendly trajectory without
classifying actions, inferring causation, learning, routing, retrying, or
executing anything.

The normalized form deliberately *contains* canonical C2.10 experiences rather
than copying their state/action/observation/verification schema. Historical
content remains inert data and conveys no authority for future execution.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import NAMESPACE_URL, UUID, uuid5

from agentx.core.causal_experience import CausalExperience
from agentx.core.episodes import EpisodeRecord
from agentx.core.ids import EpisodeId, TaskId

__all__ = [
    "NORMALIZED_TRAJECTORY_SCHEMA_VERSION",
    "NormalizedTrajectory",
    "NormalizedTrajectoryStep",
    "TrajectoryNormalizationError",
    "TrajectoryValidationError",
    "normalize_trajectory",
]

NORMALIZED_TRAJECTORY_SCHEMA_VERSION: Final[int] = 1
_SHA256_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_TRAJECTORY_ID_PREFIX: Final[str] = "agentx:c3.01:normalized-trajectory:v1:"


class TrajectoryValidationError(ValueError):
    """Raised when a normalized trajectory contract is internally inconsistent."""


class TrajectoryNormalizationError(TrajectoryValidationError):
    """Raised when source evidence cannot form one canonical trajectory."""


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID, got {type(value).__name__}")
    if value.int == 0:
        raise TrajectoryValidationError(f"{field_name} must not be the nil UUID")
    return value


def _validate_optional_task_id(value: object) -> TaskId | None:
    if value is None:
        return None
    if not isinstance(value, TaskId):
        raise TypeError(f"task_id must be a TaskId or None, got {type(value).__name__}")
    return value


def _validate_optional_episode_id(value: object) -> EpisodeId | None:
    if value is None:
        return None
    if not isinstance(value, EpisodeId):
        raise TypeError(f"episode_id must be an EpisodeId or None, got {type(value).__name__}")
    return value


def _experience_fingerprint(experience: CausalExperience) -> str:
    return hashlib.sha256(experience.to_json().encode("utf-8")).hexdigest()


def _experience_order_key(experience: CausalExperience) -> tuple[datetime, datetime, datetime, str]:
    """Return a total, deterministic ordering key without causal interpretation."""
    return (
        experience.state_before.captured_at,
        experience.action_at,
        experience.outcome_at,
        experience.to_json(),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class NormalizedTrajectoryStep:
    """One ordered reference to a canonical C2.10 causal-experience record."""

    sequence: int
    source_experience_sha256: str
    experience: CausalExperience

    def __post_init__(self) -> None:
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool):
            raise TypeError("sequence must be an integer")
        if self.sequence < 1:
            raise TrajectoryValidationError("sequence must be >= 1")
        if not isinstance(self.source_experience_sha256, str):
            raise TypeError("source_experience_sha256 must be a string")
        if not _SHA256_PATTERN.fullmatch(self.source_experience_sha256):
            raise TrajectoryValidationError(
                "source_experience_sha256 must be 64 lowercase hexadecimal characters"
            )
        if not isinstance(self.experience, CausalExperience):
            raise TypeError(
                "experience must be a canonical CausalExperience, "
                f"got {type(self.experience).__name__}"
            )
        expected = _experience_fingerprint(self.experience)
        if self.source_experience_sha256 != expected:
            raise TrajectoryValidationError(
                "source_experience_sha256 does not match the canonical CausalExperience"
            )

    @classmethod
    def from_experience(
        cls, *, sequence: int, experience: CausalExperience
    ) -> NormalizedTrajectoryStep:
        """Create a normalized step without changing canonical experience evidence."""
        if not isinstance(experience, CausalExperience):
            raise TypeError(
                f"experience must be a canonical CausalExperience, got {type(experience).__name__}"
            )
        return cls(
            sequence=sequence,
            source_experience_sha256=_experience_fingerprint(experience),
            experience=experience,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the stable analysis representation for this ordered step."""
        return {
            "sequence": self.sequence,
            "source_experience_sha256": self.source_experience_sha256,
            "experience": self.experience.to_dict(),
        }


def _trajectory_identity_basis(
    *,
    correlation_id: UUID,
    task_id: TaskId | None,
    episode_id: EpisodeId | None,
    source_episode: EpisodeRecord | None,
    steps: tuple[NormalizedTrajectoryStep, ...],
) -> str:
    raw: dict[str, object] = {
        "schema_version": NORMALIZED_TRAJECTORY_SCHEMA_VERSION,
        "correlation_id": str(correlation_id),
        "task_id": None if task_id is None else task_id.to_str(),
        "episode_id": None if episode_id is None else episode_id.to_str(),
        "source_episode": None if source_episode is None else source_episode.to_dict(),
        "steps": [step.to_dict() for step in steps],
    }
    return json.dumps(
        raw,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _derive_trajectory_id(
    *,
    correlation_id: UUID,
    task_id: TaskId | None,
    episode_id: EpisodeId | None,
    source_episode: EpisodeRecord | None,
    steps: tuple[NormalizedTrajectoryStep, ...],
) -> UUID:
    basis = _trajectory_identity_basis(
        correlation_id=correlation_id,
        task_id=task_id,
        episode_id=episode_id,
        source_episode=source_episode,
        steps=steps,
    )
    return uuid5(NAMESPACE_URL, f"{_TRAJECTORY_ID_PREFIX}{basis}")


@dataclass(frozen=True, slots=True, kw_only=True)
class NormalizedTrajectory:
    """Immutable, deterministic trajectory assembled from canonical history.

    ``steps`` are ordered for analysis only. Their order describes recorded
    chronology and a deterministic tie-break; it does not establish scientific
    causation or action importance.
    """

    trajectory_id: UUID
    correlation_id: UUID
    task_id: TaskId | None
    episode_id: EpisodeId | None
    steps: tuple[NormalizedTrajectoryStep, ...]
    source_episode: EpisodeRecord | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "trajectory_id",
            _validate_uuid(self.trajectory_id, field_name="trajectory_id"),
        )
        object.__setattr__(
            self,
            "correlation_id",
            _validate_uuid(self.correlation_id, field_name="correlation_id"),
        )
        object.__setattr__(self, "task_id", _validate_optional_task_id(self.task_id))
        object.__setattr__(self, "episode_id", _validate_optional_episode_id(self.episode_id))
        if not isinstance(self.steps, tuple):
            raise TypeError("steps must be a tuple of NormalizedTrajectoryStep values")
        if not self.steps:
            raise TrajectoryValidationError("steps must not be empty")
        for index, step in enumerate(self.steps, start=1):
            if not isinstance(step, NormalizedTrajectoryStep):
                raise TypeError(
                    "steps must contain only NormalizedTrajectoryStep values; "
                    f"index {index - 1} is {type(step).__name__}"
                )
            if step.sequence != index:
                raise TrajectoryValidationError("step sequence values must be contiguous from 1")
            experience = step.experience
            if experience.correlation_id != self.correlation_id:
                raise TrajectoryValidationError(
                    "every step experience must share the trajectory correlation_id"
                )
            if experience.task_id is not None and experience.task_id != self.task_id:
                raise TrajectoryValidationError(
                    "step task_id conflicts with the trajectory task association"
                )
            if experience.episode_id is not None and experience.episode_id != self.episode_id:
                raise TrajectoryValidationError(
                    "step episode_id conflicts with the trajectory episode association"
                )

        if self.source_episode is not None:
            if not isinstance(self.source_episode, EpisodeRecord):
                raise TypeError(
                    "source_episode must be an EpisodeRecord or None, "
                    f"got {type(self.source_episode).__name__}"
                )
            if self.episode_id != self.source_episode.episode_id:
                raise TrajectoryValidationError(
                    "source_episode episode_id conflicts with trajectory episode_id"
                )
            if (
                self.source_episode.correlation_id is not None
                and self.source_episode.correlation_id != self.correlation_id
            ):
                raise TrajectoryValidationError(
                    "source_episode correlation_id conflicts with trajectory correlation_id"
                )
            if (
                self.source_episode.task_id is not None
                and self.source_episode.task_id != self.task_id
            ):
                raise TrajectoryValidationError(
                    "source_episode task_id conflicts with trajectory task_id"
                )

        expected_id = _derive_trajectory_id(
            correlation_id=self.correlation_id,
            task_id=self.task_id,
            episode_id=self.episode_id,
            source_episode=self.source_episode,
            steps=self.steps,
        )
        if self.trajectory_id != expected_id:
            raise TrajectoryValidationError(
                "trajectory_id does not match the deterministic normalized evidence"
            )

    @property
    def started_at(self) -> datetime:
        """Timestamp of the earliest state-before snapshot in normalized order."""
        return self.steps[0].experience.state_before.captured_at

    @property
    def ended_at(self) -> datetime:
        """Latest historical outcome timestamp represented by the trajectory."""
        return max(step.experience.outcome_at for step in self.steps)

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic representation while nesting canonical C2.10 data."""
        return {
            "schema_version": NORMALIZED_TRAJECTORY_SCHEMA_VERSION,
            "trajectory_id": str(self.trajectory_id),
            "correlation_id": str(self.correlation_id),
            "task_id": None if self.task_id is None else self.task_id.to_str(),
            "episode_id": None if self.episode_id is None else self.episode_id.to_str(),
            "source_episode": (
                None if self.source_episode is None else self.source_episode.to_dict()
            ),
            "steps": [step.to_dict() for step in self.steps],
        }

    def to_json(self) -> str:
        """Serialize the normalized analysis shape deterministically."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _single_optional_identity[T](values: Iterable[T | None], *, field_name: str) -> T | None:
    present = {value for value in values if value is not None}
    if len(present) > 1:
        raise TrajectoryNormalizationError(
            f"source experiences contain conflicting {field_name} values"
        )
    return next(iter(present), None)


def normalize_trajectory(
    experiences: Iterable[CausalExperience], *, episode: EpisodeRecord | None = None
) -> NormalizedTrajectory:
    """Normalize canonical causal experiences into one deterministic trajectory.

    Input iteration order is not trusted as canonical ordering. Records are
    sorted by explicit historical timestamps with canonical C2.10 JSON as a
    deterministic tie-break. No record is dropped or reclassified.
    """
    try:
        source = tuple(experiences)
    except TypeError as exc:
        raise TypeError("experiences must be an iterable of CausalExperience values") from exc
    if not source:
        raise TrajectoryNormalizationError("at least one CausalExperience is required")
    for index, experience in enumerate(source):
        if not isinstance(experience, CausalExperience):
            raise TypeError(
                "experiences must contain only canonical CausalExperience values; "
                f"index {index} is {type(experience).__name__}"
            )
    if episode is not None and not isinstance(episode, EpisodeRecord):
        raise TypeError(f"episode must be an EpisodeRecord or None, got {type(episode).__name__}")

    ordered = tuple(sorted(source, key=_experience_order_key))
    correlation_id = ordered[0].correlation_id
    if any(experience.correlation_id != correlation_id for experience in ordered[1:]):
        raise TrajectoryNormalizationError(
            "one normalized trajectory cannot mix different correlation_id values"
        )

    task_id = _single_optional_identity(
        (experience.task_id for experience in ordered), field_name="task_id"
    )
    episode_id = _single_optional_identity(
        (experience.episode_id for experience in ordered), field_name="episode_id"
    )

    if episode is not None:
        if episode.correlation_id is not None and episode.correlation_id != correlation_id:
            raise TrajectoryNormalizationError(
                "EpisodeRecord correlation_id conflicts with source experiences"
            )
        if task_id is not None and episode.task_id is not None and episode.task_id != task_id:
            raise TrajectoryNormalizationError(
                "EpisodeRecord task_id conflicts with source experiences"
            )
        if episode_id is not None and episode.episode_id != episode_id:
            raise TrajectoryNormalizationError(
                "EpisodeRecord episode_id conflicts with source experiences"
            )
        if task_id is None:
            task_id = episode.task_id
        if episode_id is None:
            episode_id = episode.episode_id

    steps = tuple(
        NormalizedTrajectoryStep.from_experience(sequence=index, experience=experience)
        for index, experience in enumerate(ordered, start=1)
    )
    trajectory_id = _derive_trajectory_id(
        correlation_id=correlation_id,
        task_id=task_id,
        episode_id=episode_id,
        source_episode=episode,
        steps=steps,
    )
    return NormalizedTrajectory(
        trajectory_id=trajectory_id,
        correlation_id=correlation_id,
        task_id=task_id,
        episode_id=episode_id,
        steps=steps,
        source_episode=episode,
    )
