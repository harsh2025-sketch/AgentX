"""AgentX: a Windows-first, local-first Adaptive Personal Operating Intelligence.

This is the top-level import namespace for the project.

Current status: **bootstrap only**. The package installs, exposes a version,
and provides a minimal command-line entry point (``python -m agentx``).
The architectural subsystems described in the project documentation are
*not* implemented yet; their sub-packages exist solely to establish
ownership boundaries for future work.
"""

from __future__ import annotations

from agentx._version import __version__

__all__ = ["__version__"]
