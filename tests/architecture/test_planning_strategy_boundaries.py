"""Architecture guardrails for the N2.06 top-level L4 planning strategy.

The planning strategy is a top-level composition module (mirroring the M1.02
capability strategy placement): it may consume the canonical A2.03 Reasoner,
A2.07 execution levels, A1.07 execution context, A6.01 decomposition
acceptance boundary, and the A2.10 strategy-result vocabulary — and nothing
else. It may not reach any capability, kernel, procedure, store, persistence,
research, hive, task-state, or model-provider-execution surface, and it must
add zero third-party runtime dependencies.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_ROOT = _SRC_ROOT / "agentx"
_STRATEGY = _AGENTX_ROOT / "planning_strategy.py"

_ALLOWED_AGENTX_IMPORTS = frozenset(
    {
        "agentx.agent_loop",
        "agentx.cognition.model_provider",
        "agentx.cognition.reasoner",
        "agentx.cognition.router",
        "agentx.core.errors",
        "agentx.core.execution",
        "agentx.core.result",
        "agentx.core.task_decomposition",
        "agentx.core.tasks",
    }
)

_ALLOWED_STDLIB_IMPORTS = frozenset(
    {
        "__future__",
        "dataclasses",
        "json",
        "typing",
    }
)

_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "subprocess",
        "ctypes",
        "socket",
        "urllib",
        "http",
        "requests",
        "httpx",
        "win32api",
        "win32gui",
        "pywinauto",
        "uiautomation",
        "selenium",
        "playwright",
    }
)

_FORBIDDEN_AGENTX_PREFIXES = (
    "agentx.hive",
    "agentx.learning",
    "agentx.infrastructure",
    "agentx.procedures",
    "agentx.models",
    "agentx.research",
    "agentx.capabilities",
    "agentx.kernel",
    "agentx.core.task_state",
    "agentx.cognition.task_manager",
    "agentx.cognition.research_",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _defined_classes(path: Path) -> set[str]:
    return {node.name for node in ast.walk(_tree(path)) if isinstance(node, ast.ClassDef)}


def _defined_functions(path: Path) -> set[str]:
    return {
        node.name
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_strategy_is_one_top_level_composition_module() -> None:
    assert _STRATEGY.is_file()
    assert _STRATEGY.parent == _AGENTX_ROOT
    assert _STRATEGY.parent.name == "agentx"
    assert _STRATEGY.parent not in (
        _AGENTX_ROOT / "capabilities",
        _AGENTX_ROOT / "cognition",
        _AGENTX_ROOT / "core",
    )


def test_strategy_imports_only_exact_canonical_contracts_and_stdlib() -> None:
    imports = _imports(_STRATEGY)
    agentx_imports = {module for module in imports if module.startswith("agentx")}
    assert agentx_imports == _ALLOWED_AGENTX_IMPORTS
    non_agentx = set(imports) - agentx_imports
    assert non_agentx <= _ALLOWED_STDLIB_IMPORTS
    for module in imports:
        root = module.split(".")[0]
        assert root not in _FORBIDDEN_IMPORT_ROOTS
        for prefix in _FORBIDDEN_AGENTX_PREFIXES:
            assert module != prefix and not module.startswith(f"{prefix}.")


def test_strategy_defines_only_its_own_contracts() -> None:
    assert _defined_classes(_STRATEGY) == {
        "PlanningStrategyLimits",
        "PlanningStrategy",
    }
    forbidden_definitions = {
        "Capability",
        "CapabilityExecutionLoop",
        "CapabilityRegistry",
        "Executor",
        "Verifier",
        "ExecutionLevelRouter",
        "ExecutionLevelEscalator",
        "LoopGuard",
        "StrategyRegistry",
        "StrategyResult",
        "Reasoner",
        "TaskManager",
        "TaskDecomposition",
        "DecompositionNode",
        "ProcedureStore",
        "PlanningStrategyBinding",
    }
    assert not (_defined_classes(_STRATEGY) & forbidden_definitions)
    # The canonical acceptance boundary is consumed, never re-implemented.
    assert "accept_model_proposal" not in _defined_functions(_STRATEGY)


def test_only_the_canonical_reasoner_is_invoked_and_exactly_once_per_plan() -> None:
    reason_attributes = [
        node
        for node in ast.walk(_tree(_STRATEGY))
        if isinstance(node, ast.Attribute) and node.attr == "reason"
    ]
    assert len(reason_attributes) == 1
    reason = reason_attributes[0]
    assert isinstance(reason.value, ast.Attribute)
    assert reason.value.attr == "_reasoner"
    assert isinstance(reason.value.value, ast.Name)
    assert reason.value.value.id == "self"

    source = _STRATEGY.read_text(encoding="utf-8")
    # No direct provider bypass, no capability/procedure execution surface.
    assert ".invoke(" not in source
    assert ".execute(" not in source
    assert ".verify(" not in source
    assert "Capability.execute(" not in source


def test_strategy_performs_no_task_transition_or_persistence() -> None:
    source = _STRATEGY.read_text(encoding="utf-8")
    for forbidden in (
        ".transition(",
        ".persist(",
        ".save(",
        ".store(",
        ".publish(",
        ".open(",
        "write_text",
        "write_bytes",
    ):
        assert forbidden not in source
    # Strict typed acceptance happens through the one canonical boundary.
    assert source.count("accept_model_proposal(") == 1
    assert source.count("json.loads(") == 1
    assert "eval(" not in source
    assert "exec(" not in source


def test_strategy_has_no_dynamic_execution_native_or_background_surface() -> None:
    source = _STRATEGY.read_text(encoding="utf-8")
    for forbidden in (
        "__import__",
        "importlib",
        "threading",
        "multiprocessing",
        "asyncio",
        "socket",
        "requests",
        "urllib",
        "http",
        "time.sleep",
    ):
        assert forbidden not in source
    for node in ast.walk(_tree(_STRATEGY)):
        assert not isinstance(node, ast.AsyncFunctionDef)


def test_strategy_respects_the_architecture_manifest_edges() -> None:
    # The manifest already forbids inbound edges to COGNITION from subsystems;
    # the planning strategy lives at the top-level namespace root like the
    # M1.02 adapter and the A2.10 loop itself, so it adds no subsystem edge.
    assert "agentx.planning_strategy" not in _architecture.SUBSYSTEMS
    assert _STRATEGY.parent.parent.name == "src"
