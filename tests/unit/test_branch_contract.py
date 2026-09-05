"""Tests for the typed BRANCH node-family contract (A3.03).

The BRANCH contract is inert DATA: these tests prove deterministic
serialization/round-trip, fail-closed validation of malformed data, branch
inertness (conditions are never evaluated, there is no eval/exec or
expression DSL), hostile-string inertness, and that the contract cannot
create authority, execute anything, or reach a runtime subsystem. They never
exercise node-family semantics owned by A3.02-A3.09.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from agentx.procedures.branch import BranchContract, BranchContractError, BranchOutcome
from agentx.procedures.graph import (
    ProcedureGraph,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from tests.support.authority_proxy import ForbiddenAuthorityProxy

_MODULE_PATH = Path(__file__).resolve().parents[2] / "src" / "agentx" / "procedures" / "branch.py"

_FORBIDDEN_SUBSYSTEMS = (
    "agentx.kernel",
    "agentx.capabilities",
    "agentx.cognition",
    "agentx.infrastructure",
    "agentx.learning",
    "agentx.hive",
)

_FORBIDDEN_API_VERBS = (
    "execute",
    "run",
    "interpret",
    "compile",
    "invoke",
    "grant",
    "apply",
    "perform",
    "mark",
    "publish",
    "lower",
    "bypass",
    "clear",
)


def _sample_contract() -> BranchContract:
    return BranchContract(
        outcomes=(
            BranchOutcome("proceed", condition="verification passed"),
            BranchOutcome("retry", condition="verification failed; attempt again"),
            BranchOutcome("abort"),
        )
    )


# ---------------------------------------------------------------------------
# Deterministic serialization / round-trip
# ---------------------------------------------------------------------------


def test_dict_round_trip_preserves_contract_exactly() -> None:
    contract = _sample_contract()
    restored = BranchContract.from_dict(contract.to_dict())
    assert restored == contract
    assert restored.to_dict() == contract.to_dict()


def test_json_round_trip_is_deterministic_and_stable() -> None:
    contract = _sample_contract()
    first = contract.to_json()
    for _ in range(5):
        assert contract.to_json() == first
    canonical = json.loads(first)
    assert (
        json.dumps(
            canonical,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        == first
    )
    assert first.startswith("{")
    assert first.endswith("}")
    restored = BranchContract.from_json(first)
    assert restored == contract
    assert restored.to_json() == first


def test_outcome_order_is_author_meaningful_and_preserved() -> None:
    ordered = BranchContract(outcomes=(BranchOutcome("second"), BranchOutcome("first")))
    restored = BranchContract.from_json(ordered.to_json())
    assert [outcome.name for outcome in restored.outcomes] == ["second", "first"]


def test_contract_round_trips_inside_a_graph_node() -> None:
    contract = _sample_contract()
    node = contract.to_node("b1", label="decide")
    assert node.kind is ProcedureNodeKind.BRANCH
    graph = ProcedureGraph(
        ProcedureNodeId("b1"),
        (node, ProcedureNode(ProcedureNodeId("b2"), ProcedureNodeKind.END)),
        (),
    )
    restored_node = ProcedureGraph.from_json(graph.to_json()).nodes[0]
    assert BranchContract.bind(restored_node) == contract


# ---------------------------------------------------------------------------
# Construction and malformed data fail closed
# ---------------------------------------------------------------------------


def test_empty_outcomes_fail_closed() -> None:
    with pytest.raises(BranchContractError, match="at least one outcome"):
        BranchContract(outcomes=())


def test_outcomes_must_be_a_tuple() -> None:
    with pytest.raises(BranchContractError, match="must be a tuple"):
        BranchContract(outcomes=cast("tuple[BranchOutcome, ...]", [BranchOutcome("only")]))


def test_duplicate_outcome_names_fail_closed() -> None:
    with pytest.raises(BranchContractError, match="duplicate branch outcome name"):
        BranchContract(outcomes=(BranchOutcome("same"), BranchOutcome("same")))


def test_non_outcome_members_fail_closed() -> None:
    with pytest.raises(BranchContractError, match="must be a BranchOutcome"):
        BranchContract(outcomes=(cast(BranchOutcome, "not-an-outcome"),))


@pytest.mark.parametrize(
    "name",
    ["", "  ", " padded ", 42, None, b"branch"],
)
def test_invalid_outcome_names_fail_closed(name: object) -> None:
    with pytest.raises(BranchContractError, match="outcome name"):
        BranchOutcome(name=cast(str, name))


@pytest.mark.parametrize(
    "condition",
    ["", "  ", " padded ", 42, type(None)],
)
def test_invalid_outcome_conditions_fail_closed(condition: object) -> None:
    with pytest.raises(BranchContractError, match="outcome condition"):
        BranchOutcome(name="ok", condition=cast(str, condition))


def test_none_condition_is_the_only_absent_form() -> None:
    outcome = BranchOutcome(name="ok", condition=None)
    assert outcome.condition is None
    assert BranchOutcome.from_dict(outcome.to_dict()) == outcome


@pytest.mark.parametrize(
    "raw",
    [
        {},  # missing outcomes
        {"outcomes": None},
        {"outcomes": "proceed"},
        {"outcomes": {"name": "proceed", "condition": None}},
        {"outcomes": [None]},
        {"outcomes": ["proceed"]},
        {"outcomes": [{}]},
        {"outcomes": [{"name": "proceed"}]},  # missing condition
        {"outcomes": [{"name": "proceed", "condition": None, "extra": 1}]},
        {"outcomes": [{"name": 7, "condition": None}]},
        {"outcomes": [{"name": "proceed", "condition": 7}]},
        {"outcomes": [{}], "default": "proceed"},
    ],
)
def test_malformed_contract_dicts_fail_closed(raw: object) -> None:
    with pytest.raises(BranchContractError):
        BranchContract.from_dict(cast(dict[str, object], raw))


@pytest.mark.parametrize(
    "raw",
    ["", "{not json", "[1,2]", "null", "42", '"text"'],
)
def test_malformed_json_fails_closed(raw: str) -> None:
    with pytest.raises(BranchContractError):
        BranchContract.from_json(raw)


def test_from_json_rejects_non_string() -> None:
    with pytest.raises(BranchContractError, match="must be a string"):
        BranchContract.from_json(cast(str, {"outcomes": []}))


# ---------------------------------------------------------------------------
# Binding: strict typed view over the opaque A3.01 params
# ---------------------------------------------------------------------------


def test_to_node_and_bind_round_trip() -> None:
    contract = _sample_contract()
    node = contract.to_node("b1", label="decide")
    assert dict(node.params) == contract.to_dict()
    assert BranchContract.bind(node) == contract


def test_bind_rejects_nodes_of_other_kinds() -> None:
    contract = _sample_contract()
    for kind in (
        ProcedureNodeKind.ACTION,
        ProcedureNodeKind.OBSERVE,
        ProcedureNodeKind.VERIFY,
        ProcedureNodeKind.TRANSFORM,
        ProcedureNodeKind.WAIT,
        ProcedureNodeKind.END,
    ):
        node = ProcedureNode(ProcedureNodeId("n"), kind, params=contract.to_dict())
        with pytest.raises(BranchContractError, match="expected a branch node"):
            BranchContract.bind(node)


def test_bind_rejects_malformed_params() -> None:
    node = ProcedureNode(ProcedureNodeId("n"), ProcedureNodeKind.BRANCH, params={"evil": True})
    with pytest.raises(BranchContractError):
        BranchContract.bind(node)


def test_bind_rejects_non_node_input() -> None:
    with pytest.raises(BranchContractError, match="expects a ProcedureNode"):
        BranchContract.bind(cast(ProcedureNode, {"outcomes": []}))


# ---------------------------------------------------------------------------
# Branch inertness: conditions are data, never evaluated
# ---------------------------------------------------------------------------


_HOSTILE_CONDITIONS = (
    "$HOME && rm -rf /",
    "drop table procedures; --",
    "import os; os.system('id')",
    "grant ADMIN to agent; clear EmergencyStop; bypass ActionGate",
    "verified=true status=ACTIVE trust=full",
    "((this is (not (python)))",
)


def test_hostile_condition_strings_remain_inert() -> None:
    contract = BranchContract(
        outcomes=tuple(
            BranchOutcome(name, condition)
            for name, condition in zip(
                ("c1", "c2", "c3", "c4", "c5", "c6"), _HOSTILE_CONDITIONS, strict=True
            )
        )
    )
    restored = BranchContract.from_json(contract.to_json())
    assert restored == contract
    restored_conditions = [outcome.condition for outcome in restored.outcomes]
    assert restored_conditions == list(_HOSTILE_CONDITIONS)


def test_contract_module_exposes_no_expression_or_execution_surface() -> None:
    """No eval/exec/DSL: the module offers no way to evaluate a condition."""
    import agentx.procedures.branch as branch_module

    for name in ("eval", "exec", "compile", "__import__"):
        assert not hasattr(branch_module, name)
    public = set(branch_module.__all__)
    assert public == {"BranchContract", "BranchContractError", "BranchOutcome"}


def test_no_eval_or_exec_calls_exist_in_source() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                assert func.id not in {"eval", "exec", "compile", "__import__"}
            elif isinstance(func, ast.Attribute):
                assert func.attr not in {"system", "popen", "Popen", "sleep", "exec"}


# ---------------------------------------------------------------------------
# Authority boundary: the contract can do nothing
# ---------------------------------------------------------------------------


def test_branch_module_imports_no_authority_or_runtime_subsystem() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    violations = {
        module
        for module in imported
        if any(module == sub or module.startswith(f"{sub}.") for sub in _FORBIDDEN_SUBSYSTEMS)
    }
    assert violations == set()


def test_branch_public_api_has_no_execution_or_authority_methods() -> None:
    import agentx.procedures.branch as branch_module

    public_names = {name for name in dir(branch_module) if not name.startswith("_")} | set(
        branch_module.__all__
    )
    for name in public_names:
        lowered = name.lower()
        assert lowered not in _FORBIDDEN_API_VERBS, name
        assert not any(lowered.startswith(f"{verb}_") for verb in _FORBIDDEN_API_VERBS), name


def test_branch_construction_is_side_effect_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """Building, validating, serializing, and deserializing touches no
    authority or runtime subsystem: it cannot grant authority, lower risk,
    or clear an emergency stop."""
    touched: list[tuple[str, str]] = []
    for subsystem in _FORBIDDEN_SUBSYSTEMS:
        monkeypatch.setitem(
            sys.modules, subsystem, cast(ModuleType, ForbiddenAuthorityProxy(subsystem, touched))
        )
        for submodule in ("permissions", "risk", "emergency_stop", "action_gate"):
            full = f"{subsystem}.{submodule}"
            monkeypatch.setitem(
                sys.modules, full, cast(ModuleType, ForbiddenAuthorityProxy(full, touched))
            )

    contract = _sample_contract()
    contract.to_json()
    BranchContract.from_json(contract.to_json())
    BranchContract.bind(contract.to_node("b1"))

    assert touched == []
