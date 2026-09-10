"""Architecture proof for the pure core N2.01 readiness validator."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

from agentx.core.decomposition_readiness import (
    DecompositionReadinessReason,
    DecompositionReadinessResult,
    DecompositionReadinessValidator,
    ReadinessPathKind,
)
from agentx.core.task_decomposition import DecompositionNode, TaskDecomposition

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src" / "agentx" / "core" / "decomposition_readiness.py"


def _tree() -> ast.Module:
    return ast.parse(_MODULE.read_text(encoding="utf-8"))


def _imports() -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            assert node.module is not None
            result.add(node.module)
    return result


def _calls() -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                result.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                result.add(node.func.attr)
    return result


def test_readiness_contract_lives_in_core_and_reuses_decomposition_types() -> None:
    assert _MODULE.is_file()
    assert DecompositionReadinessValidator.__module__ == "agentx.core.decomposition_readiness"
    assert DecompositionReadinessResult.__module__ == "agentx.core.decomposition_readiness"
    assert TaskDecomposition.__module__ == "agentx.core.task_decomposition"
    assert DecompositionNode.__module__ == "agentx.core.task_decomposition"

    declarations = {node.name for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)}
    assert declarations.isdisjoint(
        {
            "TaskDecomposition",
            "DecompositionNode",
            "Task",
            "TaskId",
            "DecompositionId",
            "CapabilityId",
            "ProcedureId",
            "ExecutionLevel",
            "Permission",
            "RiskLevel",
            "AuthorityContext",
            "ResourceBudget",
            "EmergencyStop",
            "VerificationResult",
            "ProcedureRecord",
            "Capability",
        }
    )


def test_imports_are_stdlib_or_canonical_core_only() -> None:
    imports = _imports()
    assert "agentx.core.ids" in imports
    assert "agentx.core.task_decomposition" in imports
    for name in imports:
        if name.startswith("agentx"):
            assert name.startswith("agentx.core."), name
        else:
            assert name.split(".")[0] in sys.stdlib_module_names, name
    assert not imports & {"agentx._architecture", "agentx.agent_loop"}


def test_import_does_not_load_outward_subsystems() -> None:
    script = """
import json
import sys
import agentx.core.decomposition_readiness
outward = ('agentx.hive', 'agentx.cognition', 'agentx.kernel',
           'agentx.capabilities', 'agentx.learning', 'agentx.procedures',
           'agentx.infrastructure', 'agentx.agent_loop')
print(json.dumps(sorted(name for name in sys.modules
                       if any(name == prefix or name.startswith(prefix + '.')
                              for prefix in outward))))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(completed.stdout) == []


def test_no_execution_routing_model_research_storage_or_authority_calls() -> None:
    assert _calls().isdisjoint(
        {
            "execute",
            "verify",
            "attempt",
            "route",
            "reason",
            "invoke",
            "research",
            "search",
            "retrieve",
            "query",
            "grant",
            "permit",
            "evaluate",
            "consume",
            "reset",
            "transition",
            "activate",
            "promote",
            "publish",
            "emit",
            "connect",
            "open",
            "write_text",
            "write_bytes",
            "dump",
            "dumps",
            "load",
            "loads",
            "eval",
            "exec",
            "compile",
            "__import__",
        }
    )


def test_no_side_effect_or_provider_dependency_roots() -> None:
    roots = {name.split(".")[0] for name in _imports()}
    assert roots.isdisjoint(
        {
            "os",
            "io",
            "pathlib",
            "socket",
            "sqlite3",
            "subprocess",
            "http",
            "urllib",
            "requests",
            "httpx",
            "aiohttp",
            "importlib",
            "ctypes",
            "asyncio",
            "multiprocessing",
            "openai",
            "anthropic",
            "transformers",
            "langchain",
        }
    )


def test_result_surface_contains_no_authority_execution_or_success_claim() -> None:
    assert {field.name for field in fields(DecompositionReadinessResult)} == {
        "disposition",
        "reasons",
    }
    assert {field.name for field in fields(DecompositionReadinessReason)} == {
        "code",
        "task_id",
    }
    assert DecompositionReadinessResult.__dict__["__dataclass_params__"].frozen
    assert DecompositionReadinessReason.__dict__["__dataclass_params__"].frozen
    forbidden = {
        "permission",
        "authority",
        "risk",
        "risk_level",
        "budget",
        "approved",
        "verified",
        "passed",
        "success",
        "succeeded",
        "execution_level",
        "capability_available",
        "procedure_verified",
    }
    assert forbidden.isdisjoint(field.name for field in fields(DecompositionReadinessResult))
    assert forbidden.isdisjoint(field.name for field in fields(DecompositionReadinessReason))


def test_readiness_path_kind_is_not_execution_level_routing() -> None:
    assert tuple(kind.value for kind in ReadinessPathKind) == (
        "capability",
        "procedure",
        "higher_level",
    )
    tree = _tree()
    assert not any(
        isinstance(node, ast.Name) and node.id == "ExecutionLevel" for node in ast.walk(tree)
    )
    source = _MODULE.read_text(encoding="utf-8")
    assert "L0_CACHE" not in source
    assert "L1_DIRECT" not in source
    assert "L2_COMPILED" not in source
    assert "L3_GUIDED" not in source
    assert "L4_PLANNED" not in source
    assert "L5_EXPLORATORY" not in source
