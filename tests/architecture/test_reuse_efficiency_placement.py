from __future__ import annotations

import ast
from pathlib import Path

from agentx.core import reuse_efficiency

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src" / "agentx" / "core" / "reuse_efficiency.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)


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


def test_contract_is_placed_in_core() -> None:
    assert reuse_efficiency.__name__ == "agentx.core.reuse_efficiency"
    assert _MODULE.is_file()


def test_agentx_imports_are_core_only() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx.")}
    assert agentx_imports == {"agentx.core.events", "agentx.core.ids"}


def test_no_outward_subsystem_or_runtime_dependency() -> None:
    forbidden = (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.learning",
        "agentx.procedures",
    )
    assert not [
        name
        for name in _imports()
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden)
    ]


def test_no_clock_network_persistence_model_or_execution_calls() -> None:
    forbidden_calls = {
        "execute",
        "monotonic",
        "now",
        "open",
        "perf_counter",
        "request",
        "sleep",
        "time",
        "urlopen",
    }
    assert _called_names().isdisjoint(forbidden_calls)
    forbidden_import_roots = {
        "http",
        "os",
        "requests",
        "socket",
        "sqlite3",
        "subprocess",
        "time",
        "urllib",
    }
    roots = {name.split(".", maxsplit=1)[0] for name in _imports()}
    assert roots.isdisjoint(forbidden_import_roots)


def test_module_exposes_no_authority_or_benchmark_surface() -> None:
    public = set(reuse_efficiency.__all__)
    forbidden_fragments = {
        "activate",
        "benchmark",
        "execute",
        "permission",
        "persist",
        "route",
        "risk",
    }
    assert not [
        name for name in public if any(fragment in name.lower() for fragment in forbidden_fragments)
    ]
    assert "compare_execution_efficiency" in public
