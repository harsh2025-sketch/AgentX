"""Architecture guards for the C5.04 browser DOM selection boundary."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

from agentx.capabilities.browser_dom import BrowserDomAttribute
from agentx.capabilities.browser_selection import (
    BrowserDomSelectionResult,
    BrowserDomSelectionStatus,
    BrowserDomSelector,
    BrowserDomSelectorKind,
    select_dom_nodes,
)

_ROOT = Path(__file__).parents[2]
_MODULE = _ROOT / "src" / "agentx" / "capabilities" / "browser_selection.py"
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


def _function_names() -> set[str]:
    return {
        node.name
        for node in ast.walk(_TREE)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_selection_lives_in_capabilities_boundary() -> None:
    assert _MODULE.exists()
    assert "agentx" in _MODULE.parts
    assert "capabilities" in _MODULE.parts
    assert _MODULE.name == "browser_selection.py"


def test_selection_reuses_canonical_c503_dom_contracts() -> None:
    imported = _imports()

    assert "agentx.capabilities.browser_dom" in imported
    assert "agentx.capabilities.browser_connection" not in imported
    assert "agentx.capabilities.browser_provider" not in imported
    assert BrowserDomSelector.__annotations__["node"] == "BrowserDomNodeRef | None"
    assert BrowserDomSelectionResult.__annotations__["observation"] == "BrowserDomObservation"


def test_selector_vocabulary_is_exactly_the_assigned_minimal_set() -> None:
    assert [kind.value for kind in BrowserDomSelectorKind] == [
        "node",
        "tag",
        "role",
        "accessible_name",
        "attribute",
    ]
    assert [field.name for field in fields(BrowserDomSelector)] == [
        "node",
        "tag",
        "role",
        "accessible_name",
        "attribute",
    ]
    assert BrowserDomSelector.__annotations__["attribute"] == "BrowserDomAttribute | None"


def test_ambiguity_vocabulary_has_no_ranking_or_preference_state() -> None:
    assert [status.value for status in BrowserDomSelectionStatus] == [
        "no_match",
        "unique",
        "ambiguous",
    ]
    assert "first" not in BrowserDomSelectionStatus.__members__
    assert "last" not in BrowserDomSelectionStatus.__members__


def test_module_imports_no_browser_transport_or_automation_library() -> None:
    imported = _imports()
    forbidden = {
        "playwright",
        "selenium",
        "socket",
        "websocket",
        "websockets",
        "requests",
        "urllib",
        "http",
        "subprocess",
        "asyncio",
    }

    assert imported.isdisjoint(forbidden)
    assert all(not name.startswith("playwright.") for name in imported)
    assert all(not name.startswith("selenium.") for name in imported)


def test_module_imports_no_vision_ocr_or_image_stack() -> None:
    imported = _imports()
    forbidden = {
        "cv2",
        "PIL",
        "pytesseract",
        "easyocr",
        "torch",
        "torchvision",
    }

    assert imported.isdisjoint(forbidden)


def test_module_imports_no_persistence_or_hive_surface() -> None:
    imported = _imports()

    assert "sqlite3" not in imported
    assert "agentx.hive" not in imported
    assert "agentx.infrastructure" not in imported
    assert all(not name.startswith("agentx.hive.") for name in imported)
    assert all(not name.startswith("agentx.infrastructure.") for name in imported)


def test_module_imports_no_authority_runtime_or_procedure_surface() -> None:
    imported = _imports()
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.procedures",
        "agentx.cognition",
        "agentx.learning",
    )

    assert all(
        not imported_name.startswith(prefix)
        for imported_name in imported
        for prefix in forbidden_prefixes
    )


def test_module_defines_no_browser_action_methods() -> None:
    function_names = _function_names()
    forbidden = {
        "click",
        "fill",
        "navigate",
        "open_tab",
        "submit",
        "type",
        "upload",
        "download",
        "execute_javascript",
        "evaluate_javascript",
        "refresh",
        "reconnect",
    }

    assert function_names.isdisjoint(forbidden)


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


def test_module_has_no_regex_fuzzy_embedding_or_model_dependency() -> None:
    imported = _imports()
    calls = _call_names()

    assert "re" not in imported
    assert "regex" not in imported
    assert "rapidfuzz" not in imported
    assert "difflib" not in imported
    assert "sentence_transformers" not in imported
    assert "transformers" not in imported
    assert "openai" not in imported
    assert "search" not in calls
    assert "match" not in calls
    assert "fullmatch" not in calls
    assert "similarity" not in calls
    assert "score" not in calls
    assert "embed" not in calls


def test_selection_api_is_data_transformation_not_provider_port() -> None:
    assert callable(select_dom_nodes)
    assert "Protocol" not in _imports()
    assert "BrowserDomReader" not in _SOURCE


def test_result_fields_contain_only_source_data_selector_status_matches_and_schema() -> None:
    assert [field.name for field in fields(BrowserDomSelectionResult)] == [
        "observation",
        "selector",
        "status",
        "matches",
        "schema_version",
    ]


def test_production_module_does_not_define_css_xpath_or_selector_engine_types() -> None:
    class_names = {node.name for node in _TREE.body if isinstance(node, ast.ClassDef)}

    assert all("Css" not in name and "XPath" not in name for name in class_names)
    assert "CssSelector" not in class_names
    assert "XPathSelector" not in class_names


def test_production_module_has_no_task_permission_risk_budget_or_stop_symbols() -> None:
    forbidden_symbols = {
        "ActionGate",
        "AuthorityContext",
        "EmergencyStop",
        "Permission",
        "ResourceBudget",
        "RiskLevel",
        "Task",
        "Procedure",
    }

    assert all(symbol not in _SOURCE for symbol in forbidden_symbols)


def test_source_uses_exact_c503_node_and_attribute_types() -> None:
    selector = BrowserDomSelector(attribute=BrowserDomAttribute("data-x", "1"))

    assert isinstance(selector.attribute, BrowserDomAttribute)
    assert BrowserDomSelector.__annotations__["node"] == "BrowserDomNodeRef | None"
