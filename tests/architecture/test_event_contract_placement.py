"""Architecture tests for the canonical Event contract placement."""

from __future__ import annotations

import ast
from pathlib import Path

from agentx.core.events import Event

_REPO_ROOT = Path(__file__).resolve().parents[2]
_AGENTX_SRC = _REPO_ROOT / "src" / "agentx"


def test_event_has_exactly_one_canonical_definition() -> None:
    """Only ``agentx.core.events`` defines the canonical Event class."""
    definitions: list[Path] = []
    for path in _AGENTX_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ClassDef) and node.name == "Event" for node in tree.body):
            definitions.append(path.relative_to(_REPO_ROOT / "src"))

    assert definitions == [Path("agentx/core/events.py")]
    assert Event.__module__ == "agentx.core.events"
    assert not (_AGENTX_SRC / "infrastructure" / "events.py").exists()


def test_no_production_import_references_old_event_module() -> None:
    """Production code contains no import of the removed infrastructure contract."""
    stale_imports: list[Path] = []
    for path in _AGENTX_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "agentx.infrastructure.events"
                or alias.name.startswith("agentx.infrastructure.events.")
                for alias in node.names
            ):
                stale_imports.append(path.relative_to(_REPO_ROOT / "src"))
                break
            if isinstance(node, ast.ImportFrom) and (
                node.module == "agentx.infrastructure.events"
                or (node.module or "").startswith("agentx.infrastructure.events.")
            ):
                stale_imports.append(path.relative_to(_REPO_ROOT / "src"))
                break

    assert stale_imports == []
