"""Architecture guardrails for the A2.04 Executor boundary.

The Executor is a *boundary*, not an authority. These static checks prove the
properties that runtime tests cannot: that the module exists in exactly one
place, imports only canonical contracts, reaches no cognition/routing/
infrastructure subsystem, duplicates no governed mechanism, and adds no
dependency.

Import-level rules are architecture guardrails, not security enforcement;
authority remains owned by the Trusted Kernel.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_EXECUTOR_MODULE = _AGENTX_SRC / "capabilities" / "executor.py"
_RUNTIME_MODULE = _AGENTX_SRC / "capabilities" / "runtime.py"


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _defined_classes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}


def _defined_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


# ---------------------------------------------------------------------------
# 28. Architecture / import boundaries.
# ---------------------------------------------------------------------------


def test_executor_is_a_single_canonical_module() -> None:
    """The Executor exists in exactly one place with one entry type."""
    assert _EXECUTOR_MODULE.is_file()

    definitions = [
        path for path in _AGENTX_SRC.rglob("*.py") if "Executor" in _defined_classes(path)
    ]
    assert definitions == [_EXECUTOR_MODULE]


def test_executor_stays_inside_the_capabilities_boundary() -> None:
    """A2.04 adds no top-level package and widens no manifest edge."""
    assert _EXECUTOR_MODULE.is_relative_to(_AGENTX_SRC / "capabilities")
    assert _architecture.SUBSYSTEMS == (
        _architecture.CORE,
        _architecture.KERNEL,
        _architecture.CAPABILITIES,
        _architecture.HIVE,
        _architecture.PROCEDURES,
        _architecture.COGNITION,
        _architecture.LEARNING,
        _architecture.INFRASTRUCTURE,
    )
    for source, target in _architecture.ALLOWED_ARCHITECTURE_EDGES:
        assert source != _architecture.CAPABILITIES or target in (
            _architecture.CORE,
            _architecture.KERNEL,
        )


def test_executor_imports_only_canonical_contracts() -> None:
    """Every agentx import of the Executor is a canonical contract."""
    agentx_imports = {
        module for module in _imported_modules(_EXECUTOR_MODULE) if module.startswith("agentx")
    }
    allowed_siblings = {"agentx.capabilities.abi", "agentx.capabilities.runtime"}
    for module in agentx_imports:
        assert (
            module in allowed_siblings
            or module.startswith("agentx.core.")
            or module.startswith("agentx.kernel.")
        ), f"executor imports non-canonical module {module}"

    for forbidden in (
        "agentx.infrastructure",
        "agentx.cognition",
        "agentx.hive",
        "agentx.procedures",
        "agentx.learning",
    ):
        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.") for module in agentx_imports
        ), f"executor must not import {forbidden}"


def test_executor_imports_the_canonical_a110_execution_path() -> None:
    """Reuse is structural: the Executor depends on the A1.10 loop."""
    assert "agentx.capabilities.runtime" in _imported_modules(_EXECUTOR_MODULE)
    assert "CapabilityExecutionLoop" in _defined_classes(_RUNTIME_MODULE)


# ---------------------------------------------------------------------------
# 18. No Reasoner / no cognition of any kind.
# ---------------------------------------------------------------------------


def test_executor_imports_no_reasoner_or_model_symbol() -> None:
    """A2.04 is deterministic delegation: zero cognition dependency."""
    imported = _imported_modules(_EXECUTOR_MODULE)
    assert not any(module.startswith("agentx.cognition") for module in imported)

    source = _EXECUTOR_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    identifiers = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    for forbidden in (
        "Reasoner",
        "ModelProvider",
        "ModelRole",
        "ModelRequest",
        "ModelResponse",
        "LLM",
        "Hive",
    ):
        assert forbidden not in identifiers, f"executor must not reference {forbidden}"


# ---------------------------------------------------------------------------
# Reuse: no duplicated governed mechanism, no routing, no retry.
# ---------------------------------------------------------------------------


def test_executor_duplicates_no_governed_mechanism() -> None:
    """No second gate, permission engine, risk engine, budget, or stop."""
    executor_classes = _defined_classes(_EXECUTOR_MODULE)
    for forbidden in (
        "ActionGate",
        "PermissionEngine",
        "AuthorityContext",
        "RiskAssessment",
        "ResourceBudget",
        "ResourceEnvelope",
        "EmergencyStop",
        "CapabilityRegistry",
        "CapabilityExecutionLoop",
        "VerificationResult",
        "ClosedLoopOutcome",
        "LoopOutcome",
        "Event",
        "SecurityAuditRecord",
    ):
        assert forbidden not in executor_classes, f"{forbidden} must not be redefined by A2.04"


def test_executor_touches_no_authority_or_evidence_module_directly() -> None:
    """Governed mechanisms are reached only through the canonical A1.10 loop."""
    imported = set(_imported_modules(_EXECUTOR_MODULE))
    forbidden = {
        "agentx.kernel.action_gate",
        "agentx.kernel.permissions",
        "agentx.kernel.resource_budget",
        "agentx.kernel.emergency_stop",
        "agentx.kernel.risk",
        "agentx.kernel.audit",
        "agentx.capabilities.registry",
        "agentx.core.task_state",
        "agentx.core.events",
    }
    assert forbidden.isdisjoint(imported)


def test_executor_implements_no_verification_or_transition() -> None:
    """It cannot mark success: no verdict construction, no Task transition."""
    source = _EXECUTOR_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for forbidden in (
        "VerificationResult",
        "try_transition_task",
        "transition_task",
        "ClosedLoopOutcome",
        "check_and_consume",
        "evaluate",
        "publish",
    ):
        assert forbidden not in called, f"executor must not call {forbidden}"


def test_executor_declares_no_routing_retry_or_loop_vocabulary() -> None:
    """No router, no fallback, no escalation, no anti-loop, no agent loop."""
    names = _defined_classes(_EXECUTOR_MODULE) | _defined_functions(_EXECUTOR_MODULE)
    # ``execution_loop`` is the accessor for the *canonical A1.10 loop* the
    # Executor delegates to, so it is exempt from the "loop" prohibition.
    names.discard("execution_loop")
    assert not any("loop" in name.lower() for name in names)
    lowered = {name.lower() for name in names}
    for forbidden in (
        "router",
        "route",
        "retry",
        "fallback",
        "escalate",
        "escalation",
        "taskmanager",
        "verifier",
        "plan",
        "planner",
        "strategy",
        "select",
    ):
        assert not any(forbidden in name for name in lowered), (
            f"A2.04 must not define {forbidden!r} behaviour"
        )

    tree = ast.parse(_EXECUTOR_MODULE.read_text(encoding="utf-8"))
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.While | ast.For)], (
        "the executor performs no iteration: one request, one delegation"
    )
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Try)], (
        "the executor swallows nothing: no try/except around the governed path"
    )


def test_executor_performs_no_dynamic_loading_or_io() -> None:
    """No plugin discovery, dynamic import, subprocess, socket, or storage."""
    imported = _imported_modules(_EXECUTOR_MODULE)
    assert {
        "importlib",
        "pkgutil",
        "subprocess",
        "socket",
        "urllib",
        "sqlite3",
        "time",
        "random",
    }.isdisjoint(imported)

    tree = ast.parse(_EXECUTOR_MODULE.read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"eval", "exec", "compile", "__import__", "open", "print"}.isdisjoint(called)


# ---------------------------------------------------------------------------
# 29. Zero new dependencies.
# ---------------------------------------------------------------------------


def test_no_new_runtime_dependencies_for_a204() -> None:
    """The runtime package keeps its zero third-party dependency contract."""
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []
    assert pyproject["project"]["optional-dependencies"]["dev"] == [
        "pytest>=8.0",
        "ruff>=0.5",
        "mypy>=1.10",
    ]


def test_executor_uses_only_the_standard_library_externally() -> None:
    """Non-agentx imports are standard library only."""
    stdlib_allowed = {"__future__", "dataclasses", "typing"}
    for module in _imported_modules(_EXECUTOR_MODULE):
        if module.startswith("agentx"):
            continue
        assert module.split(".")[0] in stdlib_allowed, f"unexpected external import {module}"
