"""Architecture guards for the M5.02 repair-patch proposal contract.

``agentx.core.repair_patch`` is a pure inward ``agentx.core`` data contract.
These static guards prove it stays that way:

* it imports only ``agentx.core.*`` and a tiny inert standard-library set —
  transitively, so no outward subsystem can be reached through a re-export;
* it never imports the Procedure Graph (``ProcedureGraph``, ``ProcedureNode``,
  ACTION/branch node classes, the interpreter), keeping ``agentx.core``
  independent of ``agentx.procedures``;
* it exposes no dynamic-execution, filesystem, database, or network API and no
  generation/application/selection entry point;
* it adds no persistence, migration, store, ranking, scoring, or model
  surface;
* the canonical C4.04 ``RepairCandidate`` contract remains unique and
  untouched — this module consumes it by value and never competes with it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_MODULE = _SRC_ROOT / "agentx" / "core" / "repair_patch.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_CANDIDATES = (_SRC_ROOT / "agentx" / "core" / "repair_candidates.py").read_text(encoding="utf-8")
_DIAGNOSIS = (_SRC_ROOT / "agentx" / "core" / "failure_diagnosis.py").read_text(encoding="utf-8")
_LOCALIZATION = (_SRC_ROOT / "agentx" / "core" / "failure_localization.py").read_text(
    encoding="utf-8"
)
_PROCEDURES = (_SRC_ROOT / "agentx" / "core" / "procedures.py").read_text(encoding="utf-8")

_OUTWARD_PACKAGES = (
    "agentx.procedures",
    "agentx.learning",
    "agentx.capabilities",
    "agentx.kernel",
    "agentx.hive",
    "agentx.cognition",
    "agentx.infrastructure",
)

_ALLOWED_STDLIB = {
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "json",
    "math",
    "types",
    "typing",
}


def _tree(source: str = _SOURCE) -> ast.Module:
    return ast.parse(source)


def _imports(source: str = _SOURCE) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree(source)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def _classes(source: str = _SOURCE) -> set[str]:
    return {node.name for node in ast.walk(_tree(source)) if isinstance(node, ast.ClassDef)}


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


def _module_path(module_name: str) -> Path | None:
    candidate = _SRC_ROOT / Path(*module_name.split(".")).with_suffix(".py")
    if candidate.is_file():
        return candidate
    package = _SRC_ROOT / Path(*module_name.split(".")) / "__init__.py"
    if package.is_file():
        return package
    return None


def _transitive_agentx_imports(start: str) -> set[str]:
    """Return every ``agentx`` module reachable from ``start`` by import."""
    seen: set[str] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        path = _module_path(current)
        if path is None:
            continue
        for imported in _imports(path.read_text(encoding="utf-8")):
            if imported.startswith("agentx"):
                pending.append(imported)
    return seen


# ---------------------------------------------------------------------------
# Placement: an inward core leaf
# ---------------------------------------------------------------------------


def test_contract_lives_in_core_and_is_an_inward_leaf() -> None:
    assert _MODULE.is_file()

    agentx_imports = {name for name in _imports() if name.startswith("agentx")}

    assert agentx_imports == {"agentx.core.ids", "agentx.core.repair_candidates"}
    for foreign in _OUTWARD_PACKAGES:
        assert not any(name.startswith(foreign) for name in agentx_imports)


def test_transitive_import_closure_never_leaves_agentx_core() -> None:
    closure = _transitive_agentx_imports("agentx.core.repair_patch")

    assert closure
    for module in closure:
        assert module == "agentx.core" or module.startswith("agentx.core.")
    for foreign in _OUTWARD_PACKAGES:
        assert not any(module.startswith(foreign) for module in closure)


def test_contract_uses_only_inert_standard_library_modules() -> None:
    non_agentx = {name.split(".")[0] for name in _imports() if not name.startswith("agentx")}

    assert non_agentx <= _ALLOWED_STDLIB


def test_core_never_learns_about_the_procedure_graph() -> None:
    imported_symbols: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ImportFrom):
            imported_symbols.update(alias.name for alias in node.names)

    for graph_symbol in (
        "ProcedureGraph",
        "ProcedureNode",
        "ProcedureNodeId",
        "ProcedureNodeKind",
        "ProcedureEdge",
        "ActionNode",
        "BranchNode",
        "ConditionNode",
        "Interpreter",
        "ProcedureInterpreter",
        "ProcedureRecord",
        "ProcedureStore",
    ):
        assert graph_symbol not in imported_symbols
        assert graph_symbol not in _classes()


# ---------------------------------------------------------------------------
# No execution, IO, persistence, or policy machinery
# ---------------------------------------------------------------------------


def test_no_execution_dynamic_import_network_or_file_side_effect_primitives() -> None:
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
        "hashlib",
        "threading",
        "ctypes",
        "shutil",
        "tempfile",
        "marshal",
        "code",
        "codeop",
        "runpy",
    }
    forbidden_calls = {
        "eval",
        "exec",
        "compile",
        "open",
        "urlopen",
        "system",
        "popen",
        "spawn",
        "execute",
        "apply",
        "apply_patch",
        "generate",
        "validate",
        "verify",
        "activate",
        "promote",
        "publish",
        "transition",
        "grant",
        "revoke",
        "rollback",
        "escalate",
        "select",
        "rank",
        "score",
        "now",
        "today",
        "utcnow",
        "shuffle",
        "choice",
        "randint",
        "seed",
    }

    assert _imports().isdisjoint(forbidden_imports)
    assert "__import__" not in _called_names()
    assert _called_names().isdisjoint(forbidden_calls)
    assert "__subclasses__" not in _SOURCE
    assert "globals()" not in _SOURCE
    assert "getattr(" not in _SOURCE
    assert "setattr(" not in _SOURCE.replace("object.__setattr__(", "")


def test_contract_adds_no_persistence_store_or_migration_surface() -> None:
    for token in (
        "SQLiteDatabase",
        "_Migration",
        "agentx_schema_migrations",
        "CREATE TABLE",
        "RepairPatchStore",
        "ProposalStore",
    ):
        assert token not in _SOURCE

    persistence_source = (_SRC_ROOT / "agentx" / "infrastructure" / "persistence.py").read_text(
        encoding="utf-8"
    )
    migration_versions = [
        int(match) for match in re.findall(r"^\s*version=(\d+),", persistence_source, re.MULTILINE)
    ]

    # M5.02 is a pure core contract: the migration ladder is untouched.
    assert migration_versions == sorted(migration_versions)
    assert max(migration_versions) == 8
    assert "repair_patch" not in persistence_source
    assert "agentx_repair_patches" not in persistence_source


def test_contract_carries_no_ranking_scoring_confidence_or_model_machinery() -> None:
    lowered = _SOURCE.lower()

    for token in (
        "confidence=",
        "probability=",
        "score=",
        "weight=",
        "ranking=",
        "embedding=",
        "likelihood",
        "openai",
        "anthropic",
        "model_provider",
        "tokenizer",
        "embeddings",
        "vector_store",
        "system_prompt",
        "completion(",
    ):
        assert token not in lowered


def test_contract_defines_data_types_not_engines() -> None:
    classes = _classes()

    assert {"RepairPatchKind", "RepairPatchProposal"} <= classes
    assert classes == {
        "RepairPatchKind",
        "RepairPatchProposal",
        "RepairPatchProposalValidationError",
        "RepairPatchProposalDeserializationError",
        "UnsupportedRepairPatchProposalSchemaVersionError",
        "_PayloadBudget",
    }
    forbidden = {
        "PatchGenerator",
        "PatchApplier",
        "PatchValidator",
        "PatchSelector",
        "PatchRanker",
        "RepairEngine",
        "RepairPlanner",
        "RepairExecutor",
        "ShadowRunner",
        "ProcedureGraph",
        "ProcedureNode",
        "ActionGate",
        "AuthorityContext",
        "Permission",
        "RiskAssessment",
        "EmergencyStop",
        "RepairBudget",
        "RepairCandidate",
        "FailureDiagnosis",
    }
    assert classes.isdisjoint(forbidden)


def test_contract_exposes_no_module_level_function_at_all() -> None:
    module_level = {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }

    # No generation, application, validation, selection, or ranking entry
    # point exists — this contract is data only.
    assert module_level == set()


def test_record_exposes_no_application_or_authority_methods() -> None:
    forbidden_methods = {
        "apply",
        "apply_to",
        "generate",
        "validate",
        "verify",
        "approve",
        "authorize",
        "select",
        "rank",
        "score",
        "execute",
        "activate",
        "promote",
        "rollback",
        "retry",
        "patch",
        "repair",
        "grant",
        "revoke",
        "mark_applied",
        "mark_verified",
        "next_revision",
        "to_procedure_record",
    }
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                assert statement.name not in forbidden_methods


def test_proposal_declares_exact_fields_and_no_decision_fields() -> None:
    dataclass_fields: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ClassDef) and node.name == "RepairPatchProposal":
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    dataclass_fields.add(statement.target.id)

    assert dataclass_fields == {
        "kind",
        "candidate",
        "target_procedure_id",
        "target_revision",
        "target_node_id",
        "proposed_definition",
        "proposed_at",
        "schema_version",
    }
    for decision_field in (
        "approved",
        "verified",
        "safe",
        "authorized",
        "applied",
        "selected",
        "valid",
        "active",
        "score",
        "rank",
        "confidence",
        "probability",
        "proposed_by",
    ):
        assert decision_field not in dataclass_fields


def test_patch_kind_vocabulary_is_closed_and_narrow() -> None:
    members: list[str] = []
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ClassDef) and node.name == "RepairPatchKind":
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    for target in statement.targets:
                        if isinstance(target, ast.Name):
                            members.append(target.id)

    assert members == ["NODE_DEFINITION_REPLACEMENT"]


def test_contract_creates_no_competing_error_or_identifier_hierarchy() -> None:
    exception_bases: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name):
                exception_bases.add(base.id)

    assert "AgentXError" not in _classes()
    assert "ProcedureId" not in _classes()
    assert "RepairPatchId" not in _classes()
    assert "Exception" not in exception_bases
    assert "BaseException" not in exception_bases
    assert {"ValueError"} <= exception_bases


# ---------------------------------------------------------------------------
# The canonical C4.04 contract stays unique and untouched
# ---------------------------------------------------------------------------


def test_existing_repair_candidate_contract_remains_unique() -> None:
    definitions: list[Path] = []
    for path in _SRC_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ClassDef) and node.name in {
                "RepairCandidate",
                "RepairCandidateKind",
            }:
                definitions.append(path)

    assert {path.name for path in definitions} == {"repair_candidates.py"}
    assert len(definitions) == 2


def test_repair_patch_proposal_contract_is_defined_exactly_once() -> None:
    definitions = [
        path
        for path in _SRC_ROOT.rglob("*.py")
        if any(
            isinstance(node, ast.ClassDef)
            and node.name in {"RepairPatchProposal", "RepairPatchKind"}
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    ]

    assert definitions == [_MODULE]


def test_upstream_contracts_do_not_know_about_this_module() -> None:
    for upstream in (_CANDIDATES, _DIAGNOSIS, _LOCALIZATION, _PROCEDURES):
        assert "repair_patch" not in upstream
        assert "RepairPatchProposal" not in upstream

    # The consumed C4.04 vocabulary is still where it was.
    assert "class RepairCandidate" in _CANDIDATES
    assert "NODE_DEFINITION_REVISION" in _CANDIDATES


def test_module_consumes_the_candidate_by_value_and_re_exports_nothing() -> None:
    exported: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    value = node.value
                    assert isinstance(value, ast.List)
                    for element in value.elts:
                        assert isinstance(element, ast.Constant)
                        assert isinstance(element.value, str)
                        exported.add(element.value)

    assert exported
    for foreign in ("RepairCandidate", "RepairCandidateKind", "FailureDiagnosis", "ProcedureId"):
        assert foreign not in exported


def test_docs_page_exists_for_the_proposal_contract() -> None:
    docs = _REPO_ROOT / "docs" / "repair_patch.md"
    assert docs.is_file()

    text = docs.read_text(encoding="utf-8")
    for token in (
        "M5.02",
        "NODE_DEFINITION_REPLACEMENT",
        "proposal != applied",
        "target binding",
        "provenance",
        "payload",
        "Zero new runtime dependencies",
        "Persistence decision",
    ):
        assert token in text
