"""Architecture guards for the C5.03 browser DOM read boundary."""

from __future__ import annotations

import ast
from pathlib import Path

from agentx.capabilities.browser_connection import BrowserTargetRef
from agentx.capabilities.browser_dom import BrowserDomReader, BrowserDomReadRequest

_ROOT = Path(__file__).parents[2]
_MODULE = _ROOT / "src" / "agentx" / "capabilities" / "browser_dom.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)


def _import_roots() -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", maxsplit=1)[0])
    return roots


def _agentx_imports() -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names if alias.name.startswith("agentx."))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("agentx."):
            imports.add(node.module)
    return imports


def _called_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name):
            names.add(function.id)
        elif isinstance(function, ast.Attribute):
            names.add(function.attr)
    return names


def test_dom_boundary_lives_in_capabilities_and_reuses_canonical_browser_contracts() -> None:
    assert _MODULE.as_posix().endswith("src/agentx/capabilities/browser_dom.py")
    assert _agentx_imports() == {
        "agentx.capabilities.browser_connection",
        "agentx.capabilities.browser_provider",
    }


def test_dom_boundary_has_no_network_browser_process_or_external_dependency_imports() -> None:
    forbidden_roots = {
        "asyncio",
        "http",
        "requests",
        "selenium",
        "playwright",
        "socket",
        "subprocess",
        "urllib",
        "webbrowser",
        "websocket",
        "websockets",
    }

    assert _import_roots().isdisjoint(forbidden_roots)


def test_dom_boundary_has_no_kernel_hive_procedure_persistence_or_runtime_dependency() -> None:
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.hive",
        "agentx.procedures",
        "agentx.infrastructure",
        "agentx.capabilities.runtime",
        "agentx.capabilities.executor",
        "agentx.capabilities.registry",
    )

    for imported in _agentx_imports():
        assert not imported.startswith(forbidden_prefixes)


def test_dom_reader_protocol_exposes_only_one_read_operation() -> None:
    protocol = next(
        node
        for node in _TREE.body
        if isinstance(node, ast.ClassDef) and node.name == "BrowserDomReader"
    )
    public_methods = [
        node.name
        for node in protocol.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    ]

    assert public_methods == ["observe_dom"]


def test_dom_boundary_contains_no_browser_mutation_calls() -> None:
    forbidden_calls = {
        "accept_dialog",
        "add_cookie",
        "click",
        "close",
        "download",
        "evaluate",
        "execute",
        "execute_javascript",
        "fill",
        "goto",
        "launch",
        "navigate",
        "new_page",
        "open",
        "press",
        "send_keys",
        "set_cookie",
        "submit",
        "type_text",
        "upload",
    }

    assert _called_names().isdisjoint(forbidden_calls)


def test_dom_boundary_has_no_screenshot_ocr_or_vision_surface() -> None:
    lowered = _SOURCE.lower()
    forbidden = (
        "screenshot(",
        "ocr(",
        "computer_vision",
        "image_recognition",
        "vision_model",
    )

    assert all(token not in lowered for token in forbidden)


def test_dom_boundary_has_no_cookie_credential_authentication_surface() -> None:
    class_names = {
        node.name
        for node in _TREE.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    lowered_names = {name.lower() for name in class_names}

    assert all("cookie" not in name for name in lowered_names)
    assert all("credential" not in name for name in lowered_names)
    assert all("authenticat" not in name for name in lowered_names)


def test_dom_boundary_does_not_generate_random_or_uuid_identity() -> None:
    assert "random" not in _import_roots()
    assert "uuid" not in _import_roots()
    assert "uuid4" not in _called_names()
    assert "token_hex" not in _called_names()


def test_dom_boundary_has_no_persistence_or_cache_contract() -> None:
    lowered = _SOURCE.lower()
    forbidden = (
        "sqlite3",
        "insert into",
        "update agentx_",
        "create table",
        "migration",
        "global_cache",
        "lru_cache",
    )

    assert all(token not in lowered for token in forbidden)


def test_dom_read_request_is_canonical_c5_02_target_bound() -> None:
    annotations = BrowserDomReadRequest.__annotations__

    assert annotations["target"] in {BrowserTargetRef, "BrowserTargetRef"}
    assert hasattr(BrowserDomReader, "observe_dom")


def test_dom_module_defines_no_capability_implementation_class() -> None:
    class_names = {node.name for node in _TREE.body if isinstance(node, ast.ClassDef)}

    assert "Capability" not in class_names
    assert not any(name.endswith("Capability") for name in class_names)


def test_dom_module_has_no_c5_04_action_surface() -> None:
    lowered = _SOURCE.lower()
    forbidden_phrases = (
        "def click",
        "def type",
        "def fill",
        "def submit",
        "def navigate",
        "def execute_javascript",
        "def upload",
        "def download",
        "def open_tab",
        "def close_tab",
    )

    assert all(phrase not in lowered for phrase in forbidden_phrases)
