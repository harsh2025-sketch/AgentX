"""Architecture guards for A6.07 canonical human operating modes."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "core" / "human_operating_modes.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")


def _tree() -> ast.Module:
    return ast.parse(_SOURCE)


def _imports() -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def _classes() -> set[str]:
    return {node.name for node in ast.walk(_tree()) if isinstance(node, ast.ClassDef)}


def _public_functions() -> set[str]:
    return {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }


def _called_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_a607_lives_in_core_and_imports_no_agentx_subsystem() -> None:
    assert _MODULE.is_file()
    assert {name for name in _imports() if name.startswith("agentx")} == set()


def test_a607_uses_only_inert_standard_library_modules() -> None:
    non_agentx = {name.split(".")[0] for name in _imports() if not name.startswith("agentx")}
    assert non_agentx <= {"__future__", "enum", "typing"}


def test_a607_defines_only_the_mode_vocabulary_not_runtime_state_or_policy() -> None:
    classes = _classes()
    forbidden = {
        "ActionGate",
        "AuthorityContext",
        "Permission",
        "PermissionEngine",
        "RiskAssessment",
        "ResourceEnvelope",
        "EmergencyStop",
        "ModeRouter",
        "ModeManager",
        "ModeStore",
        "ModeSession",
        "ModeSelection",
        "LearningCapture",
        "DebugLogger",
        "Procedure",
        "Verifier",
        "TaskManager",
    }

    assert classes == {"HumanOperatingMode"}
    assert classes.isdisjoint(forbidden)
    assert _public_functions() == set()


def test_a607_exposes_no_execution_learning_research_or_authority_calls() -> None:
    forbidden_calls = {
        "grant",
        "authorize",
        "evaluate",
        "execute",
        "verify",
        "transition",
        "publish",
        "capture",
        "learn",
        "promote",
        "remember",
        "research",
        "activate",
        "clear",
        "reset",
        "open",
        "eval",
        "exec",
        "__import__",
    }

    assert _called_names().isdisjoint(forbidden_calls)


def test_a607_has_no_network_process_file_or_dynamic_import_primitives() -> None:
    forbidden_import_roots = {
        "asyncio",
        "importlib",
        "os",
        "pathlib",
        "pickle",
        "socket",
        "sqlite3",
        "subprocess",
        "threading",
        "urllib",
        "webbrowser",
    }
    roots = {name.split(".")[0] for name in _imports()}

    assert roots.isdisjoint(forbidden_import_roots)


def test_a607_adds_no_persistence_or_migration_surface() -> None:
    lowered = _SOURCE.lower()
    for token in (
        "sqlite",
        "create table",
        "migration",
        "mode_store",
        "persist_mode",
        "current_mode",
    ):
        assert token not in lowered

    persistence_source = (
        _REPO_ROOT / "src" / "agentx" / "infrastructure" / "persistence.py"
    ).read_text(encoding="utf-8")
    assert "human_operating_mode" not in persistence_source
    assert "operating_mode" not in persistence_source


def test_a607_does_not_duplicate_kernel_or_verification_contracts() -> None:
    lowered = _SOURCE.lower()
    for token in (
        "class permission",
        "class authoritycontext",
        "class browserpermission",
        "class risklevel",
        "class resourceenvelope",
        "class emergencystop",
        "class verificationresult",
        "class knowledgestatus",
    ):
        assert token not in lowered


def test_a607_adds_zero_runtime_dependencies() -> None:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []


def test_a607_semantics_are_explicitly_documented() -> None:
    docs = (_REPO_ROOT / "docs" / "human_operating_modes.md").read_text(encoding="utf-8")

    for mode in ("NORMAL", "LEARN", "TEACH", "DEBUG"):
        assert mode in docs
    assert "LEARN" in docs and "learn everything" in docs
    assert "TEACH" in docs and "trust everything" in docs
    assert "DEBUG" in docs and "disable security" in docs
