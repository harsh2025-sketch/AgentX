"""Architecture guards for the M6.01 world-state snapshot contract.

The world-state module is a pure inward ``agentx.core`` data contract. These
static guards prove it stays that way: no outward subsystem imports, no
persistence or migration surface, no capture/polling/sensing machinery, no
background threads or mutable singletons, no clock reads, no keyword or model
interpretation, and no competing identifier, provenance, scope, or error
types. The Hive environmental cache, the capability observation ABI, and the
C4.04 environment-change contract all remain separate and untouched.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path


def _count_occurrences(haystack: str, needle: str) -> int:
    return haystack.count(needle)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "core" / "world_state.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_KNOWLEDGE = (_REPO_ROOT / "src" / "agentx" / "core" / "knowledge.py").read_text(encoding="utf-8")
_CACHE = (_REPO_ROOT / "src" / "agentx" / "hive" / "environmental_cache.py").read_text(
    encoding="utf-8"
)
_ABI = (_REPO_ROOT / "src" / "agentx" / "capabilities" / "abi.py").read_text(encoding="utf-8")
_ENVIRONMENT_CHANGE = (_REPO_ROOT / "src" / "agentx" / "core" / "environment_change.py").read_text(
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


def _method_names() -> set[str]:
    return {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_contract_lives_in_core_and_is_an_inward_leaf() -> None:
    assert _MODULE.is_file()

    agentx_imports = {name for name in _imports() if name.startswith("agentx")}

    # The only AgentX import is the canonical C2.02 provenance hook.
    assert agentx_imports == {"agentx.core.knowledge"}
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
        "math",
        "types",
        "typing",
    }


def test_world_state_defines_data_contracts_not_capture_or_world_model_subsystems() -> None:
    classes = _classes()
    forbidden = {
        # Authority and runtime machinery.
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
        # Other subsystems' records that must not move into core.world_state.
        "KnowledgeRecord",
        "ProcedureRecord",
        "CapabilityObservation",
        "EnvironmentSnapshot",
        "EnvironmentalCache",
        "EnvironmentalCacheEntry",
        "WindowsProcessSnapshot",
        "UIATreeSnapshot",
        "BrowserDomObservation",
        "DeviceObservation",
        # No world model, sensor, or environment inventory may live here.
        "WorldModel",
        "WorldStateStore",
        "WorldStateCache",
        "WorldStateRegistry",
        "WorldStateMonitor",
        "WorldStatePoller",
        "WorldStateWatcher",
        "WorldStateService",
        "WorldStateScheduler",
        "EnvironmentModel",
        "EnvironmentSensor",
        "EnvironmentProbe",
        "ScreenCapture",
        "ProcessEnumerator",
        "BrowserInspector",
        "DevicePoller",
        "FileSystemInspector",
        # No competing provenance or scope ontology.
        "ScopeDimension",
        "KnowledgeScope",
        "ProvenanceKind",
        "EvidenceReference",
    }

    assert classes.isdisjoint(forbidden)
    assert classes == {
        "WorldStateError",
        "WorldStateValidationError",
        "WorldStateDeserializationError",
        "UnsupportedWorldStateSchemaVersionError",
        "WorldStateDomain",
        "WorldStateFact",
        "WorldStateSnapshot",
    }


def test_world_state_creates_no_competing_error_or_provenance_hierarchy() -> None:
    exception_bases: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name):
                exception_bases.add(base.id)

    assert "AgentXError" not in _classes()
    assert "Exception" not in exception_bases
    assert "BaseException" not in exception_bases
    assert {"ValueError"} <= exception_bases

    # Provenance comes from the landed canonical C2.02 contract.
    assert "from agentx.core.knowledge import" in _SOURCE
    for reused in ("ProvenanceReference", "KnowledgeValidationError"):
        assert reused in _SOURCE
    assert "class ProvenanceReference" not in _SOURCE
    assert "class ProvenanceKind" not in _SOURCE
    assert "class WorldStateProvenance" not in _SOURCE


def test_world_state_domain_vocabulary_is_small_closed_and_anchored() -> None:
    source = _SOURCE

    # Exactly the seven architecture-anchored domains, nothing exotic.
    assert 'PROCESS = "process"' in source
    assert 'WINDOW = "window"' in source
    assert 'APPLICATION = "application"' in source
    assert 'BROWSER = "browser"' in source
    assert 'DEVICE = "device"' in source
    assert 'FILE_CONTEXT = "file_context"' in source
    assert 'ENVIRONMENT = "environment"' in source
    for member in ("ROBOT", "IOT", "EMOTION", "GESTURE", "BIOMETRIC"):
        assert f"{member} =" not in source

    # The closed tuple is the declaration order itself.
    assert "CANONICAL_WORLD_STATE_DOMAINS" in source


def test_world_state_reproduces_the_c209_freshness_rule_without_importing_the_hive() -> None:
    # Same rule as the Hive cache: expires_at = observed_at + ttl, fresh
    # exactly while at < expires_at.
    assert "self.observed_at + self.ttl" in _SOURCE
    assert "self.observed_at + self.ttl" in _CACHE
    assert "moment < self.expires_at" in _SOURCE
    assert "moment < self.expires_at" in _CACHE
    # ... but core stays an inward leaf: the cache is never imported.
    assert not any(name.startswith("agentx.hive") for name in _imports())
    assert "class EnvironmentalCache" not in _SOURCE


def test_world_state_exposes_no_sensing_refresh_or_policy_entry_points() -> None:
    module_level = {
        node.name
        for node in _tree().body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }

    # The module is pure types: no public module-level functions at all.
    assert module_level == set()

    forbidden_methods = {
        "sense",
        "observe",
        "probe",
        "scan",
        "capture",
        "enumerate",
        "inspect",
        "poll",
        "refresh",
        "reload",
        "update",
        "subscribe",
        "publish",
        "schedule",
        "start",
        "stop",
        "run",
        "loop",
        "wait",
        "sleep",
        "execute",
        "verify",
        "validate_world",
        "authorize",
        "approve",
        "grant",
        "revoke",
        "activate",
        "transition",
        "repair",
        "diagnose",
        "infer",
        "rank",
        "score",
    }
    assert _method_names().isdisjoint(forbidden_methods)


def test_no_capture_clock_threading_or_execution_primitives() -> None:
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
        "asyncio",
        "multiprocessing",
        "concurrent",
        "queue",
        "signal",
    }
    forbidden_calls = {
        "eval",
        "exec",
        "open",
        "urlopen",
        "__import__",
        "now",
        "utcnow",
        "today",
        "time",
        "monotonic",
        "perf_counter",
        "sleep",
        "spawn",
        "Popen",
        "listdir",
        "walk",
        "glob",
        "connect",
        "listen",
        "recv",
        "send",
        "CreateProcess",
        "EnumProcesses",
        "CoCreateInstance",
    }

    assert _imports().isdisjoint(forbidden_imports)
    assert _called_names().isdisjoint(forbidden_calls)
    # No clock is ever read, even through the datetime module.
    assert ".now(" not in _SOURCE
    assert "utcnow" not in _SOURCE


def test_world_state_is_point_in_time_data_not_a_cache_or_store() -> None:
    referenced = _referenced_names()

    # No mutable shared state, no locking, no last-write-wins replacement.
    for token in ("Lock", "RLock", "self._entries", "threading", "Thread", "Timer"):
        assert token not in _SOURCE
    assert "Lock" not in referenced
    # No dataclass field may be a mutable collection type.
    for node in ast.walk(_tree()):
        if isinstance(node, ast.AnnAssign) and isinstance(node.annotation, (ast.Dict, ast.List)):
            raise AssertionError("a snapshot record must not carry a mutable collection field")

    # No persistence or migration surface.
    assert "CREATE TABLE" not in _SOURCE
    assert "agentx_schema_migrations" not in _SOURCE
    assert "world_state_store" not in _SOURCE

    persistence_source = (
        _REPO_ROOT / "src" / "agentx" / "infrastructure" / "persistence.py"
    ).read_text(encoding="utf-8")
    migration_versions = [
        int(match) for match in re.findall(r"^\s*version=(\d+),", persistence_source, re.MULTILINE)
    ]

    # The migration ladder is untouched and the highest landed migration
    # is the canonical M12 scheduling store (v9).
    assert migration_versions == sorted(migration_versions)
    assert max(migration_versions) == 9
    assert "world_state" not in persistence_source


def test_environmental_cache_remains_separate_and_unchanged_in_contract() -> None:
    # The Hive cache keeps its own classes and never knows about this contract.
    assert "class EnvironmentalCache" in _CACHE
    assert "class EnvironmentalCacheEntry" in _CACHE
    assert "world_state" not in _CACHE
    assert "WorldStateSnapshot" not in _CACHE
    assert "WorldStateFact" not in _CACHE

    # This contract never re-exports or subclasses the cache.
    assert "EnvironmentalCache" not in _referenced_names()


def test_provider_observation_contracts_remain_separate() -> None:
    # The capability ABI keeps its own observation contract...
    assert "class CapabilityObservation" in _ABI
    # ...and the C4.04 environment-change contract keeps its own snapshot.
    assert "class EnvironmentSnapshot" in _ENVIRONMENT_CHANGE
    # This contract defines no competing observation record and imports none
    # of the provider modules.
    assert "class CapabilityObservation" not in _SOURCE
    assert "class EnvironmentSnapshot" not in _SOURCE
    assert "class WorldStateObservation" not in _SOURCE
    for module in (
        "agentx.capabilities.abi",
        "agentx.capabilities.windows.process_discovery",
        "agentx.capabilities.windows.uia_tree",
        "agentx.capabilities.browser_dom",
        "agentx.capabilities.device",
    ):
        assert module not in _imports()


def test_world_state_carries_no_ranking_scoring_confidence_or_model_machinery() -> None:
    lowered = _SOURCE.lower()

    for token in (
        "confidence=",
        "confidence:",
        "probability=",
        "score=",
        "weight=",
        "likelihood",
        "openai",
        "anthropic",
        "model_provider",
        "tokenizer",
        "embedding",
        "vector_store",
    ):
        assert token not in lowered


def test_world_state_never_interprets_or_scans_text() -> None:
    lowered = _SOURCE.lower()
    for token in (".find(", "startswith", "lower()", "upper()", "split("):
        assert token not in lowered
    # strip() appears only in the trimmed-text validation of the subject,
    # fact key, and timestamp parsing — never on observed values.
    assert _count_occurrences(_SOURCE, "strip()") == 2


def test_world_state_keeps_observations_distinct_from_verification() -> None:
    # No verification surface exists anywhere in the contract.
    for token in ("verified", "verificationresult", "is_verified", "task_succeeded", "grant"):
        # The word appears only in docstring prose (the explicit invariant),
        # never as a field, attribute, or method name.
        assert not re.search(rf"(?m)^\s*{token}\s*[:=]", _SOURCE)
    assert "class WorldStateVerification" not in _SOURCE
    assert "verified:" not in _SOURCE


def test_docs_page_exists_for_world_state() -> None:
    docs = _REPO_ROOT / "docs" / "world_state.md"
    assert docs.is_file()
    text = docs.read_text(encoding="utf-8")

    lowered = text.lower()
    assert "world-state snapshot" in lowered
    for token in (
        "M6.01",
        "WorldStateSnapshot",
        "WorldStateFact",
        "WorldStateDomain",
        "PROCESS",
        "WINDOW",
        "APPLICATION",
        "BROWSER",
        "DEVICE",
        "FILE_CONTEXT",
        "ENVIRONMENT",
        "FRESH != VERIFIED",
        "STALE != FALSE",
        "128 facts",
        "duplicate",
        "schema version",
        "No persistence",
        "Zero new runtime dependencies",
        "no capture",
    ):
        assert token in text or token.lower() in lowered
