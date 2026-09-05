"""A2.08 canonical deterministic L0-L5 escalation/fallback decision contract.

The Escalator consumes explicit typed attempt evidence and returns a bounded
next-level decision. It never executes a strategy, performs I/O, invokes a
model, grants authority, mutates runtime state, or verifies an outcome.

A2.07 answers: the lowest justified *starting* execution level.
A2.08 answers: given an explicit unsuccessful/inadequate attempt at a level,
what bounded next-level decision is justified?

Decision vocabulary
-------------------

``STAY``
    Remain at the current level. Used for a verified successful attempt and
    for an unsuccessful attempt whose current strategy can still continue.
    Success is never escalated merely because a higher level exists.

``ESCALATE``
    Move to the immediate successor in the canonical hierarchy
    ``L0_CACHE -> L1_DIRECT -> L2_COMPILED -> L3_GUIDED -> L4_PLANNED ->
    L5_EXPLORATORY``. Escalation is monotonic and never skips a level.

``EXHAUSTED``
    No justified next action at this boundary. Used when the current strategy
    cannot continue and either escalation is not explicitly permitted or no
    successor exists (L5). L5 never wraps to L0.

An EscalationDecision is not authority. Moving from L2 to L3, or from L4 to
L5, does not authorize model calls, research, network access, capability
execution, filesystem writes, shell execution, higher risk, or more budget.
All Trusted Kernel gates remain mandatory.

De-escalation is not inferred. This module does not produce a cheaper level
from a failed high-level strategy. A separate fallback concept is out of
scope unless a later task supplies its own explicit typed evidence.

Fail closed
-----------

Unknown or malformed evidence is rejected. Hostile text, model output, and
exception strings cannot force L5 or any other level: the Escalator accepts
no free text. A failure does not automatically mean "research the web."
Reaching L5 does not authorize exploration.

Deliberate non-scope
--------------------

Not a Router (A2.07), Verifier (A2.05), anti-loop (A2.09), or agent loop
(A2.10). No retries, retry counters, wall-clock loops, fallback execution,
procedure interpretation, planning, research, skill compilation, repair,
models, capability execution, Task transitions, EventBus orchestration,
budget mutation, or kernel changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.cognition.router import CANONICAL_EXECUTION_LEVELS, ExecutionLevel

__all__ = [
    "CANONICAL_ESCALATION_ACTIONS",
    "EscalationAction",
    "EscalationDecision",
    "EscalationEvidence",
    "ExecutionLevelEscalator",
    "successor_execution_level",
]


class EscalationAction(StrEnum):
    """Closed A2.08 decision vocabulary. None of these grant execution permission."""

    STAY = "STAY"
    ESCALATE = "ESCALATE"
    EXHAUSTED = "EXHAUSTED"


CANONICAL_ESCALATION_ACTIONS: Final[tuple[EscalationAction, ...]] = (
    EscalationAction.STAY,
    EscalationAction.ESCALATE,
    EscalationAction.EXHAUSTED,
)


def successor_execution_level(level: ExecutionLevel) -> ExecutionLevel | None:
    """Return the immediate more-expensive successor, or None at L5.

    The hierarchy never wraps. L5 has no successor; it does not become L0.
    """
    if not isinstance(level, ExecutionLevel):
        raise TypeError("level must be an ExecutionLevel")
    try:
        index = CANONICAL_EXECUTION_LEVELS.index(level)
    except ValueError as exc:
        raise TypeError("level must be a canonical ExecutionLevel") from exc
    next_index = index + 1
    if next_index >= len(CANONICAL_EXECUTION_LEVELS):
        return None
    return CANONICAL_EXECUTION_LEVELS[next_index]


def _require_bool(value: object, field_name: str) -> None:
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be bool")


@dataclass(frozen=True, slots=True, kw_only=True)
class EscalationEvidence:
    """Explicit typed facts available to the deterministic Escalator.

    Every field must be supplied. Booleans are fail-closed: ``False`` never
    grants escalation. The Escalator deliberately accepts no free text,
    exception objects, identifiers, stores, registries, providers, callbacks,
    outcomes, verification verdicts, or executable objects.

    ``attempt_verified_successful`` is orchestration-supplied evidence that a
    prior attempt was already verified successful. This module does not
    perform verification and does not inspect a Verifier or A1.10 outcome.
    """

    current_level: ExecutionLevel
    current_strategy_can_continue: bool
    attempt_verified_successful: bool
    escalation_explicitly_permitted: bool

    def __post_init__(self) -> None:
        if not isinstance(self.current_level, ExecutionLevel):
            raise TypeError("current_level must be an ExecutionLevel")
        _require_bool(self.current_strategy_can_continue, "current_strategy_can_continue")
        _require_bool(self.attempt_verified_successful, "attempt_verified_successful")
        _require_bool(self.escalation_explicitly_permitted, "escalation_explicitly_permitted")

    @property
    def successor_exists(self) -> bool:
        """True iff the canonical hierarchy names a level above ``current_level``."""
        return successor_execution_level(self.current_level) is not None


@dataclass(frozen=True, slots=True, kw_only=True)
class EscalationDecision:
    """Inert bounded next-level decision. Grants no permission and executes nothing.

    Invariants:

    * ``ESCALATE`` carries the immediate successor of ``current_level``.
    * ``STAY`` and ``EXHAUSTED`` carry ``next_level is None``.
    * ``next_level`` is never cheaper than ``current_level`` and never wraps.
    """

    action: EscalationAction
    current_level: ExecutionLevel
    next_level: ExecutionLevel | None

    def __post_init__(self) -> None:
        if not isinstance(self.action, EscalationAction):
            raise TypeError("action must be an EscalationAction")
        if not isinstance(self.current_level, ExecutionLevel):
            raise TypeError("current_level must be an ExecutionLevel")
        if self.next_level is not None and not isinstance(self.next_level, ExecutionLevel):
            raise TypeError("next_level must be an ExecutionLevel or None")

        successor = successor_execution_level(self.current_level)
        if self.action is EscalationAction.ESCALATE:
            if self.next_level is None:
                raise ValueError("ESCALATE requires a next_level")
            if self.next_level is not successor:
                raise ValueError("ESCALATE next_level must be the immediate successor")
        elif self.next_level is not None:
            raise ValueError(f"{self.action.value} requires next_level to be None")


class ExecutionLevelEscalator:
    """Stateless deterministic Escalator for the canonical AgentX hierarchy."""

    __slots__ = ()

    def decide(self, evidence: EscalationEvidence) -> EscalationDecision:
        """Return the bounded STAY / ESCALATE / EXHAUSTED decision for ``evidence``."""
        if not isinstance(evidence, EscalationEvidence):
            raise TypeError("evidence must be EscalationEvidence")

        current = evidence.current_level
        if evidence.attempt_verified_successful or evidence.current_strategy_can_continue:
            return EscalationDecision(
                action=EscalationAction.STAY,
                current_level=current,
                next_level=None,
            )

        successor = successor_execution_level(current)
        if evidence.escalation_explicitly_permitted and successor is not None:
            return EscalationDecision(
                action=EscalationAction.ESCALATE,
                current_level=current,
                next_level=successor,
            )

        return EscalationDecision(
            action=EscalationAction.EXHAUSTED,
            current_level=current,
            next_level=None,
        )
