"""Architecture guardrails for A5.06 keyboard/text/clipboard capabilities."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WINDOWS_PKG = _REPO_ROOT / "src" / "agentx" / "capabilities" / "windows"
_SURFACE = _WINDOWS_PKG / "keyboard_clipboard.py"
_NATIVE = _WINDOWS_PKG / "_native.py"

_FORBIDDEN_NATIVE_BEHAVIOR = (
    "RegisterHotKey",
    "SetWindowsHookEx",
    "GetAsyncKeyState",
    "GetKeyboardState",
    "SetForegroundWindow",
    "FindWindow",
    "UIAutomation",
    "uiautomation",
    "pywinauto",
    "screenshot",
    "BitBlt",
    "PrintWindow",
)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def test_a506_reuses_the_existing_native_seam_and_canonical_contracts() -> None:
    assert _SURFACE.is_file()
    imported = _imports(_SURFACE)
    assert "agentx.capabilities.abi" in imported
    assert "agentx.capabilities.windows.provider" in imported
    assert "agentx.capabilities.windows" in imported
    assert "agentx.core.execution" in imported
    assert "agentx.kernel.permissions" in imported
    assert "agentx.kernel.risk" in imported
    assert "agentx.capabilities.registry" not in imported
    assert not any(module.split(".")[0] == "ctypes" for module in imported)


def test_a506_respects_canonical_subsystem_edges() -> None:
    allowed = {_architecture.CORE, _architecture.KERNEL, _architecture.CAPABILITIES}
    for module in _imports(_SURFACE):
        if not module.startswith("agentx"):
            continue
        owner = next(
            (
                package
                for package in _architecture.SUBSYSTEMS
                if module == package or module.startswith(f"{package}.")
            ),
            None,
        )
        assert owner is None or owner in allowed, f"keyboard_clipboard.py -> {module}"


def test_native_surface_contains_no_capture_hotkey_focus_uia_or_visual_fallback() -> None:
    source = _NATIVE.read_text(encoding="utf-8")
    for marker in _FORBIDDEN_NATIVE_BEHAVIOR:
        assert marker not in source, f"A5.06 native seam contains forbidden marker {marker}"


def test_a506_surface_contains_no_second_execution_loop_or_registry_authority() -> None:
    source = _SURFACE.read_text(encoding="utf-8")
    assert "CapabilityExecutionLoop(" not in source
    assert "ActionGate(" not in source
    assert "PermissionEngine(" not in source
    assert "AuthorityContext(" not in source
    assert "CapabilityRegistry(" not in source


def test_importing_a506_loads_no_ctypes_and_performs_no_native_action() -> None:
    probe = (
        "import sys\n"
        "import agentx.capabilities.windows._native as native\n"
        "def boom(*args, **kwargs):\n"
        "    raise AssertionError('native A5.06 action at import time')\n"
        "native.send_text_raw = boom\n"
        "native.send_key_raw = boom\n"
        "native.read_clipboard_text_raw = boom\n"
        "native.write_clipboard_text_raw = boom\n"
        "native.clear_clipboard_raw = boom\n"
        "before = set(sys.modules)\n"
        "import agentx.capabilities.windows.keyboard_clipboard as surface\n"
        "added = set(sys.modules) - before\n"
        "assert 'ctypes' not in {name.split('.')[0] for name in added}\n"
        "assert surface.WINDOWS_TEXT_ENTRY_IDENTITY is not None\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_a506_adds_no_runtime_dependency() -> None:
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject
    for banned in ("pywin32", "pywinauto", "comtypes", "uiautomation", "keyboard"):
        assert banned not in pyproject
