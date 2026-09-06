"""Architecture-boundary tests for the A6.01 hierarchical task decomposition.

The decomposition contract is canonical plan/decomposition *data*: it must live
in the inward ``agentx.core`` boundary, must depend only on stdlib and sibling
``agentx.core`` contracts, and must not reach outward for authority
(``agentx.kernel``), execution (``agentx.capabilities`` / ``agentx.cognition``
runtime), or plumbing. It exposes no execution, scheduling, permission, or
success-claim surface, and the model-acceptance boundary never treats model
text as executable authority.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx.core.ids import DecompositionId
from agentx.core.task_decomposition import (
    TaskDecomposition,
    accept_model_proposal,
)

_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "agentx" / "core" / "task_decomposition.py"
)


def _tree() -> ast.Module:
    return ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))


def _imports() -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_decomposition_contract_is_placed_in_core_boundary() -> None:
    assert _MODULE_PATH.is_file()
    assert _MODULE_PATH.parts[-3:] == ("agentx", "core", "task_decomposition.py")
    assert TaskDecomposition.__module__ == "agentx.core.task_decomposition"
    assert DecompositionId.__module__ == "agentx.core.ids"


def test_decomposition_contract_depends_only_on_core_contracts() -> None:
    imports = _imports()
    agentx_imports = {name for name in imports if name.startswith("agentx.")}
    assert agentx_imports
    assert all(name.startswith("agentx.core.") for name in agentx_imports)


def test_decomposition_does_not_reach_outward_for_authority_or_execution() -> None:
    forbidden = (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.hive",
        "agentx.procedures",
        "agentx.learning",
        "agentx.infrastructure",
        "agentx.cognition",
    )
    imports = _imports()
    assert not any(name.startswith(prefix) for name in imports for prefix in forbidden)


def test_decomposition_has_no_process_network_or_import_machinery() -> None:
    forbidden_roots = {
        "aiohttp",
        "asyncio",
        "http",
        "httpx",
        "importlib",
        "multiprocessing",
        "requests",
        "shutil",
        "socket",
        "subprocess",
        "urllib",
    }
    roots = {name.split(".", maxsplit=1)[0] for name in _imports()}
    assert not roots & forbidden_roots


def test_decomposition_exposes_no_execution_authority_or_completion_api() -> None:
    forbidden_prefixes = {
        "execute",
        "run",
        "invoke",
        "dispatch",
        "schedule",
        "grant",
        "permit",
        "allow",
        "mark",
        "complete",
        "succeed",
        "finish",
        "verify",
        "transition",
        "reason",
        "route",
        "plan",
        "retry",
        "fallback",
        "cancel",
    }
    methods = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert not any(name.startswith(prefix) for name in methods for prefix in forbidden_prefixes)


def test_decomposition_never_evals_or_executes_text() -> None:
    called: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    assert not called & {"eval", "exec", "compile"}


def test_decomposition_has_no_persistence_or_event_publication_machinery() -> None:
    source = _MODULE_PATH.read_text(encoding="utf-8").lower()
    forbidden_fragments = (
        "sqlite",
        "eventbus",
        "event_bus",
        "publish(",
        "emit(",
        "journal",
    )
    assert not any(fragment in source for fragment in forbidden_fragments)


def test_decomposition_records_carry_no_success_claim_fields() -> None:
    """Structural no-success invariant: no status/success-claim fields on either record."""
    tree = _tree()
    claim_fields = {
        "status",
        "success",
        "succeeded",
        "completed",
        "done",
        "finished",
        "is_success",
        "verified",
    }
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in {
            "TaskDecomposition",
            "DecompositionNode",
        }:
            field_names = {
                child.target.id
                for child in node.body
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
            }
            assert not field_names & claim_fields, field_names & claim_fields


def test_decomposition_id_is_defined_once_in_canonical_ids() -> None:
    """The decomposition identity type lives only in ``agentx.core.ids``."""
    src_root = Path(__file__).resolve().parents[2] / "src"
    owners = [
        path.relative_to(src_root)
        for path in src_root.rglob("*.py")
        if path.is_file()
        and any(
            isinstance(node, ast.ClassDef) and node.name == "DecompositionId"
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        )
    ]
    assert owners == [Path("agentx/core/ids.py")]


def test_no_module_level_decomposition_instances() -> None:
    tree = _tree()
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            assert value.func.id not in {"TaskDecomposition", "DecompositionNode"}


def test_acceptance_boundary_is_the_only_model_entry_point() -> None:
    """Model/reasoner output enters exactly one public, inert validation surface."""
    assert accept_model_proposal.__module__ == "agentx.core.task_decomposition"
    # The boundary consumes JSON-compatible data; it never imports a model or
    # provider surface from any other subsystem.
    imports = _imports()
    forbidden = ("reasoner", "model_provider", "model_roles", "research_provider")
    assert not any(name.split(".")[-1] in forbidden for name in imports)
