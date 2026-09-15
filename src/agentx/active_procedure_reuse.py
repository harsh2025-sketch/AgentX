"""Restart-safe ACTIVE procedure reuse selection (AX-172).

This composition boundary connects durable ACTIVE-only retrieval to the
canonical M4.04 applicability matcher / reuse selector. It performs no
promotion, execution, routing, model call, or authority mutation.

Capability bindings are explicit caller-supplied evidence keyed by exact
``(ProcedureId, revision)``. Missing evidence stays missing; it is never
inferred from procedure payload text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from agentx.core.ids import ProcedureId
from agentx.core.procedure_matching import (
    CapabilityRequirement,
    ProcedureCandidate,
    ProcedureRequirement,
)
from agentx.infrastructure.active_procedure_reader import (
    DEFAULT_ACTIVE_PROCEDURE_LIMIT,
    ActiveProcedureReader,
)
from agentx.procedure_reuse_selector import (
    ProcedureReuseSelectionResult,
    ProcedureReuseSelector,
)

__all__ = [
    "ActiveProcedureReuse",
    "ProcedureCapabilityBindings",
]

type ProcedureCapabilityBindings = Mapping[tuple[ProcedureId, int], CapabilityRequirement]


def _freeze_bindings(bindings: ProcedureCapabilityBindings) -> ProcedureCapabilityBindings:
    if not isinstance(bindings, Mapping):
        raise TypeError("capability_bindings must be a mapping")
    copied: dict[tuple[ProcedureId, int], CapabilityRequirement] = {}
    for key, value in bindings.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise TypeError("capability binding keys must be (ProcedureId, revision) tuples")
        procedure_id, revision = key
        if not isinstance(procedure_id, ProcedureId):
            raise TypeError("capability binding procedure identity must be a ProcedureId")
        if type(revision) is not int or revision < 1:
            raise ValueError("capability binding revision must be a positive int")
        if not isinstance(value, CapabilityRequirement):
            raise TypeError("capability binding values must be CapabilityRequirement values")
        copied[(procedure_id, revision)] = value
    return MappingProxyType(copied)


@dataclass(frozen=True, slots=True)
class ActiveProcedureReuse:
    """Select a reusable procedure from a fresh durable ACTIVE snapshot."""

    reader: ActiveProcedureReader
    capability_bindings: ProcedureCapabilityBindings

    def __post_init__(self) -> None:
        if not isinstance(self.reader, ActiveProcedureReader):
            raise TypeError("reader must be an ActiveProcedureReader")
        object.__setattr__(
            self,
            "capability_bindings",
            _freeze_bindings(self.capability_bindings),
        )

    def select(
        self,
        requirement: ProcedureRequirement,
        *,
        limit: int = DEFAULT_ACTIVE_PROCEDURE_LIMIT,
    ) -> ProcedureReuseSelectionResult:
        """Retrieve current ACTIVE revisions and deterministically select one.

        A new durable read occurs on every call. No in-memory ACTIVE cache is
        trusted across process lifetime or restart boundaries.
        """
        if not isinstance(requirement, ProcedureRequirement):
            raise TypeError("requirement must be a ProcedureRequirement")
        records = self.reader.read(limit=limit)
        candidates = tuple(
            ProcedureCandidate(
                record=record,
                capability=self.capability_bindings.get((record.procedure_id, record.revision)),
            )
            for record in records
        )
        return ProcedureReuseSelector().select(candidates, requirement)
