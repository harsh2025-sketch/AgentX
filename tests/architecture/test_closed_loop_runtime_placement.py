"""Architecture tests for the A1.10 closed-loop runtime placement.

The first closed-loop deterministic execution path lives in exactly one
module, ``agentx.capabilities.runtime``, because ``agentx.capabilities`` is
the only canonical subsystem whose dependency edges may reach both
``agentx.core`` and ``agentx.kernel``. These guardrails keep it there and
prove the properties A1.10 must preserve:

- the loop reuses canonical contracts instead of duplicating them (no second
  ActionGate, PermissionEngine, ResourceBudget, EventBus, task state machine,
  Event envelope, Capability ABI, or Registry);
- the loop imports no model/reasoning subsystem and no infrastructure
  transport (the EventBus is injected, never imported);
- no new runtime dependency was introduced;
- the A1.10 demo capabilities stay test-only and never ship in ``src``.

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


def test_runtime_loop_is_a_single_canonical_module() -> None:
    """The closed-loop path exists in exactly one place with one entry type."""
    assert _RUNTIME_MODULE.is_file()

    definitions = [
        path
        for path in _AGENTX_SRC.rglob("*.py")
        if "CapabilityExecutionLoop" in _defined_classes(path)
    ]
    assert definitions == [_RUNTIME_MODULE]


def test_runtime_loop_stays_inside_the_capabilities_boundary() -> None:
    """The loop adds no top-level package and widens no manifest edge."""
    assert _RUNTIME_MODULE.is_relative_to(_AGENTX_SRC / "capabilities")
    assert (_architecture.CAPABILITIES, _architecture.CORE) in (
        _architecture.ALLOWED_ARCHITECTURE_EDGES
    )
    assert (_architecture.CAPABILITIES, _architecture.KERNEL) in (
        _architecture.ALLOWED_ARCHITECTURE_EDGES
    )
    # The manifest itself is untouched by A1.10: no new subsystem, no new edge.
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
    for source, _target in _architecture.ALLOWED_ARCHITECTURE_EDGES:
        assert source != _architecture.CAPABILITIES or _target in (
            _architecture.CORE,
            _architecture.KERNEL,
        )


def test_runtime_loop_imports_only_canonical_contracts() -> None:
    """Every agentx import of the loop is a canonical core/kernel contract."""
    imported = _imported_modules(_RUNTIME_MODULE)
    agentx_imports = {module for module in imported if module.startswith("agentx")}

    allowed = {
        "agentx.capabilities.abi",
        "agentx.capabilities.registry",
    }
    for module in agentx_imports:
        assert (
            module in allowed
            or module.startswith("agentx.core.")
            or module.startswith("agentx.kernel.")
        ), f"runtime loop imports non-canonical module {module}"

    # No outward subsystem may be reached at all.
    for forbidden in (
        "agentx.infrastructure",
        "agentx.cognition",
        "agentx.hive",
        "agentx.procedures",
        "agentx.learning",
    ):
        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.") for module in agentx_imports
        ), f"runtime loop must not import {forbidden}"


def test_runtime_loop_imports_no_model_or_reasoning_module() -> None:
    """A1.10 is deterministic execution: zero model dependency."""
    imported = _imported_modules(_RUNTIME_MODULE)

    assert not any(module.startswith("agentx.cognition") for module in imported)
    source = _RUNTIME_MODULE.read_text(encoding="utf-8")
    for symbol in ("ModelProvider", "ModelRequest", "ModelResponse", "Reasoner"):
        assert symbol not in source


def test_runtime_loop_imports_no_event_bus_or_persistence() -> None:
    """Evidence sinks are injected; the loop never imports transport/storage."""
    imported = _imported_modules(_RUNTIME_MODULE)

    for forbidden in ("agentx.infrastructure.event_bus", "sqlite3", "agentx.infrastructure"):
        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.") for module in imported
        ), f"runtime loop must not import {forbidden}"


def test_canonical_kernel_contracts_are_defined_exactly_once() -> None:
    """The loop reuses canonical contracts; nothing re-implements them."""
    canonical_definitions: dict[str, Path] = {
        "ActionGate": _AGENTX_SRC / "kernel" / "action_gate.py",
        "PermissionEngine": _AGENTX_SRC / "kernel" / "permissions.py",
        "AuthorityContext": _AGENTX_SRC / "kernel" / "permissions.py",
        "ResourceBudget": _AGENTX_SRC / "kernel" / "resource_budget.py",
        "ResourceEnvelope": _AGENTX_SRC / "kernel" / "resource_budget.py",
        "EmergencyStop": _AGENTX_SRC / "kernel" / "emergency_stop.py",
        "SecurityAuditRecord": _AGENTX_SRC / "kernel" / "audit.py",
        "RiskAssessment": _AGENTX_SRC / "kernel" / "risk.py",
        "EventBus": _AGENTX_SRC / "infrastructure" / "event_bus.py",
        "Event": _AGENTX_SRC / "core" / "events.py",
        "ExecutionContext": _AGENTX_SRC / "core" / "execution.py",
        "TaskStatus": _AGENTX_SRC / "core" / "tasks.py",
        "CapabilityRegistry": _AGENTX_SRC / "capabilities" / "registry.py",
        "CapabilityDescriptor": _AGENTX_SRC / "capabilities" / "abi.py",
        "ExecutionResult": _AGENTX_SRC / "capabilities" / "abi.py",
        "VerificationResult": _AGENTX_SRC / "capabilities" / "abi.py",
    }

    for name, canonical_path in canonical_definitions.items():
        owners = [path for path in _AGENTX_SRC.rglob("*.py") if name in _defined_classes(path)]
        assert owners == [canonical_path], f"{name} must be defined only in {canonical_path}"


def test_runtime_loop_declares_no_plugin_or_dynamic_loading() -> None:
    """No plugin discovery, dynamic imports, or code execution."""
    source = _RUNTIME_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = _imported_modules(_RUNTIME_MODULE)
    assert {"importlib", "pkgutil", "subprocess", "socket", "urllib"}.isdisjoint(imported)

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"eval", "exec", "compile", "__import__"}.isdisjoint(called)


def test_a110_demo_capabilities_stay_out_of_the_shipped_package() -> None:
    """The deterministic demo capabilities are test fixtures, not products."""
    for name in ("DemoNoteCapability", "HostileMetadataCapability"):
        definitions = [path for path in _AGENTX_SRC.rglob("*.py") if name in _defined_classes(path)]
        assert definitions == [], f"{name} must remain a test fixture, not runtime code"


def test_no_new_runtime_dependencies_for_a110() -> None:
    """The runtime package keeps its zero third-party dependency contract."""
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []
