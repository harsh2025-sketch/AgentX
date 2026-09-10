"""Pure structural readiness policy for canonical task decompositions (N2.01).

This module answers one narrow question only: whether an already-canonical
:class:`~agentx.core.task_decomposition.TaskDecomposition` is structurally
ready to be handed to execution orchestration.

Readiness is inert data, never authority. A ``READY`` result does not select an
execution level, resolve or invoke a capability/procedure, inspect environment
state, grant permission, lower risk, consume/enlarge budget, clear emergency
stop, mutate a Task, persist anything, or fabricate verification.

A6.01 already owns the canonical decomposition shape. N2.01 reuses that shape
unchanged and adds only terminal execution-readiness policy:

* the supplied object must still satisfy the canonical A6.01 contract;
* every terminal leaf must declare at least one success criterion;
* every terminal leaf must carry exactly one closed ``execution`` metadata
  marker describing either a canonical capability id, a canonical procedure id,
  or an explicit higher-level-resolution requirement;
* non-terminal nodes must not carry terminal ``execution`` metadata.

The ``execution`` metadata marker is structural routing *input data* only. A
capability/procedure id is not looked up and a higher-level marker does not pick
an ``ExecutionLevel``. Applicability, lifecycle, availability, authority, and
actual routing remain owned by their existing canonical subsystems.

Owner: N2.01. Belongs to ``agentx.core`` and imports only standard-library and
canonical ``agentx.core`` contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.core.ids import CapabilityId, ProcedureId, TaskId
from agentx.core.task_decomposition import (
    DecompositionNode,
    TaskDecomposition,
    TaskDecompositionError,
)

__all__ = [
    "EXECUTION_METADATA_KEY",
    "DecompositionReadinessDisposition",
    "DecompositionReadinessReason",
    "DecompositionReadinessReasonCode",
    "DecompositionReadinessResult",
    "DecompositionReadinessValidator",
    "ReadinessPathKind",
]

EXECUTION_METADATA_KEY: Final[str] = "execution"
_KIND_KEY: Final[str] = "kind"
_CAPABILITY_ID_KEY: Final[str] = "capability_id"
_PROCEDURE_ID_KEY: Final[str] = "procedure_id"


class DecompositionReadinessDisposition(StrEnum):
    """Closed structural-readiness verdict."""

    READY = "ready"
    NOT_READY = "not_ready"


class DecompositionReadinessReasonCode(StrEnum):
    """Closed deterministic reason vocabulary for ``NOT_READY`` results."""

    INVALID_CANONICAL_STRUCTURE = "invalid_canonical_structure"
    MISSING_TERMINAL_SUCCESS_CRITERIA = "missing_terminal_success_criteria"
    MISSING_TERMINAL_EXECUTION_REQUIREMENT = "missing_terminal_execution_requirement"
    INVALID_TERMINAL_EXECUTION_REQUIREMENT = "invalid_terminal_execution_requirement"
    NON_TERMINAL_EXECUTION_REQUIREMENT = "non_terminal_execution_requirement"


class ReadinessPathKind(StrEnum):
    """Closed structural marker for how a terminal leaf is expected to resolve.

    These values do not select an execution level and do not establish that a
    referenced capability/procedure exists, is applicable, is verified, or may
    execute.
    """

    CAPABILITY = "capability"
    PROCEDURE = "procedure"
    HIGHER_LEVEL = "higher_level"


@dataclass(frozen=True, slots=True)
class DecompositionReadinessReason:
    """One deterministic structural reason, optionally scoped to a task node."""

    code: DecompositionReadinessReasonCode
    task_id: TaskId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, DecompositionReadinessReasonCode):
            raise TypeError("code must be a DecompositionReadinessReasonCode")
        if self.task_id is not None and not isinstance(self.task_id, TaskId):
            raise TypeError("task_id must be a TaskId or None")


@dataclass(frozen=True, slots=True)
class DecompositionReadinessResult:
    """Immutable structural-readiness data result."""

    disposition: DecompositionReadinessDisposition
    reasons: tuple[DecompositionReadinessReason, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, DecompositionReadinessDisposition):
            raise TypeError("disposition must be a DecompositionReadinessDisposition")
        if not isinstance(self.reasons, tuple) or any(
            not isinstance(reason, DecompositionReadinessReason) for reason in self.reasons
        ):
            raise TypeError("reasons must be a tuple of DecompositionReadinessReason values")
        if self.disposition is DecompositionReadinessDisposition.READY and self.reasons:
            raise ValueError("READY results must not contain reasons")
        if self.disposition is DecompositionReadinessDisposition.NOT_READY and not self.reasons:
            raise ValueError("NOT_READY results must contain at least one reason")


class DecompositionReadinessValidator:
    """Stateless deterministic readiness assessor for canonical decompositions."""

    __slots__ = ()

    def assess(self, decomposition: TaskDecomposition) -> DecompositionReadinessResult:
        """Assess structural execution readiness without side effects or authority.

        ``decomposition`` is a programming-contract input and must be exactly a
        canonical :class:`TaskDecomposition`, not an arbitrary duck-typed or
        subclassed object. Corruption of an otherwise canonical instance is
        reported as ``NOT_READY`` instead of being trusted.
        """
        if type(decomposition) is not TaskDecomposition:
            raise TypeError("decomposition must be a canonical TaskDecomposition")

        if not _still_canonical(decomposition):
            return DecompositionReadinessResult(
                disposition=DecompositionReadinessDisposition.NOT_READY,
                reasons=(
                    DecompositionReadinessReason(
                        DecompositionReadinessReasonCode.INVALID_CANONICAL_STRUCTURE
                    ),
                ),
            )

        parent_ids = {
            node.parent_task_id for node in decomposition.nodes if node.parent_task_id is not None
        }
        reasons: list[DecompositionReadinessReason] = []

        for node in decomposition.nodes:
            terminal = node.task_id not in parent_ids
            has_execution_marker = EXECUTION_METADATA_KEY in node.metadata

            if not terminal:
                if has_execution_marker:
                    reasons.append(
                        DecompositionReadinessReason(
                            DecompositionReadinessReasonCode.NON_TERMINAL_EXECUTION_REQUIREMENT,
                            node.task_id,
                        )
                    )
                continue

            if not node.success_criteria:
                reasons.append(
                    DecompositionReadinessReason(
                        DecompositionReadinessReasonCode.MISSING_TERMINAL_SUCCESS_CRITERIA,
                        node.task_id,
                    )
                )

            if not has_execution_marker:
                reasons.append(
                    DecompositionReadinessReason(
                        DecompositionReadinessReasonCode.MISSING_TERMINAL_EXECUTION_REQUIREMENT,
                        node.task_id,
                    )
                )
            elif not _valid_execution_requirement(node.metadata[EXECUTION_METADATA_KEY]):
                reasons.append(
                    DecompositionReadinessReason(
                        DecompositionReadinessReasonCode.INVALID_TERMINAL_EXECUTION_REQUIREMENT,
                        node.task_id,
                    )
                )

        disposition = (
            DecompositionReadinessDisposition.READY
            if not reasons
            else DecompositionReadinessDisposition.NOT_READY
        )
        return DecompositionReadinessResult(
            disposition=disposition,
            reasons=tuple(reasons),
        )


def _still_canonical(decomposition: TaskDecomposition) -> bool:
    """Defensively re-apply A6.01 construction validation without I/O."""
    if type(decomposition.nodes) is not tuple:
        return False
    if any(type(node) is not DecompositionNode for node in decomposition.nodes):
        return False

    try:
        rebuilt_nodes = tuple(
            DecompositionNode(
                task_id=node.task_id,
                objective=node.objective,
                parent_task_id=node.parent_task_id,
                success_criteria=node.success_criteria,
                order_index=node.order_index,
                depends_on=node.depends_on,
                metadata=node.metadata,
            )
            for node in decomposition.nodes
        )
        TaskDecomposition(
            decomposition_id=decomposition.decomposition_id,
            root_task_id=decomposition.root_task_id,
            nodes=rebuilt_nodes,
            version=decomposition.version,
            created_at=decomposition.created_at,
            metadata=decomposition.metadata,
        )
    except (TaskDecompositionError, TypeError, ValueError):
        return False
    return True


def _valid_execution_requirement(value: object) -> bool:
    """Validate the closed terminal ``execution`` metadata shape.

    Accepted forms are exactly::

        {"kind": "capability", "capability_id": "<canonical UUID>"}
        {"kind": "procedure", "procedure_id": "<canonical UUID>"}
        {"kind": "higher_level"}

    IDs are checked only for canonical typed identity syntax. No registry,
    procedure store, applicability matcher, lifecycle state, or environment is
    consulted.
    """
    if not isinstance(value, Mapping):
        return False
    if set(value) == {_KIND_KEY} and value[_KIND_KEY] == ReadinessPathKind.HIGHER_LEVEL.value:
        return type(value[_KIND_KEY]) is str

    if set(value) == {_KIND_KEY, _CAPABILITY_ID_KEY}:
        return _valid_capability_requirement(value)
    if set(value) == {_KIND_KEY, _PROCEDURE_ID_KEY}:
        return _valid_procedure_requirement(value)
    return False


def _valid_capability_requirement(value: Mapping[object, object]) -> bool:
    """Validate one exact canonical capability-id requirement."""
    raw_kind = value[_KIND_KEY]
    raw_id = value[_CAPABILITY_ID_KEY]
    if (
        type(raw_kind) is not str
        or raw_kind != ReadinessPathKind.CAPABILITY.value
        or type(raw_id) is not str
    ):
        return False
    try:
        parsed = CapabilityId.parse(raw_id)
    except ValueError:
        return False
    return parsed.to_str() == raw_id


def _valid_procedure_requirement(value: Mapping[object, object]) -> bool:
    """Validate one exact canonical procedure-id requirement."""
    raw_kind = value[_KIND_KEY]
    raw_id = value[_PROCEDURE_ID_KEY]
    if (
        type(raw_kind) is not str
        or raw_kind != ReadinessPathKind.PROCEDURE.value
        or type(raw_id) is not str
    ):
        return False
    try:
        parsed = ProcedureId.parse(raw_id)
    except ValueError:
        return False
    return parsed.to_str() == raw_id
