"""Architecture guardrails for N2.02 canonical runtime strategy assembly."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTX_ROOT = _REPO_ROOT / "src" / "agentx"
_ASSEMBLY = _AGENTX_ROOT / "strategy_assembly.py"
_AGENT_LOOP = _AGENTX_ROOT / "agent_loop.py"
_ARCHITECTURE = _AGENTX_ROOT / "_architecture.py"

_ALLOWED_AGENTX_IMPORTS = frozenset(
    {
        "agentx.agent_loop",
        "agentx.cognition.router",
    }
)

_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "subprocess",
        "ctypes",
        "sqlite3",
        "requests",
        "httpx",
        "openai",
        "anthropic",
    }
)

_FORBIDDEN_AGENTX_PREFIXES = (
    "agentx.capabilities",
    "agentx.hive",
    "agentx.infrastructure",
    "agentx.kernel",
    "agentx.learning",
    "agentx.procedures",
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


def _called_attribute_names(path: Path) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def test_assembly_is_one_top_level_composition_module() -> None:
    assert _ASSEMBLY.is_file()
    assert _ASSEMBLY.parent == _AGENTX_ROOT


def test_assembly_imports_only_canonical_registry_and_execution_level_contracts() -> None:
    imports = _imports(_ASSEMBLY)
    agentx_imports = {module for module in imports if module.startswith("agentx")}

    assert agentx_imports == _ALLOWED_AGENTX_IMPORTS
    for module in imports:
        assert module.split(".")[0] not in _FORBIDDEN_IMPORT_ROOTS
        for prefix in _FORBIDDEN_AGENTX_PREFIXES:
            assert module != prefix and not module.startswith(f"{prefix}.")


def test_assembly_defines_no_shadow_registry_router_strategy_or_execution_level() -> None:
    assert _defined_classes(_ASSEMBLY) == {
        "RuntimeStrategyAssembly",
        "StrategyAssemblyError",
        "StrategyBinding",
    }
    source = _ASSEMBLY.read_text(encoding="utf-8")
    assert "class StrategyRegistry" not in source
    assert "class ExecutionStrategy" not in source
    assert "class ExecutionLevel" not in source
    assert "class ExecutionLevelRouter" not in source


def test_assembly_calls_no_execution_routing_verification_or_fallback_surface() -> None:
    called = _called_attribute_names(_ASSEMBLY)

    assert "attempt" not in called
    assert "execute" not in called
    assert "verify" not in called
    assert "route" not in called
    assert "decide" not in called
    assert "evaluate" not in called
    assert "run" not in called


def test_assembly_has_no_dynamic_native_background_or_persistence_execution() -> None:
    tree = _tree(_ASSEMBLY)
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert not ({"eval", "exec", "compile", "__import__", "open"} & called_names)
    source = _ASSEMBLY.read_text(encoding="utf-8")
    for marker in (
        "subprocess",
        "Popen",
        "Thread(",
        "Process(",
        "asyncio",
        "sqlite",
        "database",
        "save(",
        "write(",
    ):
        assert marker not in source


def test_assembly_creates_no_global_registry_or_assembly_singleton() -> None:
    tree = _tree(_ASSEMBLY)
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        if isinstance(value.func, ast.Name):
            assert value.func.id not in {"StrategyRegistry", "RuntimeStrategyAssembly"}


def test_assembly_has_no_authority_bearing_configuration_fields() -> None:
    tree = _tree(_ASSEMBLY)
    annotated_names = {
        node.target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }

    assert not (
        {
            "authority",
            "permission",
            "permissions",
            "risk",
            "budget",
            "verified",
            "verification",
            "skip_gate",
        }
        & annotated_names
    )


def test_agent_loop_remains_independent_of_strategy_assembly() -> None:
    assert "agentx.strategy_assembly" not in _imports(_AGENT_LOOP)
    source = _AGENT_LOOP.read_text(encoding="utf-8")
    assert "RuntimeStrategyAssembly" not in source
    assert "StrategyBinding" not in source


def test_architecture_manifest_has_no_strategy_assembly_special_case() -> None:
    source = _ARCHITECTURE.read_text(encoding="utf-8")

    assert "strategy_assembly" not in source
