"""Architecture guardrails for A5.08 read-only Windows capture."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_WINDOWS_PKG = _SRC_ROOT / "agentx" / "capabilities" / "windows"
_CAPTURE = _WINDOWS_PKG / "screen_capture.py"
_NATIVE = _WINDOWS_PKG / "_native.py"


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return tuple(dict.fromkeys(modules))


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_capture_lives_in_windows_capabilities_boundary() -> None:
    assert _CAPTURE.is_file()
    assert _NATIVE.is_file()
    assert _CAPTURE.parent.parent.name == "capabilities"


def test_capture_reuses_canonical_provider_abi_core_and_kernel_contracts() -> None:
    imported = _imports(_CAPTURE)
    assert "agentx.capabilities.abi" in imported
    assert "agentx.capabilities.windows.provider" in imported
    assert "agentx.core.errors" in imported
    assert "agentx.core.execution" in imported
    assert "agentx.core.result" in imported
    assert "agentx.kernel.permissions" in imported
    assert "agentx.kernel.risk" in imported


def test_capture_does_not_take_over_registry_or_execution_loop() -> None:
    imported = _imports(_CAPTURE)
    assert "agentx.capabilities.registry" not in imported
    assert "agentx.capabilities.runtime" not in imported
    assert "agentx.kernel.action_gate" not in imported
    assert "agentx.kernel.emergency_stop" not in imported
    assert "agentx.kernel.resource_budget" not in imported


def test_capture_has_no_ocr_model_grounding_uia_or_input_dependency() -> None:
    source = _CAPTURE.read_text(encoding="utf-8").lower()
    imported = _imports(_CAPTURE)
    forbidden_import_roots = {
        "cv2",
        "easyocr",
        "mss",
        "numpy",
        "pil",
        "pyautogui",
        "pytesseract",
        "pywinauto",
        "uiautomation",
    }
    assert not {module.split(".")[0] for module in imported} & forbidden_import_roots
    for forbidden_call in (
        "click",
        "sendinput",
        "setcursorpos",
        "keybd_event",
        "mouse_event",
        "pytesseract",
    ):
        assert forbidden_call not in _called_names(_CAPTURE)
    assert "agentx.cognition" not in imported
    assert "agentx.research" not in imported
    assert "agentx.hive" not in imported
    assert "pixels are data" in source


def test_win32_capture_knowledge_stays_in_existing_native_seam() -> None:
    capture_imports = _imports(_CAPTURE)
    assert all(module.split(".")[0] != "ctypes" for module in capture_imports)
    native_source = _NATIVE.read_text(encoding="utf-8")
    for required in (
        "query_virtual_screen_bounds_raw",
        "query_window_capture_info_raw",
        "capture_bgra_raw",
        "GetSystemMetrics",
        "GetWindowRect",
        "BitBlt",
        "GetDIBits",
    ):
        assert required in native_source


def test_capture_respects_canonical_subsystem_edges() -> None:
    allowed = {_architecture.CORE, _architecture.KERNEL, _architecture.CAPABILITIES}
    for module in _imports(_CAPTURE):
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
        assert owner is None or owner in allowed, f"screen_capture.py -> {module}"


def test_clean_import_loads_no_native_or_automation_library() -> None:
    probe = (
        "import sys\n"
        "import agentx.capabilities.windows.provider  # noqa: F401\n"
        "before = set(sys.modules)\n"
        "import agentx.capabilities.windows.screen_capture  # noqa: F401\n"
        "added = set(sys.modules) - before\n"
        "forbidden = {'ctypes', 'win32api', 'win32gui', 'win32con', 'comtypes',\n"
        "             'pywinauto', 'uiautomation', 'pythoncom', 'win32com'}\n"
        "roots = {name.split('.')[0] for name in added}\n"
        "assert not (roots & forbidden), sorted(roots & forbidden)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_import_performs_no_native_capture_read() -> None:
    probe = (
        "import agentx.capabilities.windows._native as native\n"
        "def boom(*args, **kwargs):\n"
        "    raise AssertionError('native capture read at import time')\n"
        "native.query_virtual_screen_bounds_raw = boom\n"
        "native.query_window_capture_info_raw = boom\n"
        "native.capture_bgra_raw = boom\n"
        "import agentx.capabilities.windows.screen_capture as capture\n"
        "assert capture.SCREEN_CAPTURE_IDENTITY is not None\n"
        "assert capture.GdiNativeSurface() is not None\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_import_registers_nothing() -> None:
    probe = (
        "import agentx.capabilities.windows.screen_capture  # noqa: F401\n"
        "from agentx.capabilities.registry import CapabilityRegistry\n"
        "assert len(CapabilityRegistry()) == 0\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_a508_keeps_runtime_dependency_set_empty() -> None:
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject
    for banned in (
        "opencv",
        "pillow",
        "mss",
        "pyautogui",
        "pytesseract",
        "pywin32",
        "pywinauto",
    ):
        assert banned not in pyproject.lower()
