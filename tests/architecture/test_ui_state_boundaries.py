"""Architecture guards for the C7.01 runtime-to-UI state protocol boundary."""

from __future__ import annotations

import ast
import dataclasses
from collections.abc import Sequence
from pathlib import Path
from typing import get_type_hints

from agentx.cognition.router import ExecutionLevel
from agentx.core.ui_state import (
    CURRENT_UI_STATE_SCHEMA_VERSION,
    UiExecutionLevel,
    UiPermission,
    UiRiskLevel,
    UiStateApproval,
    UiStateError,
    UiStateExecution,
    UiStatePlan,
    UiStateSnapshot,
    UiStateTask,
    UiStateVerification,
    UiVerificationStatus,
    project_state_event,
)
from agentx.kernel.permissions import Permission
from agentx.kernel.risk import RiskLevel

_ROOT = Path(__file__).parents[2]
_MODULE = _ROOT / "src" / "agentx" / "core" / "ui_state.py"
_SOURCE = _MODULE.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)


def _imported_modules() -> set[str]:
    result: set[str] = set()
    for node in ast.walk(_TREE):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def _function_names() -> set[str]:
    return {
        node.name
        for node in ast.walk(_TREE)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _class_names() -> set[str]:
    return {node.name for node in _TREE.body if isinstance(node, ast.ClassDef)}


def _call_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_protocol_lives_in_the_core_boundary() -> None:
    assert _MODULE.exists()
    assert "core" in _MODULE.parts
    assert _MODULE.name == "ui_state.py"


def test_module_imports_only_stdlib_and_siblings_of_core() -> None:
    imported = _imported_modules()
    allowed_agentx_imports = {
        "agentx.core.errors",
        "agentx.core.events",
        "agentx.core.ids",
        "agentx.core.task_state",
        "agentx.core.tasks",
    }

    agentx_imports = {name for name in imported if name.startswith("agentx")}
    assert agentx_imports <= allowed_agentx_imports
    # The protocol must not reach an authority or outer subsystem: not kernel
    # (permissions/risk/gates), not capabilities (verification/runtime), not
    # cognition (execution-level authority context), not any other subsystem.
    for forbidden_prefix in (
        "agentx.kernel",
        "agentx.capabilities",
        "agentx.cognition",
        "agentx.hive",
        "agentx.procedures",
        "agentx.learning",
        "agentx.infrastructure",
    ):
        assert all(not name.startswith(forbidden_prefix) for name in imported), (
            f"{forbidden_prefix} is not importable from the C7.01 protocol"
        )


def test_module_reuses_canonical_task_error_and_event_contracts() -> None:
    imported = _imported_modules()

    assert "agentx.core.tasks" in imported
    assert "agentx.core.task_state" in imported
    assert "agentx.core.errors" in imported
    assert "agentx.core.events" in imported
    assert "agentx.core.ids" in imported

    task_hints = get_type_hints(UiStateTask)
    assert task_hints["task_id"].__name__ == "TaskId"
    assert task_hints["status"].__name__ == "TaskStatus"
    assert task_hints["priority"].__name__ == "TaskPriority"

    error_hints = get_type_hints(UiStateError)
    assert error_hints["category"].__name__ == "ErrorCategory"
    assert error_hints["retryability"].__name__ == "Retryability"

    projection_hints = get_type_hints(project_state_event)
    assert projection_hints["snapshot"].__name__ == "UiStateSnapshot"
    assert projection_hints["event"].__name__ == "Event"
    assert projection_hints["return"].__name__ == "UiStateSnapshot"


def test_mirror_vocabularies_match_their_canonical_owners_exactly() -> None:
    # The protocol mirrors values, not semantics. If a canonical vocabulary
    # ever changes, this test forces the mirror to change with it.
    assert [level.value for level in UiExecutionLevel] == [level.value for level in ExecutionLevel]
    assert [level.value for level in UiRiskLevel] == [level.value for level in RiskLevel]
    assert [permission.value for permission in UiPermission] == [
        permission.value for permission in Permission
    ]
    # And the protocol's own closed vocabulary stays closed.
    assert [status.value for status in UiVerificationStatus] == [
        "not_started",
        "passed",
        "failed",
    ]


def test_projection_transitions_are_checked_against_the_canonical_matrix() -> None:
    # The projection must consult the canonical A1.06 transition matrix.
    assert "can_transition" in _SOURCE
    assert "agentx.core.task_state" in _imported_modules()


def test_module_exposes_no_authority_or_execution_verb() -> None:
    forbidden = {
        "approve",
        "apply",
        "authorize",
        "cancel",
        "deny",
        "execute",
        "grant",
        "invoke",
        "mutate",
        "reset",
        "retry",
        "run",
        "set",
        "submit",
        "update",
    }
    for name in _function_names():
        assert name not in forbidden
        assert not name.startswith(tuple(forbidden)), name
    # No setter-style public method on the snapshot either.
    for method in dir(UiStateSnapshot):
        assert not method.startswith("set_"), method


def test_module_has_no_dynamic_code_network_or_persistence_surface() -> None:
    imported = _imported_modules()
    calls = _call_names()

    forbidden_imports = {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "subprocess",
        "socket",
        "urllib",
        "http",
        "sqlite3",
        "shutil",
        "os",
        "threading",
        "multiprocessing",
        "asyncio",
        "requests",
    }
    assert imported.isdisjoint(forbidden_imports)
    assert not any(name.startswith(("socket.", "http.", "urllib.")) for name in imported)

    forbidden_calls = {
        "eval",
        "exec",
        "compile",
        "__import__",
        "Popen",
        "run",
        "system",
        "urlopen",
        "connect",
    }
    assert calls.isdisjoint(forbidden_calls)


def test_public_api_is_the_closed_protocol_surface() -> None:
    import agentx.core.ui_state as ui_state

    expected_surface: set[str] = {
        "CURRENT_UI_STATE_SCHEMA_VERSION",
        "UiExecutionLevel",
        "UiPermission",
        "UiRiskLevel",
        "UiStateApproval",
        "UiStateDeserializationError",
        "UiStateError",
        "UiStateExecution",
        "UiStatePlan",
        "UiStateProjectionError",
        "UiStateProtocolError",
        "UiStateSnapshot",
        "UiStateTask",
        "UiStateValidationError",
        "UiStateVerification",
        "UiVerificationStatus",
        "UnsupportedUiStateSchemaVersionError",
        "project_state_event",
    }
    assert set(ui_state.__all__) == expected_surface
    assert _class_names() == {
        "UiExecutionLevel",
        "UiPermission",
        "UiRiskLevel",
        "UiStateApproval",
        "UiStateDeserializationError",
        "UiStateError",
        "UiStateExecution",
        "UiStatePlan",
        "UiStateProjectionError",
        "UiStateProtocolError",
        "UiStateSnapshot",
        "UiStateTask",
        "UiStateValidationError",
        "UiStateVerification",
        "UiVerificationStatus",
        "UnsupportedUiStateSchemaVersionError",
    }


def test_schema_version_is_an_explicit_versioned_constant() -> None:
    assert CURRENT_UI_STATE_SCHEMA_VERSION == 1
    # schema_version is a required field: no default back-door.
    assert UiStateSnapshot.__dataclass_fields__["schema_version"].default is dataclasses.MISSING
    # Version checks exist on both the construction and deserialization paths.
    assert "unsupported ui state schema version" in _SOURCE.lower()


def test_no_ui_framework_or_transport_dependency() -> None:
    imported = _imported_modules()
    forbidden = {
        "playwright",
        "selenium",
        "pywebview",
        "fastapi",
        "flask",
        "django",
        "aiohttp",
        "websocket",
        "websockets",
        "tkinter",
        "PySide6",
        "PyQt6",
        "httpx",
        "bottle",
        "starlette",
    }
    assert imported.isdisjoint(forbidden)
    lowered_source = _SOURCE.lower()
    for framework in ("html", "<div", "onclick", "document.", "window."):
        assert framework not in lowered_source


def test_wire_schema_is_a_closed_set_of_observation_fields() -> None:
    from dataclasses import fields

    field_names: dict[str, set[str]] = {
        "snapshot": {f.name for f in fields(UiStateSnapshot)},
        "task": {f.name for f in fields(UiStateTask)},
        "execution": {f.name for f in fields(UiStateExecution)},
        "plan": {f.name for f in fields(UiStatePlan)},
        "verification": {f.name for f in fields(UiStateVerification)},
        "approval": {f.name for f in fields(UiStateApproval)},
        "error": {f.name for f in fields(UiStateError)},
    }

    assert field_names["snapshot"] == {
        "schema_version",
        "task",
        "execution",
        "plan",
        "verification",
        "approval",
        "error",
        "progress",
        "timestamp",
    }
    # No field anywhere in the wire schema carries a command or a grant.
    all_fields: Sequence[str] = tuple(name for names in field_names.values() for name in names)
    for field in all_fields:
        lowered = field.lower()
        for marker in ("command", "granted", "bypass", "override", "decision", "execute", "run"):
            assert marker not in lowered, f"wire field {field!r} looks like authority"
