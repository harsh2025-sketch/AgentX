"""M7.04 boundaries, without weakening the baseline's read-only Windows guards."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/agentx/capabilities/windows/application_launch.py"


def tree() -> ast.Module:
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def imports() -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree()):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize(
    "forbidden",
    [
        "subprocess",
        "agentx.cognition",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.learning",
        "agentx.procedures",
        "keyboard",
        "mouse",
        "pyautogui",
        "agentx.capabilities.registry",
        "agentx.capabilities.runtime",
        "agentx.kernel.action_gate",
    ],
)
def test_no_shell_automation_or_alternate_authority_import(forbidden: str) -> None:
    assert not any(m == forbidden or m.startswith(forbidden + ".") for m in imports())


def test_only_canonical_execution_loop_definition_exists() -> None:
    sites: list[Path] = []
    for path in (ROOT / "src/agentx").rglob("*.py"):
        if any(
            isinstance(node, ast.ClassDef) and node.name == "CapabilityExecutionLoop"
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        ):
            sites.append(path.relative_to(ROOT))
    assert sites == [Path("src/agentx/capabilities/runtime.py")]


def test_no_unrelated_native_mutation_or_command_evaluation() -> None:
    attributes = {n.attr for n in ast.walk(tree()) if isinstance(n, ast.Attribute)}
    names = {n.id for n in ast.walk(tree()) if isinstance(n, ast.Name)}
    assert not attributes.intersection(
        {
            "system",
            "popen",
            "ShellExecuteW",
            "ShellExecuteExW",
            "WinExec",
            "SendInput",
            "keybd_event",
            "mouse_event",
            "TerminateProcess",
            "ResumeThread",
            "WriteProcessMemory",
            "SetForegroundWindow",
            "CreateProcessAsUserW",
            "CreateProcessWithLogonW",
            "Thread",
            "Popen",
            "environ",
        }
    )
    assert not names.intersection({"eval", "exec", "__import__"})
    assert "CreateProcessW" in attributes
    assert "GetProcessTimes" in attributes and "QueryFullProcessImageNameW" in attributes
    assert "CloseHandle" in attributes and "WaitForSingleObject" in attributes


def test_no_import_time_native_load_or_platform_detection() -> None:
    for node in tree().body:
        if isinstance(node, ast.Import):
            assert all(alias.name != "ctypes" for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "ctypes"
        if isinstance(node, ast.Expr):
            assert isinstance(node.value, ast.Constant)  # docstring only.
        if isinstance(node, ast.Assign | ast.AnnAssign):
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    assert not isinstance(child.func, ast.Attribute) or child.func.attr != "WinDLL"
                    assert not isinstance(child.func, ast.Name) or child.func.id not in {
                        "detect_platform_facts",
                        "_api",
                        "WindowsApplicationLaunchCapability",
                    }


def test_architecture_manifest_is_exact_canonical_baseline() -> None:
    text = (ROOT / "src/agentx/_architecture.py").read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode()).hexdigest() == (
        "211d089e9ae3effcce634eeca1feeb3151eab472c276e18a297c0460c6eacf09"
    )
