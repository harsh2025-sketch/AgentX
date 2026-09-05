"""Architecture tests for the canonical Capability Registry placement (A1.09).

The registry is discovery infrastructure for the ``agentx.capabilities``
subsystem. These guardrails keep it there, keep it dependent on the canonical
A1.08 ABI only, and keep the ABI contract itself independent of it.

Import-level rules are architecture guardrails, not security enforcement:
authority remains owned by the Trusted Kernel, which the registry never
touches.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agentx import _architecture

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_AGENTX_SRC = _SRC_ROOT / "agentx"

_CAPABILITY_ABI = _AGENTX_SRC / "capabilities" / "abi.py"
_CAPABILITY_REGISTRY = _AGENTX_SRC / "capabilities" / "registry.py"


def _class_definitions(name: str) -> list[Path]:
    definitions: list[Path] = []
    for path in _AGENTX_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.ClassDef) and node.name == name for node in ast.walk(tree)):
            definitions.append(path.relative_to(_SRC_ROOT))
    return definitions


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    return tuple(dict.fromkeys(imported))


def test_registry_has_exactly_one_canonical_definition() -> None:
    """Only ``agentx.capabilities.registry`` defines the registry contracts."""
    assert _class_definitions("CapabilityRegistry") == [Path("agentx/capabilities/registry.py")]
    for error in (
        "CapabilityRegistryError",
        "MalformedCapabilityError",
        "CapabilityAlreadyRegisteredError",
        "CapabilityNotFoundError",
    ):
        assert _class_definitions(error) == [Path("agentx/capabilities/registry.py")]


def test_registry_depends_only_on_the_canonical_capability_abi() -> None:
    """Discovery reuses A1.08 contracts and imports no other subsystem."""
    imported = _imported_modules(_CAPABILITY_REGISTRY)
    agentx_imports = {module for module in imported if module.startswith("agentx")}

    assert agentx_imports == {"agentx.capabilities.abi"}


def test_registry_touches_no_authority_or_transport_module() -> None:
    """Registration is not authority: no kernel, events, or persistence."""
    imported = _imported_modules(_CAPABILITY_REGISTRY)
    forbidden_prefixes = (
        "agentx.kernel",
        "agentx.core",
        "agentx.infrastructure",
        "agentx.hive",
        "agentx.procedures",
        "agentx.cognition",
        "agentx.learning",
    )

    assert [module for module in imported if module.startswith(forbidden_prefixes)] == []


def test_capability_abi_does_not_depend_on_the_registry() -> None:
    """The A1.08 contract stays canonical and registry-independent."""
    imported = _imported_modules(_CAPABILITY_ABI)

    assert "agentx.capabilities.registry" not in imported
    assert not any(module.endswith("registry") for module in imported)


def test_capability_abi_does_not_depend_on_the_runtime_loop() -> None:
    """The A1.08 contract stays independent of the A1.10 runtime path."""
    imported = _imported_modules(_CAPABILITY_ABI)

    assert not any(
        module == "agentx.capabilities.runtime" or module.startswith("agentx.capabilities.runtime.")
        for module in imported
    )


def test_only_the_canonical_runtime_module_imports_the_registry() -> None:
    """Discovery is wired to execution by exactly one canonical A1.10 module.

    A1.09 landed with a placeholder guardrail ("nothing imports the registry
    yet"). A1.10 is the task that closes the loop through the registry, so the
    guardrail narrows instead of vanishing: ``agentx.capabilities.runtime`` is
    the single module in the codebase allowed to import the registry, and no
    other module anywhere under ``src`` may wire execution through it.
    """
    canonical_runtime = _AGENTX_SRC / "capabilities" / "runtime.py"
    importers = [
        path.relative_to(_SRC_ROOT)
        for path in _AGENTX_SRC.rglob("*.py")
        if path not in (_CAPABILITY_REGISTRY, canonical_runtime)
        and "agentx.capabilities.registry" in _imported_modules(path)
    ]

    assert importers == []


def test_registry_adds_no_new_architecture_edge() -> None:
    """Discovery lives inside ``agentx.capabilities`` and widens no boundary."""
    assert _CAPABILITY_REGISTRY.is_relative_to(_AGENTX_SRC / "capabilities")
    assert (_architecture.CAPABILITIES, _architecture.CORE) in (
        _architecture.ALLOWED_ARCHITECTURE_EDGES
    )
    assert (_architecture.CAPABILITIES, _architecture.KERNEL) in (
        _architecture.ALLOWED_ARCHITECTURE_EDGES
    )
    # The registry itself uses neither edge; it needs only the local ABI.
    assert _imported_modules(_CAPABILITY_REGISTRY).count("agentx.capabilities.abi") == 1


def test_registry_declares_no_plugin_or_filesystem_discovery() -> None:
    """No plugin scanning, dynamic imports, entry points, or path walking."""
    source = _CAPABILITY_REGISTRY.read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported = _imported_modules(_CAPABILITY_REGISTRY)
    assert {"importlib", "importlib.metadata", "pkgutil", "pathlib", "os", "sys"}.isdisjoint(
        imported
    )

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert {"eval", "exec", "compile", "open", "__import__"}.isdisjoint(called)
