"""AX-541 architecture guards: detection is descriptive, never extension loading."""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PATH = _ROOT / "src/agentx/capability_gap.py"


def test_capability_gap_detector_imports_no_installation_or_execution_owner() -> None:
    source = _PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)

    forbidden_roots = {"subprocess", "importlib", "pip", "venv"}
    assert not {name for name in imported if name.partition(".")[0] in forbidden_roots}
    assert not ({"execute", "verify", "register", "install", "load_module"} & calls)
    assert "agentx.kernel" not in source
    assert "agentx.capabilities.runtime" not in source


def test_detector_has_no_generation_or_installation_api() -> None:
    source = _PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name not in {"__init__", "__post_init__", "is_satisfied"}
    }
    assert "detect" in methods
    assert not ({"generate", "install", "download", "import_plugin", "register"} & methods)
