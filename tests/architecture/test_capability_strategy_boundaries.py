"""Architecture guardrails for the M1.02 top-level capability strategy adapter."""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_ROOT = _SRC_ROOT / "agentx"
_ADAPTER = _AGENTX_ROOT / "capability_strategy.py"
_AGENT_LOOP = _AGENTX_ROOT / "agent_loop.py"
_ARCHITECTURE = _AGENTX_ROOT / "_architecture.py"

_ALLOWED_AGENTX_IMPORTS = frozenset(
    {
        "agentx.agent_loop",
        "agentx.capabilities.abi",
        "agentx.capabilities.executor",
        "agentx.cognition.router",
        "agentx.core.execution",
        "agentx.core.tasks",
    }
)

_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "subprocess",
        "ctypes",
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
    "agentx.capabilities.runtime",
    "agentx.capabilities.registry",
    "agentx.capabilities.verifier",
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


def test_adapter_is_one_top_level_composition_module() -> None:
    assert _ADAPTER.is_file()
    assert _ADAPTER.parent == _AGENTX_ROOT
    assert _ADAPTER.parent.name == "agentx"
    assert _ADAPTER.parent not in (
        _AGENTX_ROOT / "capabilities",
        _AGENTX_ROOT / "cognition",
    )


def test_adapter_imports_only_exact_canonical_composition_contracts() -> None:
    imports = _imports(_ADAPTER)
    agentx_imports = {module for module in imports if module.startswith("agentx")}
    assert agentx_imports == _ALLOWED_AGENTX_IMPORTS
    for module in imports:
        root = module.split(".")[0]
        assert root not in _FORBIDDEN_IMPORT_ROOTS
        for prefix in _FORBIDDEN_AGENTX_PREFIXES:
            assert module != prefix and not module.startswith(f"{prefix}.")


def test_adapter_defines_no_shadow_executor_router_verifier_or_loop_engine() -> None:
    assert _defined_classes(_ADAPTER) == {
        "CapabilityStrategyBindingError",
        "CapabilityStrategyBinding",
        "GovernedCapabilityStrategy",
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
    }
    assert not (_defined_classes(_ADAPTER) & forbidden_definitions)


def test_only_executor_execute_is_called_and_no_capability_execute_bypass_exists() -> None:
    execute_attributes = [
        node
        for node in ast.walk(_tree(_ADAPTER))
        if isinstance(node, ast.Attribute) and node.attr == "execute"
    ]
    assert len(execute_attributes) == 1
    execute = execute_attributes[0]
    assert isinstance(execute.value, ast.Attribute)
    assert execute.value.attr == "_executor"
    assert isinstance(execute.value.value, ast.Name)
    assert execute.value.value.id == "self"

    source = _ADAPTER.read_text(encoding="utf-8")
    assert "Capability.execute(" not in source
    assert ".verify(" not in source


def test_adapter_has_no_dynamic_execution_native_or_background_surface() -> None:
    tree = _tree(_ADAPTER)
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not ({"eval", "exec", "compile", "__import__"} & called_names)
    source = _ADAPTER.read_text(encoding="utf-8")
    for marker in (
        "WinDLL",
        "windll",
        "SetWindowsHookEx",
        "SendInput",
        "subprocess",
        "Popen",
        "Thread(",
        "Process(",
        "asyncio",
    ):
        assert marker not in source


def test_adapter_contains_no_model_research_hive_persistence_or_procedure_logic() -> None:
    source = _ADAPTER.read_text(encoding="utf-8").lower()
    for forbidden in (
        "openai",
        "anthropic",
        "model_provider",
        "research_provider",
        "episode_store",
        "semantic_memory",
        "proceduregraph",
        "procedure_store",
    ):
        assert forbidden not in source


def test_cognition_to_capabilities_edge_remains_forbidden() -> None:
    assert (
        _architecture.COGNITION,
        _architecture.CAPABILITIES,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES


def test_agent_loop_does_not_import_or_depend_on_the_new_adapter() -> None:
    assert "agentx.capability_strategy" not in _imports(_AGENT_LOOP)
    source = _AGENT_LOOP.read_text(encoding="utf-8")
    assert "GovernedCapabilityStrategy" not in source
    assert "CapabilityStrategyBinding" not in source


def test_architecture_manifest_has_no_adapter_special_case_or_new_edge() -> None:
    source = _ARCHITECTURE.read_text(encoding="utf-8")
    assert "capability_strategy" not in source
    assert "(COGNITION, CAPABILITIES)" not in source


def test_adapter_has_no_hidden_retry_fallback_routing_or_escalation_functions() -> None:
    functions = _defined_functions(_ADAPTER)
    assert functions == {
        "__post_init__",
        "__init__",
        "binding",
        "executor",
        "attempt",
    }
    source = _ADAPTER.read_text(encoding="utf-8")
    assert "for " not in source
    assert "while " not in source
    assert ".route(" not in source
    assert ".decide(" not in source
    assert ".evaluate(" not in source
