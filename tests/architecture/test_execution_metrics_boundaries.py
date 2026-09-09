"""N2.13 architecture tests: placement and boundaries of the metrics module.

``agentx.execution_metrics`` is a top-level composition module (like
``agentx.execution_episode``): it may consume canonical contracts inward
(``agentx.core``, ``agentx.cognition``) but must never depend on the authority
boundary (``agentx.kernel``), on execution machinery (``agentx.capabilities``),
or on concrete infrastructure. It must read no clock, touch no network, do no
persistence, and add no runtime dependencies.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import agentx.execution_metrics

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src" / "agentx" / "execution_metrics.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)

_ALLOWED_AGENTX_IMPORTS = frozenset(
    {
        "agentx.cognition.model_provider",
        "agentx.cognition.router",
        "agentx.core.events",
        "agentx.core.ids",
        "agentx.core.reuse_efficiency",
    }
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


def _now_call_bases() -> list[str]:
    """Return source-level descriptions of every ``.now()`` call site."""

    bases: list[str] = []
    for node in ast.walk(_TREE):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "now"
        ):
            bases.append(ast.unparse(node.func.value))
    return bases


def test_contract_is_a_top_level_composition_module() -> None:
    assert agentx.execution_metrics.__name__ == "agentx.execution_metrics"
    assert _MODULE.is_file()
    # Top-level: not inside any canonical subsystem package.
    parts = _MODULE.relative_to(_ROOT / "src").parts
    assert parts[0] == "agentx"
    assert len(parts) == 2


def test_agentx_imports_are_canonical_and_inward_only() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx.")}
    assert agentx_imports == set(_ALLOWED_AGENTX_IMPORTS)


def test_no_authority_or_outward_subsystem_dependency() -> None:
    forbidden = (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.infrastructure",
        "agentx.hive",
        "agentx.learning",
        "agentx.procedures",
    )
    assert not [
        name
        for name in _imports()
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden)
    ]


def test_no_clock_network_persistence_or_execution_calls() -> None:
    forbidden_calls = {
        "execute",
        "monotonic",
        "open",
        "perf_counter",
        "request",
        "route",
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


def test_time_values_are_never_constructed_and_clock_is_only_injected() -> None:
    # No wall-clock reads: the module never constructs a datetime and the only
    # ``.now()`` call sites are on the injected clock attribute.
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {"datetime", "utcnow"}:
                raise AssertionError(f"forbidden time construction: {ast.unparse(node)}")
            if isinstance(node.func, ast.Attribute):
                base = node.func.value
                if node.func.attr == "now" and not (
                    isinstance(base, ast.Attribute)
                    and base.attr == "_clock"
                    and isinstance(base.value, ast.Name)
                    and base.value.id == "self"
                ):
                    raise AssertionError(
                        f"forbidden clock read outside injected clock: {ast.unparse(node)}"
                    )
                if node.func.attr == "utcnow":
                    raise AssertionError("forbidden wall-clock read: utcnow")


def test_module_exposes_no_authority_or_benchmark_surface() -> None:
    public = set(agentx.execution_metrics.__all__)
    forbidden_fragments = {
        "activate",
        "benchmark",
        "execute",
        "gate",
        "grant",
        "permission",
        "persist",
        "promote",
        "route",
        "risk",
    }
    assert not [
        name for name in public if any(fragment in name.lower() for fragment in forbidden_fragments)
    ]


def test_canonical_contracts_are_imported_not_redefined() -> None:
    # The module reuses canonical types; it must not shadow/redefine them.
    defined = {
        node.name
        for node in ast.walk(_TREE)
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    canonical = {
        "ExecutionLevel",
        "ModelId",
        "ModelResponse",
        "ModelUsage",
        "TaskId",
        "ProcedureId",
        "VerificationPayload",
        "ExecutionEfficiencyEvidence",
        "ResourceBudget",
        "ResourceEnvelope",
        "Permission",
        "RiskLevel",
    }
    assert not (defined & canonical)


def test_no_runtime_dependency_added() -> None:
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []


def test_now_call_sites_match_injected_clock_only() -> None:
    bases = _now_call_bases()
    # Every now() call is on the injected clock slot; none elsewhere.
    assert all(base == "self._clock" for base in bases)
