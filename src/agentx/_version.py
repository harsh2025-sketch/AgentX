"""Single source of truth for the AgentX package version.

M16 builds the first v1 release candidate. The release-candidate suffix is
intentional: mandatory real-environment matrix evidence can remain outstanding
without misrepresenting the package as a final production release.
"""

from __future__ import annotations

from typing import Final

__version__: Final[str] = "1.0.0rc1"

__all__ = ["__version__"]
