"""Single source of truth for the AgentX package version.

``pyproject.toml`` reads this value at build time via
``[tool.setuptools.dynamic] version = { attr = "agentx.__version__" }``, so
the version must never be duplicated anywhere else in the repository.

The version follows PEP 440 and, once the project stabilises, semantic
versioning. During bootstrap it is a ``0.0.x`` development series.
"""

from __future__ import annotations

from typing import Final

__version__: Final[str] = "0.0.1"
"""The current AgentX version string."""

__all__ = ["__version__"]
