"""Architecture guards for the C3.04 parameter-extraction boundary."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

from agentx.learning.irrelevant_actions import ActionEliminationDecision, IrrelevantActionAnalysis
from agentx.learning.parameter_extraction import ParameterCandidate, ParameterExtraction

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "learning" / "parameter_extraction.py"


def _source() -> str:
    return _MODULE.read_text(encoding="utf-8")


def _import_roots() -> set[str]:
    tree = ast.parse(_source())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _agentx_imports() -> set[str]:
    tree = ast.parse(_source())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.startswith("agentx."):
                modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names if alias.name.startswith("agentx."))
    return modules


def test_c3_04_is_placed_in_learning() -> None:
    assert ParameterCandidate.__module__ == "agentx.learning.parameter_extraction"
    assert ParameterExtraction.__module__ == "agentx.learning.parameter_extraction"


def test_candidate_references_canonical_c3_03_decision_instead_of_copying_action_schema() -> None:
    candidate_fields = {field.name for field in fields(ParameterCandidate)}

    assert candidate_fields == {"source_decision", "field_name"}
    assert ParameterCandidate.__annotations__["source_decision"] == "ActionEliminationDecision"
    assert ActionEliminationDecision.__module__ == "agentx.learning.irrelevant_actions"


def test_extraction_references_canonical_c3_03_analysis() -> None:
    extraction_fields = {field.name for field in fields(ParameterExtraction)}

    assert extraction_fields == {"source_analysis", "candidates", "schema_version"}
    assert ParameterExtraction.__annotations__["source_analysis"] == "IrrelevantActionAnalysis"
    assert IrrelevantActionAnalysis.__module__ == "agentx.learning.irrelevant_actions"


def test_production_module_has_no_third_party_dependency() -> None:
    allowed_roots = {"__future__", "json", "collections", "dataclasses", "typing", "uuid", "agentx"}

    assert _import_roots() <= allowed_roots


def test_production_module_does_not_cross_into_execution_or_authority_layers() -> None:
    forbidden_prefixes = (
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.infrastructure",
        "agentx.kernel",
        "agentx.hive",
        "agentx.procedures",
    )

    assert not any(
        module.startswith(prefix) for module in _agentx_imports() for prefix in forbidden_prefixes
    )


def test_production_module_imports_only_canonical_analysis_and_event_data() -> None:
    assert _agentx_imports() == {
        "agentx.core.events",
        "agentx.learning.causal_actions",
        "agentx.learning.irrelevant_actions",
        "agentx.learning.trajectory",
    }


def test_no_model_research_or_semantic_inference_dependencies() -> None:
    forbidden_roots = {"openai", "anthropic", "transformers", "sentence_transformers"}
    forbidden_agentx_fragments = (
        ".model",
        ".reasoner",
        ".research",
        ".embedding",
        ".semantic_similarity",
    )

    assert _import_roots().isdisjoint(forbidden_roots)
    assert not any(
        fragment in module
        for module in _agentx_imports()
        for fragment in forbidden_agentx_fragments
    )


def test_no_environment_filesystem_process_or_secret_inspection() -> None:
    tree = ast.parse(_source())
    forbidden_imports = {"os", "pathlib", "subprocess", "shutil", "glob", "tempfile", "winreg"}

    imported = _import_roots()
    assert imported.isdisjoint(forbidden_imports)
    forbidden_calls = {"open", "eval", "exec", "compile", "getenv"}
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called_names.isdisjoint(forbidden_calls)


def test_no_procedure_creation_or_later_compiler_stage_imports() -> None:
    lowered = _source().lower()
    forbidden = (
        "proceduregraph",
        "procedurenode",
        "procedure_store",
        "environment_assumption",
        "determinism_class",
        "precondition",
        "postcondition",
        "shadow_evaluation",
        "skill_registry",
    )

    assert not any(token in lowered for token in forbidden)


def test_no_persistence_migration_or_background_worker_surface() -> None:
    lowered = _source().lower()
    forbidden = (
        "sqlite",
        "migration",
        "knowledge_store",
        "episode_store",
        "threading",
        "asyncio",
        "background",
    )

    assert not any(token in lowered for token in forbidden)
