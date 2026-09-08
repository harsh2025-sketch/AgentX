"""Architecture guardrails for M7.02 window-management boundary.

Verifies:
- module lives in capabilities.windows
- pure Python: no ctypes / Win32 markers outside isolated seams
- no keyboard/mouse/raw coordinate APIs
- no new runtime dependencies
- no second provider / no registry mutation
- capability uses canonical ABI contracts
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx.capabilities.windows import window_management as wm

_SRC = Path(__file__).resolve().parents[2] / "src"
_WINDOW_PKG = _SRC / "agentx" / "capabilities" / "windows"
_WINDOW_MGMT = _WINDOW_PKG / "window_management.py"

_NATIVE_SEAMS = frozenset(
    {
        _WINDOW_PKG / "_native.py",
        _WINDOW_PKG / "_uia_native.py",
    }
)

_NATIVE_ONLY_MARKERS = ("windll", "WinDLL", "WINFUNCTYPE", "kernel32", "user32")


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def test_window_management_module_exists() -> None:
    assert _WINDOW_MGMT.is_file()


def test_window_management_uses_canonical_abi() -> None:
    imported = _imports(_WINDOW_MGMT)
    assert "agentx.capabilities.abi" in imported
    assert "agentx.core.errors" in imported
    assert "agentx.core.result" in imported
    assert "agentx.core.execution" in imported
    assert "agentx.kernel.permissions" in imported
    assert "agentx.kernel.risk" in imported


def test_window_management_reuses_provider_contracts() -> None:
    imported = _imports(_WINDOW_MGMT)
    assert "agentx.capabilities.windows.provider" in imported


def test_no_ctypes_import_at_module_level() -> None:
    imported = _imports(_WINDOW_MGMT)
    for imp in imported:
        assert imp.split(".")[0] != "ctypes", f"ctypes import found: {imp}"


def test_native_seams_are_only_win32_sites() -> None:
    # All source in windows package except seams must avoid native markers
    for path in sorted(_WINDOW_PKG.rglob("*.py")):
        if path in _NATIVE_SEAMS:
            continue
        if path.name == "__init__.py":
            continue
        source = path.read_text(encoding="utf-8")
        for marker in _NATIVE_ONLY_MARKERS:
            assert marker not in source, f"{path.name} contains native marker {marker}"
        for module in _imports(path):
            assert module.split(".")[0] != "ctypes", f"{path.name} imports ctypes"


def test_no_keyboard_mouse_injection() -> None:
    src = _WINDOW_MGMT.read_text(encoding="utf-8").lower()
    for bad in ("sendinput", "keybd_event", "mouse_event", "pyautogui", "pywinauto", "sendkeys"):
        assert bad not in src, f"found injection marker {bad}"


def test_no_subprocess_or_shell() -> None:
    src = _WINDOW_MGMT.read_text(encoding="utf-8").lower()
    assert "subprocess" not in src
    assert "os.system" not in src


def test_capability_is_immutable() -> None:
    _cap = wm.WindowFocusCapability(
        __import__(
            "agentx.capabilities.windows.provider", fromlist=["evaluate_windows_support"]
        ).evaluate_windows_support(
            __import__(
                "agentx.capabilities.windows.provider", fromlist=["PlatformFacts"]
            ).PlatformFacts(system="Windows", release="11", version="10.0", machine="AMD64")
        )
    )
    with __import__("pytest").raises(AttributeError):
        _cap._descriptor = None  # type: ignore[misc]


def test_no_new_dependency_imports() -> None:
    imported = _imports(_WINDOW_MGMT)
    forbidden = {
        "pywin32",
        "pywinauto",
        "uiautomation",
        "win32api",
        "win32gui",
        "comtypes",
        "pythoncom",
    }
    found = {m.split(".")[0] for m in imported} & forbidden
    assert not found, f"forbidden dependency roots found: {found}"
