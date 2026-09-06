"""Architecture guards for the C8.01 device abstraction/protocol boundary."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE = _REPO_ROOT / "src" / "agentx" / "capabilities" / "device.py"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"


def _tree() -> ast.Module:
    return ast.parse(_MODULE.read_text(encoding="utf-8"), filename=str(_MODULE))


def _imports() -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def _called_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def _class_methods(class_name: str) -> set[str]:
    for node in _tree().body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                member.name
                for member in node.body
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
            }
    raise AssertionError(f"class {class_name} not found")


def _public_defs() -> set[str]:
    defined: set[str] = set()
    for node in _tree().body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            if not node.name.startswith("_"):
                defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    defined.add(target.id)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and not node.target.id.startswith("_")
        ):
            defined.add(node.target.id)
    return defined


def test_c8_01_lives_in_capabilities_subsystem() -> None:
    assert _MODULE.is_file()
    assert _MODULE.parts[-3:] == ("agentx", "capabilities", "device.py")


def test_c8_01_reuses_only_canonical_capability_abi_identity() -> None:
    agentx_imports = {name for name in _imports() if name.startswith("agentx.")}
    assert agentx_imports == {"agentx.capabilities.abi"}


def test_c8_01_has_no_kernel_cognition_hive_learning_procedure_or_infrastructure_import() -> None:
    forbidden_prefixes = (
        "agentx.cognition",
        "agentx.core",
        "agentx.hive",
        "agentx.infrastructure",
        "agentx.kernel",
        "agentx.learning",
        "agentx.procedures",
        "agentx.capabilities.runtime",
        "agentx.capabilities.executor",
        "agentx.capabilities.registry",
        "agentx.capabilities.windows",
        "agentx.capabilities.browser",
    )
    assert not any(name.startswith(prefix) for name in _imports() for prefix in forbidden_prefixes)


def test_c8_01_has_no_transport_process_or_persistence_imports() -> None:
    forbidden_roots = {
        "aiohttp",
        "asyncio",
        "http",
        "httpx",
        "os",
        "playwright",
        "requests",
        "selenium",
        "socket",
        "sqlite3",
        "subprocess",
        "urllib",
        "websocket",
        "websockets",
        "socketserver",
    }
    roots = {name.split(".", maxsplit=1)[0] for name in _imports()}
    assert not roots & forbidden_roots


def test_c8_01_calls_no_transport_execution_authority_or_registration_surface() -> None:
    forbidden_calls = {
        "bind",
        "connect",
        "connect_to",
        "create_connection",
        "disconnect",
        "execute",
        "launch",
        "listener",
        "open",
        "pair",
        "Popen",
        "read",
        "register",
        "route",
        "send",
        "socket",
        "write",
    }
    assert not _called_names() & forbidden_calls


def test_c8_01_defines_no_authority_execution_or_verification_contracts() -> None:
    class_names = {node.name for node in _tree().body if isinstance(node, ast.ClassDef)}
    forbidden = {
        "ActionGate",
        "AndroidCompanion",
        "AndroidAccessibility",
        "AuthorityContext",
        "Capability",
        "CapabilityRegistry",
        "CrossDeviceRouter",
        "CrossDeviceVerifier",
        "DevicePairing",
        "EmergencyStop",
        "ExecutionContext",
        "FileTransfer",
        "Permission",
        "ProcedureGraph",
        "ResourceBudget",
        "ResourceEnvelope",
        "RiskLevel",
        "SharedHive",
        "Task",
        "TransportClient",
        "VerificationResult",
        "VpnTunnel",
    }
    assert class_names.isdisjoint(forbidden)


def test_c8_01_has_no_pairing_transport_or_android_specific_design_surface() -> None:
    # The module may *describe* excluded scope in its docstring, but it must
    # not define a class, function, or method whose name belongs to pairing,
    # transport, remote execution, routing, verification, or Android-specific
    # APIs. Names are checked so descriptive prose in the docstring is allowed.
    defined_names = {
        node.name
        for node in ast.walk(_tree())
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }
    forbidden = {
        "AndroidCompanion",
        "AndroidAccessibility",
        "CrossDeviceRouter",
        "CrossDeviceVerifier",
        "DevicePairing",
        "PairingToken",
        "SharedHive",
        "Socket",
        "Transport",
        "TransportClient",
        "VpnTunnel",
    }
    assert defined_names.isdisjoint(forbidden)
    assert "DevicePairing" not in defined_names


def test_c8_01_descriptor_exposes_inert_snapshot_methods_only() -> None:
    assert _class_methods("DeviceDescriptor") == {
        "__post_init__",
        "availability",
        "connectivity",
        "last_seen",
        "provider_id",
        "to_dict",
        "to_json",
    }


def test_c8_01_observation_exposes_inert_snapshot_methods_only() -> None:
    assert _class_methods("DeviceObservation") == {
        "__post_init__",
        "to_dict",
        "to_json",
    }


def test_c8_01_production_surface_is_minimal_and_closed() -> None:
    assert _public_defs() == {
        "CANONICAL_DEVICE_AVAILABILITY",
        "CANONICAL_DEVICE_CAPABILITY_AVAILABILITY",
        "CANONICAL_DEVICE_CONNECTIVITY",
        "CANONICAL_DEVICE_ENVIRONMENTS",
        "CANONICAL_DEVICE_FRESHNESS",
        "CANONICAL_DEVICE_KINDS",
        "CANONICAL_DEVICE_PLATFORMS",
        "DEVICE_CAPABILITY_SCHEMA_VERSION",
        "DEVICE_OBSERVATION_SCHEMA_VERSION",
        "DEVICE_PROTOCOL_SCHEMA_VERSION",
        "DeviceAvailability",
        "DeviceCapabilityAvailability",
        "DeviceCapabilityRef",
        "DeviceConnectivity",
        "DeviceDescriptor",
        "DeviceEnvironment",
        "DeviceFreshness",
        "DeviceId",
        "DeviceKind",
        "DeviceObservation",
        "DevicePlatform",
        "DeviceProtocolVersion",
        "DeviceProviderId",
        "DeviceScope",
        "DeviceValidationError",
        "evaluate_device_freshness",
    }


def test_c8_01_adds_no_runtime_dependency() -> None:
    config = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    assert config["project"]["dependencies"] == []


def test_c8_01_has_no_uuid_or_random_identity_generation() -> None:
    source = _MODULE.read_text(encoding="utf-8")
    assert "uuid4" not in _called_names()
    assert "import uuid" not in source
