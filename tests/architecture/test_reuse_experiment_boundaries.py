"""Architecture tests for the N2.12 reuse-experiment harness boundary.

Pins the placement and composition decisions of ``agentx.reuse_experiment``:

* the harness is a single top-level ``agentx`` namespace-root module (like the
  M1.04 retrieval boundary) — inside no canonical subsystem, and no subsystem
  depends on it;
* it composes only the landed M8.01 contracts
  (``agentx.core.reuse_efficiency``) plus canonical ``TaskId``; it defines no
  metric, ratio, delta, disposition, or verified-success rule of its own;
* it owns no clock, network, model, persistence, or execution: measurement
  arrives exclusively through the injected ``PhaseRunner`` port, and every
  forbidden runtime surface is absent from the module source;
* the canonical M8.01 module remains the measurement truth boundary and this
  harness adds no authority surface anywhere.
"""

from __future__ import annotations

import ast
from pathlib import Path

import agentx.reuse_experiment as reuse_experiment
from agentx import _architecture
from agentx.core import reuse_efficiency
from agentx.core.reuse_efficiency import (
    EfficiencyMetric,
    ReuseEfficiencyDisposition,
    compare_execution_efficiency,
)
from agentx.reuse_experiment import classify_reuse_mode

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_MODULE = _AGENTX_SRC / "reuse_experiment.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)

_CANONICAL_MODULE = _AGENTX_SRC / "core" / "reuse_efficiency.py"
_DOCS = _REPO_ROOT / "docs" / "reuse_experiment.md"

#: Every subsystem except ``core``: the harness composes core contracts only
#: and must not depend on any outer subsystem.
_FORBIDDEN_SUBSYSTEMS = tuple(
    package for package in _architecture.SUBSYSTEMS if package != _architecture.CORE
)


def _imports() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def _called_names() -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            result.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            result.add(node.func.attr)
    return result


def _defined_class_names() -> set[str]:
    return {node.name for node in ast.walk(_TREE) if isinstance(node, ast.ClassDef)}


def _defined_function_names() -> set[str]:
    return {
        node.name
        for node in ast.walk(_TREE)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _executable_source() -> str:
    """Module source with every docstring blanked and comments already removed."""

    tree = ast.parse(_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            node.value.value = ""
    return ast.unparse(tree)


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------


def test_harness_is_placed_at_namespace_root() -> None:
    assert _MODULE.is_file()
    assert _MODULE.parent == _AGENTX_SRC
    # Not inside any canonical subsystem package.
    assert not any(
        _MODULE.is_relative_to(_AGENTX_SRC / package.split(".")[-1])
        for package in _architecture.SUBSYSTEMS
    )


def test_canonical_m801_module_stays_the_measurement_boundary() -> None:
    """The landed M8.01 contract is untouched and still owns comparison."""

    assert _CANONICAL_MODULE.is_file()
    for name in (
        "ExecutionEfficiencyEvidence",
        "compare_execution_efficiency",
        "ReuseMode",
        "TaskRelationship",
        "MetricComparison",
    ):
        assert name in reuse_efficiency.__all__
    assert callable(compare_execution_efficiency)
    assert len(list(EfficiencyMetric)) == 7
    assert len(list(ReuseEfficiencyDisposition)) == 5


# ---------------------------------------------------------------------------
# Dependency direction
# ---------------------------------------------------------------------------


def test_agentx_imports_are_core_contracts_only() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx.")}
    assert agentx_imports == {"agentx.core.ids", "agentx.core.reuse_efficiency"}


def test_no_outward_subsystem_dependency() -> None:
    violations = [
        name
        for name in _imports()
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in _FORBIDDEN_SUBSYSTEMS)
    ]
    assert violations == []


def test_no_subsystem_or_sibling_module_imports_the_harness() -> None:
    """The harness is a leaf: nothing under ``src/`` depends on it."""

    importers: list[str] = []
    for path in _AGENTX_SRC.rglob("*.py"):
        if path == _MODULE:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "agentx.reuse_experiment" or alias.name.startswith(
                        "agentx.reuse_experiment."
                    ):
                        importers.append(f"{path}: {alias.name}")
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and (
                    node.module == "agentx.reuse_experiment"
                    or node.module.startswith("agentx.reuse_experiment.")
                )
            ):
                importers.append(f"{path}: {node.module}")
    assert importers == []


def test_harness_module_imports_no_test_code() -> None:
    assert not [name for name in _imports() if name.startswith("tests")]


# ---------------------------------------------------------------------------
# No runtime surface of its own
# ---------------------------------------------------------------------------


def test_no_clock_network_persistence_model_or_execution_calls() -> None:
    forbidden_calls = {
        "connect",
        "execute",
        "mkdir",
        "monotonic",
        "now",
        "open",
        "perf_counter",
        "persist",
        "read",
        "remove",
        "request",
        "sleep",
        "time",
        "unlink",
        "urlopen",
        "write",
    }
    assert _called_names().isdisjoint(forbidden_calls)
    forbidden_import_roots = {
        "asyncio",
        "http",
        "os",
        "pathlib",
        "random",
        "requests",
        "socket",
        "sqlite3",
        "subprocess",
        "threading",
        "time",
        "urllib",
    }
    roots = {name.split(".", maxsplit=1)[0] for name in _imports()}
    assert roots.isdisjoint(forbidden_import_roots)


def test_metric_arithmetic_is_delegated_not_reimplemented() -> None:
    """No independent ratio/delta/disposition arithmetic exists in the harness."""

    executable = _executable_source()
    assert "compare_execution_efficiency" in executable
    assert "Fraction" not in executable
    assert "delta" not in executable
    for owned_name in ("MetricComparison", "EfficiencyMetric", "ReuseEfficiencyDisposition"):
        assert owned_name not in _defined_class_names()
    assert "compare_execution_efficiency" not in _defined_function_names()


def test_public_api_exposes_no_authority_or_benchmark_surface() -> None:
    forbidden_fragments = (
        "activate",
        "allow",
        "authoriz",
        "benchmark",
        "grant",
        "permission",
        "persist",
        "promote",
        "route",
        "risk",
        "stop",
    )
    assert not [
        name
        for name in reuse_experiment.__all__
        if any(fragment in name.lower() for fragment in forbidden_fragments)
    ]
    assert set(reuse_experiment.__all__) == {
        "COLD_NO_REUSE_MODES",
        "ExperimentPhase",
        "PhaseEvidenceError",
        "PhaseExecutionRequest",
        "PhaseRunner",
        "REUSE_EXPERIMENT_SCHEMA_VERSION",
        "ReuseExperimentDeserializationError",
        "ReuseExperimentError",
        "ReuseExperimentHarness",
        "ReuseExperimentResult",
        "ReuseExperimentSpec",
        "ReuseExperimentVerdict",
        "UnsupportedReuseExperimentSchemaVersionError",
        "WARM_REUSE_MODES",
        "classify_reuse_mode",
    }
    assert "classify_reuse_mode" in reuse_experiment.__all__


def test_phase_runner_port_is_the_only_execution_surface() -> None:
    """The single injected port is one method; the harness executes nothing."""

    protocol_methods = [
        item.name
        for node in ast.walk(_TREE)
        if isinstance(node, ast.ClassDef) and node.name == "PhaseRunner"
        for item in node.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert protocol_methods == ["run_phase"]
    # The harness itself owns no async, thread, or loop machinery.
    assert not {node.name for node in ast.walk(_TREE) if isinstance(node, ast.AsyncFunctionDef)}


def test_cold_warm_classification_is_a_closed_partition() -> None:
    from agentx.core.reuse_efficiency import ReuseMode
    from agentx.reuse_experiment import (
        COLD_NO_REUSE_MODES,
        WARM_REUSE_MODES,
        ExperimentPhase,
    )

    assert set(ReuseMode) == WARM_REUSE_MODES | COLD_NO_REUSE_MODES
    assert not WARM_REUSE_MODES & COLD_NO_REUSE_MODES
    for mode in ReuseMode:
        assert classify_reuse_mode(mode) in set(ExperimentPhase)


# ---------------------------------------------------------------------------
# Documentation contract
# ---------------------------------------------------------------------------


def test_documentation_exists_and_states_the_required_sections() -> None:
    text = _DOCS.read_text(encoding="utf-8")
    for required in (
        "cold",
        "warm",
        "verified",
        "M8.01",
        "deterministic",
        "evidence only",
        "no authority",
    ):
        assert required.lower() in text.lower()
    assert "ExecutionEfficiencyEvidence" in text
    assert "compare_execution_efficiency" in text
