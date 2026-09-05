"""Architecture guardrails for A4.01 deterministic gap detection."""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

from agentx.cognition import gap_detector
from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_GAP_DETECTOR = _AGENTX_SRC / "cognition" / "gap_detector.py"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _module_tree(path: Path = _GAP_DETECTOR) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in sorted(_AGENTX_SRC.rglob("*.py")):
        if any(
            isinstance(node, ast.ClassDef) and node.name == name
            for node in ast.walk(_module_tree(path))
        ):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


def _imports(path: Path = _GAP_DETECTOR) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _called_names(path: Path = _GAP_DETECTOR) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def _executable_identifiers(path: Path = _GAP_DETECTOR) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.arg | ast.keyword) and node.arg is not None:
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add(node.name)
            if node.asname is not None:
                names.add(node.asname)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return frozenset(name.lower() for name in names)


def test_gap_detector_lives_in_cognition_boundary() -> None:
    assert _GAP_DETECTOR.is_file()
    assert gap_detector.__name__ == "agentx.cognition.gap_detector"


def test_gap_detector_public_api_is_narrow() -> None:
    public_classes = {
        node.name
        for node in _module_tree().body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }

    assert public_classes == {
        "KnowledgeGapAssessment",
        "KnowledgeGapAssessmentRequest",
        "KnowledgeGapAssessmentStatus",
        "KnowledgeGapDetector",
        "KnowledgeGapRequirement",
        "KnowledgeGapValidationError",
        "KnowledgeRequirementAssessment",
    }

    detector_methods = {
        item.name
        for node in _module_tree().body
        if isinstance(node, ast.ClassDef) and node.name == "KnowledgeGapDetector"
        for item in node.body
        if isinstance(item, ast.FunctionDef) and not item.name.startswith("__")
    }
    assert detector_methods == {"assess"}


def test_gap_detector_definitions_are_unique() -> None:
    for name in (
        "KnowledgeGapRequirement",
        "KnowledgeGapAssessmentRequest",
        "KnowledgeGapAssessment",
        "KnowledgeGapDetector",
    ):
        assert _class_definitions(name) == [Path("agentx/cognition/gap_detector.py")]


def test_gap_detector_reuses_core_knowledge_contracts_only() -> None:
    agentx_imports = {module for module in _imports() if module.startswith("agentx.")}

    assert agentx_imports == {"agentx.core.ids", "agentx.core.knowledge"}


def test_gap_detector_uses_only_standard_library_and_agentx() -> None:
    imports = _imports()
    non_stdlib = [
        module
        for module in imports
        if module.split(".", maxsplit=1)[0] not in sys.stdlib_module_names
        and not module.startswith("agentx")
    ]

    assert non_stdlib == []


def test_gap_detector_has_no_persistence_or_migration() -> None:
    source = _GAP_DETECTOR.read_text(encoding="utf-8").lower()
    forbidden_fragments = (
        "sqlite",
        "migration",
        "create table",
        "knowledge_store",
        "event_bus",
        "event_journal",
        "artifact_store",
        "procedure_store",
    )

    assert not any(fragment in source for fragment in forbidden_fragments)
    assert not any("gap" in migration.name for migration in _MIGRATIONS)


def test_gap_detector_imports_no_authority_execution_model_or_retrieval_subsystem() -> None:
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.procedures",
        "agentx.learning",
        "agentx.cognition.model_provider",
        "agentx.cognition.model_roles",
        "agentx.cognition.reasoner",
        "agentx.cognition.task_manager",
        "agentx.cognition.router",
        "http",
        "urllib",
        "socket",
        "subprocess",
        "requests",
        "aiohttp",
        "httpx",
        "importlib",
        "pkgutil",
        "os",
    )

    imports = _imports()
    assert not any(module.startswith(prefix) for module in imports for prefix in forbidden_prefixes)


def test_gap_detector_does_not_execute_capabilities_or_procedures() -> None:
    forbidden_calls = {
        "execute",
        "verify",
        "invoke",
        "dispatch",
        "run",
        "route",
        "plan",
        "research",
        "retrieve",
        "remember",
        "insert",
        "update_status",
        "record_contradiction",
        "apply_supersession",
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
    }

    assert _called_names().isdisjoint(forbidden_calls)


def test_gap_detector_does_not_duplicate_router_or_execution_level() -> None:
    forbidden_class_names = {
        "Router",
        "RoutingDecision",
        "ExecutionLevel",
        "ExecutionRoute",
        "RouteDecision",
        "L0",
        "L1",
        "L2",
        "L3",
        "L4",
        "L5",
    }
    public_classes = {
        node.name
        for node in _module_tree().body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }

    assert public_classes.isdisjoint(forbidden_class_names)
    identifiers = _executable_identifiers()
    forbidden_identifiers = {"router", "route", "routingdecision"}
    assert not any(identifier in forbidden_identifiers for identifier in identifiers)


def test_gap_detector_contains_no_a402_plus_research_behaviour() -> None:
    identifiers = _executable_identifiers()
    forbidden_fragments = (
        "researchobjective",
        "researchprovider",
        "documentacquisition",
        "claimextraction",
        "hypothesis",
        "experimentplanner",
        "provider",
        "browse",
        "web",
        "url",
        "embedding",
        "vector",
        "ranking",
        "fallback",
        "retry",
        "synthesize",
        "compiler",
        "repair",
    )

    assert not any(
        fragment in identifier for identifier in identifiers for fragment in forbidden_fragments
    )


def test_runtime_distribution_still_declares_no_dependencies() -> None:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]

    assert project["dependencies"] == []
