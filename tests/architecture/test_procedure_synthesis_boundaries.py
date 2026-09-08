"""Architecture-boundary tests for the M4.01 procedure-synthesis composition root.

M4.01 reads canonical learning-analysis outputs and produces canonical
``ProcedureGraph`` DATA. The canonical manifest has no ``(LEARNING,
PROCEDURES)`` edge, so no canonical subsystem may legally perform this
composition. Per the task ruling the manifest is NOT widened; instead the
builder lives at the ``agentx`` namespace root, which the boundary checker
explicitly treats as "not a subsystem", alongside ``agent_loop``.

These tests pin that decision and prove it smuggled in no forbidden
dependency, authority, execution, or persistence by another route.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_MODULE_PATH = _AGENTX_SRC / "procedure_synthesis.py"
_LEARNING_PKG = _AGENTX_SRC / "learning"
_PROCEDURES_PKG = _AGENTX_SRC / "procedures"

_CANONICAL_AGENTX_IMPORTS = frozenset(
    {
        "agentx.core.causal_experience",
        "agentx.core.events",
        "agentx.core.ids",
        "agentx.core.procedures",
        "agentx.learning.causal_actions",
        "agentx.learning.irrelevant_actions",
        "agentx.learning.parameter_extraction",
        "agentx.learning.parameter_generalization",
        "agentx.learning.region_classification",
        "agentx.learning.trajectory",
        "agentx.procedures.graph",
        "agentx.procedures.reason_research",
    }
)

_CANONICAL_STDLIB_ROOTS = frozenset(
    {
        "__future__",
        "hashlib",
        "json",
        "re",
        "collections",
        "dataclasses",
        "enum",
        "typing",
        "uuid",
    }
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


def _called_names(path: Path = _MODULE_PATH) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
    return names


def _called_attributes(path: Path = _MODULE_PATH) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
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


# ---------------------------------------------------------------------------
# Placement.
# ---------------------------------------------------------------------------


def test_synthesis_is_a_single_top_level_composition_module() -> None:
    """M4.01 exists exactly once, directly under the ``agentx`` namespace root."""
    assert _MODULE_PATH.is_file()
    assert _MODULE_PATH.parent == _AGENTX_SRC
    assert "synthesize_procedure_candidate" in {
        node.name for node in ast.walk(_tree()) if isinstance(node, ast.FunctionDef)
    }
    from agentx import procedure_synthesis

    assert procedure_synthesis.SynthesisResult.__module__ == "agentx.procedure_synthesis"
    assert procedure_synthesis.SynthesisOutcome.__module__ == "agentx.procedure_synthesis"


def test_synthesis_is_not_inside_any_canonical_subsystem() -> None:
    """The composition root sits outside every subsystem it composes."""
    for subsystem in _architecture.SUBSYSTEMS:
        package = _SRC_ROOT / Path(*subsystem.split("."))
        assert not _MODULE_PATH.is_relative_to(package)


def test_synthesis_did_not_create_a_new_top_level_subsystem() -> None:
    """No new package/subsystem was introduced for M4.01."""
    assert "agentx.procedure_synthesis" not in _architecture.SUBSYSTEMS
    assert not (_AGENTX_SRC / "procedure_synthesis").exists()
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
    """The forbidden edge was NOT added; the allowed edge set is exactly canonical."""
    assert (
        _architecture.LEARNING,
        _architecture.PROCEDURES,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES
    assert (
        _architecture.PROCEDURES,
        _architecture.LEARNING,
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


def test_learning_still_does_not_depend_on_procedures() -> None:
    """The invariant the ruling protects: no learning -> procedures import."""
    offenders: list[tuple[str, str]] = []
    for path in sorted(_LEARNING_PKG.rglob("*.py")):
        for imported in _imports(path):
            if imported == "agentx.procedures" or imported.startswith("agentx.procedures."):
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
            if "agentx.procedure_synthesis" in _imports(path):
                offenders.append(str(path.relative_to(_SRC_ROOT)))
    assert offenders == []


# ---------------------------------------------------------------------------
# Composition, not redefinition.
# ---------------------------------------------------------------------------


def test_synthesis_consumes_exactly_the_canonical_contracts() -> None:
    """M4.01 composes the real canonical modules; nothing more, nothing less."""
    agentx_imports = {name for name in _imports() if name.startswith("agentx.")}
    assert agentx_imports == set(_CANONICAL_AGENTX_IMPORTS)


def test_synthesis_uses_only_an_inert_stdlib_surface() -> None:
    """Only hashing/serialization/containers/typing roots; no os/sys/random/time."""
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


@pytest.mark.parametrize(
    ("contract", "owner"),
    [
        ("ProcedureGraph", "agentx/procedures/graph.py"),
        ("ProcedureNode", "agentx/procedures/graph.py"),
        ("ProcedureNodeKind", "agentx/procedures/graph.py"),
        ("ReasonNodeSpec", "agentx/procedures/reason_research.py"),
        ("EndNodeSpec", "agentx/procedures/end.py"),
        ("NormalizedTrajectory", "agentx/learning/trajectory.py"),
        ("CausalActionExtraction", "agentx/learning/causal_actions.py"),
        ("IrrelevantActionAnalysis", "agentx/learning/irrelevant_actions.py"),
        ("ParameterExtraction", "agentx/learning/parameter_extraction.py"),
        ("ParameterGeneralization", "agentx/learning/parameter_generalization.py"),
        ("RegionClassificationAnalysis", "agentx/learning/region_classification.py"),
        ("ProcedureStatus", "agentx/core/procedures.py"),
        ("ProcedureId", "agentx/core/ids.py"),
        ("CausalExperience", "agentx/core/causal_experience.py"),
        ("ActionPayload", "agentx/core/events.py"),
    ],
)
def test_canonical_contracts_keep_exactly_one_definition(contract: str, owner: str) -> None:
    """M4.01 duplicated no canonical contract anywhere in the tree."""
    assert _class_definition_paths(contract) == [Path(owner)]


def test_synthesis_invents_no_node_family() -> None:
    """Only synthesis-report types are defined; no node/graph contract is remade."""
    assert _defined_classes() == {
        "SynthesisError",
        "SynthesisOutcome",
        "SynthesisRejectionReason",
        "SynthesisBounds",
        "IncludedAction",
        "ExcludedAction",
        "SynthesizedParameter",
        "SynthesisResult",
        "_Failure",
        "_RetainedActionEvidence",
    }
    source = _MODULE_PATH.read_text(encoding="utf-8")
    assert "ProcedureNodeKind.RESEARCH" not in source
    assert "ProcedureNodeKind.BRANCH" not in source


# ---------------------------------------------------------------------------
# Authority, execution, and persistence exclusion.
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
    )
    for imported in _imports():
        assert not imported.startswith(forbidden_prefixes), imported


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
        "setenv",
        "monotonic",
        "perf_counter",
        "sleep",
    }
    assert _called_attributes().isdisjoint(forbidden_attributes)
    # AST-level: docstring prose may name these, but no code may reference them.
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
    }
    assert _referenced_names().isdisjoint(forbidden_references)


def test_no_persistence_or_procedure_store() -> None:
    """Synthesis builds data; it never stores, updates status, or writes files."""
    forbidden_references = {
        "ProcedureStore",
        "procedure_store",
        "update_status",
        "persist",
        "save",
        "write_text",
        "mkdir",
        "write_bytes",
        "ProcedureRecord",
    }
    assert _referenced_names().isdisjoint(forbidden_references)
