"""Tests for the typed TRANSFORM node-family contract (A3.03).

The TRANSFORM contract is inert DATA: these tests prove deterministic
serialization/round-trip, fail-closed validation of malformed data, that a
transform can never carry an executable callable or any other live object,
that no dynamic imports or shell surface exist, hostile-string inertness,
and that the contract cannot create authority, execute anything, or reach a
runtime subsystem. They never exercise node-family semantics owned by
A3.02-A3.09.
"""

from __future__ import annotations

import ast
import json
import math
import sys
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from agentx.procedures.graph import (
    ProcedureGraph,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.procedures.transform import (
    MAX_ARGUMENT_NESTING,
    TransformContract,
    TransformContractError,
)
from tests.support.authority_proxy import ForbiddenAuthorityProxy

_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "agentx" / "procedures" / "transform.py"
)

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

_NESTING_DEPTH: int = MAX_ARGUMENT_NESTING


def _sample_contract() -> TransformContract:
    return TransformContract(
        operation="normalize_whitespace",
        arguments={
            "collapse": True,
            "indent": 2,
            "ratio": 0.5,
            "none_value": None,
            "items": [1, 2.5, "three", False],
            "nested": {"inner": "value"},
        },
    )


# ---------------------------------------------------------------------------
# Deterministic serialization / round-trip
# ---------------------------------------------------------------------------


def test_dict_round_trip_preserves_contract_exactly() -> None:
    contract = _sample_contract()
    restored = TransformContract.from_dict(contract.to_dict())
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
    restored = TransformContract.from_json(first)
    assert restored == contract
    assert restored.to_json() == first


def test_argument_containers_are_normalized_for_exact_round_trips() -> None:
    contract = TransformContract(
        operation="shape",
        arguments={"sequence": (1, 2), "table": {"a": (3, 4)}},
    )
    restored = TransformContract.from_json(contract.to_json())
    assert restored == contract
    sequence = restored.arguments["sequence"]
    assert isinstance(sequence, list)
    assert sequence == [1, 2]
    assert isinstance(restored.arguments, type(contract.arguments))


def test_contract_round_trips_inside_a_graph_node() -> None:
    contract = _sample_contract()
    node = contract.to_node("t1", label="shape data")
    assert node.kind is ProcedureNodeKind.TRANSFORM
    graph = ProcedureGraph(
        ProcedureNodeId("t1"),
        (node, ProcedureNode(ProcedureNodeId("t2"), ProcedureNodeKind.END)),
        (),
    )
    restored_node = ProcedureGraph.from_json(graph.to_json()).nodes[0]
    assert TransformContract.bind(restored_node) == contract


# ---------------------------------------------------------------------------
# Construction and malformed data fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["", "  ", " padded ", 42, None])
def test_invalid_operations_fail_closed(operation: object) -> None:
    with pytest.raises(TransformContractError, match="operation"):
        TransformContract(operation=cast(str, operation))


def test_arguments_must_be_a_mapping() -> None:
    with pytest.raises(TransformContractError, match="arguments must be a mapping"):
        TransformContract(operation="op", arguments=cast(dict[str, object], ["nope"]))


def test_non_string_argument_keys_fail_closed() -> None:
    with pytest.raises(TransformContractError, match="argument keys must be strings"):
        TransformContract(operation="op", arguments=cast(dict[str, object], {7: "x"}))


def test_empty_arguments_are_valid() -> None:
    contract = TransformContract(operation="op")
    assert dict(contract.arguments) == {}
    assert TransformContract.from_json(contract.to_json()) == contract


@pytest.mark.parametrize(
    "raw",
    [
        {},  # missing fields
        {"operation": "op"},  # missing arguments
        {"arguments": {}},  # missing operation
        {"operation": "op", "arguments": None},
        {"operation": "op", "arguments": "not-an-object"},
        {"operation": 7, "arguments": {}},
        {"operation": " op", "arguments": {}},
        {"operation": "op", "arguments": {1: "x"}},
        {"operation": "op", "arguments": {}, "extra": True},
    ],
)
def test_malformed_contract_dicts_fail_closed(raw: object) -> None:
    with pytest.raises(TransformContractError):
        TransformContract.from_dict(cast(dict[str, object], raw))


@pytest.mark.parametrize(
    "raw",
    ["", "{not json", "[1,2]", "null", "42"],
)
def test_malformed_json_fails_closed(raw: str) -> None:
    with pytest.raises(TransformContractError):
        TransformContract.from_json(raw)


def test_from_json_rejects_non_string() -> None:
    with pytest.raises(TransformContractError, match="must be a string"):
        TransformContract.from_json(cast(str, {"operation": "op", "arguments": {}}))


# ---------------------------------------------------------------------------
# No executable content: callables and live objects are rejected
# ---------------------------------------------------------------------------


def _lambda() -> None:
    return None


@pytest.mark.parametrize(
    "value",
    [
        _lambda,
        str.upper,
        int,  # a class is callable and executable (it constructs)
        (lambda: None),
    ],
)
def test_rejects_callable_arguments(value: object) -> None:
    with pytest.raises(TransformContractError, match="never permitted"):
        TransformContract(operation="op", arguments={"fn": value})


@pytest.mark.parametrize(
    "value",
    [
        b"bytes",
        object(),
        3.14j,
        frozenset({1}),
        bytearray(b"x"),
        _lambda.__code__,
    ],
)
def test_rejects_non_inert_live_object_arguments(value: object) -> None:
    with pytest.raises(TransformContractError, match="inert JSON-compatible data"):
        TransformContract(operation="op", arguments={"value": value})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_rejects_non_finite_number_arguments(value: float) -> None:
    assert not math.isfinite(value)
    with pytest.raises(TransformContractError, match="finite number"):
        TransformContract(operation="op", arguments={"value": value})


def test_rejects_unbounded_nesting() -> None:
    pathological: object = "leaf"
    for _ in range(_NESTING_DEPTH + 2):
        pathological = [pathological]
    with pytest.raises(TransformContractError, match="nesting depth"):
        TransformContract(operation="op", arguments={"deep": pathological})


def test_allows_nesting_up_to_the_bound() -> None:
    # The top-level arguments mapping is itself one level of nesting, so the
    # deepest *allowed* value is one bracket short of the bound.
    bounded: object = "leaf"
    for _ in range(_NESTING_DEPTH - 1):
        bounded = [bounded]
    contract = TransformContract(operation="op", arguments={"deep": bounded})
    assert TransformContract.from_json(contract.to_json()) == contract


# ---------------------------------------------------------------------------
# No dynamic imports or shell surface
# ---------------------------------------------------------------------------


def test_module_imports_no_dynamic_loading_or_shell_module() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    forbidden = {
        "importlib",
        "subprocess",
        "os",
        "shutil",
        "ctypes",
        "pickle",
        "socket",
        "threading",
        "asyncio",
        "multiprocessing",
        "signal",
        "sys",
    }
    assert imported.isdisjoint(forbidden)


def test_no_dynamic_import_or_shell_calls_exist_in_source() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            assert func.id not in {"eval", "exec", "compile", "__import__", "input"}
        elif isinstance(func, ast.Attribute):
            assert func.attr not in {
                "system",
                "popen",
                "Popen",
                "call",
                "run",
                "spawn",
                "sleep",
                "exec",
                "execfile",
            }


# ---------------------------------------------------------------------------
# Hostile strings remain inert
# ---------------------------------------------------------------------------


def test_hostile_operation_and_arguments_remain_inert() -> None:
    hostile = TransformContract(
        operation="__import__('os').system('id')",
        arguments={
            "command": "rm -rf /",
            "shell": "cmd.exe /c dir",
            "import": "os",
            "permission": "ADMIN",
            "clear_emergency_stop": True,
            "verified": True,
        },
    )
    restored = TransformContract.from_json(hostile.to_json())
    assert restored == hostile
    assert restored.operation == "__import__('os').system('id')"
    assert dict(restored.arguments)["command"] == "rm -rf /"


def test_hostile_content_inside_a_graph_node_stays_inert() -> None:
    contract = TransformContract(
        operation="; drop table procedures;--",
        arguments={"sql": "DELETE FROM agentx_procedures; --"},
    )
    node = contract.to_node("t1", label="grant admin; bypass ActionGate")
    graph = ProcedureGraph(
        ProcedureNodeId("t1"),
        (node, ProcedureNode(ProcedureNodeId("t2"), ProcedureNodeKind.END)),
        (),
    )
    restored = ProcedureGraph.from_json(graph.to_json())
    assert restored == graph
    restored_node = restored.nodes[0]
    assert restored_node.label == node.label
    assert TransformContract.bind(restored_node) == contract


def test_module_exposes_no_execution_surface() -> None:
    import agentx.procedures.transform as transform_module

    for name in ("eval", "exec", "compile", "__import__", "Popen", "system"):
        assert not hasattr(transform_module, name)
    assert set(transform_module.__all__) == {
        "MAX_ARGUMENT_NESTING",
        "TransformContract",
        "TransformContractError",
    }


# ---------------------------------------------------------------------------
# Authority boundary: the contract can do nothing
# ---------------------------------------------------------------------------


def test_transform_module_imports_no_authority_or_runtime_subsystem() -> None:
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


def test_transform_public_api_has_no_execution_or_authority_methods() -> None:
    import agentx.procedures.transform as transform_module

    public_names = {name for name in dir(transform_module) if not name.startswith("_")} | set(
        transform_module.__all__
    )
    for name in public_names:
        lowered = name.lower()
        assert lowered not in _FORBIDDEN_API_VERBS, name
        assert not any(lowered.startswith(f"{verb}_") for verb in _FORBIDDEN_API_VERBS), name


def test_transform_construction_is_side_effect_free(monkeypatch: pytest.MonkeyPatch) -> None:
    """Building, validating, serializing, and deserializing touches no
    authority or runtime subsystem and imports nothing dynamically."""
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
    TransformContract.from_json(contract.to_json())
    TransformContract.bind(contract.to_node("t1"))

    assert touched == []
