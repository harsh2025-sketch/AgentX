"""Deterministic authority-enforcement gate for governed AgentX actions.

Every governed action must receive an explicit gate decision before any later
execution layer may treat it as authorized. This module evaluates authority only:
it never executes capabilities, callbacks, shell commands, network operations,
file writes, persistence, or event publication.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from agentx.kernel.permissions import AuthorityContext, Permission, PermissionEngine
from agentx.kernel.risk import RiskAssessment, RiskLevel


class GateDecision(Enum):
    """Canonical non-boolean outcome of Action Gate evaluation."""

    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"


@dataclass(frozen=True, slots=True)
class GateRequest:
    """Inert descriptor containing only facts needed for gate evaluation."""

    operation: str
    required_permission: Permission
    risk_assessment: RiskAssessment

    def __post_init__(self) -> None:
        if not isinstance(self.operation, str):
            raise TypeError("operation must be a string")
        if not self.operation or self.operation != self.operation.strip():
            raise ValueError("operation must be non-empty and trimmed")
        if not isinstance(self.required_permission, Permission):
            raise TypeError("required_permission must be a Permission")
        if not isinstance(self.risk_assessment, RiskAssessment):
            raise TypeError("risk_assessment must be a RiskAssessment")


@dataclass(frozen=True, slots=True)
class GateResult:
    """Immutable, inspectable authority decision; never an execution result."""

    decision: GateDecision
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.decision, GateDecision):
            raise TypeError("decision must be a GateDecision")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")
        if not self.reason or self.reason != self.reason.strip():
            raise ValueError("reason must be non-empty and trimmed")


class ActionGate:
    """Single deterministic authority-enforcement boundary for governed actions."""

    __slots__ = ()

    def evaluate(
        self,
        request: GateRequest,
        authority: AuthorityContext | None,
    ) -> GateResult:
        if not isinstance(request, GateRequest):
            raise TypeError("request must be a GateRequest")
        if authority is not None and not isinstance(authority, AuthorityContext):
            raise TypeError("authority must be an AuthorityContext or None")

        permission_check = PermissionEngine().check(
            request.required_permission,
            authority,
        )
        if not permission_check.present:
            return GateResult(
                decision=GateDecision.DENY,
                reason=f"DENY: {permission_check.reason}",
            )

        level = request.risk_assessment.level
        if level in (RiskLevel.R0, RiskLevel.R1):
            return GateResult(
                decision=GateDecision.ALLOW,
                reason=(
                    f"ALLOW: {request.required_permission.value} is explicit and "
                    f"{level.value} does not require confirmation under C1.07 policy."
                ),
            )

        if level is RiskLevel.R2:
            return GateResult(
                decision=GateDecision.ALLOW,
                reason=(
                    f"ALLOW: {request.required_permission.value} is explicit; R2 requires "
                    "explicit permission but not confirmation under C1.07 policy."
                ),
            )

        if level is RiskLevel.R3:
            return GateResult(
                decision=GateDecision.REQUIRE_CONFIRMATION,
                reason=(
                    "REQUIRE_CONFIRMATION: R3 external-effect actions remain blocked "
                    "pending a separate approval flow even with explicit permission."
                ),
            )

        if level is RiskLevel.R4:
            if authority is None or Permission.DESTRUCTIVE not in authority.permissions:
                return GateResult(
                    decision=GateDecision.DENY,
                    reason=(
                        "DENY: R4 requires explicit DESTRUCTIVE authority in addition "
                        "to the requested permission."
                    ),
                )
            return GateResult(
                decision=GateDecision.REQUIRE_CONFIRMATION,
                reason=(
                    "REQUIRE_CONFIRMATION: R4 has explicit required and DESTRUCTIVE "
                    "authority but can never be silently allowed."
                ),
            )

        raise ValueError(f"unsupported risk level: {level!r}")


__all__ = [
    "ActionGate",
    "GateDecision",
    "GateRequest",
    "GateResult",
]
