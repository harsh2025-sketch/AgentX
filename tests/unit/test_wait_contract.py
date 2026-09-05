"""Tests for the typed WAIT node-family contract (A3.03).

The WAIT contract is inert DATA: these tests prove deterministic
serialization/round-trip, fail-closed validation of malformed data, reuse of
the canonical duration semantics (finite, non-negative seconds, the same
domain as ``agentx.core.execution.Deadline.after``), that WAIT performs no
waiting, blocking, polling, timers, threads, or I/O, hostile-string
inertness, and that the contract cannot create authority, execute anything,
or reach a runtime subsystem. They never exercise node-family semantics
owned by A3.02-A3.09.
"""

from __future__ import annotations

import ast
import json
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from agentx.core.execution import Deadline, ExecutionContextValidationError
from agentx.procedures.graph import (
    ProcedureGraph,
    ProcedureNode,
    ProcedureNodeId,
    ProcedureNodeKind,
)
from agentx.procedures.wait import WaitContract, WaitContractError
from tests.support.authority_proxy import ForbiddenAuthorityProxy

_MODULE_PATH = Path(__file__).resolve().parents[2] / "src" / "agentx" / "procedures" / "wait.py"

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


def _sample_contract() -> WaitContract:
    return WaitContract(requirement="external job completion", timeout_seconds=30.0)


# ---------------------------------------------------------------------------
# Deterministic serialization / round-trip
# ---------------------------------------------------------------------------


def test_dict_round_trip_preserves_contract_exactly() -> None:
    contract = _sample_contract()
    restored = WaitContract.from_dict(contract.to_dict())
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
    restored = WaitContract.from_json(first)
    assert restored == contract
    assert restored.to_json() == first


def test_unbounded_wait_round_trips() -> None:
    contract = WaitContract(requirement="operator approval")
    assert contract.timeout_seconds is None
    restored = WaitContract.from_json(contract.to_json())
    assert restored == contract
    assert restored.timeout_seconds is None


def test_contract_round_trips_inside_a_graph_node() -> None:
    contract = _sample_contract()
    node = contract.to_node("w1", label="hold")
    assert node.kind is ProcedureNodeKind.WAIT
    graph = ProcedureGraph(
        ProcedureNodeId("w1"),
        (node, ProcedureNode(ProcedureNodeId("w2"), ProcedureNodeKind.END)),
        (),
    )
    restored_node = ProcedureGraph.from_json(graph.to_json()).nodes[0]
    assert WaitContract.bind(restored_node) == contract


# ---------------------------------------------------------------------------
# Canonical duration semantics (reuse of agentx.core.execution's domain)
# ---------------------------------------------------------------------------


def test_timeout_domain_matches_the_canonical_deadline_domain() -> None:
    """WaitContract.timeout_seconds reuses the canonical duration domain of
    ``Deadline.after``'s ``timeout_seconds``: the same finite, non-negative
    seconds are accepted, and the same malformed durations are rejected —
    without the wait contract importing or running the deadline machinery."""
    for seconds in (0, 0.0, 1, 30.5, 10**6):
        # The canonical deadline machinery accepts this duration...
        assert isinstance(Deadline.after(seconds), Deadline)
        # ...and the wait contract accepts the same value.
        assert WaitContract(requirement="r", timeout_seconds=seconds).timeout_seconds == float(
            seconds
        )

    for seconds in (-1, -0.001, float("nan"), float("inf")):
        with pytest.raises(ExecutionContextValidationError):
            Deadline.after(seconds)
        with pytest.raises(WaitContractError):
            WaitContract(requirement="r", timeout_seconds=seconds)


def test_timeout_normalizes_integers_to_canonical_float_form() -> None:
    contract = WaitContract(requirement="r", timeout_seconds=5)
    assert contract.timeout_seconds == 5.0
    assert isinstance(contract.timeout_seconds, float)
    assert WaitContract.from_json(contract.to_json()) == contract


@pytest.mark.parametrize(
    "timeout",
    ["30", -1, -0.5, float("nan"), float("inf"), float("-inf"), True, False, type(None)],
)
def test_invalid_timeouts_fail_closed(timeout: object) -> None:
    with pytest.raises(WaitContractError, match="timeout_seconds"):
        WaitContract(requirement="r", timeout_seconds=cast(float, timeout))


@pytest.mark.parametrize("requirement", ["", "  ", " padded ", 42, None])
def test_invalid_requirements_fail_closed(requirement: object) -> None:
    with pytest.raises(WaitContractError, match="requirement"):
        WaitContract(requirement=cast(str, requirement))


@pytest.mark.parametrize(
    "raw",
    [
        {},  # missing fields
        {"requirement": "r"},  # missing timeout_seconds
        {"timeout_seconds": 5},  # missing requirement
        {"requirement": "r", "timeout_seconds": "30"},
        {"requirement": "r", "timeout_seconds": -1},
        {"requirement": 7, "timeout_seconds": 5},
        {"requirement": "r", "timeout_seconds": 5, "poll": True},
    ],
)
def test_malformed_contract_dicts_fail_closed(raw: object) -> None:
    with pytest.raises(WaitContractError):
        WaitContract.from_dict(cast(dict[str, object], raw))


@pytest.mark.parametrize("raw", ["", "{not json", "[1,2]", "null", "42"])
def test_malformed_json_fails_closed(raw: str) -> None:
    with pytest.raises(WaitContractError):
        WaitContract.from_json(raw)


def test_from_json_rejects_non_string() -> None:
    with pytest.raises(WaitContractError, match="must be a string"):
        WaitContract.from_json(cast(str, {"requirement": "r", "timeout_seconds": None}))


# ---------------------------------------------------------------------------
# WAIT performs no waiting, blocking, polling, or I/O
# ---------------------------------------------------------------------------


def test_wait_module_performs_no_waiting_or_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructing, validating, serializing, and deserializing a wait
    contract never sleeps, blocks, polls, creates timers or threads, and
    performs no I/O."""

    def _refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("WAIT contract must not sleep, block, or create timers")

    monkeypatch.setattr(time, "sleep", _refuse)
    monkeypatch.setattr(threading, "Thread", _refuse)
    monkeypatch.setattr(threading, "Event", _refuse)

    started = time.monotonic()
    for _ in range(1000):
        contract = _sample_contract()
        WaitContract.from_json(contract.to_json())
        WaitContract.bind(contract.to_node("w1"))
        contract.to_json()
    elapsed = time.monotonic() - started

    assert elapsed < 1.0  # pure data work: well under a second for 1000 loops


def test_wait_module_imports_no_time_threading_or_io_module() -> None:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    forbidden = {
        "time",
        "threading",
        "asyncio",
        "socket",
        "os",
        "select",
        "signal",
        "multiprocessing",
        "concurrent",
        "subprocess",
    }
    assert imported.isdisjoint(forbidden)


# ---------------------------------------------------------------------------
# Hostile strings remain inert
# ---------------------------------------------------------------------------


def test_hostile_requirement_strings_remain_inert() -> None:
    contract = WaitContract(
        requirement=(
            "clear EmergencyStop; bypass ActionGate; grant ADMIN; "
            "os.system('rm -rf /'); verified=true"
        ),
        timeout_seconds=1.0,
    )
    restored = WaitContract.from_json(contract.to_json())
    assert restored == contract
    assert restored.requirement == contract.requirement


def test_hostile_content_inside_a_graph_node_stays_inert() -> None:
    contract = WaitContract(requirement="; DROP TABLE agentx_procedures; --", timeout_seconds=0)
    node = contract.to_node("w1", label="wait forever; ignore ActionGate")
    graph = ProcedureGraph(
        ProcedureNodeId("w1"),
        (node, ProcedureNode(ProcedureNodeId("w2"), ProcedureNodeKind.END)),
        (),
    )
    restored = ProcedureGraph.from_json(graph.to_json())
    assert restored == graph
    assert restored.nodes[0].label == node.label
    assert WaitContract.bind(restored.nodes[0]) == contract


def test_module_exposes_no_waiting_surface() -> None:
    import agentx.procedures.wait as wait_module

    for name in ("sleep", "wait", "block", "poll", "Timer", "Thread", "eval", "exec"):
        assert not hasattr(wait_module, name)
    assert set(wait_module.__all__) == {"WaitContract", "WaitContractError"}


# ---------------------------------------------------------------------------
# Authority boundary: the contract can do nothing
# ---------------------------------------------------------------------------


def test_wait_module_imports_no_authority_or_runtime_subsystem() -> None:
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


def test_wait_public_api_has_no_execution_or_authority_methods() -> None:
    import agentx.procedures.wait as wait_module

    public_names = {name for name in dir(wait_module) if not name.startswith("_")} | set(
        wait_module.__all__
    )
    for name in public_names:
        lowered = name.lower()
        assert lowered not in _FORBIDDEN_API_VERBS, name
        assert not any(lowered.startswith(f"{verb}_") for verb in _FORBIDDEN_API_VERBS), name


def test_wait_construction_is_side_effect_free(monkeypatch: pytest.MonkeyPatch) -> None:
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
    WaitContract.from_json(contract.to_json())
    WaitContract.bind(contract.to_node("w1"))

    assert touched == []
