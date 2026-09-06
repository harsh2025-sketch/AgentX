"""Architecture guards for the C5.09 world-state observation cache boundary."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

from agentx.capabilities.world_state_cache import (
    CANONICAL_WORLD_STATE_CACHE_OUTCOMES,
    DEFAULT_WORLD_STATE_CACHE_MAX_ENTRIES,
    WORLD_STATE_CACHE_SCHEMA_VERSION,
    WorldStateCache,
    WorldStateCacheEntry,
    WorldStateCacheKey,
    WorldStateCacheOutcome,
    WorldStateCacheResult,
)

_ROOT = Path(__file__).parents[2]
_MODULE = _ROOT / "src" / "agentx" / "capabilities" / "world_state_cache.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)


def _imports() -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _call_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _class_names() -> set[str]:
    return {node.name for node in _TREE.body if isinstance(node, ast.ClassDef)}


def test_world_state_cache_lives_in_capabilities_boundary() -> None:
    assert _MODULE.exists()
    assert "agentx" in _MODULE.parts
    assert "capabilities" in _MODULE.parts
    assert _MODULE.name == "world_state_cache.py"


def test_module_imports_no_other_agentx_subsystem() -> None:
    """The cache reuses C2.09 concepts without depending on the Hive or kernel."""
    imported = _imports()

    assert all(not name.startswith("agentx") for name in imported)


def test_module_imports_no_persistence_transport_or_automation_stack() -> None:
    imported = _imports()
    forbidden = {
        "sqlite3",
        "socket",
        "websocket",
        "websockets",
        "requests",
        "urllib",
        "http",
        "subprocess",
        "asyncio",
        "pickle",
        "playwright",
        "selenium",
        "ctypes",
    }

    assert imported.isdisjoint(forbidden)
    assert all(not name.startswith("playwright.") for name in imported)
    assert all(not name.startswith("selenium.") for name in imported)


def test_module_creates_no_background_thread_or_process() -> None:
    calls = _call_names()

    assert {"Thread", "Timer", "Process", "Popen", "fork", "spawn"}.isdisjoint(calls)


def test_module_calls_no_dynamic_code_network_or_process_primitive() -> None:
    calls = _call_names()
    forbidden = {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "urlopen",
        "connect",
        "send",
        "recv",
        "Popen",
        "run",
        "system",
    }

    assert calls.isdisjoint(forbidden)


def test_outcome_vocabulary_is_exactly_the_assigned_minimal_set() -> None:
    assert [outcome.value for outcome in WorldStateCacheOutcome] == [
        "missing",
        "fresh",
        "stale",
        "invalidated",
    ]
    assert tuple(WorldStateCacheOutcome) == CANONICAL_WORLD_STATE_CACHE_OUTCOMES
    assert "unknown" not in WorldStateCacheOutcome.__members__
    assert "verified" not in WorldStateCacheOutcome.__members__


def test_cache_identity_identifies_scope_target_kind_and_environment() -> None:
    assert [field.name for field in fields(WorldStateCacheKey)] == [
        "scope",
        "target",
        "kind",
        "environment",
    ]


def test_entry_identifies_value_source_observed_at_ttl_and_invalidation() -> None:
    assert [field.name for field in fields(WorldStateCacheEntry)] == [
        "key",
        "value",
        "source",
        "observed_at",
        "ttl",
        "invalidated_at",
        "schema_version",
    ]


def test_result_is_the_only_lookup_surface_and_defaults_to_no_entry() -> None:
    assert [field.name for field in fields(WorldStateCacheResult)] == [
        "key",
        "outcome",
        "entry",
    ]
    assert fields(WorldStateCacheResult)[2].default is None


def test_cache_public_api_is_observe_get_and_invalidate_only() -> None:
    public = {name for name in vars(WorldStateCache) if not name.startswith("_")}

    assert public == {"clock", "max_entries", "observe", "get", "invalidate"}


def test_cache_growth_bound_is_enabled_by_default() -> None:
    assert DEFAULT_WORLD_STATE_CACHE_MAX_ENTRIES >= 1
    assert WorldStateCache().max_entries == DEFAULT_WORLD_STATE_CACHE_MAX_ENTRIES


def test_module_defines_no_verified_result_cache_surface() -> None:
    """C5.09 is an observation cache; A8.07 verified-result semantics stay out."""
    class_names = _class_names()

    assert all("Verified" not in name for name in class_names)
    assert all("Verification" not in name for name in class_names)
    assert "VerificationResult" not in _SOURCE
    assert "verified_at" not in _SOURCE


def test_module_documents_c209_reuse_and_a807_separation() -> None:
    assert "C2.09" in _SOURCE
    assert "A8.07" in _SOURCE


def test_module_has_no_authority_task_or_procedure_symbols() -> None:
    forbidden_symbols = {
        "ActionGate",
        "AuthorityContext",
        "EmergencyStop",
        "Permission",
        "ResourceBudget",
        "RiskLevel",
        "Task",
        "Procedure",
        "KnowledgeRecord",
    }

    assert all(symbol not in _SOURCE for symbol in forbidden_symbols)


def test_schema_version_is_a_single_positive_integer() -> None:
    assert isinstance(WORLD_STATE_CACHE_SCHEMA_VERSION, int)
    assert WORLD_STATE_CACHE_SCHEMA_VERSION == 1
