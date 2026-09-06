"""Architecture guardrails for C6.07 bounded cognition-context construction."""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

from agentx.cognition import context_construction

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_MODULE = _AGENTX_SRC / "cognition" / "context_construction.py"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _module_tree(path: Path = _MODULE) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in sorted(_AGENTX_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            # A module using newer-than-running-interpreter syntax cannot
            # define a class with these names anyway; skip it.
            continue
        if any(isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree)):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


def _imports(path: Path = _MODULE) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _called_names(path: Path = _MODULE) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def test_context_construction_lives_in_cognition_boundary() -> None:
    assert _MODULE.is_file()
    assert context_construction.__name__ == "agentx.cognition.context_construction"


def test_context_construction_public_api_is_narrow() -> None:
    public_classes = {
        node.name
        for node in _module_tree().body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }
    assert public_classes == {
        "ContextBudget",
        "ContextBuilder",
        "ContextCategory",
        "ContextConstructionError",
        "ContextConstructionValidationError",
        "ContextItem",
        "ContextSource",
        "CognitionContext",
        "CognitionContextBuildRequest",
        "EnvironmentalObservation",
        "OmittedContextItem",
        "OmissionReason",
    }

    builder_methods = {
        item.name
        for node in _module_tree().body
        if isinstance(node, ast.ClassDef) and node.name == "ContextBuilder"
        for item in node.body
        if isinstance(item, ast.FunctionDef) and not item.name.startswith("_")
    }
    assert builder_methods == {"build"}


def test_context_construction_definitions_are_unique() -> None:
    for name in (
        "ContextBuilder",
        "CognitionContext",
        "ContextItem",
        "ContextBudget",
    ):
        assert _class_definitions(name) == [Path("agentx/cognition/context_construction.py")]


def test_context_construction_imports_only_core_contracts_within_agentx() -> None:
    agentx_imports = {module for module in _imports() if module.startswith("agentx.")}
    assert {module.split(".")[1] for module in agentx_imports} == {"core"}
    # Only canonical core record/scope contracts, never kernel authority types.
    for module in agentx_imports:
        assert module.startswith("agentx.core.")
        assert not module.startswith("agentx.core.execution")


def test_context_construction_uses_only_standard_library_and_agentx() -> None:
    non_stdlib = [
        module
        for module in _imports()
        if module.split(".", maxsplit=1)[0] not in sys.stdlib_module_names
        and not module.startswith("agentx")
    ]
    assert non_stdlib == []


def test_context_construction_touches_no_retrieval_store_or_authority_subsystem() -> None:
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.procedures",
        "agentx.learning",
        "agentx.cognition.model_provider",
        "agentx.cognition.model_roles",
        "agentx.cognition.reasoner",
        "http",
        "urllib",
        "socket",
        "subprocess",
        "requests",
        "aiohttp",
        "httpx",
        "importlib",
        "pkgutil",
    )
    imports = _imports()
    assert not any(module.startswith(prefix) for module in imports for prefix in forbidden_prefixes)


def test_context_construction_does_not_invoke_models_execute_or_retrieve() -> None:
    forbidden_calls = {
        "invoke",
        "execute",
        "dispatch",
        "run",
        "route",
        "plan",
        "research",
        "retrieve",
        "remember",
        "insert",
        "update_status",
        "observe",
        "record_episode",
        "record_negative_experience",
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
    }
    assert _called_names().isdisjoint(forbidden_calls)


def test_context_construction_has_no_persistence_or_model_surface() -> None:
    source = _MODULE.read_text(encoding="utf-8").lower()
    forbidden_fragments = (
        "sqlite",
        "create table",
        "migration",
        "event_bus",
        "event_journal",
        "knowledge_store",
        "episode_store",
        "procedure_store",
        "modelprovider",
        "model_id",
        "embedding",
        "vector",
        "cosine",
    )
    assert not any(fragment in source for fragment in forbidden_fragments)


def test_context_construction_does_not_implement_c608_protection() -> None:
    # C6.08 owns content protection; C6.07 keeps content verbatim and only
    # frames/indents it structurally. Scan executable code only (module/function
    # docstrings are documentation, not behaviour).
    tree = _module_tree()
    code_chunks: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            code_chunks.extend(
                ast.unparse(stmt)
                for stmt in node.body
                if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Constant)
            )
        elif isinstance(node, ast.ClassDef):
            code_chunks.extend(
                ast.unparse(stmt)
                for stmt in node.body
                if isinstance(stmt, ast.Assign | ast.AnnAssign)
            )
    code = "\n".join(code_chunks).lower()
    for forbidden in ("sanitize", "sanitise", "escape(", "strip_instruction", "defang"):
        assert forbidden not in code


def test_runtime_distribution_still_declares_no_dependencies() -> None:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert project["dependencies"] == []
