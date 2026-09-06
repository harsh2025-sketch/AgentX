"""Architecture guardrails for A9.01 missing-capability detection."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from agentx.capabilities import capability_gap
from agentx.infrastructure.persistence import _MIGRATIONS

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_MODULE = _SRC_ROOT / "agentx" / "capabilities" / "capability_gap.py"


def _module_tree(path: Path = _MODULE) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imports(path: Path = _MODULE) -> tuple[str, ...]:
    imported: list[str] = []
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def _called_names(path: Path = _MODULE) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_module_tree(path)):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def _public_classes(path: Path = _MODULE) -> set[str]:
    return {
        node.name
        for node in _module_tree(path).body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }


def test_detector_lives_in_the_capabilities_boundary() -> None:
    assert _MODULE.is_file()
    assert capability_gap.__name__ == "agentx.capabilities.capability_gap"


def test_public_api_is_narrow_and_matches_all() -> None:
    exported = set(capability_gap.__all__)
    assert _public_classes() <= exported
    assert all(hasattr(capability_gap, name) for name in exported)

    detector_methods = {
        item.name
        for node in _module_tree().body
        if isinstance(node, ast.ClassDef) and node.name == "CapabilityGapDetector"
        for item in node.body
        if isinstance(item, ast.FunctionDef) and not item.name.startswith("_")
    }
    assert detector_methods == {"assess"}


def test_detector_reuses_canonical_contracts_only() -> None:
    agentx_imports = {module for module in _imports() if module.startswith("agentx.")}

    assert agentx_imports == {
        "agentx.capabilities.abi",
        "agentx.core.procedures",
        "agentx.kernel.action_gate",
        "agentx.kernel.emergency_stop",
        "agentx.kernel.resource_budget",
        "agentx.kernel.risk",
    }


def test_detector_uses_only_standard_library_and_agentx() -> None:
    non_stdlib = [
        module
        for module in _imports()
        if module.split(".", maxsplit=1)[0] not in sys.stdlib_module_names
        and not module.startswith("agentx")
    ]

    assert non_stdlib == []


def test_detector_imports_no_discovery_network_or_codegen_surface() -> None:
    forbidden_prefixes = (
        "agentx.capabilities.registry",
        "agentx.capabilities.runtime",
        "agentx.capabilities.executor",
        "agentx.capabilities.verifier",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.learning",
        "agentx.procedures",
        "agentx.cognition",
        "http",
        "urllib",
        "socket",
        "ssl",
        "ftplib",
        "subprocess",
        "shutil",
        "requests",
        "httpx",
        "aiohttp",
        "importlib",
        "pkgutil",
        "pip",
        "setuptools",
        "venv",
        "sysconfig",
        "ctypes",
        "os",
        "sys",
        "platform",
        "pathlib",
        "sqlite3",
        "ast",
        "inspect",
    )

    imports = _imports()
    assert not any(module.startswith(prefix) for module in imports for prefix in forbidden_prefixes)


def test_detector_performs_no_execution_generation_or_registration() -> None:
    forbidden_calls = {
        "execute",
        "verify",
        "invoke",
        "register",
        "acquire",
        "install",
        "download",
        "generate",
        "compile",
        "eval",
        "exec",
        "open",
        "__import__",
        "import_module",
        "system",
        "popen",
        "run",
        "spawn",
        "connect",
        "request",
        "insert",
        "update_status",
        "request_stop",
        "evaluate",
        "check",
        "assess_risk",
        "consume",
    }

    assert _called_names().isdisjoint(forbidden_calls)


def test_detector_has_no_persistence_or_migration() -> None:
    source = _MODULE.read_text(encoding="utf-8").lower()
    forbidden_fragments = (
        "sqlite",
        "create table",
        "migration",
        "procedure_store",
        "knowledge_store",
        "event_bus",
        "event_journal",
        "artifact_store",
        "audit_store",
        "pip install",
    )

    assert not any(fragment in source for fragment in forbidden_fragments)
    assert not any("capability_gap" in migration.name for migration in _MIGRATIONS)


def test_detector_defines_no_authority_or_self_extension_contract() -> None:
    forbidden_class_names = {
        "AuthorityContext",
        "Permission",
        "PermissionEngine",
        "ActionGate",
        "GateResult",
        "RiskAssessment",
        "ResourceBudget",
        "EmergencyStop",
        "CapabilityRegistry",
        "CapabilityAcquisition",
        "AdapterGenerator",
        "SdkDiscovery",
        "DependencyInstaller",
        "SelfExtension",
    }

    assert _public_classes().isdisjoint(forbidden_class_names)


def test_result_contracts_carry_no_authority_fields() -> None:
    forbidden_field_fragments = (
        "permission",
        "authority",
        "authorized",
        "approved",
        "allow",
        "grant",
        "bypass",
        "install",
        "register",
        "generate",
        "acquire",
        "sdk",
        "adapter",
        "dependency",
    )
    for node in _module_tree().body:
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                name = item.target.id.lower()
                assert not any(fragment in name for fragment in forbidden_field_fragments), (
                    f"{node.name}.{name}"
                )


def test_detector_is_stateless() -> None:
    detector_slots = capability_gap.CapabilityGapDetector.__slots__
    assert detector_slots == ()
