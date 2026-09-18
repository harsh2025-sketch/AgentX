"""Explicit typed application binding for governed L4 plan leaves.

The planner may name only canonical CapabilityId values in decomposition
metadata.  This binder maps those ids to composition-owned BoundPlanAction
objects.  It never parses an objective, model text, success criterion, or
metadata value into executable parameters, and it never executes anything.

A plan can therefore select only among actions the application explicitly
pre-bound.  Unknown ids, higher-level placeholders, malformed metadata and
procedure leaves fail closed.  Procedure binding is owned by the separate
procedure binder in plan_execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.ids import CapabilityId
from agentx.core.result import Result
from agentx.core.task_decomposition import DecompositionNode
from agentx.plan_execution import BoundPlanAction

__all__ = ["ApplicationActionBinder"]


def _failure(code: str, message: str) -> Result[BoundPlanAction, AgentXError]:
    return Result.failure(
        AgentXError(
            code=f"application_binding.{code}",
            message=message,
            category=ErrorCategory.VALIDATION,
        )
    )


class ApplicationActionBinder:
    """Resolve exact typed capability ids to trusted pre-bound actions."""

    __slots__ = ("_actions",)

    def __init__(self, actions: Mapping[CapabilityId, BoundPlanAction]) -> None:
        if not isinstance(actions, Mapping):
            raise TypeError("actions must be a mapping")
        copied: dict[CapabilityId, BoundPlanAction] = {}
        for capability_id, action in actions.items():
            if not isinstance(capability_id, CapabilityId):
                raise TypeError("action keys must be CapabilityId values")
            if not isinstance(action, BoundPlanAction):
                raise TypeError("action values must be BoundPlanAction values")
            if action.capability_id != capability_id:
                raise ValueError("BoundPlanAction capability_id must match its application key")
            copied[capability_id] = action
        self._actions = MappingProxyType(copied)

    def bind(self, node: DecompositionNode) -> Result[BoundPlanAction, AgentXError]:
        if not isinstance(node, DecompositionNode):
            raise TypeError("node must be a DecompositionNode")
        execution = node.metadata.get("execution")
        if not isinstance(execution, Mapping):
            return _failure("invalid_execution", "leaf has no typed execution descriptor")
        if execution.get("kind") != "capability":
            return _failure(
                "unsupported_execution_kind",
                "application action binder accepts only explicit capability leaves",
            )
        raw_id = execution.get("capability_id")
        if not isinstance(raw_id, str):
            return _failure("invalid_capability_id", "capability_id must be a canonical string")
        try:
            capability_id = CapabilityId.parse(raw_id)
        except ValueError:
            return _failure("invalid_capability_id", "capability_id is not canonical")
        if capability_id.to_str() != raw_id:
            return _failure("invalid_capability_id", "capability_id is not canonical")
        action = self._actions.get(capability_id)
        if action is None:
            return _failure(
                "unsupported_action",
                "no trusted application binding exists for the requested capability id",
            )
        return Result.success(action)
