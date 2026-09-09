"""Architecture guardrails for the N2.05 top-level guided-procedure adapter.

These tests pin *where* the adapter lives, *what* it is allowed to import, and
— most importantly — what it is structurally incapable of being: a second
Reasoner, interpreter, Executor, Verifier, router, planner, research engine, or
persistence layer.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_ROOT = _SRC_ROOT / "agentx"
_ADAPTER = _AGENTX_ROOT / "guided_procedure_strategy.py"
_AGENT_LOOP = _AGENTX_ROOT / "agent_loop.py"
_INTERPRETER = _AGENTX_ROOT / "procedures" / "interpreter.py"
_REASONER = _AGENTX_ROOT / "cognition" / "reasoner.py"
_CAPABILITY_STRATEGY = _AGENTX_ROOT / "capability_strategy.py"
_ARCHITECTURE = _AGENTX_ROOT / "_architecture.py"

_ALLOWED_AGENTX_IMPORTS = frozenset(
    {
        "agentx.agent_loop",
        "agentx.capabilities.abi",
        "agentx.capabilities.executor",
        "agentx.capabilities.runtime",
        "agentx.cognition.model_provider",
        "agentx.cognition.reasoner",
        "agentx.cognition.router",
        "agentx.core.errors",
        "agentx.core.execution",
        "agentx.core.procedure_execution",
        "agentx.core.result",
        "agentx.core.tasks",
        "agentx.procedures.graph",
        "agentx.procedures.interpreter",
        "agentx.procedures.nodes",
        "agentx.procedures.reason_research",
    }
)

_ALLOWED_STDLIB_IMPORTS = frozenset(
    {"__future__", "collections.abc", "dataclasses", "types", "typing"}
)

_FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "subprocess",
        "socket",
        "sqlite3",
        "threading",
        "asyncio",
        "multiprocessing",
        "urllib",
        "http",
        "requests",
        "ctypes",
        "random",
        "time",
        "datetime",
        "pathlib",
        "os",
        "sys",
        "win32api",
        "pywinauto",
        "uiautomation",
        "selenium",
        "playwright",
    }
)

#: The adapter holds no authority and reaches no store, memory, or learner.
_FORBIDDEN_AGENTX_PREFIXES = (
    "agentx.kernel",
    "agentx.hive",
    "agentx.learning",
    "agentx.infrastructure",
    "agentx.capability_strategy",
    "agentx.capabilities.registry",
    "agentx.capabilities.verifier",
    "agentx.cognition.anti_loop",
    "agentx.cognition.escalation",
    "agentx.cognition.gap_detector",
    "agentx.cognition.research_acquisition",
    "agentx.cognition.research_objective",
    "agentx.cognition.research_provider",
    "agentx.cognition.task_manager",
    "agentx.procedure_synthesis",
    "agentx.procedure_validation",
    "agentx.procedures.condition_evaluation",
    "agentx.procedures.recovery",
    "agentx.procedures.subprocedure",
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


def _code_source(path: Path) -> str:
    """Return the module's executable source with docstrings and comments removed.

    Prose about the ActionGate, permissions, or verification is documentation,
    not surface. Only what the module actually *does* is checked below.
    """
    tree = _tree(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = list(node.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _called_attributes(path: Path) -> set[str]:
    return {
        node.func.attr
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def test_adapter_is_one_top_level_composition_module() -> None:
    assert _ADAPTER.is_file()
    assert _ADAPTER.parent == _AGENTX_ROOT
    assert _ADAPTER.parent.name == "agentx"
    assert not (_AGENTX_ROOT / "guided").exists()
    assert not (_AGENTX_ROOT / "guided_procedure_strategy").exists()


def test_adapter_imports_only_exact_canonical_composition_contracts() -> None:
    imports = set(_imports(_ADAPTER))
    agentx_imports = {module for module in imports if module.startswith("agentx")}
    assert agentx_imports == _ALLOWED_AGENTX_IMPORTS
    assert imports - agentx_imports == _ALLOWED_STDLIB_IMPORTS
    for module in imports:
        assert module.split(".")[0] not in _FORBIDDEN_IMPORT_ROOTS
        for prefix in _FORBIDDEN_AGENTX_PREFIXES:
            assert module != prefix and not module.startswith(f"{prefix}.")


def test_adapter_holds_no_kernel_authority_surface() -> None:
    source = _code_source(_ADAPTER)
    tree = _tree(_ADAPTER)
    referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    for forbidden in (
        "Permission",
        "AuthorityContext",
        "ActionGate",
        "GateRequest",
        "EmergencyStop",
        "ResourceBudget",
        "ResourceEnvelope",
        "RiskLevel",
        "PermissionEngine",
    ):
        assert forbidden not in referenced
    assert "agentx.kernel" not in source


def test_adapter_defines_no_shadow_engine_of_any_canonical_owner() -> None:
    defined = _defined_classes(_ADAPTER)
    assert defined == {
        "GuidedActionBinding",
        "GuidedProcedureBinding",
        "GuidedProcedureBindingError",
        "GuidedProcedureRun",
        "GuidedProcedureStrategy",
        "GuidedReasoningBinding",
        "GuidedStepRecord",
        "_StepOutcome",
    }
    forbidden_definitions = {
        "Capability",
        "CapabilityExecutionLoop",
        "CapabilityRegistry",
        "Executor",
        "Verifier",
        "VerificationResult",
        "Reasoner",
        "ModelProvider",
        "ResearchProvider",
        "ProcedureInterpreter",
        "ProcedureGraph",
        "BranchContract",
        "ExecutionLevelRouter",
        "ExecutionLevelEscalator",
        "LoopGuard",
        "TaskManager",
        "StrategyRegistry",
        "StrategyResult",
        "AgentLoop",
    }
    assert not (defined & forbidden_definitions)


def test_only_the_injected_canonical_boundaries_are_ever_called() -> None:
    called = _called_attributes(_ADAPTER)
    assert "execute" in called
    assert "reason" in called
    # No second verifier, router, escalator, anti-loop, task manager, planner,
    # research engine, condition evaluator, or store is ever driven from here.
    for forbidden in (
        "verify",
        "evaluate",
        "route",
        "decide",
        "acquire",
        "research",
        "synthesize",
        "compile",
        "transition",
        "save",
        "load",
        "commit",
        "publish",
        "request_stop",
        "consume",
        "reserve",
    ):
        assert forbidden not in called

    tree = _tree(_ADAPTER)
    execute_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
    ]
    assert len(execute_calls) == 1
    executor_attribute = execute_calls[0].func
    assert isinstance(executor_attribute, ast.Attribute)
    assert isinstance(executor_attribute.value, ast.Attribute)
    assert executor_attribute.value.attr == "_executor"

    reason_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "reason"
    ]
    assert len(reason_calls) == 1

    source = _ADAPTER.read_text(encoding="utf-8")
    assert "Capability.execute(" not in source
    assert "ModelProvider" not in source.replace("agentx.cognition.model_provider", "").replace(
        "model_provider", ""
    )


def test_the_adapter_declares_exactly_one_execution_level() -> None:
    tree = _tree(_ADAPTER)
    levels = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ExecutionLevel"
    }
    assert levels == {"L3_GUIDED"}
    source = _code_source(_ADAPTER)
    for other in ("L0_CACHE", "L1_DIRECT", "L2_COMPILED", "L4_PLANNED", "L5_EXPLORATORY"):
        assert other not in source


def test_the_walk_is_bounded_and_has_no_unbounded_loop_or_recursion() -> None:
    tree = _tree(_ADAPTER)
    assert not any(isinstance(node, ast.While) for node in ast.walk(tree))
    walk_loops = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Name)
        and node.iter.func.id == "range"
    ]
    assert len(walk_loops) == 1
    # The single control-flow loop is bounded by the canonical interpreter's own
    # step ceiling, not by anything this module invented.
    assert "max_steps" in ast.dump(walk_loops[0].iter)


def test_adapter_has_no_dynamic_execution_native_or_background_surface() -> None:
    tree = _tree(_ADAPTER)
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not ({"eval", "exec", "compile", "__import__", "open", "getattr"} & called_names)
    source = _code_source(_ADAPTER)
    for marker in (
        "WinDLL",
        "windll",
        "SendInput",
        "subprocess",
        "Popen",
        "Thread(",
        "Process(",
        "asyncio",
        "sleep(",
        "socket",
    ):
        assert marker not in source


def test_adapter_contains_no_persistence_research_or_hive_surface() -> None:
    source = _code_source(_ADAPTER).lower()
    for forbidden in (
        "sqlite",
        "procedure_store",
        "episode_store",
        "knowledge_store",
        "semantic_memory",
        "event_journal",
        "artifact_store",
        "audit_store",
        "researchprovider",
        "researchnodespec",
        "research_acquisition",
        "openai",
        "anthropic",
        "http",
    ):
        assert forbidden not in source


def test_the_adapter_can_never_assess_task_verification() -> None:
    source = _code_source(_ADAPTER)
    assert "ProcedureTaskVerification.NOT_ASSESSED" in source
    assert "TASK_VERIFIED" not in source
    assert "TASK_VERIFICATION_FAILED" not in source
    # No Task status vocabulary, no verification verdict construction.
    assert "TaskStatus" not in source
    assert "VerificationResult" not in source
    assert "SUCCEEDED" not in source


def test_canonical_owners_are_not_modified_or_made_aware_of_the_adapter() -> None:
    for owner in (_AGENT_LOOP, _INTERPRETER, _REASONER, _CAPABILITY_STRATEGY, _ARCHITECTURE):
        source = owner.read_text(encoding="utf-8")
        assert "guided_procedure_strategy" not in source
        assert "GuidedProcedureStrategy" not in source


def test_architecture_manifest_has_no_adapter_special_case_or_new_edge() -> None:
    assert (
        _architecture.COGNITION,
        _architecture.CAPABILITIES,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES
    assert (
        _architecture.PROCEDURES,
        _architecture.CAPABILITIES,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES
    assert (
        _architecture.PROCEDURES,
        _architecture.COGNITION,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES
    source = _ARCHITECTURE.read_text(encoding="utf-8")
    assert "guided" not in source.lower()


def test_the_adapter_is_the_only_new_production_module_for_this_task() -> None:
    guided_modules = sorted(
        path.relative_to(_SRC_ROOT).as_posix() for path in _AGENTX_ROOT.rglob("*guided*.py")
    )
    assert guided_modules == ["agentx/guided_procedure_strategy.py"]
