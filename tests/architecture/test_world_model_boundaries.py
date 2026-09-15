from __future__ import annotations

import ast
from pathlib import Path

import agentx.world_model as world_model


def _source_path() -> Path:
    path = Path(world_model.__file__ or "")
    assert path.is_file()
    return path


def test_world_model_is_outer_composition_not_core_replacement() -> None:
    assert world_model.__name__ == "agentx.world_model"
    assert "agentx.core.world_state" not in _source_path().read_text(encoding="utf-8")


def test_world_model_does_not_import_kernel_authority_or_capability_runtime() -> None:
    tree = ast.parse(_source_path().read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.capabilities.runtime",
        "agentx.capabilities.executor",
    )
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported
        for prefix in forbidden_prefixes
    )


def test_world_model_has_no_future_visual_provider_scope() -> None:
    names = set(vars(world_model))
    forbidden = {
        "ScreenCaptureProvider",
        "ScreenFrameIdentity",
        "VisualGrounding",
        "VisualFallbackRouter",
        "VisualTargetProposal",
        "EvidenceRanker",
        "DpiNormalizer",
        "MultiMonitorManager",
    }
    assert names.isdisjoint(forbidden)


def test_world_model_does_not_define_a_second_event_bus_or_hive_store() -> None:
    classes = {
        node.name
        for node in ast.walk(ast.parse(_source_path().read_text(encoding="utf-8")))
        if isinstance(node, ast.ClassDef)
    }
    assert "EventBus" not in classes
    assert "KnowledgeStore" not in classes
    assert "SQLiteDatabase" not in classes
