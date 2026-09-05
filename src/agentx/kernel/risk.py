"""Canonical deterministic R0-R4 risk classification contract for AgentX.

Risk classification informs Trusted Kernel authority decisions; it never grants
permission by itself. A low-risk assessment is only descriptive input to later
policy/gating work. This module intentionally contains no authorization,
permission-engine, action-gate, capability-execution, event-emission, or prompt
interpretation logic.

Classification is deterministic and operates only on explicitly supplied action
characteristics. Conflicting characteristics are resolved conservatively: a
higher-risk characteristic always dominates a lower-risk one, and R4 dominates
all other levels.

``RiskAssessment.level`` remains part of the stable descriptive contract, but
it is never allowed to lower the risk implied by the assessment's canonical
action characteristics. ``effective_level`` is therefore the greater of the
stored level and the kernel-derived characteristic floor. This preserves
compatibility with existing assessments while preventing caller-selected
severity from downgrading explicit state-change, external-effect, critical, or
destructive facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from functools import total_ordering
from typing import Final


@total_ordering
class RiskLevel(Enum):
    """Canonical AgentX risk levels with deliberate severity ordering."""

    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"

    @property
    def label(self) -> str:
        """Return the canonical semantic label for this level."""
        return _RISK_LABELS[self]

    @property
    def severity(self) -> int:
        """Return the explicit numeric severity used only for ordering."""
        return _RISK_SEVERITY[self]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.severity < other.severity


_RISK_LABELS: Final[dict[RiskLevel, str]] = {
    RiskLevel.R0: "READ",
    RiskLevel.R1: "REVERSIBLE",
    RiskLevel.R2: "MODIFY",
    RiskLevel.R3: "EXTERNAL_EFFECT",
    RiskLevel.R4: "CRITICAL",
}

_RISK_SEVERITY: Final[dict[RiskLevel, int]] = {
    RiskLevel.R0: 0,
    RiskLevel.R1: 1,
    RiskLevel.R2: 2,
    RiskLevel.R3: 3,
    RiskLevel.R4: 4,
}


def _validate_characteristic(name: str, value: bool) -> None:
    if type(value) is not bool:
        raise TypeError(f"{name} must be bool")


def _characteristic_floor(
    *,
    read_only: bool,
    modifies_state: bool,
    reversible: bool,
    external_effect: bool,
    critical: bool,
    destructive: bool,
) -> RiskLevel:
    """Return the minimum risk forced by explicit canonical characteristics.

    A directly constructed historical/legacy ``RiskAssessment`` may not carry
    every positive characteristic. Absence of a positive signal contributes no
    additional floor; the stored level remains a conservative upper input. Any
    explicit positive characteristic, however, can only raise the effective
    level and can never be overridden by a lower caller-selected ``level``.
    """
    characteristics = {
        "read_only": read_only,
        "modifies_state": modifies_state,
        "reversible": reversible,
        "external_effect": external_effect,
        "critical": critical,
        "destructive": destructive,
    }
    for name, value in characteristics.items():
        _validate_characteristic(name, value)

    if critical or destructive:
        return RiskLevel.R4
    if external_effect:
        return RiskLevel.R3
    if modifies_state or reversible:
        return RiskLevel.R1 if reversible else RiskLevel.R2
    return RiskLevel.R0


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Immutable description of one deterministic risk classification.

    The complete canonical action-characteristic vocabulary is retained so a
    later Trusted Kernel boundary can recompute a conservative effective risk
    instead of blindly trusting the descriptive ``level`` field. The level may
    conservatively overstate risk, but it can never reduce the floor implied by
    the characteristics.

    ``RiskAssessment`` remains descriptive only. It contains no authority
    decision and exposes no API for authorization, approval, execution, or
    bypassing policy.
    """

    level: RiskLevel
    reason: str
    reversible: bool
    external_effect: bool
    read_only: bool = False
    modifies_state: bool = False
    critical: bool = False
    destructive: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.level, RiskLevel):
            raise TypeError("level must be a RiskLevel")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")
        if not self.reason:
            raise ValueError("reason must not be empty")
        if self.reason != self.reason.strip():
            raise ValueError("reason must be trimmed")
        _validate_characteristic("read_only", self.read_only)
        _validate_characteristic("modifies_state", self.modifies_state)
        _validate_characteristic("reversible", self.reversible)
        _validate_characteristic("external_effect", self.external_effect)
        _validate_characteristic("critical", self.critical)
        _validate_characteristic("destructive", self.destructive)

    @property
    def characteristic_floor(self) -> RiskLevel:
        """Return the minimum level forced solely by canonical characteristics."""
        return _characteristic_floor(
            read_only=self.read_only,
            modifies_state=self.modifies_state,
            reversible=self.reversible,
            external_effect=self.external_effect,
            critical=self.critical,
            destructive=self.destructive,
        )

    @property
    def effective_level(self) -> RiskLevel:
        """Return the fail-safe level used by authority/resource boundaries.

        A caller-declared level is permitted to be more conservative than the
        characteristic floor, but never less conservative. Consequently a
        forged R0 carrying an explicit external-effect fact evaluates as R3 and
        a forged R0 carrying critical/destructive facts evaluates as R4.
        """
        floor = self.characteristic_floor
        return floor if floor > self.level else self.level


def assess_risk(
    *,
    read_only: bool,
    modifies_state: bool,
    reversible: bool,
    external_effect: bool,
    critical: bool = False,
    destructive: bool = False,
) -> RiskAssessment:
    """Classify explicit action characteristics into the canonical risk model.

    Rules are intentionally conservative and deterministic:

    * critical or destructive characteristics -> R4;
    * external effects -> at least R3;
    * state-changing and explicitly reversible -> R1;
    * state-changing without explicit reversibility -> R2;
    * explicitly read-only with no higher-risk characteristic -> R0.

    ``reversible=True`` is itself treated as evidence that a state change is
    being described. Therefore ``reversible=True`` with ``modifies_state=False``
    is not allowed to downgrade to R0. Likewise, ``read_only=True`` never lowers
    a simultaneous state-change, external-effect, or critical characteristic.
    """
    characteristics = {
        "read_only": read_only,
        "modifies_state": modifies_state,
        "reversible": reversible,
        "external_effect": external_effect,
        "critical": critical,
        "destructive": destructive,
    }
    for name, value in characteristics.items():
        _validate_characteristic(name, value)

    state_change = modifies_state or reversible
    conflicts: list[str] = []

    if reversible and not modifies_state:
        conflicts.append(
            "reversible=True conflicts with modifies_state=False; "
            "treated conservatively as a state-change signal."
        )
    if read_only and (state_change or external_effect or critical or destructive):
        conflicts.append(
            "read_only=True conflicts with higher-risk characteristics; "
            "higher-risk characteristics dominate."
        )

    if critical or destructive:
        level = RiskLevel.R4
        reason = (
            "R4 CRITICAL: a critical or destructive characteristic requires "
            "the strongest risk classification."
        )
    elif external_effect:
        level = RiskLevel.R3
        reason = (
            "R3 EXTERNAL_EFFECT: an externally visible or consequential effect "
            "requires at least R3."
        )
    elif state_change:
        if reversible:
            level = RiskLevel.R1
            reason = (
                "R1 REVERSIBLE: the action changes state, is explicitly reversible, "
                "and has no external or critical characteristic."
            )
        else:
            level = RiskLevel.R2
            reason = (
                "R2 MODIFY: the action changes persistent state without explicit "
                "reversibility and has no external or critical characteristic."
            )
    elif read_only:
        level = RiskLevel.R0
        reason = (
            "R0 READ: the action is explicitly read-only with no intended state "
            "change, external effect, or critical characteristic."
        )
    else:
        raise ValueError(
            "at least one action characteristic must be explicit: read_only, "
            "modifies_state, reversible, external_effect, critical, or destructive"
        )

    if conflicts:
        reason = f"{reason} {' '.join(conflicts)}"

    return RiskAssessment(
        level=level,
        reason=reason,
        read_only=read_only,
        modifies_state=modifies_state,
        reversible=reversible,
        external_effect=external_effect,
        critical=critical,
        destructive=destructive,
    )


__all__ = ["RiskAssessment", "RiskLevel", "assess_risk"]
