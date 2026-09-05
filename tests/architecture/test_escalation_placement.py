"""Architecture-boundary tests for the A2.08 escalation/fallback contract."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_ESCALATION_PATH = _AGENTX_SRC / "cognition" / "escalation.py"
_ROUTER_PATH = _AGENTX_SRC / "cognition" / "router.py"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _tree(path: Path = _ESCALATION_PATH) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path = _ESCALATION_PATH) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def _called_names(path: Path = _ESCALATION_PATH) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _defined_classes(path: Path = _ESCALATION_PATH) -> set[str]:
    return {node.name for node in ast.walk(_tree(path)) if isinstance(node, ast.ClassDef)}


def _defined_functions(path: Path = _ESCALATION_PATH) -> set[str]:
    return {
        node.name
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _class_definition_paths(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in sorted(_AGENTX_SRC.rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.lstrip()
            if stripped.startswith(f"class {name}(") or stripped.startswith(f"class {name}:"):
                definitions.append(path.relative_to(_SRC_ROOT))
                break
    return definitions


def test_escalation_is_placed_in_cognition_boundary() -> None:
    assert _ESCALATION_PATH.is_file()
    assert _ESCALATION_PATH.parts[-3:] == ("agentx", "cognition", "escalation.py")


def test_escalation_production_surface_is_contract_only() -> None:
    classes = {node.name for node in _tree().body if isinstance(node, ast.ClassDef)}
    assert classes == {
        "EscalationAction",
        "EscalationDecision",
        "EscalationEvidence",
        "ExecutionLevelEscalator",
    }


def test_escalation_reuses_router_execution_level_and_does_not_duplicate_it() -> None:
    assert _class_definition_paths("ExecutionLevel") == [Path("agentx/cognition/router.py")]
    assert _class_definition_paths("ExecutionLevelRouter") == [Path("agentx/cognition/router.py")]
    assert _class_definition_paths("RoutingEvidence") == [Path("agentx/cognition/router.py")]
    assert _class_definition_paths("RoutingDecision") == [Path("agentx/cognition/router.py")]
    assert "ExecutionLevel" not in _defined_classes()
    assert "ExecutionLevelRouter" not in _defined_classes()
    assert "RoutingEvidence" not in _defined_classes()
    assert "RoutingDecision" not in _defined_classes()
    assert "agentx.cognition.router" in _imports()


def test_escalation_depends_only_on_the_canonical_router_level_vocabulary() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx.")}
    assert agentx_imports == {"agentx.cognition.router"}


def test_escalation_has_no_model_hive_store_kernel_or_execution_imports() -> None:
    forbidden_prefixes = (
        "agentx.capabilities",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.kernel",
        "agentx.learning",
        "agentx.procedures",
        "agentx.cognition.model_provider",
        "agentx.cognition.model_roles",
        "agentx.cognition.reasoner",
        "agentx.cognition.task_manager",
        "agentx.cognition.gap_detector",
    )
    assert not any(name.startswith(prefix) for name in _imports() for prefix in forbidden_prefixes)


def test_escalation_has_no_network_filesystem_process_or_async_runtime_dependency() -> None:
    forbidden_roots = {
        "aiohttp",
        "asyncio",
        "http",
        "httpx",
        "multiprocessing",
        "os",
        "pathlib",
        "requests",
        "socket",
        "sqlite3",
        "subprocess",
        "urllib",
        "importlib",
        "pkgutil",
        "time",
        "random",
        "threading",
    }
    roots = {name.split(".", maxsplit=1)[0] for name in _imports()}
    assert not roots & forbidden_roots


def test_escalation_calls_no_execution_verification_model_or_io_surface() -> None:
    forbidden_calls = {
        "activate",
        "execute",
        "get",
        "invoke",
        "open",
        "plan",
        "publish",
        "put",
        "read",
        "request",
        "research",
        "retry",
        "route",
        "transition",
        "verify",
        "write",
        "VerificationResult",
        "ClosedLoopOutcome",
        "CapabilityObservation",
        "ExecutionLevelRouter",
        "Verifier",
        "Executor",
        "TaskManager",
    }
    assert not _called_names() & forbidden_calls


def test_escalation_exposes_no_router_retry_loop_or_execution_methods() -> None:
    forbidden = {
        "execute",
        "fallback",
        "plan",
        "reason",
        "research",
        "retry",
        "route",
        "run",
        "run_loop",
        "verify",
    }
    assert not _defined_functions() & forbidden


def test_a207_router_remains_routing_only() -> None:
    forbidden = {
        "decide",
        "escalate",
        "fallback",
        "plan",
        "reason",
        "research",
        "retry",
        "run",
        "run_loop",
    }
    methods = _defined_functions(_ROUTER_PATH)
    assert "route" in methods
    assert not methods & forbidden
    router_imports = _imports(_ROUTER_PATH)
    assert "agentx.cognition.escalation" not in router_imports
    assert not any(name.startswith("agentx.") for name in router_imports)


def test_a209_anti_loop_behavior_is_absent() -> None:
    names = {name.lower() for name in _defined_classes() | _defined_functions()}
    for forbidden in (
        "antiloop",
        "anti_loop",
        "retry",
        "retrycount",
        "attemptcount",
        "loopguard",
        "visited",
        "backoff",
        "runloop",
        "cycle",
    ):
        assert not any(forbidden in name for name in names)
    assert not [node for node in ast.walk(_tree()) if isinstance(node, ast.While)]
    field_names = {
        node.arg for node in ast.walk(_tree()) if isinstance(node, ast.arg) and node.arg is not None
    }
    assert field_names.isdisjoint(
        {"retry_count", "attempt_count", "visited_levels", "loop_guard", "backoff"}
    )


def test_escalation_does_not_define_a_verifier_or_fabricate_verification() -> None:
    classes = _defined_classes()
    for forbidden in (
        "Verifier",
        "VerificationResult",
        "VerificationRequirement",
        "RequirementEvaluation",
        "ClosedLoopOutcome",
        "CapabilityObservation",
        "Executor",
        "TaskManager",
        "ActionGate",
        "Permission",
        "ResourceEnvelope",
        "EmergencyStop",
    ):
        assert forbidden not in classes
    source = _ESCALATION_PATH.read_text(encoding="utf-8")
    assert "VerificationResult(" not in source
    assert "def verify" not in source


def test_escalation_starts_no_background_worker() -> None:
    calls = _called_names()
    assert "Thread" not in calls
    assert "create_task" not in calls
    assert "start" not in calls


def test_escalation_has_no_module_level_escalator_singleton() -> None:
    for node in _tree().body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            assert value.func.id != "ExecutionLevelEscalator"


def test_escalation_has_no_persistence_or_migration() -> None:
    source = _ESCALATION_PATH.read_text(encoding="utf-8").lower()
    forbidden_fragments = (
        "sqlite",
        "migration",
        "create table",
        "event_bus",
        "event_journal",
        "knowledge_store",
        "procedure_store",
        "artifact_store",
    )
    assert not any(fragment in source for fragment in forbidden_fragments)
    assert not any("escalat" in migration.name for migration in _MIGRATIONS)


def test_escalation_declares_no_runtime_dependency_installation() -> None:
    source = _ESCALATION_PATH.read_text(encoding="utf-8")
    assert "pip install" not in source
    assert "importlib" not in source
    assert "entry_points" not in source


def test_no_new_runtime_dependencies_for_a208() -> None:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["dependencies"] == []
    assert project["optional-dependencies"]["dev"] == [
        "pytest>=8.0",
        "ruff>=0.5",
        "mypy>=1.10",
    ]


def test_escalation_uses_only_the_standard_library_externally() -> None:
    stdlib_allowed = {"__future__", "dataclasses", "enum", "typing"}
    for module in _imports():
        if module.startswith("agentx"):
            continue
        assert module in stdlib_allowed, f"unexpected external import {module}"
