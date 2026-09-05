"""Architecture guards for the C3.01 trajectory normalization boundary."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

from agentx.core.causal_experience import CausalExperience
from agentx.learning.trajectory import NormalizedTrajectoryStep

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src" / "agentx" / "learning" / "trajectory.py"


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


def test_trajectory_normalization_lives_in_learning_and_depends_inward_on_core_only() -> None:
    imports = _imports()
    forbidden = (
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.kernel",
        "agentx.procedures",
    )

    assert _MODULE.parts[-3:] == ("agentx", "learning", "trajectory.py")
    assert any(name == "agentx.core.causal_experience" for name in imports)
    assert any(name == "agentx.core.episodes" for name in imports)
    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden)


def test_c2_10_remains_the_canonical_step_evidence_contract() -> None:
    names = {field.name for field in fields(NormalizedTrajectoryStep)}
    classes = {node.name for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)}

    assert names == {"sequence", "source_experience_sha256", "experience"}
    assert NormalizedTrajectoryStep.__annotations__["experience"] == "CausalExperience"
    assert CausalExperience.__module__ == "agentx.core.causal_experience"
    assert "CausalExperience" not in classes
    assert "ExperienceState" not in classes
    assert "ActionPayload" not in classes
    assert "ObservationPayload" not in classes
    assert "VerificationPayload" not in classes
    assert "CausalOutcome" not in classes


def test_normalization_has_no_persistence_execution_or_dynamic_code_surface() -> None:
    imports = _imports()
    forbidden_imports = (
        "sqlite3",
        "subprocess",
        "importlib",
        "pickle",
    )
    forbidden_calls = {"eval", "exec", "open", "__import__"}
    called_names: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called_names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called_names.add(node.func.attr)

    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden_imports)
    assert called_names.isdisjoint(forbidden_calls)
    assert "execute" not in called_names
    assert "verify" not in called_names
    assert "reason" not in called_names
    assert "generate" not in called_names


def test_normalization_defines_no_analysis_or_compilation_stage() -> None:
    defined_names = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    forbidden = {
        "classify_action",
        "classify_importance",
        "extract_causal_actions",
        "extract_parameters",
        "infer_preconditions",
        "infer_postconditions",
        "classify_determinism",
        "compile_procedure",
        "synthesize_procedure",
        "learn",
        "repair",
        "route",
        "retry",
    }

    assert defined_names.isdisjoint(forbidden)
