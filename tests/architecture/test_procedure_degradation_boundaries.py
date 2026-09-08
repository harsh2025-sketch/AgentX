"""Architecture guards for the M5.01 procedure degradation evidence detector.

The detector is a pure top-level composition module at the agentx namespace root.
These static guards prove it stays that way: core-only imports, no subsystem
package residence, no lifecycle/repair/authority surface, no architecture
manifest widening, and no ProcedureStatus mutation vocabulary.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_MODULE = _AGENTX_SRC / "procedure_degradation.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_ARCHITECTURE_SOURCE = (_AGENTX_SRC / "_architecture.py").read_text(encoding="utf-8")
_PROCEDURES_SOURCE = (_AGENTX_SRC / "core" / "procedures.py").read_text(encoding="utf-8")
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


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
        for node in _tree().body
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


def _referenced_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


# --------------------------------------------------------------------------
# placement
# --------------------------------------------------------------------------


def test_module_is_single_top_level_composition_file() -> None:
    assert _MODULE.is_file()
    assert _MODULE.parent == _AGENTX_SRC
    assert not (_AGENTX_SRC / "procedure_degradation").exists()


def test_module_is_not_inside_any_canonical_subsystem() -> None:
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        assert not _MODULE.is_relative_to(package)


def test_module_did_not_create_a_new_top_level_subsystem() -> None:
    assert "agentx.procedure_degradation" not in _architecture.SUBSYSTEMS
    assert _architecture.SUBSYSTEMS == (
        "agentx.core",
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.hive",
        "agentx.procedures",
        "agentx.cognition",
        "agentx.learning",
        "agentx.infrastructure",
    )


def test_architecture_manifest_unchanged() -> None:
    """_architecture.py must not be modified by this task."""
    assert "procedure_degradation" not in _ARCHITECTURE_SOURCE
    assert "DEGRADATION" not in _ARCHITECTURE_SOURCE
    assert (
        frozenset(
            {
                (_architecture.KERNEL, _architecture.CORE),
                (_architecture.CAPABILITIES, _architecture.CORE),
                (_architecture.HIVE, _architecture.CORE),
                (_architecture.PROCEDURES, _architecture.CORE),
                (_architecture.COGNITION, _architecture.CORE),
                (_architecture.LEARNING, _architecture.CORE),
                (_architecture.INFRASTRUCTURE, _architecture.CORE),
                (_architecture.CAPABILITIES, _architecture.KERNEL),
                (_architecture.PROCEDURES, _architecture.KERNEL),
                (_architecture.COGNITION, _architecture.KERNEL),
            }
        )
        == _architecture.ALLOWED_ARCHITECTURE_EDGES
    )


def test_no_subsystem_imports_the_composition_module() -> None:
    offenders: list[str] = []
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        for path in sorted(package.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "agentx.procedure_degradation" in text or "procedure_degradation" in text:
                # Docstring mentions in unrelated modules would be surprising; fail closed.
                tree = ast.parse(text)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if "procedure_degradation" in alias.name:
                                offenders.append(str(path.relative_to(_SRC_ROOT)))
                    elif (
                        isinstance(node, ast.ImportFrom)
                        and node.module is not None
                        and "procedure_degradation" in node.module
                    ):
                        offenders.append(str(path.relative_to(_SRC_ROOT)))
    assert offenders == []


# --------------------------------------------------------------------------
# imports / surface
# --------------------------------------------------------------------------


def test_imports_only_core_and_stdlib() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx")}
    assert agentx_imports == {
        "agentx.core.causal_experience",
        "agentx.core.environment_change",
        "agentx.core.failure_diagnosis",
        "agentx.core.failure_taxonomy",
        "agentx.core.ids",
        "agentx.core.procedures",
    }
    for foreign in (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.hive",
        "agentx.procedures",
        "agentx.learning",
        "agentx.infrastructure",
    ):
        # agentx.core.procedures is allowed; agentx.procedures (execution) is not.
        if foreign == "agentx.procedures":
            assert "agentx.procedures" not in agentx_imports
            assert not any(name.startswith("agentx.procedures.") for name in agentx_imports)
            continue
        assert not any(name == foreign or name.startswith(foreign + ".") for name in agentx_imports)

    non_agentx = {name.split(".")[0] for name in _imports() if not name.startswith("agentx")}
    assert non_agentx <= {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "typing",
    }


def test_public_entry_point_is_assessment_only() -> None:
    assert _public_functions() == {"assess_procedure_degradation"}
    forbidden = {
        "retire",
        "activate",
        "repair",
        "execute",
        "update_status",
        "derive_repair_candidates",
        "grant",
        "revoke",
        "publish",
        "scan",
        "query_store",
        "persist",
        "rank",
        "score",
    }
    assert _public_functions().isdisjoint(forbidden)


def test_defined_classes_are_assessment_data_only() -> None:
    classes = _classes()
    assert "ProcedureDegradationAssessment" in classes
    assert "DegradationState" in classes
    assert "ProcedureSuccessEvidence" in classes
    assert "BoundFailureDiagnosis" in classes
    assert "BoundEnvironmentChange" in classes
    forbidden = {
        "ActionGate",
        "AuthorityContext",
        "Permission",
        "ResourceEnvelope",
        "EmergencyStop",
        "RepairCandidate",
        "ProcedureStore",
        "ProcedureGraph",
        "Executor",
        "Verifier",
        "Reasoner",
        "TaskManager",
    }
    assert classes.isdisjoint(forbidden)


def test_no_io_clock_network_or_dynamic_execution() -> None:
    forbidden_imports = {
        "importlib",
        "pickle",
        "subprocess",
        "socket",
        "sqlite3",
        "urllib",
        "http",
        "os",
        "pathlib",
        "random",
        "secrets",
        "re",
        "threading",
        "ctypes",
        "time",
        "sys",
        "asyncio",
    }
    assert _imports().isdisjoint(forbidden_imports)
    forbidden_calls = {
        "eval",
        "exec",
        "open",
        "now",
        "utcnow",
        "today",
        "sleep",
        "urlopen",
        "__import__",
        "system",
        "Popen",
        "update_status",
        "retire",
        "activate",
        "repair",
        "execute",
        "grant",
        "publish",
    }
    assert _called_names().isdisjoint(forbidden_calls)


def test_adds_no_runtime_dependency() -> None:
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []


# --------------------------------------------------------------------------
# ProcedureStatus / lifecycle proof
# --------------------------------------------------------------------------


def test_procedure_status_vocabulary_unchanged_and_has_no_degraded() -> None:
    assert "class ProcedureStatus" in _PROCEDURES_SOURCE
    assert "CANDIDATE" in _PROCEDURES_SOURCE
    assert "ACTIVE" in _PROCEDURES_SOURCE
    assert "RETIRED" in _PROCEDURES_SOURCE
    # ProcedureStatus must not gain DEGRADED from this task.
    status_block = _PROCEDURES_SOURCE.split("class ProcedureStatus")[1].split("class ")[0]
    assert "DEGRADED" not in status_block
    assert "update_status" not in _SOURCE
    assert "ProcedureStatus.DEGRADED" not in _SOURCE
    # Detector never assigns or transitions status.
    assert "ProcedureStatus." not in _SOURCE


def test_module_never_mutates_or_writes_status() -> None:
    referenced = _referenced_names()
    # May import ProcedureRecord but must not reference update_status / RETIRED assignment.
    assert "update_status" not in referenced
    assert "derive_repair_candidates" not in referenced
    assert "RepairCandidate" not in referenced
    assert "ProcedureStore" not in referenced


def test_docs_page_exists() -> None:
    docs = _REPO_ROOT / "docs" / "procedure_degradation.md"
    assert docs.is_file()
    text = docs.read_text(encoding="utf-8")
    assert "M5.01" in text
    assert "CONFIRMED_DEGRADED" in text
    assert "ProcedureStatus" in text
    assert "never" in text.lower()
    assert "hard bound" in text.lower() or "Hard bounds" in text


def test_no_confidence_floats_or_scoring() -> None:
    lowered = _SOURCE.lower()
    for token in (
        "confidence=",
        "probability=",
        "score=",
        "weight=",
        "openai",
        "anthropic",
        "embeddings",
        "vector_store",
    ):
        assert token not in lowered
    # Docstring may name the non-goal "embedding"; ensure no field/API uses it.
    assert "embedding=" not in lowered
    assert "embedding:" not in lowered


def test_no_keyword_inference_from_free_text() -> None:
    """States are never derived by scanning summary/detail strings."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Compare):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    lowered = comparator.value.lower()
                    assert lowered not in {
                        "broken",
                        "degraded",
                        "retire",
                        "confirmed=true",
                        "permission denied",
                        "procedure broken",
                    }
