"""Deterministic selection of canonical ACTIVE procedure revisions.

This module is the reuse-selection boundary. It consumes only a caller-supplied,
bounded collection of canonical matching candidates and one canonical
``ProcedureRequirement``. Applicability is delegated to the M4.04
``ProcedureApplicabilityMatcher``; this module never reimplements scope or
capability matching.

Selection is deliberately narrower than execution or lifecycle management:
records are not retrieved, promoted, retired, persisted, interpreted, or
executed. ``SELECTED`` is an inert statement about the best deterministic
reuse candidate among the supplied records, not permission or proof of
success.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from agentx.core.procedure_matching import (
    ProcedureApplicabilityMatcher,
    ProcedureCandidate,
    ProcedureMatchOutcome,
    ProcedureMatchResult,
    ProcedureRequirement,
)
from agentx.core.procedures import ProcedureId, ProcedureStatus

__all__ = [
    "ProcedureReuseSelectionOutcome",
    "ProcedureReuseSelectionResult",
    "ProcedureReuseSelector",
    "select_reusable_procedure",
]


class ProcedureReuseSelectionOutcome(StrEnum):
    """Closed, inert outcome vocabulary for reuse selection."""

    NO_MATCH = "no_match"
    SELECTED = "selected"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class ProcedureReuseSelectionResult:
    """The result of one pure reuse-selection assessment.

    ``selected`` is present only for ``SELECTED`` and preserves the exact
    canonical candidate, including its ``ProcedureId`` and revision. The
    matched assessments are retained as evidence, not as authority.
    """

    outcome: ProcedureReuseSelectionOutcome
    selected: ProcedureCandidate | None = None
    matches: tuple[ProcedureMatchResult, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ProcedureReuseSelectionOutcome):
            raise TypeError("outcome must be a ProcedureReuseSelectionOutcome")
        if self.selected is not None and not isinstance(self.selected, ProcedureCandidate):
            raise TypeError("selected must be a ProcedureCandidate or None")
        if not isinstance(self.matches, tuple) or not all(
            isinstance(item, ProcedureMatchResult) for item in self.matches
        ):
            raise TypeError("matches must be a tuple of ProcedureMatchResult values")
        if self.outcome is ProcedureReuseSelectionOutcome.SELECTED:
            if self.selected is None or len(self.matches) != 1:
                raise ValueError("SELECTED requires exactly one selected matching candidate")
        elif self.selected is not None:
            raise ValueError("NO_MATCH and AMBIGUOUS cannot contain a selected candidate")

    @property
    def status(self) -> ProcedureReuseSelectionOutcome:
        """Alias for callers that use status terminology."""
        return self.outcome

    @property
    def procedure_id(self) -> ProcedureId | None:
        """The selected identity, or ``None`` when selection did not succeed."""
        return None if self.selected is None else self.selected.record.procedure_id

    @property
    def revision(self) -> int | None:
        """The selected revision, or ``None`` when selection did not succeed."""
        return None if self.selected is None else self.selected.record.revision

    grants_execution_authority: ClassVar[bool] = False


class ProcedureReuseSelector:
    """Stateless deterministic selector over explicitly supplied candidates."""

    __slots__ = ("_matcher",)

    def __init__(self) -> None:
        self._matcher = ProcedureApplicabilityMatcher()

    def select(
        self,
        candidates: Collection[ProcedureCandidate],
        requirement: ProcedureRequirement,
    ) -> ProcedureReuseSelectionResult:
        """Select one reusable ACTIVE candidate, without any side effect.

        Duplicate identical candidate records are treated as one supplied
        record. Conflicting records with the same ``(ProcedureId, revision)``
        identity are not resolved by payload, timestamps, or ordering and
        therefore make the result ``AMBIGUOUS`` if they could otherwise be
        eligible. Candidate order never affects the result.
        """
        if not isinstance(candidates, Collection) or isinstance(candidates, (str, bytes)):
            raise TypeError("candidates must be a bounded collection of ProcedureCandidate values")
        if not isinstance(requirement, ProcedureRequirement):
            raise TypeError("requirement must be a ProcedureRequirement")
        if not all(isinstance(candidate, ProcedureCandidate) for candidate in candidates):
            raise TypeError("candidates must contain only ProcedureCandidate values")

        unique: dict[tuple[ProcedureId, int], ProcedureCandidate] = {}
        conflicting: set[tuple[ProcedureId, int]] = set()
        for candidate in candidates:
            identity = (candidate.record.procedure_id, candidate.record.revision)
            previous = unique.get(identity)
            if previous is None:
                unique[identity] = candidate
            elif previous != candidate:
                conflicting.add(identity)

        applicable: list[tuple[ProcedureCandidate, ProcedureMatchResult]] = []
        for identity in sorted(unique, key=lambda item: (item[0].to_str(), item[1])):
            candidate = unique[identity]
            # Lifecycle eligibility belongs here, while structural
            # applicability belongs exclusively to the canonical M4.04 matcher.
            if candidate.record.status is not ProcedureStatus.ACTIVE:
                continue
            if identity in conflicting:
                continue
            assessment = self._matcher.assess(candidate, requirement)
            if assessment.outcome in (
                ProcedureMatchOutcome.EXACT_MATCH,
                ProcedureMatchOutcome.COMPATIBLE,
            ):
                applicable.append((candidate, assessment))

        if not applicable:
            return ProcedureReuseSelectionResult(ProcedureReuseSelectionOutcome.NO_MATCH)

        # M4.04's exact verdict is the only canonical specificity distinction
        # available here. It is a category, not a score. A single exact match
        # beats broader compatible matches; equal categories remain ambiguous.
        exact = [
            item for item in applicable if item[1].outcome is ProcedureMatchOutcome.EXACT_MATCH
        ]
        best = exact if exact else applicable
        if len(best) != 1:
            return ProcedureReuseSelectionResult(
                ProcedureReuseSelectionOutcome.AMBIGUOUS,
                matches=tuple(item[1] for item in best),
            )

        candidate, assessment = best[0]
        return ProcedureReuseSelectionResult(
            ProcedureReuseSelectionOutcome.SELECTED,
            selected=candidate,
            matches=(assessment,),
        )


def select_reusable_procedure(
    candidates: Collection[ProcedureCandidate],
    requirement: ProcedureRequirement,
) -> ProcedureReuseSelectionResult:
    """Pure convenience entry point for :class:`ProcedureReuseSelector`."""
    return ProcedureReuseSelector().select(candidates, requirement)
