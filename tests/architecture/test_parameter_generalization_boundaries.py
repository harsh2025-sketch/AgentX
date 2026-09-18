"""Architecture guards for the C3.05 parameter-generalization boundary."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

from agentx.learning.parameter_extraction import ParameterCandidate, ParameterExtraction
from agentx.learning.parameter_generalization import (
    ParameterGeneralization,
    ParameterObservationGroup,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "learning" / "parameter_generalization.py"


def _source() -> str:
    return _MODULE.read_text(encoding="utf-8")


def _tree() -> ast.Module:
    return ast.parse(_source())


def _agentx_imports() -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module.startswith("agentx."):
                modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names if alias.name.startswith("agentx."))
    return modules


def _import_roots() -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_c3_05_is_placed_in_learning() -> None:
    assert ParameterGeneralization.__module__ == "agentx.learning.parameter_generalization"
    assert ParameterObservationGroup.__module__ == "agentx.learning.parameter_generalization"


def test_c3_05_has_only_c3_04_as_agentx_compiler_stage_dependency() -> None:
    assert _agentx_imports() == {"agentx.learning.parameter_extraction"}
    assert ParameterCandidate.__module__ == "agentx.learning.parameter_extraction"
    assert ParameterExtraction.__module__ == "agentx.learning.parameter_extraction"


def test_contract_references_c3_04_objects_instead_of_reconstructing_trajectory_data() -> None:
    result_fields = {field.name for field in fields(ParameterGeneralization)}
    group_fields = {field.name for field in fields(ParameterObservationGroup)}

    assert result_fields == {
        "source_extraction",
        "groups",
        "supporting_extractions",
        "schema_version",
    }
    assert group_fields == {"action_name", "field_name", "observations", "evidence"}
    assert ParameterGeneralization.__annotations__["source_extraction"] == "ParameterExtraction"
    assert (
        ParameterGeneralization.__annotations__["supporting_extractions"]
        == "tuple[ParameterExtraction, ...]"
    )
    assert (
        ParameterObservationGroup.__annotations__["observations"]
        == "tuple[ParameterCandidate, ...]"
    )


def test_no_procedure_synthesis_or_later_compiler_stage_dependency() -> None:
    forbidden_prefixes = (
        "agentx.procedures",
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.infrastructure",
        "agentx.hive",
    )
    assert not any(
        module.startswith(prefix) for module in _agentx_imports() for prefix in forbidden_prefixes
    )

    lowered = _source().lower()
    forbidden_contracts = (
        "proceduregraph",
        "procedurenode",
        "procedure_store",
        "environment_assumption",
        "determinism_class",
        "precondition",
        "postcondition",
        "skill_registry",
        "shadow_evaluation",
    )
    assert not any(token in lowered for token in forbidden_contracts)


def test_no_model_embedding_or_research_dependency() -> None:
    forbidden_roots = {"openai", "anthropic", "transformers", "sentence_transformers"}
    assert _import_roots().isdisjoint(forbidden_roots)
    forbidden_agentx_fragments = (".model", ".reasoner", ".research", ".embedding")
    assert not any(
        fragment in module
        for module in _agentx_imports()
        for fragment in forbidden_agentx_fragments
    )


def test_no_external_environment_filesystem_or_process_calls() -> None:
    forbidden_imports = {
        "os",
        "pathlib",
        "subprocess",
        "socket",
        "urllib",
        "http",
        "requests",
        "shutil",
        "glob",
        "tempfile",
        "winreg",
    }
    assert _import_roots().isdisjoint(forbidden_imports)

    forbidden_calls = {"open", "eval", "exec", "compile", "getenv"}
    called_names = {
        node.func.id
        for node in ast.walk(_tree())
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called_names.isdisjoint(forbidden_calls)


def test_no_persistence_or_background_worker_surface() -> None:
    lowered = _source().lower()
    forbidden = (
        "sqlite",
        "migration",
        "knowledge_store",
        "episode_store",
        "procedure_store",
        "threading",
        "asyncio",
        "background",
    )
    assert not any(token in lowered for token in forbidden)


def test_no_authority_or_execution_layer_imports() -> None:
    forbidden = (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.core.tasks",
        "agentx.core.risk",
        "agentx.infrastructure",
    )
    assert not any(
        module.startswith(prefix) for module in _agentx_imports() for prefix in forbidden
    )


def test_zero_new_third_party_runtime_dependencies() -> None:
    allowed_roots = {
        "__future__",
        "json",
        "dataclasses",
        "enum",
        "typing",
        "uuid",
        "agentx",
    }
    assert _import_roots() <= allowed_roots
