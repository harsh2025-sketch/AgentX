"""Architecture guardrails for the A4.04 research acquisition boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import agentx.cognition.research_acquisition as acquisition

_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _ROOT / "src" / "agentx" / "cognition" / "research_acquisition.py"
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
    return {node.name for node in ast.walk(_TREE) if isinstance(node, ast.ClassDef)}


def test_boundary_lives_in_research_specific_cognition_module() -> None:
    assert acquisition.__name__ == "agentx.cognition.research_acquisition"
    assert _MODULE.as_posix().endswith("src/agentx/cognition/research_acquisition.py")


def test_boundary_uses_research_specific_vocabulary_only() -> None:
    class_names = _class_names()

    assert "ResearchAcquisitionPort" in class_names
    # No generic universal provider abstraction.
    assert "Provider" not in class_names
    assert "ProviderPort" not in class_names
    assert "AcquisitionPort" not in class_names


def test_boundary_consumes_only_canonical_a403_contracts() -> None:
    import agentx.cognition.research_provider as provider

    agentx_imports = {name for name in _imports() if name.startswith("agentx.")}

    assert agentx_imports == {"agentx.cognition.research_provider"}
    # The A4.03 contracts are reused, never redefined in A4.04: every
    # reference is the exact canonical class object.
    assert acquisition.ResearchRequest is provider.ResearchRequest
    assert acquisition.ResearchResponse is provider.ResearchResponse
    assert acquisition.ResearchProviderValidationError is (provider.ResearchProviderValidationError)
    assert not hasattr(acquisition, "ResearchProviderIdentity")


def test_boundary_defines_no_competing_failure_hierarchy() -> None:
    class_names = _class_names()

    assert not any("Failure" in name for name in class_names)
    assert not any("Error" in name for name in class_names)
    assert not any("Exception" in name for name in class_names)


def test_boundary_has_no_kernel_hive_persistence_or_capability_edge() -> None:
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.procedures",
        "agentx.capabilities",
    )

    assert not any(
        imported.startswith(prefix)
        for prefix in forbidden_prefixes
        for imported in _imports()
        if imported.startswith("agentx.")
    )


def test_boundary_has_no_network_browser_process_or_model_imports() -> None:
    forbidden_roots = {
        "aiohttp",
        "asyncio",
        "http",
        "httpx",
        "multiprocessing",
        "openai",
        "os",
        "requests",
        "selenium",
        "socket",
        "sqlite3",
        "subprocess",
        "threading",
        "urllib",
        "webbrowser",
        "websocket",
        "websockets",
        "playwright",
    }
    roots = {name.split(".", maxsplit=1)[0] for name in _imports()}

    assert roots.isdisjoint(forbidden_roots)


def test_boundary_has_no_search_network_execution_or_io_call_surface() -> None:
    forbidden_calls = {
        "browse",
        "connect",
        "execute",
        "get",
        "launch",
        "open",
        "post",
        "request",
        "run",
        "search",
        "send",
        "spawn",
        "scrape",
        "fetch",
        "download",
        "invoke_model",
    }

    assert _called_names().isdisjoint(forbidden_calls)


def test_boundary_defines_no_authority_trust_or_persistence_surface() -> None:
    module_names = {name.lower() for name in vars(acquisition)}
    class_names = _class_names()

    forbidden_tokens = (
        "permission",
        "authority",
        "authorize",
        "gate",
        "risk",
        "budget",
        "verified",
        "verify",
        "store",
        "cache",
        "registry",
        "database",
        "migration",
        "claim",
        "reputation",
        "score",
        "summar",
    )

    assert all(not any(token in name.lower() for token in forbidden_tokens) for name in class_names)
    # Scan public module surface only; dunder metadata (e.g. ``__package__``)
    # is Python bookkeeping, not an A4.04 contract.
    public_names = {name for name in module_names if not name.startswith("_")}

    assert all(not any(token in name for token in forbidden_tokens) for name in public_names)


def test_boundary_has_no_persistence_global_state_or_registration() -> None:
    lowered = {name.lower() for name in vars(acquisition)}

    assert "current_provider" not in lowered
    assert "provider_registry" not in lowered
    assert "research_acquisition_store" not in lowered
    assert "research_cache" not in lowered


def test_boundary_does_not_add_runtime_dependency_imports() -> None:
    roots = {name.split(".", maxsplit=1)[0] for name in _imports()}

    assert roots <= {"__future__", "agentx", "typing"}


def test_boundary_ship_no_concrete_provider_implementation() -> None:
    # The only class is the Protocol itself; there is no concrete provider.
    class_names = _class_names()

    assert class_names == {"ResearchAcquisitionPort"}
    # Protocol methods have no body beyond the ellipsis sentinel.
    for node in ast.walk(_TREE):
        if isinstance(node, ast.ClassDef) and node.name == "ResearchAcquisitionPort":
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    # A docstring plus the ``...`` sentinel, and nothing else:
                    # the port ships no implementation.
                    body = list(item.body)
                    if (
                        isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)
                    ):
                        body = body[1:]
                    assert len(body) == 1
                    assert isinstance(body[0], ast.Expr)
                    assert isinstance(body[0].value, ast.Constant)
                    assert body[0].value.value is Ellipsis
