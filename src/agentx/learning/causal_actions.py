"""Conservative extraction of explicit action candidates from normalized history.

C3.02 is the second stage of the AgentX Skill Compiler pipeline. It consumes a
canonical C3.01 :class:`~agentx.learning.trajectory.NormalizedTrajectory` and
exposes each normalized step's already-recorded C2.10 action as an inert action
*candidate*.

The word "causal" here names the source record shape, not a claim of general
causal inference. A candidate means only that canonical history contains an
explicit ``ActionPayload`` at that normalized position. It does NOT mean the
action was necessary, sufficient, relevant, useful, executed successfully, or
safe to repeat.

Extraction deliberately performs no filtering. Current C2.10
``CausalExperience`` makes ``action`` mandatory, so every valid C3.01 step
contains explicit action evidence and therefore yields exactly one candidate.
C3.03, not this module, owns later irrelevant-action elimination.

The representation keeps the canonical C3.01 source step unchanged. This
preserves source identity, ordering, duplicates, optional missing evidence,
verification state, failure outcomes, timestamps, and the canonical C2.10
payload types without copying them into a competing schema.

Everything in this module is DATA-only. Extraction cannot execute a capability,
create or activate a procedure, register a skill, grant permission, lower risk,
enlarge budgets, mark a task successful, fabricate verification, mutate Hive,
route/retry/repair work, or call a model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from agentx.core.causal_experience import (
    CausalExperience,
    CausalOutcome,
    ExperienceState,
)
from agentx.core.events import ActionPayload, ObservationPayload, VerificationPayload
from agentx.learning.trajectory import NormalizedTrajectory, NormalizedTrajectoryStep

__all__ = [
    "CAUSAL_ACTION_EXTRACTION_SCHEMA_VERSION",
    "CausalActionExtraction",
    "CausalActionExtractionError",
    "ExtractedActionCandidate",
    "extract_causal_action_candidates",
]

CAUSAL_ACTION_EXTRACTION_SCHEMA_VERSION: Final[int] = 1


class CausalActionExtractionError(ValueError):
    """Raised when an extracted candidate set violates the C3.02 contract."""


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{field_name} must be a UUID, got {type(value).__name__}")
    if value.int == 0:
        raise CausalActionExtractionError(f"{field_name} must not be the nil UUID")
    return value


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractedActionCandidate:
    """One explicit recorded-action candidate linked to its normalized source.

    The candidate stores only provenance plus the unchanged canonical C3.01
    source step. Convenience properties expose canonical C2.10 evidence without
    copying or reinterpreting it.

    Candidate status is intentionally weak: presence here says only "this
    normalized source step records an explicit action". It never means
    causally necessary, useful, successful, relevant, trusted, or executable.
    """

    source_trajectory_id: UUID
    source_step: NormalizedTrajectoryStep

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_trajectory_id",
            _validate_uuid(self.source_trajectory_id, field_name="source_trajectory_id"),
        )
        if not isinstance(self.source_step, NormalizedTrajectoryStep):
            raise TypeError(
                "source_step must be a canonical NormalizedTrajectoryStep, "
                f"got {type(self.source_step).__name__}"
            )

    @property
    def source_sequence(self) -> int:
        """Return the exact sequence number assigned by C3.01."""
        return self.source_step.sequence

    @property
    def source_experience_sha256(self) -> str:
        """Return the exact C3.01 fingerprint of the source experience."""
        return self.source_step.source_experience_sha256

    @property
    def experience(self) -> CausalExperience:
        """Return the unchanged canonical C2.10 source experience."""
        return self.source_step.experience

    @property
    def action(self) -> ActionPayload:
        """Return the explicit canonical action payload; no interpretation occurs."""
        return self.experience.action

    @property
    def outcome(self) -> CausalOutcome:
        """Return the historical attempt outcome without converting it to usefulness."""
        return self.experience.outcome

    @property
    def observation(self) -> ObservationPayload | None:
        """Return canonical observation evidence exactly as present or absent."""
        return self.experience.observation

    @property
    def state_after(self) -> ExperienceState | None:
        """Return canonical post-action state exactly as present or absent."""
        return self.experience.state_after

    @property
    def verification(self) -> VerificationPayload | None:
        """Return canonical verification evidence exactly as present or absent."""
        return self.experience.verification

    def to_dict(self) -> dict[str, object]:
        """Return deterministic provenance plus the unchanged normalized source step."""
        return {
            "source_trajectory_id": str(self.source_trajectory_id),
            "source_step": self.source_step.to_dict(),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class CausalActionExtraction:
    """Immutable deterministic C3.02 output for one normalized trajectory.

    No new trajectory or compiler identity is invented. ``source_trajectory_id``
    is the exact C3.01 identity, and candidate ordering must exactly match the
    normalized source sequences. Distinct source steps are retained even when
    their canonical experience fingerprints are identical.
    """

    source_trajectory_id: UUID
    candidates: tuple[ExtractedActionCandidate, ...]
    schema_version: int = CAUSAL_ACTION_EXTRACTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_trajectory_id",
            _validate_uuid(self.source_trajectory_id, field_name="source_trajectory_id"),
        )
        if not isinstance(self.candidates, tuple):
            raise TypeError("candidates must be a tuple of ExtractedActionCandidate values")
        if not self.candidates:
            raise CausalActionExtractionError("candidates must not be empty")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise TypeError("schema_version must be an integer")
        if self.schema_version != CAUSAL_ACTION_EXTRACTION_SCHEMA_VERSION:
            raise CausalActionExtractionError(
                f"unsupported causal-action extraction schema version {self.schema_version}; "
                f"supported version is {CAUSAL_ACTION_EXTRACTION_SCHEMA_VERSION}"
            )

        for expected_sequence, candidate in enumerate(self.candidates, start=1):
            if not isinstance(candidate, ExtractedActionCandidate):
                raise TypeError(
                    "candidates must contain only ExtractedActionCandidate values; "
                    f"index {expected_sequence - 1} is {type(candidate).__name__}"
                )
            if candidate.source_trajectory_id != self.source_trajectory_id:
                raise CausalActionExtractionError(
                    "every candidate must reference the extraction source_trajectory_id"
                )
            if candidate.source_sequence != expected_sequence:
                raise CausalActionExtractionError(
                    "candidate source sequences must preserve the complete C3.01 order "
                    "contiguously from 1"
                )

    def to_dict(self) -> dict[str, object]:
        """Return the deterministic JSON-compatible C3.02 representation."""
        return {
            "schema_version": self.schema_version,
            "source_trajectory_id": str(self.source_trajectory_id),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    def to_json(self) -> str:
        """Serialize deterministically without executable reconstruction hooks."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def extract_causal_action_candidates(trajectory: NormalizedTrajectory) -> CausalActionExtraction:
    """Extract one action candidate for every canonical normalized source step.

    Exact deterministic rule:

    1. Require one canonical C3.01 ``NormalizedTrajectory``.
    2. Iterate ``trajectory.steps`` in their already-canonical sequence order.
    3. Emit exactly one :class:`ExtractedActionCandidate` per step, preserving
       that exact ``NormalizedTrajectoryStep`` object and the trajectory's
       existing C3.01 identity.
    4. Perform no filtering, scoring, text inspection, success test, failure
       suppression, deduplication, causal-necessity inference, or model call.

    A valid current C3.01 step necessarily contains a valid C2.10
    ``CausalExperience``, whose ``action`` is mandatory. Consequently there is
    no valid-current-schema "missing action" case to fill in or guess about.
    Optional observation/state-after/verification evidence may be absent and is
    preserved as absent through the unchanged source step.
    """
    if not isinstance(trajectory, NormalizedTrajectory):
        raise TypeError(
            f"trajectory must be a canonical NormalizedTrajectory, got {type(trajectory).__name__}"
        )

    candidates = tuple(
        ExtractedActionCandidate(
            source_trajectory_id=trajectory.trajectory_id,
            source_step=step,
        )
        for step in trajectory.steps
    )
    return CausalActionExtraction(
        source_trajectory_id=trajectory.trajectory_id,
        candidates=candidates,
    )
