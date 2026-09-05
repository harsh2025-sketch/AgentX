"""Architecture guards for the C3.03 irrelevant-action analysis boundary."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

from agentx.learning.causal_actions import ExtractedActionCandidate
from agentx.learning.irrelevant_actions import (
    ActionEliminationDecision,
    IrrelevantActionAnalysis,
)
from agentx.learning.trajectory import NormalizedTrajectoryStep

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src" / "agentx" / "learning" / "irrelevant_actions.py"


def _tree() -> ast.Module:
    return ast.parse(_MODULE.read_text(encoding="utf-8"), filename=str(_MODULE))


def _imports() -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def _called_names() -> set[str]:
    called: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    return called


def test_c3_03_lives_in_learning_and_reuses_c3_02_and_c2_10_contracts() -> None:
    imports = _imports()

    assert _MODULE.parts[-3:] == ("agentx", "learning", "irrelevant_actions.py")
    assert "agentx.learning.causal_actions" in imports
    assert "agentx.learning.trajectory" in imports
    assert "agentx.core.causal_experience" in imports


def test_decision_preserves_source_candidate_and_normalized_step_reference() -> None:
    decision_fields = {field.name for field in fields(ActionEliminationDecision)}
    analysis_fields = {field.name for field in fields(IrrelevantActionAnalysis)}

    assert decision_fields == {
        "source_trajectory_id",
        "source_candidate",
        "disposition",
        "reason",
    }
    assert analysis_fields == {"source_trajectory_id", "decisions", "schema_version"}
    assert ActionEliminationDecision.__annotations__["source_candidate"] == "ExtractedActionCandidate"
    assert ExtractedActionCandidate.__module__ == "agentx.learning.causal_actions"
    assert NormalizedTrajectoryStep.__module__ == "agentx.learning.trajectory"


def test_c3_03_does_not_import_runtime_authority_hive_or_procedure_subsystems() -> None:
    imports = _imports()
    forbidden = (
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.kernel",
        "agentx.procedures",
    )

    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden)


def test_c3_03_has_no_model_embedding_similarity_or_text_heuristic_imports() -> None:
    imports = _imports()
    forbidden = (
        "openai",
        "anthropic",
        "transformers",
        "sentence_transformers",
        "sklearn",
        "numpy",
        "scipy",
        "re",
    )

    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden)


def test_c3_03_has_no_text_heuristic_model_embedding_or_scoring_calls() -> None:
    called = _called_names()
    forbidden_calls = {
        "lower",
        "upper",
        "casefold",
        "startswith",
        "endswith",
        "search",
        "match",
        "find",
        "findall",
        "embed",
        "embedding",
        "similarity",
        "predict",
        "generate",
        "complete",
        "score",
        "rank",
    }

    assert called.isdisjoint(forbidden_calls)


def test_c3_03_has_no_persistence_network_dynamic_code_or_execution_surface() -> None:
    imports = _imports()
    called = _called_names()
    forbidden_imports = (
        "sqlite3",
        "subprocess",
        "importlib",
        "pickle",
        "socket",
        "http",
        "urllib",
        "threading",
        "asyncio",
    )
    forbidden_calls = {
        "eval",
        "exec",
        "compile",
        "open",
        "__import__",
        "execute",
        "invoke",
        "run",
        "sleep",
        "write",
        "insert",
        "update",
        "delete",
        "commit",
        "publish",
    }

    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden_imports)
    assert called.isdisjoint(forbidden_calls)


def test_c3_03_does_not_define_c3_04_or_later_compiler_stages() -> None:
    defined = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    forbidden = {
        "extract_parameters",
        "extract_constants",
        "infer_environmental_assumptions",
        "classify_determinism",
        "classify_reasoning",
        "infer_preconditions",
        "infer_postconditions",
        "synthesize_procedure",
        "compile_procedure",
        "register_skill",
        "activate_skill",
        "shadow_execute",
        "learn",
        "repair",
        "route",
        "retry",
    }

    assert defined.isdisjoint(forbidden)


def test_c3_03_uses_only_standard_library_and_agentx_imports() -> None:
    imports = _imports()
    standard_roots = {"__future__", "dataclasses", "enum", "json", "typing", "uuid"}

    assert all(
        name.startswith("agentx.") or name.split(".", 1)[0] in standard_roots for name in imports
    )
