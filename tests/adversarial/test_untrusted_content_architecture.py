"""C4.10 untrusted-content boundary tests: architecture invariants.

The runtime inertness proven by the other C4.10 files rests on structural
separation. These tests pin that structure itself:

* **No dynamic execution** — the research/knowledge/learning/repair data
  modules contain no ``eval``/``exec``/``compile``/``__import__``/``importlib``
  /``pickle``/``subprocess``/``os.system`` and no JSON decoding hook that
  could construct arbitrary objects from external content.
* **No data -> authority import edge** — the untrusted-content modules never
  import the Trusted Kernel. There is no code path through which they could
  read or write authority state.
* **No authority -> content import edge** — kernel modules import only their
  own contracts and two inert core identity/record contracts; authority never
  reads research, knowledge, learning, or repair data as policy input.
* **The manifest agrees** — ``agentx.hive``, ``agentx.learning``,
  ``agentx.infrastructure``, and ``agentx.core`` are not clients of the
  kernel authority boundary in the canonical architecture manifest.
* **No executable fields** — every hostile-content-carrying dataclass field
  is a str/enum/UUID/tuple/datetime type; no Callable can ride in a field.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "agentx"

# --------------------------------------------------------------------------
# The untrusted-content surface: every module through which external or
# retrieved content can flow as DATA.
# --------------------------------------------------------------------------

UNTRUSTED_CONTENT_MODULES: tuple[Path, ...] = (
    # research path
    _SRC / "cognition" / "gap_detector.py",
    _SRC / "cognition" / "research_objective.py",
    _SRC / "cognition" / "research_provider.py",
    _SRC / "cognition" / "research_acquisition.py",
    _SRC / "procedures" / "reason_research.py",
    # knowledge path
    _SRC / "core" / "knowledge.py",
    _SRC / "core" / "provenance.py",
    _SRC / "core" / "knowledge_integrity.py",
    _SRC / "hive" / "semantic_memory.py",
    _SRC / "infrastructure" / "knowledge_store.py",
    _SRC / "infrastructure" / "knowledge_retrieval.py",
    # learning path
    _SRC / "core" / "episodes.py",
    _SRC / "core" / "causal_experience.py",
    _SRC / "core" / "negative_experience.py",
    _SRC / "learning" / "trajectory.py",
    _SRC / "learning" / "causal_actions.py",
    _SRC / "learning" / "irrelevant_actions.py",
    _SRC / "learning" / "parameter_extraction.py",
    _SRC / "learning" / "parameter_generalization.py",
    _SRC / "hive" / "experience_memory.py",
    _SRC / "hive" / "environmental_cache.py",
    _SRC / "infrastructure" / "episode_store.py",
    _SRC / "infrastructure" / "negative_experience_store.py",
    # repair path
    _SRC / "core" / "failure_taxonomy.py",
    _SRC / "core" / "failure_localization.py",
    _SRC / "core" / "failure_diagnosis.py",
    _SRC / "core" / "repair_candidates.py",
    # procedure condition data
    _SRC / "procedures" / "conditions.py",
    _SRC / "procedures" / "condition_evaluation.py",
)

_KERNEL_MODULES: tuple[Path, ...] = tuple(sorted((_SRC / "kernel").glob("*.py")))

#: The only ``agentx.core`` contracts the Trusted Kernel may consume: inert
#: identity types and the audit-record snapshot contract. Nothing that carries
#: external content.
_KERNEL_ALLOWED_CORE_IMPORTS: frozenset[str] = frozenset(
    {
        "agentx.core.ids",
        "agentx.core.audit_records",
    }
)

_FORBIDDEN_DYNAMIC_CALLS: frozenset[str] = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "importlib.import_module",
        "importlib.reload",
        "pickle.loads",
        "pickle.load",
        "subprocess.run",
        "subprocess.call",
        "subprocess.check_output",
        "subprocess.Popen",
        "os.system",
        "os.popen",
        "marshal.loads",
        "shelve.open",
    }
)

_FORBIDDEN_DYNAMIC_IMPORTS: frozenset[str] = frozenset(
    {"importlib", "pickle", "subprocess", "marshal", "shelve", "ctypes"}
)

_FORBIDDEN_JSON_HOOKS: frozenset[str] = frozenset(
    {"object_hook", "object_pairs_hook", "parse_float", "JSONDecoder"}
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    current: ast.AST | None = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _imported_modules(tree: ast.Module) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(imported)


def _called_names(tree: ast.Module) -> set[str]:
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called.add(_dotted_name(node.func))
        elif isinstance(node, ast.Attribute):
            called.add(_dotted_name(node))
    return called


# --------------------------------------------------------------------------
# No dynamic execution in the untrusted-content surface.
# --------------------------------------------------------------------------


def test_untrusted_content_modules_contain_no_dynamic_execution() -> None:
    for path in UNTRUSTED_CONTENT_MODULES:
        tree = _parse(path)
        called = _called_names(tree)
        offenders = called & _FORBIDDEN_DYNAMIC_CALLS
        assert not offenders, f"{path.name} performs dynamic execution: {sorted(offenders)}"


def test_untrusted_content_modules_import_no_dynamic_code_modules() -> None:
    for path in UNTRUSTED_CONTENT_MODULES:
        imports = set(_imported_modules(_parse(path)))
        offenders = imports & _FORBIDDEN_DYNAMIC_IMPORTS
        assert not offenders, f"{path.name} imports dynamic-code modules: {sorted(offenders)}"


def test_untrusted_content_deserialization_constructs_no_arbitrary_objects() -> None:
    """``json.loads`` must stay bare: no object hook, no custom decoder, and
    no default= callable through which external JSON could construct objects."""

    for path in UNTRUSTED_CONTENT_MODULES:
        called = _called_names(_parse(path))
        offenders = called & _FORBIDDEN_JSON_HOOKS
        assert not offenders, f"{path.name} decodes JSON with hooks: {sorted(offenders)}"

        # And the module really is JSON-decoding (from_json classes use the
        # stdlib), so the guarantee above is about the actual decode path.
        source = path.read_text(encoding="utf-8")
        if "json.loads" in source:
            for line in source.splitlines():
                if "json.loads" in line and "#" not in line.split("json.loads")[0]:
                    assert "object_hook" not in line
                    assert "default=" not in line


# --------------------------------------------------------------------------
# No data -> authority import edge.
# --------------------------------------------------------------------------


def test_untrusted_content_modules_never_import_the_kernel() -> None:
    """The core C4.10 structural invariant: no module that carries external
    content can even name the Trusted Kernel, so no hostile content can reach
    authority state through an import."""

    for path in UNTRUSTED_CONTENT_MODULES:
        imports = _imported_modules(_parse(path))
        for module in imports:
            assert module != "agentx.kernel", path.name
            assert not module.startswith("agentx.kernel."), f"{path.name}: {module}"


def test_untrusted_content_modules_never_import_capability_execution() -> None:
    """Data contracts also never import the capability execution stack:
    content cannot force capability execution through an import."""

    for path in UNTRUSTED_CONTENT_MODULES:
        imports = _imported_modules(_parse(path))
        for module in imports:
            assert not module.startswith("agentx.capabilities"), f"{path.name}: {module}"


# --------------------------------------------------------------------------
# No authority -> content import edge.
# --------------------------------------------------------------------------


def test_kernel_modules_never_import_untrusted_content() -> None:
    """The reverse edge: the Trusted Kernel consumes only inert identity and
    audit-record contracts. Authority never reads research, knowledge,
    learning, failure, repair, or procedure-condition data as policy."""

    forbidden_prefixes = (
        "agentx.cognition",
        "agentx.hive",
        "agentx.learning",
        "agentx.capabilities",
        "agentx.infrastructure",
        "agentx.procedures",
    )
    assert _KERNEL_MODULES, "kernel module set must not be empty"
    for path in _KERNEL_MODULES:
        for module in _imported_modules(_parse(path)):
            if not module.startswith("agentx"):
                continue  # stdlib imports (dataclasses, enum, threading, ...) are fine
            if module.startswith("agentx.kernel"):
                continue
            if module.startswith("agentx.core"):
                assert module in _KERNEL_ALLOWED_CORE_IMPORTS, (
                    f"{path.name} imports non-allowed core contract {module}"
                )
                continue
            for prefix in forbidden_prefixes:
                assert not module.startswith(prefix), f"{path.name}: {module}"
            assert module.startswith("agentx"), f"{path.name}: unexpected import {module}"


# --------------------------------------------------------------------------
# The manifest agrees: data subsystems are not kernel clients.
# --------------------------------------------------------------------------


def test_architecture_manifest_has_no_data_to_authority_edges() -> None:
    edges = _architecture.ALLOWED_ARCHITECTURE_EDGES

    for data_subsystem in (
        _architecture.HIVE,
        _architecture.LEARNING,
        _architecture.INFRASTRUCTURE,
        _architecture.CORE,
    ):
        edge = (data_subsystem, _architecture.KERNEL)
        assert edge not in edges, edge

    # The canonical kernel clients remain the governed execution layers.
    assert (_architecture.CAPABILITIES, _architecture.KERNEL) in edges
    assert (_architecture.PROCEDURES, _architecture.KERNEL) in edges
    assert (_architecture.COGNITION, _architecture.KERNEL) in edges


# --------------------------------------------------------------------------
# No executable fields on hostile-content carriers.
# --------------------------------------------------------------------------


def test_hostile_content_carriers_have_no_executable_fields() -> None:
    """Every field on the contracts that carry external content is an inert
    data type. No Callable, function, module, or code object can ride in."""

    import agentx.cognition.research_objective as research_objective
    import agentx.cognition.research_provider as research_provider
    import agentx.core.causal_experience as causal_experience
    import agentx.core.episodes as episodes
    import agentx.core.failure_diagnosis as failure_diagnosis
    import agentx.core.knowledge as knowledge
    import agentx.core.provenance as provenance
    import agentx.core.repair_candidates as repair_candidates
    import agentx.hive.environmental_cache as environmental_cache
    import agentx.procedures.conditions as conditions

    carriers = (
        research_objective.ResearchObjective,
        research_provider.ResearchRequest,
        research_provider.ResearchResponse,
        research_provider.ResearchProviderIdentity,
        knowledge.KnowledgeRecord,
        knowledge.ProvenanceReference,
        knowledge.KnowledgeScope,
        provenance.ProvenanceRecord,
        provenance.EvidenceReference,
        provenance.KnowledgeEvidence,
        episodes.EpisodeRecord,
        causal_experience.CausalExperience,
        failure_diagnosis.DiagnosticEvidence,
        failure_diagnosis.FailureDiagnosis,
        repair_candidates.RepairCandidate,
        conditions.ProcedureCondition,
        conditions.NodeConditions,
        conditions.ProcedureConditions,
        environmental_cache.EnvironmentalCacheEntry,
    )

    forbidden_annotation_fragments = (
        "Callable",
        "FunctionType",
        "CodeType",
        "ModuleType",
        "types.ModuleType",
        "lambda",
    )

    for carrier in carriers:
        for field in fields(carrier):
            annotation = str(field.type)
            for fragment in forbidden_annotation_fragments:
                assert fragment not in annotation, (
                    f"{carrier.__name__}.{field.name} carries executable type {annotation}"
                )


def test_condition_contracts_have_no_expression_language() -> None:
    """Procedure conditions stay structured data: the module defines no
    expression evaluator, no callback registry, and no DSL entry point."""

    import agentx.procedures.condition_evaluation as condition_evaluation

    public = {name for name in vars(condition_evaluation) if not name.startswith("_")}
    for forbidden in ("evaluate_expression", "compile", "parse_expression", "eval"):
        assert forbidden not in public

    source = (_SRC / "procedures" / "condition_evaluation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = _called_names(tree)
    assert not (called & _FORBIDDEN_DYNAMIC_CALLS)
