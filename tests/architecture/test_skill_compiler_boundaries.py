"""Architecture-boundary tests for the N2.08 skill-compiler orchestrator.

N2.08 is the canonical top-level composition layer over the existing learning
stages. The canonical manifest has no ``(LEARNING, PROCEDURES)`` edge, so no
canonical subsystem may legally chain those stages into a procedure candidate.
Following the accepted M4.01 ruling, the orchestrator lives at the ``agentx``
namespace root - which the boundary checker treats as "not a subsystem" - and
the manifest is NOT widened.

These tests pin that placement and prove the composition smuggled in no
forbidden dependency, authority, execution, lifecycle, or persistence path, and
duplicated no canonical contract.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_MODULE_PATH = _AGENTX_SRC / "skill_compiler.py"
_LEARNING_PKG = _AGENTX_SRC / "learning"
_PROCEDURES_PKG = _AGENTX_SRC / "procedures"

_CANONICAL_AGENTX_IMPORTS = frozenset(
    {
        "agentx.core.causal_experience",
        "agentx.core.episodes",
        "agentx.core.ids",
        "agentx.core.procedures",
        "agentx.learning.causal_actions",
        "agentx.learning.irrelevant_actions",
        "agentx.learning.parameter_extraction",
        "agentx.learning.parameter_generalization",
        "agentx.learning.region_classification",
        "agentx.learning.trajectory",
        "agentx.procedure_synthesis",
        "agentx.procedures.graph",
    }
)

_CANONICAL_STDLIB_ROOTS = frozenset(
    {
        "__future__",
        "collections",
        "dataclasses",
        "enum",
        "json",
        "typing",
        "uuid",
    }
)

_CANONICAL_STAGE_CALLS = (
    "normalize_trajectory",
    "extract_causal_action_candidates",
    "analyze_irrelevant_actions",
    "extract_parameter_candidates",
    "analyze_parameter_generalization",
    "classify_regions",
    "synthesize_procedure_candidate",
)


def _tree(path: Path = _MODULE_PATH) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path = _MODULE_PATH) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def _defined_classes(path: Path = _MODULE_PATH) -> set[str]:
    return {node.name for node in ast.walk(_tree(path)) if isinstance(node, ast.ClassDef)}


def _defined_functions(path: Path = _MODULE_PATH) -> set[str]:
    return {node.name for node in ast.walk(_tree(path)) if isinstance(node, ast.FunctionDef)}


def _called_names(path: Path = _MODULE_PATH) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


def _called_attributes(path: Path = _MODULE_PATH) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _referenced_names(path: Path = _MODULE_PATH) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def _class_definition_paths(name: str) -> list[Path]:
    return [
        path.relative_to(_SRC_ROOT)
        for path in sorted(_AGENTX_SRC.rglob("*.py"))
        if name in _defined_classes(path)
    ]


def _function_definition_paths(name: str) -> list[Path]:
    return [
        path.relative_to(_SRC_ROOT)
        for path in sorted(_AGENTX_SRC.rglob("*.py"))
        if name in _defined_functions(path)
    ]


# ---------------------------------------------------------------------------
# Placement.
# ---------------------------------------------------------------------------


def test_compiler_is_a_single_top_level_composition_module() -> None:
    """N2.08 exists exactly once, directly under the ``agentx`` namespace root."""
    assert _MODULE_PATH.is_file()
    assert _MODULE_PATH.parent == _AGENTX_SRC
    assert "compile_skill_candidate" in _defined_functions()

    from agentx import skill_compiler

    assert skill_compiler.SkillCompilationResult.__module__ == "agentx.skill_compiler"
    assert skill_compiler.CompilerStage.__module__ == "agentx.skill_compiler"


def test_compiler_is_not_inside_any_canonical_subsystem() -> None:
    """The composition root sits outside every subsystem it composes."""
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        assert not _MODULE_PATH.is_relative_to(package)


def test_compiler_did_not_create_a_new_top_level_subsystem() -> None:
    """No new package/subsystem was introduced for N2.08."""
    assert "agentx.skill_compiler" not in _architecture.SUBSYSTEMS
    assert not (_AGENTX_SRC / "skill_compiler").exists()
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


def test_boundary_manifest_was_not_widened() -> None:
    """The forbidden edges were NOT added; the allowed edge set stays canonical."""
    assert (
        _architecture.LEARNING,
        _architecture.PROCEDURES,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES
    assert (
        _architecture.LEARNING,
        _architecture.CAPABILITIES,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES
    assert (
        _architecture.LEARNING,
        _architecture.KERNEL,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES
    assert (
        _architecture.LEARNING,
        _architecture.INFRASTRUCTURE,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES
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


def test_learning_stages_stay_isolated_from_later_stages() -> None:
    """Composition did not give learning modules a procedures/synthesis edge."""
    offenders: list[tuple[str, str]] = []
    for path in sorted(_LEARNING_PKG.rglob("*.py")):
        for imported in _imports(path):
            if (
                imported == "agentx.procedures"
                or imported.startswith("agentx.procedures.")
                or imported in {"agentx.procedure_synthesis", "agentx.skill_compiler"}
            ):
                offenders.append((str(path.relative_to(_SRC_ROOT)), imported))
    assert offenders == []


def test_procedures_still_does_not_depend_on_learning() -> None:
    """No reverse edge was smuggled in either direction."""
    offenders: list[tuple[str, str]] = []
    for path in sorted(_PROCEDURES_PKG.rglob("*.py")):
        for imported in _imports(path):
            if imported == "agentx.learning" or imported.startswith("agentx.learning."):
                offenders.append((str(path.relative_to(_SRC_ROOT)), imported))
    assert offenders == []


def test_no_subsystem_imports_the_composition_root() -> None:
    """Composition depends on the layers; the layers never depend on composition."""
    offenders: list[str] = []
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        for path in sorted(package.rglob("*.py")):
            if "agentx.skill_compiler" in _imports(path):
                offenders.append(str(path.relative_to(_SRC_ROOT)))
    assert offenders == []


# ---------------------------------------------------------------------------
# Composition, not redefinition.
# ---------------------------------------------------------------------------


def test_compiler_consumes_exactly_the_canonical_contracts() -> None:
    """N2.08 composes the real canonical modules; nothing more, nothing less."""
    agentx_imports = {name for name in _imports() if name.startswith("agentx.")}
    assert agentx_imports == set(_CANONICAL_AGENTX_IMPORTS)


def test_compiler_uses_only_an_inert_stdlib_surface() -> None:
    """Only serialization/containers/typing roots; no os/sys/random/time/subprocess."""
    roots: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and not node.module.startswith("agentx.")
        ):
            roots.add(node.module.split(".", 1)[0])
    assert roots == set(_CANONICAL_STDLIB_ROOTS)


def test_compiler_calls_every_canonical_stage_entry_point() -> None:
    """Each canonical stage is invoked through its exact canonical function."""
    called = _called_names()
    for stage_function in _CANONICAL_STAGE_CALLS:
        assert stage_function in called, stage_function


@pytest.mark.parametrize("stage_function", _CANONICAL_STAGE_CALLS)
def test_canonical_stage_functions_keep_exactly_one_definition(stage_function: str) -> None:
    """No stage entry point was reimplemented inside the orchestrator."""
    definitions = _function_definition_paths(stage_function)
    assert len(definitions) == 1
    assert Path("agentx/skill_compiler.py") not in definitions


@pytest.mark.parametrize(
    ("contract", "owner"),
    [
        ("NormalizedTrajectory", "agentx/learning/trajectory.py"),
        ("NormalizedTrajectoryStep", "agentx/learning/trajectory.py"),
        ("CausalActionExtraction", "agentx/learning/causal_actions.py"),
        ("ExtractedActionCandidate", "agentx/learning/causal_actions.py"),
        ("IrrelevantActionAnalysis", "agentx/learning/irrelevant_actions.py"),
        ("ActionEliminationDecision", "agentx/learning/irrelevant_actions.py"),
        ("ParameterExtraction", "agentx/learning/parameter_extraction.py"),
        ("ParameterGeneralization", "agentx/learning/parameter_generalization.py"),
        ("RegionClassificationAnalysis", "agentx/learning/region_classification.py"),
        ("ClassifiedRegion", "agentx/learning/region_classification.py"),
        ("SynthesisResult", "agentx/procedure_synthesis.py"),
        ("SynthesisBounds", "agentx/procedure_synthesis.py"),
        ("ProcedureGraph", "agentx/procedures/graph.py"),
        ("ProcedureNode", "agentx/procedures/graph.py"),
        ("ProcedureStatus", "agentx/core/procedures.py"),
        ("ProcedureRecord", "agentx/core/procedures.py"),
        ("ProcedureId", "agentx/core/ids.py"),
        ("CausalExperience", "agentx/core/causal_experience.py"),
        ("CausalOutcome", "agentx/core/causal_experience.py"),
    ],
)
def test_canonical_contracts_keep_exactly_one_definition(contract: str, owner: str) -> None:
    """N2.08 duplicated no canonical contract anywhere in the tree."""
    assert _class_definition_paths(contract) == [Path(owner)]


def test_compiler_defines_only_report_types() -> None:
    """Only compiler-report vocabulary is defined; no stage contract is remade."""
    assert _defined_classes() == {
        "SkillCompilerError",
        "CompilerStage",
        "EvidenceStatus",
        "EvidenceRole",
        "SkillCompilationOutcome",
        "CompilerRejectionReason",
        "EvidenceAssessment",
        "RunEvidence",
        "SkillCompilationResult",
    }


# ---------------------------------------------------------------------------
# Authority, execution, lifecycle, and persistence exclusion.
# ---------------------------------------------------------------------------


def test_no_kernel_capability_cognition_hive_or_infrastructure() -> None:
    """The composer reaches no authority, execution, model, memory, or store layer."""
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.agent_loop",
        "agentx.episode_retrieval",
        "agentx.execution_episode",
        "agentx.capability_strategy",
    )
    for imported in _imports():
        assert not imported.startswith(forbidden_prefixes), imported


def test_no_validation_promotion_or_lifecycle_path() -> None:
    """The pipeline stops at candidate construction; nothing downstream is called."""
    forbidden_imports = {
        "agentx.procedure_validation",
        "agentx.procedure_degradation",
        "agentx.core.procedure_lifecycle",
        "agentx.core.procedure_execution",
        "agentx.core.procedure_matching",
        "agentx.core.procedure_replacement",
        "agentx.procedures.interpreter",
    }
    assert _imports().isdisjoint(forbidden_imports)
    forbidden_references = {
        "ProcedureStore",
        "procedure_store",
        "ProcedureRecord",
        "ProcedureLifecycle",
        "update_status",
        "activate",
        "promote",
        "validate_procedure",
        "persist",
        "save",
        "insert",
        "write_text",
        "write_bytes",
        "mkdir",
    }
    assert _referenced_names().isdisjoint(forbidden_references)


def test_no_dynamic_execution_or_nondeterminism() -> None:
    """No eval/exec/open, no uuid/time/random/subprocess calls anywhere."""
    forbidden_calls = {"eval", "exec", "compile", "open", "__import__", "breakpoint"}
    assert _called_names().isdisjoint(forbidden_calls)
    forbidden_attributes = {
        "uuid4",
        "utcnow",
        "now",
        "today",
        "timestamp",
        "random",
        "randint",
        "randrange",
        "choice",
        "shuffle",
        "seed",
        "system",
        "popen",
        "run",
        "check_output",
        "check_call",
        "getenv",
        "putenv",
        "monotonic",
        "perf_counter",
        "sleep",
    }
    assert _called_attributes().isdisjoint(forbidden_attributes)
    forbidden_references = {
        "datetime",
        "date",
        "uuid4",
        "uuid5",
        "random",
        "subprocess",
        "os",
        "sys",
        "time",
        "socket",
        "pathlib",
        "importlib",
        "threading",
        "asyncio",
    }
    assert _referenced_names().isdisjoint(forbidden_references)


def test_no_new_runtime_dependency_was_introduced() -> None:
    """The runtime package still declares zero third-party dependencies."""
    manifest = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["project"]["dependencies"] == []
