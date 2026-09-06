"""Architecture guards for the C4.04 canonical environment-change contract.

The environment-change module is a pure inward ``agentx.core`` data contract.
These static guards prove it stays that way: no outward subsystem imports, no
persistence or migration surface, no environment sensing or world model, no
repair/patch/execution/selection machinery, no keyword or model interpretation,
no clock reads, no ranking or scoring, and no competing identifier, scope,
provenance, or error types. C4.01 (taxonomy), C4.02 (localization), C4.03
(diagnosis), the C2.02 scope/provenance contracts, and the historical C4.04
repair-candidate contract all remain untouched.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "core" / "environment_change.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TAXONOMY = (_REPO_ROOT / "src" / "agentx" / "core" / "failure_taxonomy.py").read_text(
    encoding="utf-8"
)
_LOCALIZATION = (_REPO_ROOT / "src" / "agentx" / "core" / "failure_localization.py").read_text(
    encoding="utf-8"
)
_DIAGNOSIS = (_REPO_ROOT / "src" / "agentx" / "core" / "failure_diagnosis.py").read_text(
    encoding="utf-8"
)
_REPAIR_CANDIDATES = (_REPO_ROOT / "src" / "agentx" / "core" / "repair_candidates.py").read_text(
    encoding="utf-8"
)
_KNOWLEDGE = (_REPO_ROOT / "src" / "agentx" / "core" / "knowledge.py").read_text(encoding="utf-8")


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


def _referenced_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
    return names


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

    assert agentx_imports == {
        "agentx.core.failure_diagnosis",
        "agentx.core.failure_localization",
        "agentx.core.failure_taxonomy",
        "agentx.core.knowledge",
        "agentx.core.provenance",
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
        assert not any(name.startswith(foreign) for name in agentx_imports)


def test_contract_uses_only_inert_standard_library_modules() -> None:
    non_agentx = {name.split(".")[0] for name in _imports() if not name.startswith("agentx")}

    assert non_agentx <= {
        "__future__",
        "collections",
        "dataclasses",
        "datetime",
        "enum",
        "itertools",
        "json",
        "types",
        "typing",
    }


def test_c404_defines_data_contracts_not_sensing_repair_or_policy_subsystems() -> None:
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
        "ProcedureGraph",
        "ProcedureNode",
        "ProcedureNodeId",
        "RepairPlanner",
        "RepairEngine",
        "RepairSelector",
        "PatchGenerator",
        "RetryPolicy",
        "FallbackPolicy",
        "EscalationPolicy",
        "FailureClassifier",
        "FailureLocalizer",
        "FailureDiagnoser",
        "FailureClassification",
        "FailureLocalization",
        "FailureDiagnosis",
        # No world model, sensor, or environment inventory may live here.
        "EnvironmentModel",
        "WorldModel",
        "EnvironmentState",
        "EnvironmentSnapshotStore",
        "EnvironmentSensor",
        "EnvironmentProbe",
        "EnvironmentScanner",
        "EnvironmentInventory",
        "EnvironmentChangeDetector",
        "EnvironmentChangeEngine",
        "EnvironmentChangeService",
        "EnvironmentChangeStore",
        "EnvironmentalCache",
        "EnvironmentalCacheEntry",
        "ScopeDimension",
        "KnowledgeScope",
        "ProvenanceReference",
        "ProvenanceKind",
        "EvidenceReference",
        "EvidenceKind",
    }

    assert classes.isdisjoint(forbidden)
    assert classes == {
        "EnvironmentChangeValidationError",
        "EnvironmentChangeDeserializationError",
        "UnsupportedEnvironmentChangeSchemaVersionError",
        "EnvironmentFactKind",
        "EnvironmentFactValueKind",
        "EnvironmentChangeResult",
        "EnvironmentChangeReason",
        "EnvironmentFactKey",
        "EnvironmentFactValue",
        "EnvironmentObservation",
        "EnvironmentSnapshot",
        "EnvironmentFactChange",
        "EnvironmentChangeDetection",
    }


def test_c404_creates_no_competing_error_id_or_scope_hierarchy() -> None:
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
    assert "ProcedureId" not in _classes()
    assert "AgentXError" not in exception_bases
    assert "Exception" not in exception_bases
    assert "BaseException" not in exception_bases
    assert {"ValueError"} <= exception_bases

    # Scope, provenance, and evidence come from the landed canonical contracts.
    assert "from agentx.core.knowledge import" in _SOURCE
    assert "from agentx.core.provenance import" in _SOURCE
    for reused in (
        "KnowledgeScope",
        "ScopeDimension",
        "ProvenanceReference",
        "ProvenanceKind",
    ):
        assert reused in _SOURCE
    assert "EvidenceReference" in _SOURCE
    assert "class ScopeDimension" not in _SOURCE
    assert "class KnowledgeScope" not in _SOURCE
    assert "class ProvenanceReference" not in _SOURCE
    assert "class EvidenceReference" not in _SOURCE


def test_c404_fact_vocabulary_is_anchored_to_landed_canonical_names() -> None:
    """The fact taxonomy indexes existing vocabularies; it is not a new model."""
    assert "CANONICAL_ENVIRONMENT_FACT_ANCHORS" in _SOURCE
    assert "ScopeDimension.OPERATING_SYSTEM.value" in _SOURCE
    assert "ScopeDimension.APPLICATION.value" in _SOURCE
    assert "ScopeDimension.APPLICATION_VERSION.value" in _SOURCE
    assert "FailureCategory.API_CHANGE.value" in _SOURCE
    assert "FailureCategory.UI_CHANGE.value" in _SOURCE
    assert "FailureLocationKind.CAPABILITY.value" in _SOURCE
    assert "FailureLocationKind.DEPENDENCY.value" in _SOURCE

    # The anchors must exist in the vocabularies they claim to index.
    for anchor in (
        '"os"',
        '"environment"',
        '"application"',
        '"application_version"',
        '"project"',
        '"context"',
    ):
        assert anchor in _KNOWLEDGE
    for anchor in ('"api_change"', '"ui_change"', '"capability"', '"dependency"'):
        assert anchor in _TAXONOMY or anchor in _LOCALIZATION


def test_c404_reproduces_the_c209_freshness_rule_without_importing_the_hive() -> None:
    cache_source = (_REPO_ROOT / "src" / "agentx" / "hive" / "environmental_cache.py").read_text(
        encoding="utf-8"
    )

    # Same rule: expires_at = observed_at + ttl, fresh exactly while at < expires_at.
    assert "self.observed_at + self.ttl" in _SOURCE
    assert "self.observed_at + self.ttl" in cache_source
    assert "moment < self.expires_at" in _SOURCE
    assert "moment < self.expires_at" in cache_source
    # ... but core stays an inward leaf: the cache is never imported, so the
    # rule is reproduced by value (the docstring may *name* the hive module).
    assert not any(name.startswith("agentx.hive") for name in _imports())
    assert "EnvironmentalCache" not in _classes()
    assert "class EnvironmentalCache" not in _SOURCE


def test_c404_exposes_only_the_pure_comparison_entry_point() -> None:
    module_level = {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }

    assert module_level == {"detect_environment_change"}
    forbidden_functions = {
        "sense",
        "sense_environment",
        "observe",
        "observe_environment",
        "probe",
        "scan",
        "collect_environment",
        "build_world_model",
        "refresh",
        "repair",
        "generate_patch",
        "apply_patch",
        "patch",
        "retry",
        "fallback",
        "escalate",
        "suppress",
        "select",
        "authorize",
        "approve",
        "grant",
        "revoke",
        "execute",
        "verify",
        "rollback",
        "mutate_procedure",
        "activate_procedure",
        "classify",
        "diagnose",
        "localize",
        "infer",
        "infer_change",
        "analyze_traceback",
        "analyze_trajectory",
        "query_store",
        "query_graph",
        "research",
        "embed",
        "rank",
        "score",
        "predict",
    }

    assert module_level.isdisjoint(forbidden_functions)
    assert _public_functions().isdisjoint(forbidden_functions)


def test_no_execution_dynamic_import_network_file_or_clock_primitives() -> None:
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
        "time",
        "platform",
        "sys",
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
        "patch",
        "rollback",
        "escalate",
        "suppress",
        "classify",
        "infer",
        "select",
        "rank",
        "score",
        "findall",
        "search",
        "match",
        "fullmatch",
        "sub",
        "shuffle",
        "choice",
        "sample",
        "randint",
        "random",
        "seed",
        # Detection is evaluated only against the caller-supplied instant.
        "now",
        "today",
        "utcnow",
        "time",
        "monotonic",
        "sleep",
    }

    assert _imports().isdisjoint(forbidden_imports)
    assert "__import__" not in _called_names()
    assert _called_names().isdisjoint(forbidden_calls)


def test_c404_adds_no_persistence_or_migration_surface() -> None:
    assert "SQLiteDatabase" not in _SOURCE
    assert "_Migration" not in _SOURCE
    assert "agentx_schema_migrations" not in _SOURCE
    assert "CREATE TABLE" not in _SOURCE
    assert "EnvironmentChangeStore" not in _SOURCE

    persistence_source = (
        _REPO_ROOT / "src" / "agentx" / "infrastructure" / "persistence.py"
    ).read_text(encoding="utf-8")
    migration_versions = [
        int(match) for match in re.findall(r"^\s*version=(\d+),", persistence_source, re.MULTILINE)
    ]

    # C4.04 is a pure core contract: it adds no migration of its own. The
    # highest landed migration at integration is the C7.07 event-watcher state
    # store (v10); A8.01 owns v9 (strategy-performance store) and v8 remains
    # the C2.06 negative-experience store.
    assert migration_versions == sorted(migration_versions)
    assert max(migration_versions) == 10
    assert "environment_change" not in persistence_source
    assert "agentx_environment_change" not in persistence_source


def test_c404_carries_no_ranking_scoring_confidence_or_model_machinery() -> None:
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
        "vector_store",
    ):
        assert token not in lowered

    assert "embedding=" not in lowered
    assert "ranking=" not in lowered


def test_c404_does_not_implement_keyword_inference() -> None:
    """Facts are typed members; no free text is ever scanned for change words."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Compare):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    lowered = comparator.value.lower()
                    assert lowered not in {
                        "changed",
                        "change",
                        "updated",
                        "update",
                        "new version",
                        "version",
                        "unavailable",
                        "missing",
                        "permission",
                        "button",
                        "api",
                        "ui",
                        "selector",
                        "environment",
                        "stale",
                        "verified",
                        "traceback",
                        "root cause",
                        "patch",
                    }

    lowered = _SOURCE.lower()
    for token in (" in summary", "in detail", "in text", ".find(", "startswith", "lower()"):
        assert token not in lowered


def test_c404_never_mutates_the_c401_c402_c403_contracts() -> None:
    assert "class FailureCategory" in _TAXONOMY
    assert "class FailureClassification" in _TAXONOMY
    assert "class EnvironmentFactKind" not in _TAXONOMY
    assert "detect_environment_change" not in _TAXONOMY
    # C4.01 still documents C4.04 as a later non-goal, not as owned surface.
    assert "environment-change detection (C4.04)" in _TAXONOMY

    assert "class FailureLocalization" in _LOCALIZATION
    assert "class FailureLocationKind" in _LOCALIZATION
    assert "class EnvironmentSnapshot" not in _LOCALIZATION
    assert "detect_environment_change" not in _LOCALIZATION

    assert "class FailureDiagnosis" in _DIAGNOSIS
    assert "class DiagnosticConclusion" in _DIAGNOSIS
    assert "class EnvironmentChangeDetection" not in _DIAGNOSIS
    assert "detect_environment_change" not in _DIAGNOSIS
    # C4.03 still names environment-change detection as a later non-goal.
    assert (
        "environment-change detection\n(C4.04)" in _DIAGNOSIS
        or "environment-change detection" in (_DIAGNOSIS)
    )


def test_the_historical_c404_repair_candidate_contract_is_preserved() -> None:
    """The label collision is documented, not resolved by deletion or renaming."""
    module = _REPO_ROOT / "src" / "agentx" / "core" / "repair_candidates.py"
    assert module.is_file()
    assert "class RepairCandidateKind" in _REPAIR_CANDIDATES
    assert "class RepairCandidate" in _REPAIR_CANDIDATES
    assert "def derive_repair_candidates" in _REPAIR_CANDIDATES
    assert "C4.04" in _REPAIR_CANDIDATES

    # The canonical C4.04 neither imports, re-exports, nor shadows it. The
    # module docstring *names* the collision on purpose, so this is checked
    # against code identifiers and imports rather than raw prose.
    assert "agentx.core.repair_candidates" not in _imports()
    assert "RepairCandidate" not in _referenced_names()
    assert "RepairCandidateKind" not in _referenced_names()
    assert "derive_repair_candidates" not in _called_names()
    assert "class RepairCandidate" not in _SOURCE

    # ... and the repair-candidate contract does not import the new one either.
    assert "environment_change" not in _REPAIR_CANDIDATES
    assert "detect_environment_change" not in _REPAIR_CANDIDATES

    # The collision itself is recorded in both the module and the docs.
    assert "collision" in _SOURCE.lower()
    docs = (_REPO_ROOT / "docs" / "environment_change.md").read_text(encoding="utf-8")
    assert "collision" in docs.lower()
    assert "repair_candidates" in docs


def test_c404_does_not_query_or_analyze_other_records_or_stores() -> None:
    referenced = _referenced_names()
    called = _called_names()

    for foreign in (
        "NormalizedTrajectory",
        "normalize_trajectory",
        "EpisodeStore",
        "NegativeExperienceStore",
        "ExperienceMemory",
        "ProcedureStore",
        "KnowledgeStore",
        "ArtifactStore",
        "AuditStore",
        "EventJournal",
        "CausalExperience",
    ):
        assert foreign not in referenced
        assert foreign not in called

    for module in (
        "agentx.core.causal_experience",
        "agentx.core.negative_experience",
        "agentx.core.episodes",
        "agentx.learning.trajectory",
        "agentx.infrastructure.procedure_store",
        "agentx.infrastructure.knowledge_store",
        "agentx.infrastructure.episode_store",
        "agentx.infrastructure.negative_experience_store",
        "agentx.infrastructure.event_journal",
        "agentx.capabilities.browser_provider",
        "agentx.capabilities.windows.process_discovery",
    ):
        assert module not in _imports()


def test_c404_records_expose_no_repair_or_authority_methods() -> None:
    forbidden_methods = {
        "repair",
        "retry",
        "rollback",
        "patch",
        "grant",
        "revoke",
        "execute",
        "escalate",
        "suppress",
        "fallback",
        "verify",
        "classify",
        "infer",
        "diagnose",
        "apply",
        "promote",
        "activate",
        "publish",
        "root_cause",
        "sense",
        "observe",
        "probe",
        "refresh",
        "select",
        "authorize",
        "approve",
        "rank",
        "score",
    }
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                assert statement.name not in forbidden_methods


def test_docs_page_exists_for_canonical_c404() -> None:
    docs = _REPO_ROOT / "docs" / "environment_change.md"
    assert docs.is_file()
    text = docs.read_text(encoding="utf-8")

    lowered = text.lower()
    assert "environment-change detection" in lowered
    for token in (
        "C4.04",
        "RELEVANT_CHANGE_DETECTED",
        "NO_RELEVANT_CHANGE",
        "INSUFFICIENT_EVIDENCE",
        "Absence is not change",
        "Persistence decision",
        "Zero new runtime dependencies",
        "diagnosis data",
    ):
        assert token in text
