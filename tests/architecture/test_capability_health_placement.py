"""Architecture guards for the M7.03 canonical capability-health contract.

The capability-health module is a pure inward ``agentx.core`` data/assessment
contract. These static guards prove it stays that way: no outward subsystem
imports, no capability probing or provider calls, no registry or router
mutation, no authority surface, no persistence or migration surface, no keyword
or model interpretation, no clock reads, no ranking or scoring, and no
competing identifier, version, or error types. The A1.08 capability ABI, the
A1.09 registry, the A1.10 runtime, the A2.07 router, ``agentx.core.ids``, and
``agentx._architecture`` all remain untouched.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "core" / "capability_health.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_ABI = (_REPO_ROOT / "src" / "agentx" / "capabilities" / "abi.py").read_text(encoding="utf-8")
_REGISTRY = (_REPO_ROOT / "src" / "agentx" / "capabilities" / "registry.py").read_text(
    encoding="utf-8"
)
_RUNTIME = (_REPO_ROOT / "src" / "agentx" / "capabilities" / "runtime.py").read_text(
    encoding="utf-8"
)
_ROUTER = (_REPO_ROOT / "src" / "agentx" / "cognition" / "router.py").read_text(encoding="utf-8")
_IDS = (_REPO_ROOT / "src" / "agentx" / "core" / "ids.py").read_text(encoding="utf-8")
_TAXONOMY = (_REPO_ROOT / "src" / "agentx" / "core" / "failure_taxonomy.py").read_text(
    encoding="utf-8"
)
_ENVIRONMENT_CHANGE = (_REPO_ROOT / "src" / "agentx" / "core" / "environment_change.py").read_text(
    encoding="utf-8"
)
_CORE_INIT = (_REPO_ROOT / "src" / "agentx" / "core" / "__init__.py").read_text(encoding="utf-8")


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


# --------------------------------------------------------------------------
# Placement and imports.
# --------------------------------------------------------------------------


def test_contract_lives_in_core_and_is_an_inward_leaf() -> None:
    assert _MODULE.is_file()

    agentx_imports = {name for name in _imports() if name.startswith("agentx")}

    assert agentx_imports == {
        "agentx.core.environment_change",
        "agentx.core.failure_taxonomy",
        "agentx.core.ids",
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


def test_no_capability_runtime_registry_or_abi_is_imported() -> None:
    """Core must not import outward Capability objects."""
    for forbidden in (
        "agentx.capabilities.abi",
        "agentx.capabilities.registry",
        "agentx.capabilities.runtime",
        "agentx.capabilities.executor",
        "agentx.capabilities.device",
        "agentx.capabilities.browser_provider",
        "agentx.capabilities.windows.provider",
        "agentx.cognition.router",
        "agentx.kernel.action_gate",
        "agentx.kernel.permissions",
        "agentx.kernel.risk",
        "agentx.infrastructure.persistence",
    ):
        assert forbidden not in _imports()

    referenced = _referenced_names()
    for forbidden in (
        "CapabilityRegistry",
        "CapabilityDescriptor",
        "CapabilityIdentity",
        "Capability",
        "CapabilityExecutionLoop",
        "ExecutionResult",
        "VerificationResult",
        "ExecutionLevel",
        "Router",
    ):
        assert forbidden not in referenced


# --------------------------------------------------------------------------
# Defined surface.
# --------------------------------------------------------------------------


def test_m703_defines_assessment_contracts_not_probers_monitors_or_policies() -> None:
    classes = _classes()
    forbidden = {
        # No authority, kernel, or routing machinery.
        "ActionGate",
        "AuthorityContext",
        "Permission",
        "RiskAssessment",
        "ResourceEnvelope",
        "EmergencyStop",
        "Router",
        "ExecutionLevel",
        # No probing, sensing, or monitoring subsystem.
        "CapabilityHealthProber",
        "CapabilityHealthProbe",
        "CapabilityHealthMonitor",
        "CapabilityHealthService",
        "CapabilityHealthStore",
        "CapabilityHealthTracker",
        "CapabilityHealthDaemon",
        "CapabilityHealthScheduler",
        "CapabilityHealthScanner",
        "CapabilityHealthDetector",
        "CapabilityHealthEngine",
        "CapabilityHealthRegistry",
        "CapabilityHealthCache",
        "CapabilityHealthHistory",
        "ProviderHealthMonitor",
        "AvailabilityMonitor",
        "MissingCapabilityDetector",
        # No outward capability or runtime object is redefined here.
        "Capability",
        "CapabilityRegistry",
        "CapabilityDescriptor",
        "CapabilityIdentity",
        "CapabilityVersion",
        "CapabilityName",
        "ExecutionResult",
        "VerificationResult",
        # No competing identity or provenance type.
        "CapabilityId",
        "TaskId",
        "ProcedureId",
        "AgentXError",
        "AgentXException",
        "ProvenanceReference",
        "KnowledgeScope",
    }

    assert classes.isdisjoint(forbidden)
    assert classes == {
        "CapabilityHealthValidationError",
        "CapabilityHealthState",
        "CapabilityHealthEvidenceKind",
        "CapabilityHealthFact",
        "CapabilityHealthReason",
        "CapabilityVersionKey",
        "CapabilityHealthSubject",
        "CapabilityHealthEvidence",
        "CapabilityHealthConflict",
        "CapabilityHealthAssessment",
    }


def test_m703_creates_no_competing_error_or_id_hierarchy() -> None:
    exception_bases: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name):
                exception_bases.add(base.id)

    assert "AgentXError" not in _classes()
    assert "AgentXException" not in _classes()
    assert "AgentXError" not in exception_bases
    assert "Exception" not in exception_bases
    assert "BaseException" not in exception_bases
    assert {"ValueError"} <= exception_bases

    # Capability identity comes from the landed canonical ``agentx.core.ids``.
    assert "from agentx.core.ids import CapabilityId" in _SOURCE
    assert "class CapabilityId" not in _SOURCE


def test_m703_binds_the_canonical_capability_id_and_never_redefines_it() -> None:
    assert "class CapabilityId" in _IDS
    assert "CapabilityId" in _SOURCE
    assert "class CapabilityId" not in _SOURCE
    assert "capability_health" not in _IDS


# --------------------------------------------------------------------------
# Vocabulary anchoring and freshness reuse.
# --------------------------------------------------------------------------


def test_m703_evidence_vocabulary_is_anchored_to_landed_canonical_names() -> None:
    """The evidence taxonomy indexes existing vocabularies; it is not a new model."""
    assert "CANONICAL_CAPABILITY_HEALTH_EVIDENCE_ANCHORS" in _SOURCE
    assert "EnvironmentFactKind.PLATFORM_IDENTITY.value" in _SOURCE
    assert "EnvironmentFactKind.CAPABILITY_AVAILABILITY.value" in _SOURCE
    assert "EnvironmentFactKind.DEPENDENCY_AVAILABILITY.value" in _SOURCE
    assert "FailureCategory.ENVIRONMENT.value" in _SOURCE
    assert "FailureCategory.CAPABILITY.value" in _SOURCE
    assert "FailureCategory.DEPENDENCY.value" in _SOURCE
    assert "FailureCategory.TRANSIENT.value" in _SOURCE
    assert "FailureCategory.VERIFICATION.value" in _SOURCE
    assert "EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED.value" in _SOURCE
    assert "EnvironmentChangeResult.NO_RELEVANT_CHANGE.value" in _SOURCE

    # The anchors must exist in the vocabularies they claim to index.
    for anchor in ('"platform_identity"', '"capability_availability"', '"dependency_availability"'):
        assert anchor in _ENVIRONMENT_CHANGE
    for anchor in ('"relevant_change_detected"', '"no_relevant_change"'):
        assert anchor in _ENVIRONMENT_CHANGE
    for anchor in ('"environment"', '"capability"', '"dependency"'):
        assert anchor in _TAXONOMY
    for anchor in ('"transient"', '"verification"'):
        assert anchor in _TAXONOMY


def test_m703_reproduces_the_c209_freshness_rule_without_importing_the_hive() -> None:
    cache_source = (_REPO_ROOT / "src" / "agentx" / "hive" / "environmental_cache.py").read_text(
        encoding="utf-8"
    )

    # Same rule: expires_at = observed_at + ttl, fresh exactly while at < expires_at.
    assert "self.observed_at + self.ttl" in _SOURCE
    assert "self.observed_at + self.ttl" in cache_source
    assert "moment < self.expires_at" in _SOURCE
    assert "moment < self.expires_at" in cache_source
    assert "moment < self.expires_at" in _ENVIRONMENT_CHANGE
    # ... but core stays an inward leaf: the cache is never imported, so the
    # rule is reproduced by value.
    assert not any(name.startswith("agentx.hive") for name in _imports())
    assert "EnvironmentalCache" not in _classes()
    assert "class EnvironmentalCache" not in _SOURCE


def test_the_version_key_reproduces_the_a108_rule_without_importing_capabilities() -> None:
    """Core-safe identity: the A1.08 version rule is reused by value."""
    # A1.08 owns the outward typed version contract.
    assert "class CapabilityVersion" in _ABI
    assert "major.minor.patch" in _ABI

    # M7.03 reproduces the same three-integer shape and canonical string form,
    # under a distinct core-safe name, and never imports the outward contract.
    assert "class CapabilityVersionKey:" in _SOURCE
    # The outward contract is never redefined here (exact definition, not a
    # prefix match: "class CapabilityVersion" is a prefix of the key's name).
    assert "class CapabilityVersion:" not in _SOURCE
    assert "CapabilityVersion" not in _classes()
    assert "agentx.capabilities" not in _imports()
    assert "CapabilityVersionKey" not in _ABI
    assert 'f"{self.major}.{self.minor}.{self.patch}"' in _SOURCE

    # There is no wildcard, "latest", or prefix-matching version surface. This
    # is checked structurally, not against prose: the docstring *names* what
    # the contract refuses to offer.
    assert "startswith" not in _called_names()
    assert "fnmatch" not in _called_names()
    version_members: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ClassDef) and node.name == "CapabilityVersionKey":
            version_members = {
                statement.name for statement in node.body if isinstance(statement, ast.FunctionDef)
            }
    assert version_members == {"__post_init__", "to_str", "__str__", "from_str", "to_dict"}
    assert version_members.isdisjoint({"ANY", "LATEST", "WILDCARD", "ALL", "UNSPECIFIED"})
    # The version key exposes no wildcard accessor and no range comparison.
    for token in ("matches", "covers", "is_compatible_with", "satisfies", "range_of"):
        assert token not in _referenced_names()


def test_the_state_vocabulary_is_the_closed_five_state_set() -> None:
    assert "class CapabilityHealthState" in _SOURCE
    for member in ("UNKNOWN", "AVAILABLE", "DEGRADED", "UNAVAILABLE", "UNSUPPORTED"):
        assert f"{member} =" in _SOURCE


# --------------------------------------------------------------------------
# Entry point and behaviour surface.
# --------------------------------------------------------------------------


def test_m703_exposes_only_the_pure_assessment_entry_point() -> None:
    module_level = {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }

    assert module_level == {"assess_capability_health"}

    forbidden_functions = {
        # No probing or sensing.
        "probe",
        "probe_capability",
        "probe_provider",
        "check",
        "check_availability",
        "check_health",
        "ping",
        "sense",
        "observe",
        "scan",
        "poll",
        "monitor",
        "watch",
        "start_monitor",
        "schedule",
        "spawn",
        "refresh",
        "measure",
        "detect_missing_capability",
        # No repair, retry, or recovery.
        "repair",
        "retry",
        "restart",
        "recover",
        "heal",
        "remediate",
        "fallback",
        "escalate",
        # No routing or selection.
        "route",
        "route_task",
        "select",
        "select_capability",
        "select_level",
        "choose",
        "rank",
        "score",
        "predict",
        # No authority.
        "grant",
        "revoke",
        "authorize",
        "approve",
        "permit",
        "bypass",
        "execute",
        "verify",
        # No registry or persistence mutation.
        "register",
        "unregister",
        "persist",
        "store",
        "load",
        "save",
        "infer",
    }

    assert module_level.isdisjoint(forbidden_functions)
    assert _public_functions().isdisjoint(forbidden_functions)


def test_no_execution_dynamic_import_network_process_or_clock_primitives() -> None:
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
        "asyncio",
        "ctypes",
        "shutil",
        "tempfile",
        "time",
        "platform",
        "sys",
        "winreg",
        "requests",
    }
    forbidden_calls = {
        "eval",
        "exec",
        "open",
        "urlopen",
        "connect",
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
        # Health is evaluated only against the caller-supplied instant.
        "now",
        "today",
        "utcnow",
        "time",
        "monotonic",
        "sleep",
        "spawn",
        "popen",
        "system",
    }

    assert _imports().isdisjoint(forbidden_imports)
    assert "__import__" not in _called_names()
    assert _called_names().isdisjoint(forbidden_calls)


def test_m703_never_probes_a_provider_process_file_or_registry() -> None:
    called = _called_names()
    referenced = _referenced_names()

    for forbidden in (
        "Popen",
        "run",
        "check_output",
        "connect",
        "create_connection",
        "socket",
        "urlopen",
        "Request",
        "CDP",
        "webdriver",
        "CreateToolhelp32Snapshot",
        "EnumProcesses",
        "OpenProcess",
        "RegOpenKeyEx",
        "RegQueryValueEx",
        "Path",
        "listdir",
        "walk",
        "exists",
        "is_file",
        "read_text",
        "which",
        "getenv",
        "system",
    ):
        assert forbidden not in called
        assert forbidden not in referenced


def test_m703_adds_no_persistence_or_migration_surface() -> None:
    assert "SQLiteDatabase" not in _SOURCE
    assert "_Migration" not in _SOURCE
    assert "agentx_schema_migrations" not in _SOURCE
    assert "CREATE TABLE" not in _SOURCE
    assert "CapabilityHealthStore" not in _SOURCE

    persistence_source = (
        _REPO_ROOT / "src" / "agentx" / "infrastructure" / "persistence.py"
    ).read_text(encoding="utf-8")
    migration_versions = [
        int(match) for match in re.findall(r"^\s*version=(\d+),", persistence_source, re.MULTILINE)
    ]

    # M7.03 is a pure core contract: the migration ladder is untouched and the
    # highest landed migration remains the C2.06 negative-experience store (v8).
    assert migration_versions == sorted(migration_versions)
    assert max(migration_versions) == 8
    assert "capability_health" not in persistence_source
    assert "agentx_capability_health" not in persistence_source


def test_m703_carries_no_ranking_scoring_confidence_or_model_machinery() -> None:
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
        "statistics",
    ):
        assert token not in lowered

    assert "embedding=" not in lowered
    assert "ranking=" not in lowered


def test_m703_performs_no_averaging_voting_or_weighting() -> None:
    """The verdict is a rule-table lookup, never an aggregate over evidence."""
    called = _called_names()

    for aggregate in ("sum", "mean", "median", "average", "mode", "stdev", "variance", "round"):
        assert aggregate not in called

    # No state is derived from a numeric accumulation of the evidence set.
    assert "float" not in _SOURCE.split('"""', 2)[-1]


def test_m703_does_not_implement_keyword_inference() -> None:
    """Facts are typed members; no free text is ever scanned for a verdict."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Compare):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    lowered = comparator.value.lower()
                    assert lowered not in {
                        "available",
                        "unavailable",
                        "degraded",
                        "unsupported",
                        "unknown",
                        "healthy",
                        "unhealthy",
                        "permission",
                        "admin",
                        "verified",
                        "verified=true",
                        "available=true",
                        "ignore failure",
                        "force router l1",
                        "budget=unlimited",
                        "missing",
                        "transient",
                        "stale",
                        "traceback",
                        "timeout",
                    }

    lowered = _SOURCE.lower()
    for token in (" in summary", "in detail", "in text", ".find(", "startswith", "lower()"):
        assert token not in lowered


def test_m703_records_expose_no_authority_routing_or_probing_methods() -> None:
    forbidden_methods = {
        "grant",
        "revoke",
        "authorize",
        "approve",
        "allows",
        "allow",
        "permit",
        "bypass",
        "clear",
        "route",
        "select",
        "execute",
        "verify",
        "probe",
        "ping",
        "refresh",
        "repair",
        "retry",
        "rollback",
        "patch",
        "escalate",
        "suppress",
        "fallback",
        "classify",
        "infer",
        "diagnose",
        "apply",
        "promote",
        "activate",
        "publish",
        "register",
        "unregister",
        "sense",
        "observe",
        "scan",
        "poll",
        "monitor",
        "rank",
        "score",
        "persist",
        "store",
        "load",
        "save",
    }
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
                assert statement.name not in forbidden_methods


# --------------------------------------------------------------------------
# Untouched neighbours.
# --------------------------------------------------------------------------


def test_m703_never_mutates_the_capability_abi_registry_runtime_or_router() -> None:
    assert "class CapabilityDescriptor" in _ABI
    assert "class CapabilityRegistry" in _REGISTRY
    assert "class CapabilityExecutionLoop" in _RUNTIME
    assert "class ExecutionLevel" in _ROUTER

    for source in (_ABI, _REGISTRY, _RUNTIME, _ROUTER):
        assert "capability_health" not in source
        assert "CapabilityHealthState" not in source
        assert "assess_capability_health" not in source
        assert "CapabilityVersionKey" not in source

    # The router still owns L0-L5 selection and knows nothing about health.
    for level in (
        "L0_CACHE",
        "L1_DIRECT",
        "L2_COMPILED",
        "L3_GUIDED",
        "L4_PLANNED",
        "L5_EXPLORATORY",
    ):
        assert level in _ROUTER
        assert level not in _SOURCE


def test_m703_does_not_touch_the_architecture_manifest_or_any_package_init() -> None:
    architecture = (_REPO_ROOT / "src" / "agentx" / "_architecture.py").read_text(encoding="utf-8")

    assert "capability_health" not in architecture
    # The canonical edge set is unchanged: core imports nothing outward.
    assert "(CORE, " not in architecture
    assert "capability_health" not in _CORE_INIT
    assert "CapabilityHealthState" not in _CORE_INIT


def test_m703_is_not_re_exported_from_the_core_package_init() -> None:
    """This task owns no ``__init__.py``; the module is imported by path."""
    assert "capability_health" not in _CORE_INIT
    assert "from agentx.core.capability_health" not in _SOURCE


def test_m703_queries_no_store_or_other_record_contract() -> None:
    referenced = _referenced_names()

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
        "FailureDiagnosis",
        "RepairCandidate",
        "derive_repair_candidates",
        "detect_environment_change",
    ):
        assert foreign not in referenced

    for module in (
        "agentx.core.causal_experience",
        "agentx.core.negative_experience",
        "agentx.core.episodes",
        "agentx.core.repair_candidates",
        "agentx.core.failure_diagnosis",
        "agentx.learning.trajectory",
        "agentx.infrastructure.procedure_store",
        "agentx.infrastructure.knowledge_store",
        "agentx.infrastructure.episode_store",
        "agentx.infrastructure.event_journal",
        "agentx.capabilities.browser_provider",
        "agentx.capabilities.windows.process_discovery",
    ):
        assert module not in _imports()


def test_m703_bounds_the_evidence_it_will_consult() -> None:
    """No unbounded health history: the record and TTL bounds are explicit."""
    assert "MAX_HEALTH_EVIDENCE_RECORDS" in _SOURCE
    assert "MAX_HEALTH_EVIDENCE_TTL" in _SOURCE
    assert "timedelta(days=7)" in _SOURCE
    assert "unbounded health history" in _SOURCE
    assert "unbounded freshness claim" in _SOURCE


def test_m703_never_reads_a_clock() -> None:
    """``assessed_at`` is caller-supplied; determinism depends on it alone."""
    assert "datetime.now" not in _SOURCE
    assert "utcnow" not in _SOURCE
    assert "time.monotonic" not in _SOURCE
    assert "assessed_at" in _SOURCE


# --------------------------------------------------------------------------
# Docs.
# --------------------------------------------------------------------------


def test_docs_page_exists_for_canonical_m703() -> None:
    docs = _REPO_ROOT / "docs" / "capability_health.md"
    assert docs.is_file()
    text = docs.read_text(encoding="utf-8")

    lowered = text.lower()
    assert "capability health" in lowered
    for token in (
        "M7.03",
        "UNKNOWN",
        "AVAILABLE",
        "DEGRADED",
        "UNAVAILABLE",
        "UNSUPPORTED",
        "Health is not authority",
        "Health is not routing",
        "No probing",
        "Persistence decision",
        "Zero new runtime dependencies",
        "assess_capability_health",
        "CapabilityHealthEvidence",
        "Registry presence is not availability",
    ):
        assert token in text
