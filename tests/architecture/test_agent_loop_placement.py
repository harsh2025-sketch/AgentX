"""Architecture-boundary tests for the A2.10 agent execution loop.

A2.10 is a composition layer. It must consume both the cognition-owned
decision contracts (A2.06 Task Manager, A2.07 Router, A2.08 Escalation, A2.09
anti-loop) and the capabilities-owned execution/verification contracts (A1.10
runtime outcomes, A2.05 Verifier).

The canonical manifest has no ``(COGNITION, CAPABILITIES)`` edge — indeed no
inbound edge to ``COGNITION`` at all — so no canonical subsystem may legally
import both sides. Per the Technical Lead ruling the manifest is **not**
widened; instead the loop lives at the ``agentx`` namespace root, which the
boundary checker explicitly treats as "not a subsystem", alongside the
existing top-level modules ``__main__``, ``_architecture`` and ``_version``.

These tests pin that decision and prove it did not smuggle in the forbidden
dependency by another route.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_AGENT_LOOP_PATH = _AGENTX_SRC / "agent_loop.py"
_COGNITION_PKG = _AGENTX_SRC / "cognition"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _tree(path: Path = _AGENT_LOOP_PATH) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path = _AGENT_LOOP_PATH) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def _defined_classes(path: Path) -> set[str]:
    return {node.name for node in ast.walk(_tree(path)) if isinstance(node, ast.ClassDef)}


def _called_names(path: Path = _AGENT_LOOP_PATH) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _class_definition_paths(name: str) -> list[Path]:
    return [
        path.relative_to(_SRC_ROOT)
        for path in sorted(_AGENTX_SRC.rglob("*.py"))
        if name in _defined_classes(path)
    ]


# ---------------------------------------------------------------------------
# Placement.
# ---------------------------------------------------------------------------


def test_agent_loop_is_a_single_top_level_composition_module() -> None:
    """A2.10 exists exactly once, directly under the ``agentx`` namespace root."""
    assert _AGENT_LOOP_PATH.is_file()
    assert _AGENT_LOOP_PATH.parent == _AGENTX_SRC
    assert _class_definition_paths("AgentLoop") == [Path("agentx/agent_loop.py")]


def test_agent_loop_is_not_inside_any_canonical_subsystem() -> None:
    """The composition root sits outside every subsystem it composes."""
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        assert not _AGENT_LOOP_PATH.is_relative_to(package)


def test_agent_loop_did_not_create_a_new_top_level_subsystem() -> None:
    """No new package/subsystem was introduced for A2.10."""
    assert "agentx.agent_loop" not in _architecture.SUBSYSTEMS
    assert not (_AGENTX_SRC / "agent_loop").exists()
    # The canonical subsystem list is unchanged and remains authoritative.
    assert _architecture.SUBSYSTEMS == (
        "agentx.core",
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.hive",
        "agentx.procedures",
        "agentx.cognition",
        "agentx.learning",
        "agentx.infrastructure",
    )


def test_boundary_manifest_was_not_widened() -> None:
    """The forbidden edge was NOT added; the allowed edge set is exactly canonical."""
    assert (
        _architecture.COGNITION,
        _architecture.CAPABILITIES,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES
    assert (
        _architecture.CAPABILITIES,
        _architecture.COGNITION,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES
    assert (
        frozenset(
            {
                (_architecture.KERNEL, _architecture.CORE),
                (_architecture.CAPABILITIES, _architecture.CORE),
                (_architecture.HIVE, _architecture.CORE),
                (_architecture.PROCEDURES, _architecture.CORE),
                (_architecture.COGNITION, _architecture.CORE),
                (_architecture.LEARNING, _architecture.CORE),
                (_architecture.INFRASTRUCTURE, _architecture.CORE),
                (_architecture.CAPABILITIES, _architecture.KERNEL),
                (_architecture.PROCEDURES, _architecture.KERNEL),
                (_architecture.COGNITION, _architecture.KERNEL),
            }
        )
        == _architecture.ALLOWED_ARCHITECTURE_EDGES
    )


def test_cognition_still_does_not_depend_on_capabilities() -> None:
    """The invariant the ruling protects: no cognition -> capabilities import."""
    offenders: list[tuple[str, str]] = []
    for path in sorted(_COGNITION_PKG.rglob("*.py")):
        for imported in _imports(path):
            if imported == "agentx.capabilities" or imported.startswith("agentx.capabilities."):
                offenders.append((str(path.relative_to(_SRC_ROOT)), imported))
    assert offenders == []


def test_no_subsystem_imports_the_composition_root() -> None:
    """Composition depends on the layers; the layers never depend on composition."""
    offenders: list[str] = []
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        for path in sorted(package.rglob("*.py")):
            if "agentx.agent_loop" in _imports(path):
                offenders.append(str(path.relative_to(_SRC_ROOT)))
    assert offenders == []


# ---------------------------------------------------------------------------
# Composition, not redefinition.
# ---------------------------------------------------------------------------


def test_agent_loop_imports_the_canonical_contracts_it_composes() -> None:
    """A2.10 consumes the real canonical modules rather than substitutes."""
    imports = _imports()
    for required in (
        "agentx.capabilities.runtime",
        "agentx.capabilities.verifier",
        "agentx.cognition.anti_loop",
        "agentx.cognition.escalation",
        "agentx.cognition.router",
        "agentx.cognition.task_manager",
        "agentx.core.execution",
        "agentx.core.task_state",
        "agentx.core.tasks",
    ):
        assert required in imports, f"A2.10 must compose the canonical {required}"


@pytest.mark.parametrize(
    ("contract", "owner"),
    [
        ("ClosedLoopOutcome", "agentx/capabilities/runtime.py"),
        ("LoopOutcome", "agentx/capabilities/runtime.py"),
        ("CapabilityExecutionLoop", "agentx/capabilities/runtime.py"),
        ("Verifier", "agentx/capabilities/verifier.py"),
        ("VerificationRequirement", "agentx/capabilities/verifier.py"),
        ("RequirementEvaluation", "agentx/capabilities/verifier.py"),
        ("ExecutionLevel", "agentx/cognition/router.py"),
        ("ExecutionLevelRouter", "agentx/cognition/router.py"),
        ("ExecutionLevelEscalator", "agentx/cognition/escalation.py"),
        ("LoopGuard", "agentx/cognition/anti_loop.py"),
        ("TaskManager", "agentx/cognition/task_manager.py"),
    ],
)
def test_canonical_contracts_keep_exactly_one_definition(contract: str, owner: str) -> None:
    """A2.10 duplicated no canonical contract anywhere in the tree."""
    assert _class_definition_paths(contract) == [Path(owner)]


def test_agent_loop_defines_only_orchestration_value_types() -> None:
    """A2.10 introduces composition types only, never a canonical contract."""
    assert _defined_classes(_AGENT_LOOP_PATH) == {
        "AgentLoop",
        "AttemptDisposition",
        "AttemptRecord",
        "ExecutionStrategy",
        "OrchestrationLimits",
        "OrchestrationOutcome",
        "OrchestrationRequest",
        "OrchestrationRequestError",
        "OrchestrationStatus",
        "OrchestrationStopReason",
        "StrategyRegistry",
        "StrategyResult",
    }


def test_agent_loop_defines_no_protocol_substitute_for_a_canonical_contract() -> None:
    """The only Protocol is the caller-supplied strategy port."""
    protocols = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.ClassDef)
        and any(
            (isinstance(base, ast.Name) and base.id == "Protocol")
            or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
            for base in node.bases
        )
    }
    assert protocols == {"ExecutionStrategy"}


# ---------------------------------------------------------------------------
# Authority remains untouched.
# ---------------------------------------------------------------------------


def test_agent_loop_imports_no_kernel_module() -> None:
    """A2.10 holds no authority: the Trusted Kernel is not a dependency of it."""
    assert not any(imported.startswith("agentx.kernel") for imported in _imports())


def test_agent_loop_imports_no_out_of_scope_subsystem() -> None:
    """No learning, hive, procedures, or infrastructure dependency."""
    imports = _imports()
    for forbidden in (
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.learning",
        "agentx.procedures",
    ):
        assert not any(imported.startswith(forbidden) for imported in imports)


def test_agent_loop_never_calls_capability_execute_directly() -> None:
    """Governed execution is reached only through the canonical A1.10 path."""
    called = _called_names()
    for forbidden in ("execute", "verify", "check_and_consume", "evaluate_gate", "request_stop"):
        assert forbidden not in called, f"A2.10 must not call {forbidden}() itself"


def test_trusted_kernel_authority_modules_are_untouched_by_a210() -> None:
    """A2.10 changed no kernel module: the authority boundary is unchanged."""
    for module in (
        "action_gate.py",
        "permissions.py",
        "risk.py",
        "emergency_stop.py",
        "resource_budget.py",
    ):
        path = _AGENTX_SRC / "kernel" / module
        assert path.is_file()
        assert "agent_loop" not in path.read_text(encoding="utf-8")


def test_agent_loop_adds_no_runtime_dependency() -> None:
    """A2.10 is pure composition over the standard library."""
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []


def test_agent_loop_performs_no_io_or_background_work() -> None:
    """No persistence, no network, no threads, no dynamic import."""
    imports = _imports()
    roots = {imported.split(".", maxsplit=1)[0] for imported in imports}
    assert roots.isdisjoint(
        {
            "asyncio",
            "concurrent",
            "importlib",
            "multiprocessing",
            "os",
            "pathlib",
            "random",
            "socket",
            "sqlite3",
            "subprocess",
            "threading",
            "time",
            "urllib",
        }
    )
    called = _called_names()
    assert called.isdisjoint({"eval", "exec", "compile", "__import__", "open", "print"})


def test_agent_loop_has_no_module_level_singleton() -> None:
    """No ambient state: the loop is constructed by its caller."""
    for node in _tree().body:
        if isinstance(node, ast.Assign | ast.AnnAssign):
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                assert value.func.id not in {
                    "AgentLoop",
                    "ExecutionLevelEscalator",
                    "ExecutionLevelRouter",
                    "LoopGuard",
                    "StrategyRegistry",
                    "TaskManager",
                    "Verifier",
                }
