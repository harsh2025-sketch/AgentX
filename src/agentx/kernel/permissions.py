"""Canonical explicit authority permissions for the AgentX Trusted Kernel.

Permissions describe authority that was explicitly granted to the current
authority context. They are not risk levels, priorities, roles, prompt
instructions, or capability availability. Permission membership is only one
input to the Action Gate; it does not by itself authorize governed execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Permission(Enum):
    """Small canonical permission vocabulary for governed operations."""

    READ = "READ"
    WRITE = "WRITE"
    EXECUTE = "EXECUTE"
    EXTERNAL_EFFECT = "EXTERNAL_EFFECT"
    DESTRUCTIVE = "DESTRUCTIVE"


@dataclass(frozen=True, slots=True)
class AuthorityContext:
    """Immutable set of permissions explicitly granted to one evaluation."""

    permissions: frozenset[Permission]

    def __post_init__(self) -> None:
        if not isinstance(self.permissions, frozenset):
            raise TypeError("permissions must be a frozenset of Permission values")
        for permission in self.permissions:
            if not isinstance(permission, Permission):
                raise TypeError("permissions must contain only Permission values")


@dataclass(frozen=True, slots=True)
class PermissionCheck:
    """Ordinary result of checking one required permission against authority."""

    required_permission: Permission
    present: bool
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.required_permission, Permission):
            raise TypeError("required_permission must be a Permission")
        if type(self.present) is not bool:
            raise TypeError("present must be bool")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")
        if not self.reason or self.reason != self.reason.strip():
            raise ValueError("reason must be non-empty and trimmed")


class PermissionEngine:
    """Deterministically check explicit permission membership only.

    This engine does not execute actions and does not replace Action Gate
    evaluation. A present permission is necessary for a governed operation but
    is not sufficient to treat that operation as authorized.
    """

    __slots__ = ()

    def check(
        self,
        required_permission: Permission,
        authority: AuthorityContext | None,
    ) -> PermissionCheck:
        if not isinstance(required_permission, Permission):
            raise TypeError("required_permission must be a Permission")
        if authority is not None and not isinstance(authority, AuthorityContext):
            raise TypeError("authority must be an AuthorityContext or None")

        if authority is None:
            return PermissionCheck(
                required_permission=required_permission,
                present=False,
                reason=(
                    f"{required_permission.value} is not permitted because no explicit "
                    "AuthorityContext was supplied."
                ),
            )

        if required_permission not in authority.permissions:
            return PermissionCheck(
                required_permission=required_permission,
                present=False,
                reason=(
                    f"{required_permission.value} is not explicitly present in the "
                    "AuthorityContext."
                ),
            )

        return PermissionCheck(
            required_permission=required_permission,
            present=True,
            reason=f"{required_permission.value} is explicitly present in the AuthorityContext.",
        )


__all__ = [
    "AuthorityContext",
    "Permission",
    "PermissionCheck",
    "PermissionEngine",
]
