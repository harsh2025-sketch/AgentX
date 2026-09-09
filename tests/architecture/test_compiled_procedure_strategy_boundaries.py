"""Architecture guardrails for the N2.04 top-level L2 procedure adapter."""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTX_ROOT = _REPO_ROOT / "src" / "agentx"
_ADAPTER = _AGENTX_ROOT / "compiled_procedure_strategy.py"
_AGENT_LOOP = _AGENTX_ROOT / "agent_loop.py"
_ARCHITECTURE = _AGENTX_ROOT / "_architecture.py"
_PROCEDURES = _AGENTX_ROOT / "procedures"

_FORBIDDEN_IMPORTS = (
    "agentx.capabilities.verifier",
    "agentx.capabilities.registry",
    "agentx.kernel.action_gate",
    "agentx.kernel.permissions",
    "agentx.kernel.resource_budget",
    "agentx.kernel.risk",
    "agentx.kernel.emergency_stop",
    "agentx.cognition.escalation",
    "agentx.cognition.router.ExecutionLevelRouter",
    "agentx.models",
    "agentx.research",
    "agentx.hive",
    "agentx.learning",
    "agentx.infrastructure",
)

_FORBIDDEN_EXTERNAL_ROOTS = frozenset(
    {
        "subprocess",
        "ctypes",
        "win32api",
        "win32gui",
        "pywinauto",
        "uiautomation",
        "selenium",
        "playwright",
        "openai",
        "anthropic",
    }
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


def _attribute_calls(path: Path, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == name
    ]


def test_adapter_is_top_level_composition_not_procedure_or_capability_subsystem() -> None:
    assert _ADAPTER.is_file()
    assert _ADAPTER.parent == _AGENTX_ROOT
    assert _ADAPTER.parent != _PROCEDURES
    assert _ADAPTER.parent != _AGENTX_ROOT / "capabilities"


def test_adapter_uses_canonical_interpreter_and_executor_without_shadow_engines() -> None:
    classes = _defined_classes(_ADAPTER)
    assert classes == {
        "CompiledProcedureStrategyBindingError",
        "CompiledProcedureStrategyBinding",
        "CompiledProcedureAttempt",
        "GovernedCompiledProcedureStrategy",
    }
    assert not (
        classes
        & {
            "ProcedureInterpreter",
            "Executor",
            "Verifier",
            "ActionGate",
            "CapabilityExecutionLoop",
            "CapabilityRegistry",
            "StrategyResult",
            "ExecutionLevelRouter",
        }
    )

    imports = set(_imports(_ADAPTER))
    assert "agentx.procedures.interpreter" in imports
    assert "agentx.capabilities.executor" in imports
    assert "agentx.core.procedure_execution" in imports
    assert "agentx.core.procedure_matching" in imports


def test_only_governed_executor_execute_is_called_for_capability_work() -> None:
    calls = _attribute_calls(_ADAPTER, "execute")
    assert len(calls) == 1
    call = calls[0]
    assert isinstance(call.func, ast.Attribute)
    owner = call.func.value
    assert isinstance(owner, ast.Attribute)
    assert owner.attr == "_executor"
    assert isinstance(owner.value, ast.Name)
    assert owner.value.id == "self"

    source = _ADAPTER.read_text(encoding="utf-8")
    assert "Capability.execute(" not in source
    assert "CapabilityExecutionLoop(" not in source
    assert ".verify(" not in source
    assert "Verifier(" not in source
    assert "ActionGate(" not in source


def test_adapter_cannot_construct_authority_gate_risk_budget_or_stop_objects() -> None:
    imports = _imports(_ADAPTER)
    for module in imports:
        assert not any(
            module == item or module.startswith(f"{item}.") for item in _FORBIDDEN_IMPORTS
        )
        assert module.split(".")[0] not in _FORBIDDEN_EXTERNAL_ROOTS

    source = _ADAPTER.read_text(encoding="utf-8")
    for forbidden in (
        "AuthorityContext(",
        "Permission(",
        "ActionGate(",
        "RiskLevel(",
        "ResourceBudget(",
        "ResourceEnvelope(",
        "EmergencyStop(",
    ):
        assert forbidden not in source


def test_procedure_subsystem_still_has_no_capability_dependency() -> None:
    offenders: list[str] = []
    for path in _PROCEDURES.rglob("*.py"):
        if any(module.startswith("agentx.capabilities") for module in _imports(path)):
            offenders.append(path.relative_to(_REPO_ROOT).as_posix())
    assert offenders == []


def test_architecture_manifest_has_no_procedure_to_capability_shortcut() -> None:
    source = _ARCHITECTURE.read_text(encoding="utf-8")
    assert "compiled_procedure_strategy" not in source
    assert "(PROCEDURES, CAPABILITIES)" not in source
    assert (_architecture.PROCEDURES, _architecture.CAPABILITIES) not in (
        _architecture.ALLOWED_ARCHITECTURE_EDGES
    )


def test_agent_loop_remains_independent_of_concrete_l2_adapter() -> None:
    imports = _imports(_AGENT_LOOP)
    assert "agentx.compiled_procedure_strategy" not in imports
    source = _AGENT_LOOP.read_text(encoding="utf-8")
    assert "GovernedCompiledProcedureStrategy" not in source
    assert "CompiledProcedureStrategyBinding" not in source


def test_adapter_has_no_hidden_fallback_escalation_model_or_research_path() -> None:
    source = _ADAPTER.read_text(encoding="utf-8")
    lowered = source.lower()
    for marker in (
        "ExecutionLevel.L3_GUIDED",
        "ExecutionLevel.L4_PLANNING",
        "ExecutionLevel.L5_EXPLORATORY",
        ".route(",
        ".escalate(",
        "model_provider",
        "research_provider",
        "openai",
        "anthropic",
    ):
        assert marker not in source and marker.lower() not in lowered

    tree = _tree(_ADAPTER)
    dynamic_calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not ({"eval", "exec", "compile", "__import__"} & dynamic_calls)


def test_l2_level_is_the_only_execution_level_member_referenced() -> None:
    level_members = {
        node.attr
        for node in ast.walk(_tree(_ADAPTER))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ExecutionLevel"
    }
    assert level_members == {"L2_COMPILED"}


def test_task_success_is_not_derived_from_end_or_procedure_text() -> None:
    source = _ADAPTER.read_text(encoding="utf-8")
    assert "ProcedureTaskVerification.TASK_VERIFIED" not in source
    assert "TaskStatus.SUCCEEDED" not in source
    assert "task_success =" not in source
    assert "task.status =" not in source
