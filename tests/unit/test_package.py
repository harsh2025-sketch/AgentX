"""Unit tests for the ``agentx`` package itself (import surface and version)."""

from __future__ import annotations

import ast
import importlib
import re
from pathlib import Path

import pytest

import agentx

# PEP 440 "public version" shape, sufficient for a sanity check without a
# third-party packaging dependency.
_PEP440_PUBLIC = re.compile(r"^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")

# Every sub-package that establishes an ownership boundary. Keep in sync with
# the canonical architecture manifest.
BOUNDARY_PACKAGES = (
    "agentx.core",
    "agentx.kernel",
    "agentx.capabilities",
    "agentx.hive",
    "agentx.procedures",
    "agentx.cognition",
    "agentx.learning",
    "agentx.infrastructure",
)


def test_agentx_imports() -> None:
    """``import agentx`` succeeds and exposes the version as its public API."""
    assert agentx.__name__ == "agentx"
    assert agentx.__all__ == ["__version__"]


def test_version_exists_and_is_non_empty() -> None:
    assert isinstance(agentx.__version__, str)
    assert agentx.__version__.strip() != ""


def test_version_is_pep440_compliant() -> None:
    assert _PEP440_PUBLIC.match(agentx.__version__), agentx.__version__


def test_version_has_single_source_of_truth() -> None:
    """The top-level version re-exports ``agentx._version`` (no duplication)."""
    from agentx import _version

    assert agentx.__version__ is _version.__version__


def test_installed_distribution_metadata_matches_package_version() -> None:
    """The installed distribution reports the same version as the package."""
    from importlib import metadata

    assert metadata.version("agentx") == agentx.__version__


@pytest.mark.parametrize("name", BOUNDARY_PACKAGES)
def test_boundary_packages_import(name: str) -> None:
    """Each ownership-boundary package imports cleanly and is a real package."""
    module = importlib.import_module(name)
    assert module.__name__ == name
    # Regular package (has __init__.py), not an implicit namespace package.
    assert module.__file__ is not None
    assert module.__file__.endswith("__init__.py")


@pytest.mark.parametrize("name", BOUNDARY_PACKAGES)
def test_boundary_package_initializers_are_clean(name: str) -> None:
    """Subsystem ``__init__.py`` files stay declarative and side-effect free.

    A subsystem may gain explicitly owned implementation modules without turning
    its package initializer into an implementation surface. This preserves the
    cleanliness invariant while allowing legitimate child modules such as
    ``agentx.infrastructure.events`` and ``agentx.infrastructure.config``.
    """
    module = importlib.import_module(name)
    assert module.__file__ is not None
    init_path = Path(module.__file__)
    tree = ast.parse(init_path.read_text(encoding="utf-8"))

    assert len(tree.body) == 1, f"{name} initializer must contain only its docstring"
    statement = tree.body[0]
    assert isinstance(statement, ast.Expr)
    assert isinstance(statement.value, ast.Constant)
    assert isinstance(statement.value.value, str)
