"""Architecture guardrails for the A4.02 research objective contract."""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

from agentx.cognition import research_objective
from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_MODULE = _AGENTX_SRC / "cognition" / "research_objective.py"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _module_tree(path: Path = _MODULE) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _code_source(path: Path = _MODULE) -> str:
    """Return executable source only: docstrings/comments are documentation."""

    return "\n".join(
        ast.unparse(node) for node in _module_tree(path).body if not _is_docstring(node)
    )


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in sorted(_AGENTX_SRC.rglob("*.py")):
        if any(
            isinstance(node, ast.ClassDef) and node.name == name
            for node in ast.walk(_module_tree(path))
        ):
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


def _executable_identifiers(path: Path = _MODULE) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.arg | ast.keyword) and node.arg is not None:
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add(node.name)
            if node.asname is not None:
                names.add(node.asname)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return frozenset(name.lower() for name in names)


def test_research_objective_lives_in_cognition_boundary() -> None:
    assert _MODULE.is_file()
    assert research_objective.__name__ == "agentx.cognition.research_objective"


def test_public_api_is_narrow() -> None:
    tree = _module_tree()
    public_classes = {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }
    public_functions = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and not node.name.startswith("_")
    }

    assert public_classes == {
        "ResearchObjective",
        "ResearchObjectiveValidationError",
        "UnsupportedResearchObjectiveSchemaVersionError",
    }
    assert public_functions == {"research_objective_from_gap"}
    assert set(research_objective.__all__) == public_classes | public_functions | {
        "CURRENT_RESEARCH_OBJECTIVE_SCHEMA_VERSION"
    }


def test_objective_methods_are_pure_serialization() -> None:
    methods = {
        item.name
        for node in _module_tree().body
        if isinstance(node, ast.ClassDef) and node.name == "ResearchObjective"
        for item in node.body
        if isinstance(item, ast.FunctionDef) and not item.name.startswith("__")
    }

    assert methods == {"to_dict", "to_json", "from_dict", "from_json"}


def test_definitions_are_unique() -> None:
    for name in (
        "ResearchObjective",
        "ResearchObjectiveValidationError",
        "UnsupportedResearchObjectiveSchemaVersionError",
    ):
        assert _class_definitions(name) == [Path("agentx/cognition/research_objective.py")]


def test_a401_and_core_contracts_are_reused_not_duplicated() -> None:
    agentx_imports = {module for module in _imports() if module.startswith("agentx.")}

    assert agentx_imports == {"agentx.cognition.gap_detector", "agentx.core.knowledge"}

    for name in (
        "KnowledgeGapRequirement",
        "KnowledgeGapAssessment",
        "KnowledgeRecord",
        "KnowledgeScope",
        "KnowledgeStatus",
        "ProvenanceKind",
        "ProvenanceReference",
    ):
        assert Path("agentx/cognition/research_objective.py") not in _class_definitions(name)


def test_uses_only_standard_library_and_agentx() -> None:
    non_stdlib = [
        module
        for module in _imports()
        if module.split(".", maxsplit=1)[0] not in sys.stdlib_module_names
        and not module.startswith("agentx")
    ]

    assert non_stdlib == []


def test_imports_no_authority_execution_model_or_retrieval_subsystem() -> None:
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
        "agentx.cognition.task_manager",
        "agentx.cognition.router",
        "http",
        "urllib",
        "socket",
        "ssl",
        "ftplib",
        "subprocess",
        "webbrowser",
        "requests",
        "aiohttp",
        "httpx",
        "importlib",
        "pkgutil",
        "os",
        "pathlib",
        "sqlite3",
        "shutil",
    )
    imports = _imports()

    assert not any(module.startswith(prefix) for module in imports for prefix in forbidden_prefixes)


def test_has_no_persistence_or_migration() -> None:
    source = _code_source().lower()
    forbidden_fragments = (
        "sqlite",
        "migration",
        "create table",
        "knowledge_store",
        "event_bus",
        "event_journal",
        "artifact_store",
        "procedure_store",
        "open(",
    )

    assert not any(fragment in source for fragment in forbidden_fragments)
    assert not any("research" in migration.name for migration in _MIGRATIONS)
    assert not any("objective" in migration.name for migration in _MIGRATIONS)


def test_does_not_execute_research_capabilities_or_models() -> None:
    forbidden_calls = {
        "execute",
        "invoke",
        "dispatch",
        "run",
        "route",
        "plan",
        "research",
        "browse",
        "fetch",
        "get",
        "post",
        "download",
        "crawl",
        "search",
        "rank",
        "embed",
        "complete",
        "verify",
        "promote",
        "remember",
        "insert",
        "update_status",
        "open",
        "eval",
        "exec",
        "compile",
        "__import__",
        "system",
        "popen",
    }

    assert _called_names().isdisjoint(forbidden_calls)


def test_contains_no_a403_plus_research_execution_behaviour() -> None:
    identifiers = _executable_identifiers()
    forbidden_fragments = (
        "researchprovider",
        "documentacquisition",
        "claimextraction",
        "hypothesis",
        "experiment",
        "provider",
        "crawler",
        "browser",
        "endpoint",
        "apikey",
        "querydsl",
        "ranking",
        "embedding",
        "vector",
        "model",
        "prompt",
        "fallback",
        "retry",
        "budget",
        "permission",
        "risk",
        "authoriz",
        "credential",
    )

    assert not any(
        fragment in identifier for identifier in identifiers for fragment in forbidden_fragments
    )


def test_no_url_or_domain_trust_inference() -> None:
    source = _code_source().lower()
    forbidden_fragments = (
        "https://",
        "http://",
        "www.",
        ".com",
        "domain",
        "hostname",
        "netloc",
        "urlparse",
        "tld",
        "whitelist",
        "allowlist",
        "trusted_domains",
    )
    assert not any(fragment in source for fragment in forbidden_fragments)


def test_runtime_distribution_still_declares_no_dependencies() -> None:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]

    assert project["dependencies"] == []
