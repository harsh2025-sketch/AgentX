"""Architecture guardrails for A6.08 human approval data contracts."""

from __future__ import annotations

import ast
from pathlib import Path

import agentx.capabilities.human_approval as approval
from agentx.capabilities.human_approval import HumanApprovalDecision, HumanApprovalRequest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "src" / "agentx" / "capabilities" / "human_approval.py"
_ACTION_GATE_PATH = _REPO_ROOT / "src" / "agentx" / "kernel" / "action_gate.py"


def _source() -> str:
    return _MODULE_PATH.read_text(encoding="utf-8")


def _imports() -> set[str]:
    tree = ast.parse(_source())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def test_contract_lives_in_capabilities_boundary() -> None:
    assert approval.__name__ == "agentx.capabilities.human_approval"
    assert _MODULE_PATH.is_file()


def test_contract_uses_only_existing_allowed_subsystem_edges() -> None:
    imports = _imports()
    agentx_imports = {name for name in imports if name.startswith("agentx.")}

    assert agentx_imports == {
        "agentx.capabilities.abi",
        "agentx.core.execution",
        "agentx.core.ids",
        "agentx.kernel.action_gate",
    }


def test_contract_does_not_depend_on_outer_or_adaptive_subsystems() -> None:
    imports = _imports()
    forbidden_prefixes = (
        "agentx.cognition",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.learning",
        "agentx.procedures",
    )

    assert not any(name.startswith(forbidden_prefixes) for name in imports)


def test_contract_does_not_import_execution_runtime_or_executor() -> None:
    imports = _imports()

    assert "agentx.capabilities.runtime" not in imports
    assert "agentx.capabilities.executor" not in imports
    assert "agentx.capabilities.verifier" not in imports
    assert "agentx.capabilities.registry" not in imports


def test_action_gate_remains_independent_of_approval_contract() -> None:
    action_gate_source = _ACTION_GATE_PATH.read_text(encoding="utf-8")

    assert "human_approval" not in action_gate_source
    assert "HumanApproval" not in action_gate_source


def test_production_contract_does_not_import_operating_modes() -> None:
    imports = _imports()

    assert "agentx.core.human_operating_modes" not in imports


def test_contract_has_no_io_persistence_network_process_or_background_runtime() -> None:
    imports = _imports()
    forbidden = {
        "asyncio",
        "http",
        "httpx",
        "multiprocessing",
        "os",
        "pathlib",
        "requests",
        "socket",
        "sqlite3",
        "subprocess",
        "threading",
        "time",
        "urllib",
    }

    assert imports.isdisjoint(forbidden)


def test_contract_has_no_execution_authority_or_side_effect_methods() -> None:
    request_methods = set(HumanApprovalRequest.__dict__)
    decision_methods = set(HumanApprovalDecision.__dict__)
    forbidden = {
        "activate",
        "allow",
        "approve",
        "authorize",
        "browse",
        "execute",
        "grant",
        "invoke",
        "persist",
        "promote",
        "research",
        "run",
        "save",
        "transition",
        "verify",
    }

    assert request_methods.isdisjoint(forbidden)
    assert decision_methods.isdisjoint(forbidden)


def test_contract_exports_only_bounded_data_vocabulary() -> None:
    assert set(approval.__all__) == {
        "HumanApprovalDecision",
        "HumanApprovalMismatchError",
        "HumanApprovalOutcome",
        "HumanApprovalRequest",
        "HumanApprovalRequestId",
        "HumanApprovalValidationError",
    }


def test_no_module_level_request_decision_or_global_approval_state() -> None:
    for value in vars(approval).values():
        assert not isinstance(value, HumanApprovalRequest)
        assert not isinstance(value, HumanApprovalDecision)

    names = {name.lower() for name in vars(approval)}
    assert "approved" not in names
    assert "current_approval" not in names
    assert "approval_cache" not in names
    assert "approval_store" not in names
