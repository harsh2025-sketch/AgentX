"""Unit tests for N2.01 deterministic decomposition readiness."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

import pytest

from agentx.core.decomposition_readiness import (
    EXECUTION_METADATA_KEY,
    DecompositionReadinessDisposition,
    DecompositionReadinessReasonCode,
    DecompositionReadinessResult,
    DecompositionReadinessValidator,
)
from agentx.core.ids import CapabilityId, ProcedureId, TaskId
from agentx.core.task_decomposition import DecompositionNode, TaskDecomposition

_NAMESPACE = uuid5(NAMESPACE_URL, "agentx://n2.01/readiness-tests")
_NOW = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _task_id(label: str) -> TaskId:
    return TaskId.parse(str(uuid5(_NAMESPACE, f"task/{label}")))


def _capability_id(label: str = "capability") -> CapabilityId:
    return CapabilityId.parse(str(uuid5(_NAMESPACE, f"capability/{label}")))


def _procedure_id(label: str = "procedure") -> ProcedureId:
    return ProcedureId.parse(str(uuid5(_NAMESPACE, f"procedure/{label}")))


def _execution_capability(label: str = "capability") -> dict[str, object]:
    return {
        "kind": "capability",
        "capability_id": _capability_id(label).to_str(),
    }


def _execution_procedure(label: str = "procedure") -> dict[str, object]:
    return {
        "kind": "procedure",
        "procedure_id": _procedure_id(label).to_str(),
    }


def _execution_higher_level() -> dict[str, object]:
    return {"kind": "higher_level"}


def _node(
    label: str,
    *,
    parent: TaskId | None = None,
    order_index: int = 0,
    criteria: tuple[str, ...] = (),
    depends_on: tuple[TaskId, ...] = (),
    execution: object | None = None,
) -> DecompositionNode:
    metadata: dict[str, object] = {}
    if execution is not None:
        metadata[EXECUTION_METADATA_KEY] = execution
    return DecompositionNode(
        task_id=_task_id(label),
        objective=f"objective {label}",
        parent_task_id=parent,
        success_criteria=criteria,
        order_index=order_index,
        depends_on=depends_on,
        metadata=metadata,
    )


def _decomposition(nodes: tuple[DecompositionNode, ...]) -> TaskDecomposition:
    return TaskDecomposition.create(
        nodes[0].task_id,
        nodes,
        created_at=_NOW,
    )


def _codes(result: DecompositionReadinessResult) -> tuple[DecompositionReadinessReasonCode, ...]:
    return tuple(reason.code for reason in result.reasons)


def test_minimal_valid_root_only_decomposition_is_ready() -> None:
    root = _node(
        "root",
        criteria=("result is independently verifiable",),
        execution=_execution_capability(),
    )
    decomposition = _decomposition((root,))

    result = DecompositionReadinessValidator().assess(decomposition)

    assert result.disposition is DecompositionReadinessDisposition.READY
    assert result.reasons == ()


def test_valid_multi_level_decomposition_is_ready() -> None:
    root_id = _task_id("root")
    branch_id = _task_id("branch")
    nodes = (
        _node("root"),
        _node("branch", parent=root_id),
        _node(
            "leaf-a",
            parent=branch_id,
            order_index=0,
            criteria=("leaf a postcondition",),
            execution=_execution_procedure("a"),
        ),
        _node(
            "leaf-b",
            parent=branch_id,
            order_index=1,
            criteria=("leaf b postcondition",),
            execution=_execution_higher_level(),
        ),
        _node(
            "leaf-c",
            parent=root_id,
            order_index=1,
            criteria=("leaf c postcondition",),
            execution=_execution_capability("c"),
        ),
    )
    decomposition = _decomposition(nodes)

    result = DecompositionReadinessValidator().assess(decomposition)

    assert result.disposition is DecompositionReadinessDisposition.READY
    assert result.reasons == ()


def test_leaf_missing_success_criteria_is_not_ready() -> None:
    decomposition = _decomposition((_node("root", execution=_execution_higher_level()),))

    result = DecompositionReadinessValidator().assess(decomposition)

    assert result.disposition is DecompositionReadinessDisposition.NOT_READY
    assert _codes(result) == (DecompositionReadinessReasonCode.MISSING_TERMINAL_SUCCESS_CRITERIA,)
    assert result.reasons[0].task_id == decomposition.root_task_id


def test_leaf_missing_execution_requirement_is_not_ready() -> None:
    decomposition = _decomposition((_node("root", criteria=("done",)),))

    result = DecompositionReadinessValidator().assess(decomposition)

    assert _codes(result) == (
        DecompositionReadinessReasonCode.MISSING_TERMINAL_EXECUTION_REQUIREMENT,
    )


@pytest.mark.parametrize(
    "execution",
    [
        "capability",
        {},
        {"kind": "capability"},
        {"kind": "procedure"},
        {"kind": "higher_level", "capability_id": "unexpected"},
        {"kind": "capability", "procedure_id": _procedure_id().to_str()},
        {"kind": "procedure", "capability_id": _capability_id().to_str()},
        {"kind": "unknown"},
        {"kind": "capability", "capability_id": "permission=ADMIN"},
        {"kind": "procedure", "procedure_id": "not-a-uuid"},
        {
            "kind": "capability",
            "capability_id": _capability_id().to_str().upper(),
        },
    ],
)
def test_invalid_or_contradictory_terminal_execution_metadata_is_not_ready(
    execution: object,
) -> None:
    decomposition = _decomposition((_node("root", criteria=("done",), execution=execution),))

    result = DecompositionReadinessValidator().assess(decomposition)

    assert _codes(result) == (
        DecompositionReadinessReasonCode.INVALID_TERMINAL_EXECUTION_REQUIREMENT,
    )


def test_non_terminal_execution_metadata_is_invalid_terminal_structure() -> None:
    root_id = _task_id("root")
    decomposition = _decomposition(
        (
            _node("root", execution=_execution_higher_level()),
            _node(
                "leaf",
                parent=root_id,
                criteria=("done",),
                execution=_execution_higher_level(),
            ),
        )
    )

    result = DecompositionReadinessValidator().assess(decomposition)

    assert _codes(result) == (DecompositionReadinessReasonCode.NON_TERMINAL_EXECUTION_REQUIREMENT,)
    assert result.reasons[0].task_id == root_id


def test_corrupted_parent_child_relationship_fails_closed() -> None:
    root_id = _task_id("root")
    leaf = _node(
        "leaf",
        parent=root_id,
        criteria=("done",),
        execution=_execution_higher_level(),
    )
    decomposition = _decomposition((_node("root"), leaf))
    object.__setattr__(decomposition.nodes[0], "parent_task_id", leaf.task_id)

    result = DecompositionReadinessValidator().assess(decomposition)

    assert _codes(result) == (DecompositionReadinessReasonCode.INVALID_CANONICAL_STRUCTURE,)


def test_corrupted_sibling_order_fails_closed() -> None:
    root_id = _task_id("root")
    leaf = _node(
        "leaf",
        parent=root_id,
        criteria=("done",),
        execution=_execution_higher_level(),
    )
    decomposition = _decomposition((_node("root"), leaf))
    object.__setattr__(leaf, "order_index", 4)

    result = DecompositionReadinessValidator().assess(decomposition)

    assert _codes(result) == (DecompositionReadinessReasonCode.INVALID_CANONICAL_STRUCTURE,)


def test_dependency_reference_outside_decomposition_fails_closed() -> None:
    root_id = _task_id("root")
    leaf = _node(
        "leaf",
        parent=root_id,
        criteria=("done",),
        execution=_execution_higher_level(),
    )
    decomposition = _decomposition((_node("root"), leaf))
    object.__setattr__(leaf, "depends_on", (_task_id("outside"),))

    result = DecompositionReadinessValidator().assess(decomposition)

    assert _codes(result) == (DecompositionReadinessReasonCode.INVALID_CANONICAL_STRUCTURE,)


def test_structurally_unreachable_node_fails_closed() -> None:
    root_id = _task_id("root")
    leaf = _node(
        "leaf",
        parent=root_id,
        criteria=("done",),
        execution=_execution_capability(),
    )
    decomposition = _decomposition((_node("root"), leaf))
    object.__setattr__(leaf, "parent_task_id", _task_id("missing-parent"))

    result = DecompositionReadinessValidator().assess(decomposition)

    assert _codes(result) == (DecompositionReadinessReasonCode.INVALID_CANONICAL_STRUCTURE,)


def test_corrupted_required_objective_fails_closed() -> None:
    root = _node(
        "root",
        criteria=("done",),
        execution=_execution_higher_level(),
    )
    decomposition = _decomposition((root,))
    object.__setattr__(root, "objective", "")

    result = DecompositionReadinessValidator().assess(decomposition)

    assert _codes(result) == (DecompositionReadinessReasonCode.INVALID_CANONICAL_STRUCTURE,)


def test_repeated_assessment_is_deterministic_and_non_mutating() -> None:
    decomposition = _decomposition(
        (
            _node(
                "root",
                criteria=("done",),
                execution=_execution_procedure(),
            ),
        )
    )
    before = decomposition.to_dict()
    validator = DecompositionReadinessValidator()

    first = validator.assess(decomposition)
    second = validator.assess(decomposition)

    assert first == second
    assert decomposition.to_dict() == before


def test_result_is_frozen_data() -> None:
    decomposition = _decomposition(
        (_node("root", criteria=("done",), execution=_execution_higher_level()),)
    )
    result = DecompositionReadinessValidator().assess(decomposition)

    with pytest.raises(FrozenInstanceError):
        result.disposition = DecompositionReadinessDisposition.NOT_READY  # type: ignore[misc]


def test_wrong_input_type_is_programming_error() -> None:
    with pytest.raises(TypeError, match="TaskDecomposition"):
        DecompositionReadinessValidator().assess(object())  # type: ignore[arg-type]
