"""Architecture guards for AX-260-265 and AX-296-297 completion."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast
from unittest import TestCase

from agentx.capabilities.windows.input_verification import (
    ClipboardReadbackPort,
    KeyboardActionVerificationPort,
    VerifiedWindowsClipboardClearCapability,
    VerifiedWindowsClipboardReadTextCapability,
    VerifiedWindowsClipboardWriteTextCapability,
    VerifiedWindowsSendKeysCapability,
    VerifiedWindowsSendTextCapability,
)
from agentx.capabilities.windows.keyboard_text_clipboard import (
    ClipboardNativePort,
    KeyboardNativePort,
)
from tests.support.fake_windows_native import windows_support

_REPO = Path(__file__).resolve().parents[2]
_STRUCTURAL = _REPO / "src" / "agentx" / "capabilities" / "filesystem_structural.py"
_INPUT_VERIFY = _REPO / "src" / "agentx" / "capabilities" / "windows" / "input_verification.py"


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            values.append(node.module)
    return tuple(values)


def test_structural_capabilities_have_no_shell_or_second_authority_owner() -> None:
    source = _STRUCTURAL.read_text(encoding="utf-8")
    imports = _imports(_STRUCTURAL)
    forbidden_imports = ("subprocess", "shutil", "agentx.kernel.action_gate")
    assert not any(
        imported == prefix or imported.startswith(f"{prefix}.")
        for imported in imports
        for prefix in forbidden_imports
    )
    for forbidden in (
        "CapabilityExecutionLoop(",
        "PermissionEngine(",
        "ActionGate(",
        "rmtree(",
        "shell=True",
        "safe: bool",
        "safe=True",
    ):
        assert forbidden not in source


def test_input_verification_is_observation_only_and_cannot_own_authority() -> None:
    imports = _imports(_INPUT_VERIFY)
    for forbidden in (
        "agentx.kernel.action_gate",
        "agentx.kernel.permissions",
        "agentx.kernel.resource_budget",
        "agentx.kernel.risk",
        "agentx.capabilities.runtime",
    ):
        assert not any(
            imported == forbidden or imported.startswith(f"{forbidden}.") for imported in imports
        )


def test_input_verification_requires_distinct_execution_and_observation_ports() -> None:
    # Exercise the invariant for every adapter instead of matching variable names.
    # The same object must be refused before native port methods can be inspected.
    shared = object()
    assertion = TestCase()
    for keyboard in (VerifiedWindowsSendTextCapability, VerifiedWindowsSendKeysCapability):
        with assertion.assertRaisesRegex(ValueError, "distinct"):
            keyboard(
                windows_support(),
                execution_port=cast(KeyboardNativePort, shared),
                verification_port=cast(KeyboardActionVerificationPort, shared),
            )
    for clipboard in (
        VerifiedWindowsClipboardReadTextCapability,
        VerifiedWindowsClipboardWriteTextCapability,
        VerifiedWindowsClipboardClearCapability,
    ):
        with assertion.assertRaisesRegex(ValueError, "distinct"):
            clipboard(
                windows_support(),
                execution_port=cast(ClipboardNativePort, shared),
                verification_port=cast(ClipboardReadbackPort, shared),
            )
