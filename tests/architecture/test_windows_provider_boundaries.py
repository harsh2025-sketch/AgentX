"""Architecture guardrails for the A5.01 Windows provider boundary.

These are static, deterministic checks. They run identically on Windows,
Linux and macOS and they never touch a native API. They are architecture
guardrails, not security enforcement: authority remains owned by the Trusted
Kernel.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"
_WINDOWS_PKG = _AGENTX_SRC / "capabilities" / "windows"
_PROVIDER = _WINDOWS_PKG / "provider.py"

# Native/automation surfaces that belong to A5.02+ and must not appear yet.
_FORBIDDEN_IMPORTS = (
    "ctypes",
    "win32api",
    "win32gui",
    "win32con",
    "win32process",
    "win32clipboard",
    "pywintypes",
    "pythoncom",
    "comtypes",
    "win32com",
    "uiautomation",
    "pywinauto",
    "winreg",
    "msvcrt",
    "subprocess",
    "PIL",
    "mss",
    "pytesseract",
    "cv2",
    "numpy",
    "keyboard",
    "mouse",
    "pyautogui",
    "pyperclip",
    "selenium",
    "playwright",
)

# Automation verbs A5.01 must not implement.
_FORBIDDEN_SYMBOLS = (
    "EnumWindows",
    "FindWindow",
    "GetForegroundWindow",
    "SetForegroundWindow",
    "SendInput",
    "keybd_event",
    "mouse_event",
    "CoInitialize",
    "CreateObject",
    "screenshot",
    "grab_screen",
    "ocr",
)

_FORBIDDEN_COUPLING = (
    "agentx.cognition",
    "agentx.hive",
    "agentx.learning",
    "agentx.procedures",
    "agentx.infrastructure",
)


def _windows_sources() -> tuple[Path, ...]:
    return tuple(sorted(_WINDOWS_PKG.rglob("*.py")))


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in _AGENTX_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree)):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


def test_windows_provider_lives_in_the_capabilities_subsystem() -> None:
    assert _PROVIDER.is_file()
    assert (_WINDOWS_PKG / "__init__.py").is_file()
    assert _WINDOWS_PKG.parent.name == "capabilities"


@pytest.mark.parametrize("path", _windows_sources(), ids=lambda p: p.name)
def test_windows_package_imports_no_native_or_automation_module(path: Path) -> None:
    for module in _imported_modules(path):
        root = module.split(".")[0]
        assert root not in _FORBIDDEN_IMPORTS, f"{path.name} imports forbidden module {module}"


@pytest.mark.parametrize("path", _windows_sources(), ids=lambda p: p.name)
def test_windows_package_defines_no_automation_surface(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for symbol in _FORBIDDEN_SYMBOLS:
        assert symbol not in names
        assert symbol not in attributes


@pytest.mark.parametrize("path", _windows_sources(), ids=lambda p: p.name)
def test_windows_package_has_no_model_hive_or_persistence_coupling(path: Path) -> None:
    for module in _imported_modules(path):
        for forbidden in _FORBIDDEN_COUPLING:
            assert module != forbidden and not module.startswith(f"{forbidden}."), (
                f"{path.name} couples to {module}"
            )


@pytest.mark.parametrize("path", _windows_sources(), ids=lambda p: p.name)
def test_windows_package_uses_only_stdlib_and_canonical_agentx(path: Path) -> None:
    stdlib = set(sys.stdlib_module_names)
    for module in _imported_modules(path):
        root = module.split(".")[0]
        assert root in stdlib or root == "agentx", f"{path.name} imports third-party {module}"


def test_provider_reuses_the_canonical_capability_contracts() -> None:
    imported = _imported_modules(_PROVIDER)
    assert "agentx.capabilities.abi" in imported
    assert "agentx.capabilities.registry" in imported
    assert "agentx.core.errors" in imported
    assert "agentx.core.result" in imported


def test_provider_does_not_duplicate_the_capability_abi_or_registry() -> None:
    """No shadow ABI: canonical contracts keep exactly one definition site."""
    for name in (
        "Capability",
        "CapabilityDescriptor",
        "CapabilityIdentity",
        "CapabilityName",
        "CapabilityVersion",
        "CapabilityScope",
        "CapabilityPlatform",
        "CapabilityParams",
        "CapabilityRequest",
        "ExecutionResult",
        "VerificationResult",
    ):
        assert _class_definitions(name) == [Path("agentx/capabilities/abi.py")], name
    assert _class_definitions("CapabilityRegistry") == [Path("agentx/capabilities/registry.py")]


def test_provider_defines_no_windows_capability_variant() -> None:
    tree = ast.parse(_PROVIDER.read_text(encoding="utf-8"))
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert defined == {
        "WindowsProviderIdentity",
        "PlatformFacts",
        "WindowsSupportStatus",
        "WindowsSupport",
        "WindowsProvider",
    }
    for banned in ("WindowsCapability", "WindowsCapabilityV2", "CapabilityV2"):
        assert banned not in defined


def test_windows_package_has_no_module_level_side_effects() -> None:
    """Top-level statements stay declarative: no detection, no registration."""
    for path in _windows_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            assert not isinstance(node, ast.Expr | ast.For | ast.While | ast.With | ast.Try) or (
                isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            ), f"{path.name} has an executable top-level statement"
            if isinstance(node, ast.If):  # pragma: no cover - defensive
                raise AssertionError(f"{path.name} has conditional module-level logic")


def test_windows_package_respects_the_canonical_subsystem_edges() -> None:
    allowed = {_architecture.CORE, _architecture.KERNEL, _architecture.CAPABILITIES}
    for path in _windows_sources():
        for module in _imported_modules(path):
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
            assert owner is None or owner in allowed, f"{path.name} -> {module}"


def test_importing_the_provider_in_a_clean_interpreter_loads_nothing_native() -> None:
    """Import safety, provable on any host: no native/automation module loads."""
    probe = (
        "import sys\n"
        "import agentx.capabilities.windows.provider as provider\n"
        "loaded = set(sys.modules)\n"
        "forbidden = {'ctypes', 'win32api', 'win32gui', 'comtypes', 'pywinauto',\n"
        "             'uiautomation', 'winreg', 'pythoncom', 'win32com'}\n"
        "assert not (loaded & forbidden), sorted(loaded & forbidden)\n"
        "assert provider.WINDOWS_PROVIDER_NAME == 'windows'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_importing_the_provider_performs_no_platform_detection() -> None:
    """``platform``/``sys`` reads happen only inside explicit function calls."""
    probe = (
        # Import the canonical dependencies first so that stdlib modules which
        # legitimately read platform facts at their own import time (e.g. uuid)
        # cannot be misattributed to the provider.
        "import agentx.capabilities.registry  # noqa: F401\n"
        "import platform\n"
        "calls = []\n"
        "for name in ('system', 'release', 'version', 'machine'):\n"
        "    original = getattr(platform, name)\n"
        "    def spy(*args, _name=name, _original=original, **kwargs):\n"
        "        calls.append(_name)\n"
        "        return _original(*args, **kwargs)\n"
        "    setattr(platform, name, spy)\n"
        "import agentx.capabilities.windows.provider as provider\n"
        "assert calls == [], calls\n"
        "provider.detect_platform_facts()\n"
        "assert calls, 'explicit detection must read platform facts'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
        cwd=_REPO_ROOT,
    )
    assert completed.returncode == 0, completed.stderr


def test_importing_the_provider_registers_nothing() -> None:
    probe = (
        "from agentx.capabilities.registry import CapabilityRegistry\n"
        "import agentx.capabilities.windows.provider  # noqa: F401\n"
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


def test_runtime_dependency_set_is_unchanged() -> None:
    """A5.01 adds no runtime dependency (in particular no pywin32)."""
    pyproject = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "dependencies = []" in pyproject
    assert "pywin32" not in pyproject
