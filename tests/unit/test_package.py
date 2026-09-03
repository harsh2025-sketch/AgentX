"""Unit tests for the ``agentx`` package itself (import surface and version)."""

from __future__ import annotations

import importlib
import re

import pytest

import agentx

# PEP 440 "public version" shape, sufficient for a sanity check without a
# third-party packaging dependency.
_PEP440_PUBLIC = re.compile(r"^\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?$")

# Every sub-package that establishes an ownership boundary. Keep in sync with
# the module map in README.md.
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
    """The installed distribution (``pip install -e .``) reports the same version."""
    from importlib import metadata

    try:
        dist_version = metadata.version("agentx")
    except metadata.PackageNotFoundError:
        pytest.skip("agentx distribution is not installed (running from source tree only)")
    assert dist_version == agentx.__version__


@pytest.mark.parametrize("name", BOUNDARY_PACKAGES)
def test_boundary_packages_import(name: str) -> None:
    """Each ownership-boundary package imports cleanly and is a real package."""
    module = importlib.import_module(name)
    assert module.__name__ == name
    # Regular package (has __init__.py), not an implicit namespace package.
    assert module.__file__ is not None
    assert module.__file__.endswith("__init__.py")


@pytest.mark.parametrize("name", BOUNDARY_PACKAGES)
def test_boundary_packages_are_empty(name: str) -> None:
    """Bootstrap invariant: boundary packages carry a docstring and nothing else.

    This guards against speculative placeholder implementations creeping in
    outside of a task that explicitly owns the subsystem.
    """
    module = importlib.import_module(name)
    assert module.__doc__ and "not implemented" in module.__doc__
    public_names = [n for n in vars(module) if not n.startswith("__")]
    assert public_names == [], f"{name} unexpectedly defines: {public_names}"
