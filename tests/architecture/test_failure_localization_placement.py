"""Architecture guards for the C4.02 canonical failure-localization contract.

The failure-localization module is a pure inward ``agentx.core`` data contract.
These static guards prove it stays that way: no outward subsystem imports, no
persistence or migration surface, no execution/diagnosis/repair machinery, no
keyword/model inference, and no competing identifier types.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "core" / "failure_localization.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TAXONOMY = (_REPO_ROOT / "src" / "agentx" / "core" / "failure_taxonomy.py").read_text(
    encoding="utf-8"
)


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


def test_contract_lives_in_core_and_is_an_inward_leaf() -> None:
    assert _MODULE.is_file()

    agentx_imports = {name for name in _imports() if name.startswith("agentx")}

    assert agentx_imports == {"agentx.core.failure_taxonomy", "agentx.core.ids"}
    for foreign in (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.hive",
        "agentx.procedures",
        "agentx.learning",
        "agentx.infrastructure",
    ):
        assert not any(name.startswith(foreign) for name in agentx_imports)


def test_contract_uses_only_inert_standard_library_modules() -> None:
    non_agentx = {name.split(".")[0] for name in _imports() if not name.startswith("agentx")}

    assert non_agentx <= {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "json",
        "typing",
        "uuid",
    }


def test_c402_defines_data_contracts_not_execution_diagnosis_or_policy_subsystems() -> None:
    classes = _classes()
    forbidden = {
        "ActionGate",
        "AuthorityContext",
        "Capability",
        "Executor",
        "Reasoner",
        "Router",
        "TaskManager",
        "Verifier",
        "Permission",
        "RiskAssessment",
        "ResourceEnvelope",
        "EmergencyStop",
        "KnowledgeRecord",
        "ProcedureRecord",
        "FailureClassifier",
        "FailureDiagnoser",
        "FailureLocalizer",
        "RepairPlanner",
        "RetryPolicy",
        "FallbackPolicy",
        "EscalationPolicy",
        "FailureLocalizationStore",
        "CausalExperience",
        "NormalizedTrajectory",
        "ProcedureGraph",
        "ProcedureNode",
        "ProcedureNodeId",
    }

    assert classes.isdisjoint(forbidden)
    assert {
        "FailureLocationKind",
        "LocalizationEvidence",
        "FailureLocalization",
    } <= classes


def test_c402_creates_no_competing_error_or_id_hierarchy() -> None:
    exception_bases: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name):
                exception_bases.add(base.id)

    assert "AgentXError" not in _classes()
    assert "AgentXException" not in _classes()
    assert "TaskId" not in _classes()
    assert "CapabilityId" not in _classes()
    assert "ProcedureId" not in _classes()
    assert "ProcedureNodeId" not in _classes()
    assert "AgentXError" not in exception_bases
    assert "Exception" not in exception_bases
    assert "BaseException" not in exception_bases
    assert {"ValueError"} <= exception_bases


def test_c402_exposes_only_the_pure_localize_packaging_entry_point() -> None:
    # Module-level public functions only (exclude methods nested under classes).
    module_level = {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }

    assert module_level == {"localize_failure"}
    forbidden_functions = {
        "classify",
        "classify_failure",
        "diagnose",
        "infer_category",
        "infer_location",
        "repair",
        "retry",
        "fallback",
        "escalate",
        "suppress",
        "patch",
        "analyze_trajectory",
        "query_store",
        "embed",
        "score",
    }

    assert module_level.isdisjoint(forbidden_functions)
    assert _public_functions().isdisjoint(forbidden_functions)


def test_no_execution_dynamic_import_network_or_file_side_effect_primitives() -> None:
    forbidden_imports = {
        "importlib",
        "pickle",
        "subprocess",
        "socket",
        "sqlite3",
        "urllib",
        "os",
        "pathlib",
        "random",
        "re",
        "hashlib",
    }
    forbidden_calls = {
        "eval",
        "exec",
        "open",
        "urlopen",
        "execute",
        "verify",
        "reason",
        "publish",
        "transition",
        "activate",
        "promote",
        "retry",
        "repair",
        "grant",
        "revoke",
        "findall",
        "search",
        "match",
        "fullmatch",
        "sub",
    }

    assert _imports().isdisjoint(forbidden_imports)
    # __import__ appears only as a documented forbidden name in comments if at
    # all; ensure the live AST does not call it.
    assert "__import__" not in _called_names()
    assert _called_names().isdisjoint(forbidden_calls)


def test_c402_adds_no_persistence_or_migration_surface() -> None:
    assert "SQLiteDatabase" not in _SOURCE
    assert "_Migration" not in _SOURCE
    assert "agentx_schema_migrations" not in _SOURCE
    assert "CREATE TABLE" not in _SOURCE
    assert "FailureLocalizationStore" not in _SOURCE

    persistence_source = (
        _REPO_ROOT / "src" / "agentx" / "infrastructure" / "persistence.py"
    ).read_text(encoding="utf-8")
    migration_versions = [
        int(match) for match in re.findall(r"^\s*version=(\d+),", persistence_source, re.MULTILINE)
    ]

    # C4.02 is a pure core contract: it adds no migration of its own. The
    # ladder stays sorted and consecutive; the landed v1-v8 ladder stays
    # immutable. Later append-only store tasks may extend it (A8.01 owns v9,
    # create_strategy_performance_store; C7.07 owns v10,
    # create_event_watcher_state); nothing here rewrites history.
    assert migration_versions == sorted(migration_versions)
    assert migration_versions == list(range(1, len(migration_versions) + 1))
    assert migration_versions[:8] == list(range(1, 9))
    assert max(migration_versions) >= 9
    assert "failure_localization" not in persistence_source
    assert "agentx_failure_localization" not in persistence_source


def test_c402_carries_no_probabilistic_confidence_or_model_machinery() -> None:
    lowered = _SOURCE.lower()

    for token in (
        "confidence=",
        "probability=",
        "score=",
        "weight=",
        "likelihood",
        "openai",
        "anthropic",
        "model_provider",
        "tokenizer",
        "embeddings",
    ):
        assert token not in lowered

    # The module may document that it carries "no ... embedding ...", but it
    # must not define or assign embedding/confidence machinery.
    assert "embedding=" not in lowered
    assert "class " not in lowered or "Embedding" not in _SOURCE


def test_c402_does_not_implement_keyword_inference() -> None:
    # The module must not scan free text for location keywords. Mentions of
    # forbidden words may appear in documentation strings explaining the ban,
    # but there must be no membership/contains tests against error text.
    tree = _tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    lowered = comparator.value.lower()
                    assert lowered not in {
                        "permission",
                        "button",
                        "api",
                        "verify",
                        "traceback",
                    }


def test_c402_does_not_mutate_c401_taxonomy_module() -> None:
    assert "FailureCategory" in _TAXONOMY
    assert "FailureClassification" in _TAXONOMY
    assert "class FailureLocationKind" not in _TAXONOMY
    assert "localize_failure" not in _TAXONOMY
    # C4.01 still documents C4.02 as a later non-goal, not as owned surface.
    assert "failure localization (C4.02)" in _TAXONOMY


def test_c402_does_not_query_or_analyze_c210_or_c301() -> None:
    assert "causal_experience" not in _SOURCE
    assert "NormalizedTrajectory" not in _SOURCE
    assert "normalize_trajectory" not in _SOURCE
    assert "EpisodeStore" not in _SOURCE
    assert "NegativeExperienceStore" not in _SOURCE
    assert "ExperienceMemory" not in _SOURCE


def test_docs_page_exists_for_c402() -> None:
    docs = _REPO_ROOT / "docs" / "failure_localization.md"
    assert docs.is_file()
    text = docs.read_text(encoding="utf-8")
    assert "C4.02" in text
    assert "UNKNOWN" in text
    assert "orthogonal" in text.lower()
    assert "Persistence decision" in text
