"""Architecture guards for C2.04 ArtifactStore/AuditStore boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CORE_ARTIFACTS = _REPO_ROOT / "src" / "agentx" / "core" / "artifacts.py"
_CORE_AUDIT = _REPO_ROOT / "src" / "agentx" / "core" / "audit_records.py"
_ARTIFACT_STORE = _REPO_ROOT / "src" / "agentx" / "infrastructure" / "artifact_store.py"
_AUDIT_STORE = _REPO_ROOT / "src" / "agentx" / "infrastructure" / "audit_store.py"
_AUDIT_ADAPTER = _REPO_ROOT / "src" / "agentx" / "kernel" / "audit_persistence.py"
_MODULES = (
    _CORE_ARTIFACTS,
    _CORE_AUDIT,
    _ARTIFACT_STORE,
    _AUDIT_STORE,
    _AUDIT_ADAPTER,
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            result.add(node.module)
    return result


def _called_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_core_records_remain_dependency_leaf_contracts() -> None:
    for path in (_CORE_ARTIFACTS, _CORE_AUDIT):
        imports = _imports(path)
        agentx_imports = {name for name in imports if name.startswith("agentx.")}
        assert agentx_imports <= {"agentx.core.ids"}


def test_kernel_adapter_points_inward_and_never_imports_infrastructure() -> None:
    imports = _imports(_AUDIT_ADAPTER)

    assert "agentx.core.audit_records" in imports
    assert "agentx.kernel.audit" in imports
    assert not any(name.startswith("agentx.infrastructure") for name in imports)


def test_concrete_stores_reuse_only_core_and_canonical_persistence() -> None:
    allowed = {
        "agentx.core.artifacts",
        "agentx.core.audit_records",
        "agentx.core.ids",
        "agentx.infrastructure.persistence",
    }
    for path in (_ARTIFACT_STORE, _AUDIT_STORE):
        imports = _imports(path)
        agentx_imports = {name for name in imports if name.startswith("agentx.")}
        assert agentx_imports <= allowed
        assert "agentx.infrastructure.persistence" in agentx_imports


def test_stores_have_no_cross_store_event_procedure_knowledge_or_model_coupling() -> None:
    forbidden_fragments = {
        "event_journal",
        "event_bus",
        "episode_store",
        "procedure_store",
        "knowledge_store",
        "model_provider",
        "agentx.cognition",
        "agentx.capabilities",
        "agentx.procedures",
    }
    for path in _MODULES:
        imports = _imports(path)
        assert not any(
            fragment in imported
            for imported in imports
            for fragment in forbidden_fragments
        )


def test_no_pickle_eval_exec_dynamic_import_or_artifact_open_fetch_calls() -> None:
    forbidden_imports = {"pickle", "importlib"}
    forbidden_calls = {"eval", "exec", "__import__", "urlopen", "open"}

    for path in _MODULES:
        assert _imports(path).isdisjoint(forbidden_imports)
        assert _called_names(path).isdisjoint(forbidden_calls)
