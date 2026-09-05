"""Architecture guardrails for the A4.03 research-provider data boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import agentx.cognition.research_provider as provider

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src" / "agentx" / "cognition" / "research_provider.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)


def _imports() -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def _called_names() -> set[str]:
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


def test_contract_lives_in_research_specific_cognition_module() -> None:
    assert provider.__name__ == "agentx.cognition.research_provider"
    assert _MODULE.as_posix().endswith("src/agentx/cognition/research_provider.py")


def test_contract_uses_research_specific_provider_vocabulary_only() -> None:
    class_names = _class_names()

    assert {
        "ResearchProviderIdentity",
        "ResearchProviderAvailability",
        "ResearchProviderFailure",
        "ResearchRequest",
        "ResearchResponse",
    }.issubset(class_names)
    assert "ProviderIdentity" not in class_names
    assert "ProviderAvailability" not in class_names
    assert "ProviderFailure" not in class_names


def test_contract_reuses_only_required_canonical_agentx_data_edges() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx.")}

    assert agentx_imports == {
        "agentx.cognition.research_objective",
        "agentx.core.knowledge",
    }


def test_contract_does_not_unify_browser_or_windows_providers() -> None:
    imports = _imports()

    assert "agentx.capabilities.browser_provider" not in imports
    assert "agentx.capabilities.browser_connection" not in imports
    assert "agentx.capabilities.windows" not in imports


def test_contract_has_no_kernel_hive_persistence_or_capability_runtime_edge() -> None:
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.procedures",
        "agentx.capabilities",
    )

    assert not any(
        imported.startswith(forbidden_prefixes)
        for imported in _imports()
        if imported.startswith("agentx.")
    )


def test_contract_has_no_network_model_process_or_background_runtime_imports() -> None:
    forbidden_roots = {
        "aiohttp",
        "asyncio",
        "http",
        "httpx",
        "multiprocessing",
        "openai",
        "os",
        "requests",
        "socket",
        "sqlite3",
        "subprocess",
        "threading",
        "urllib",
        "webbrowser",
        "websocket",
        "websockets",
    }
    roots = {name.split(".", maxsplit=1)[0] for name in _imports()}

    assert roots.isdisjoint(forbidden_roots)


def test_contract_has_no_io_execution_or_model_call_surface() -> None:
    forbidden_calls = {
        "browse",
        "connect",
        "execute",
        "get",
        "invoke",
        "launch",
        "open",
        "post",
        "request",
        "research",
        "run",
        "search",
        "send",
        "spawn",
    }

    assert _called_names().isdisjoint(forbidden_calls)


def test_objective_does_not_directly_produce_provider_response() -> None:
    assert "research_response_from_objective" not in provider.__all__
    assert not hasattr(provider, "research_response_from_objective")
    assert provider.ResearchRequest.__annotations__["objective"] == "ResearchObjective"


def test_response_carries_provenance_not_knowledge_lifecycle() -> None:
    fields = set(provider.ResearchResponse.__annotations__)

    assert "evidence" in fields
    assert "knowledge_type" not in fields
    assert "knowledge_status" not in fields
    assert "verified" not in fields
    assert "verification_result" not in fields


def test_contract_has_no_persistence_migration_or_global_provider_state() -> None:
    class_names = _class_names()
    lowered_names = {name.lower() for name in vars(provider)}

    assert all("store" not in name.lower() for name in class_names)
    assert all("database" not in name.lower() for name in class_names)
    assert "research_provider_store" not in lowered_names
    assert "research_provider_registry" not in lowered_names
    assert "current_provider" not in lowered_names


def test_contract_does_not_add_runtime_dependency_imports() -> None:
    roots = {name.split(".", maxsplit=1)[0] for name in _imports()}

    assert roots <= {
        "__future__",
        "agentx",
        "collections",
        "dataclasses",
        "enum",
        "json",
        "typing",
    }
