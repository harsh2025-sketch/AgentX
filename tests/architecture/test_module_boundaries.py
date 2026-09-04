"""Machine-testable guardrails for the canonical top-level module boundaries.

A1.02 establishes an explicit, narrow dependency model for the top-level
``agentx`` subsystem packages. These tests inspect that model from the single
authority in ``agentx._architecture`` and confirm implementation code respects
it.

The checker is deliberately simple: it parses each source file under a
subsystem with the standard library :mod:`ast` and reports direct imports into
other canonical subsystems. It does not chase through imported modules.

These are architecture guardrails, not a security boundary. Static import
rules do not prove security and cannot detect indirect or delegated execution.
Security remains the responsibility of the Trusted Kernel (``agentx.kernel``).
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import pytest

from agentx import _architecture
from tests.unit.test_package import BOUNDARY_PACKAGES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"


def _owning_subsystem(module_name: str) -> str | None:
    """Return the canonical subsystem that owns ``module_name``, if any.

    ``agentx.kernel`` and ``agentx.kernel.policy`` are owned by
    ``agentx.kernel``; ``agentx`` itself is not a subsystem.
    """
    for package in _architecture.SUBSYSTEMS:
        if module_name == package or module_name.startswith(f"{package}."):
            return package
    return None


def _resolve_import_from(node: ast.ImportFrom, source_package: str) -> str:
    """Resolve an :mod:`ast` ``ImportFrom`` to its absolute module name."""
    if node.level == 0:
        return node.module or ""

    package_parts = source_package.split(".")
    drop = node.level - 1
    if drop >= len(package_parts):
        # A relative import that reaches above ``agentx`` is not a canonical
        # subsystem edge; return the raw form so it is treated as unexpected.
        return f"{'.' * node.level}{node.module or ''}"

    base_parts = package_parts[: len(package_parts) - drop]
    base = ".".join(base_parts)
    if node.module:
        return f"{base}.{node.module}"
    return base


def _ast_import_targets(source: str, source_package: str) -> tuple[tuple[str, str], ...]:
    """Return ``(imported_module, owned_subsystem)`` from an ``ast`` tree.

    ``source`` and ``source_package`` are used as a module definition would be.
    Only imports that resolve into canonical subsystems are reported.
    """
    tree = ast.parse(source)
    targets: list[tuple[str, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                subsystem = _owning_subsystem(alias.name)
                if subsystem is not None:
                    targets.append((alias.name, subsystem))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and (node.module or "") == "agentx":
                # ``from agentx import capabilities`` imports a subsystem package.
                for alias in node.names:
                    imported = f"agentx.{alias.name}"
                    subsystem = _owning_subsystem(imported)
                    if subsystem is not None:
                        targets.append((imported, subsystem))
            else:
                imported = _resolve_import_from(node, source_package)
                subsystem = _owning_subsystem(imported)
                if subsystem is not None:
                    targets.append((imported, subsystem))

    # de-duplicate while preserving order.
    return tuple(dict.fromkeys(targets))


def _boundary_violations(
    source: str,
    targets: Iterable[tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    """Return the imports from ``source`` that violate the boundary model.

    An allowed subsystem edge permits imports of the target package itself and
    modules owned beneath that package. The permission does not extend to any
    other subsystem, so forbidden reverse/cross edges remain violations.
    """
    violations: list[tuple[str, str]] = []
    for imported, target in targets:
        if source == target:
            # Internal implementation details within the same subsystem.
            continue
        allowed_subsystem = (source, target) in _architecture.ALLOWED_ARCHITECTURE_EDGES
        imported_is_owned_by_target = imported == target or imported.startswith(f"{target}.")
        if not (allowed_subsystem and imported_is_owned_by_target):
            violations.append((imported, target))
    return tuple(violations)


def _subsystem_files(subsystem: str) -> tuple[Path, ...]:
    """Return every ``.py`` module under a canonical subsystem."""
    root = _SRC_ROOT / Path(*subsystem.split("."))
    return tuple(sorted(p for p in root.rglob("*.py") if p.is_file()))


def _module_import_edges(subsystem: str) -> tuple[tuple[str, str], ...]:
    """Collect direct cross-subsystem imports across a subsystem's source tree."""
    edges: list[tuple[str, str]] = []
    for path in _subsystem_files(subsystem):
        module_parts = [str(part) for part in path.relative_to(_SRC_ROOT).with_suffix("").parts]
        if module_parts and module_parts[-1] == "__init__":
            module_parts = module_parts[:-1]
        module_name = ".".join(module_parts)
        source_package = (
            module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]
        )
        source = path.read_text(encoding="utf-8")
        edges.extend(_ast_import_targets(source, source_package))
    return tuple(dict.fromkeys(edges))


def test_canonical_subsystems_single_source_of_truth() -> None:
    """The canonical subsystem list is the production module, not a copy."""
    assert _architecture.SUBSYSTEMS == BOUNDARY_PACKAGES


def test_manifest_declares_only_canonical_subsystems() -> None:
    """Every allowed edge references only canonical subsystem names."""
    assert _architecture.SUBSYSTEMS
    assert _architecture.ALLOWED_ARCHITECTURE_EDGES
    for source, target in _architecture.ALLOWED_ARCHITECTURE_EDGES:
        assert source in _architecture.SUBSYSTEMS
        assert target in _architecture.SUBSYSTEMS
        assert source != target


def test_manifest_allows_outer_subsystems_to_depend_on_core() -> None:
    """Shared domain contracts are the inward foundation for every outer subsystem."""
    for package in _architecture.SUBSYSTEMS:
        if package == _architecture.CORE:
            continue
        assert (package, _architecture.CORE) in _architecture.ALLOWED_ARCHITECTURE_EDGES


def test_core_does_not_depend_on_other_subsystems() -> None:
    """Agent Runtime shared contracts must not depend on outer subsystems."""
    assert all(
        source != _architecture.CORE for source, _target in _architecture.ALLOWED_ARCHITECTURE_EDGES
    )


def test_kernel_does_not_depend_on_infrastructure() -> None:
    """Kernel may consume core contracts but must not depend outward on infrastructure."""
    assert (
        _architecture.KERNEL,
        _architecture.INFRASTRUCTURE,
    ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES


def test_unneeded_future_infrastructure_edges_are_not_predeclared() -> None:
    """Hive/procedures/learning do not gain outward plumbing dependencies speculatively."""
    for source in (_architecture.HIVE, _architecture.PROCEDURES, _architecture.LEARNING):
        assert (
            source,
            _architecture.INFRASTRUCTURE,
        ) not in _architecture.ALLOWED_ARCHITECTURE_EDGES


def test_boundary_manifest_module_imports_no_subsystem() -> None:
    """The manifest is metadata and must not establish a runtime dependency."""
    source = (_SRC_ROOT / "agentx" / "_architecture.py").read_text(encoding="utf-8")
    assert _ast_import_targets(source, "agentx") == ()


@pytest.mark.parametrize("source", _architecture.SUBSYSTEMS)
def test_source_package_respects_allowed_dependency_model(source: str) -> None:
    """Each canonical subsystem only imports from its allowed dependency set."""
    violations = _boundary_violations(source, _module_import_edges(source))
    assert violations == (), f"{source} violates the boundary model: " + ", ".join(
        f"{imported} (owned by {target})" for imported, target in violations
    )


def test_checker_detects_forbidden_top_level_edge() -> None:
    """The guardrail fails on a real forbidden edge.

    A synthetic fixture imports ``agentx.capabilities`` as ``_capabilities``.
    When treated as being owned by ``agentx.learning``, that is an edge
    ``learning -> capabilities``, which is deliberately not in the allowed set.
    """
    violation_path = _REPO_ROOT / "tests" / "architecture" / "violations" / "forbidden_edges.py"
    source = violation_path.read_text(encoding="utf-8")
    edges = _ast_import_targets(source, "agentx.learning")

    assert ("agentx.capabilities", "agentx.capabilities") in edges
    violations = _boundary_violations("agentx.learning", edges)
    assert ("agentx.capabilities", "agentx.capabilities") in violations


def test_checker_allows_explicitly_allowed_submodule_import() -> None:
    """Positive control: ``infrastructure -> core.events`` is a valid inward edge."""
    source = "from agentx.core.events import Event\n"
    edges = _ast_import_targets(source, "agentx.infrastructure")

    assert ("agentx.core.events", "agentx.core") in edges
    assert _boundary_violations("agentx.infrastructure", edges) == ()


def test_checker_allows_allowed_package_boundary_import() -> None:
    """Positive control: the target package itself remains valid on an allowed edge."""
    source = "import agentx.core as _core\n"
    edges = _ast_import_targets(source, "agentx.infrastructure")

    assert ("agentx.core", "agentx.core") in edges
    assert _boundary_violations("agentx.infrastructure", edges) == ()


def test_checker_rejects_core_to_infrastructure_submodule() -> None:
    """Negative control: moving Event inward must not license the reverse edge."""
    source = "from agentx.infrastructure.event_bus import EventBus\n"
    edges = _ast_import_targets(source, "agentx.core")

    assert ("agentx.infrastructure.event_bus", "agentx.infrastructure") in edges
    violations = _boundary_violations("agentx.core", edges)
    assert ("agentx.infrastructure.event_bus", "agentx.infrastructure") in violations


def test_checker_rejects_kernel_to_infrastructure_submodule() -> None:
    """Negative control: kernel remains independent of concrete infrastructure."""
    source = "from agentx.infrastructure.event_bus import EventBus\n"
    edges = _ast_import_targets(source, "agentx.kernel")

    assert ("agentx.infrastructure.event_bus", "agentx.infrastructure") in edges
    violations = _boundary_violations("agentx.kernel", edges)
    assert ("agentx.infrastructure.event_bus", "agentx.infrastructure") in violations


def test_checker_does_not_let_allowed_edge_cover_another_subsystem() -> None:
    """An allowed core edge cannot make an unrelated subsystem import legal."""
    source = "import agentx.capabilities._internal as _capability_internal\n"
    edges = _ast_import_targets(source, "agentx.infrastructure")

    assert ("agentx.capabilities._internal", "agentx.capabilities") in edges
    violations = _boundary_violations("agentx.infrastructure", edges)
    assert ("agentx.capabilities._internal", "agentx.capabilities") in violations
