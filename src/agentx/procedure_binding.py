"""Exact typed procedure-leaf binding for governed L4 composition.

Only composition-owned BoundPlanProcedure values may be registered.  Each value
contains a GovernedCompiledProcedureStrategy whose canonical binding already
validates ACTIVE lifecycle, graph shape and M4.04 applicability.  This binder
adds exact ProcedureId resolution and refuses unknown/mismatched identities.

No procedure payload, objective, model text or success criterion is interpreted
as authority, and binding performs no execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from agentx.core.errors import AgentXError, ErrorCategory
from agentx.core.ids import ProcedureId
from agentx.core.result import Result
from agentx.core.task_decomposition import DecompositionNode
from agentx.plan_execution import BoundPlanProcedure

__all__ = ["PreparedProcedureBinder"]


def _failure(code: str, message: str) -> Result[BoundPlanProcedure, AgentXError]:
    return Result.failure(
        AgentXError(
            code=f"procedure_binding.{code}",
            message=message,
            category=ErrorCategory.VALIDATION,
        )
    )


class PreparedProcedureBinder:
    """Resolve exact ProcedureId metadata to validated prepared runtimes."""

    __slots__ = ("_procedures",)

    def __init__(self, procedures: Mapping[ProcedureId, BoundPlanProcedure]) -> None:
        if not isinstance(procedures, Mapping):
            raise TypeError("procedures must be a mapping")
        copied: dict[ProcedureId, BoundPlanProcedure] = {}
        for procedure_id, binding in procedures.items():
            if not isinstance(procedure_id, ProcedureId):
                raise TypeError("procedure keys must be ProcedureId values")
            if not isinstance(binding, BoundPlanProcedure):
                raise TypeError("procedure values must be BoundPlanProcedure values")
            if binding.procedure_id != procedure_id:
                raise ValueError("BoundPlanProcedure id must match its registry key")
            copied[procedure_id] = binding
        self._procedures = MappingProxyType(copied)

    def bind(self, node: DecompositionNode) -> Result[BoundPlanProcedure, AgentXError]:
        if not isinstance(node, DecompositionNode):
            raise TypeError("node must be a DecompositionNode")
        execution = node.metadata.get("execution")
        if not isinstance(execution, Mapping) or execution.get("kind") != "procedure":
            return _failure(
                "unsupported_execution_kind",
                "procedure binder accepts only explicit procedure leaves",
            )
        raw_id = execution.get("procedure_id")
        if not isinstance(raw_id, str):
            return _failure("invalid_procedure_id", "procedure_id must be a canonical string")
        try:
            procedure_id = ProcedureId.parse(raw_id)
        except ValueError:
            return _failure("invalid_procedure_id", "procedure_id is not canonical")
        if procedure_id.to_str() != raw_id:
            return _failure("invalid_procedure_id", "procedure_id is not canonical")
        binding = self._procedures.get(procedure_id)
        if binding is None:
            return _failure(
                "unavailable",
                "procedure is not present in the trusted prepared ACTIVE procedure registry",
            )
        return Result.success(binding)
