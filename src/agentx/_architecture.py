"""AgentX canonical top-level package boundary manifest.

This module is the single, machine-readable source of truth for the canonical
top-level subsystem boundaries introduced by A1.02. It deliberately contains
*no runtime logic*: it is metadata/contract only. Other tasks are expected to
import the constants on this module (never to duplicate the lists) when they
need to know which packages form the canonical boundary.

The boundary model here is an architecture guardrail. It is *not* the Trusted
Kernel and it does *not* enforce security by itself. Security and authority are
owned by ``agentx.kernel`` and will be enforced there by its own task.
"""

from __future__ import annotations

from typing import Final

# --------------------------------------------------------------------------
# Canonical top-level subsystem packages.
# --------------------------------------------------------------------------

CORE: Final[str] = "agentx.core"
KERNEL: Final[str] = "agentx.kernel"
CAPABILITIES: Final[str] = "agentx.capabilities"
HIVE: Final[str] = "agentx.hive"
PROCEDURES: Final[str] = "agentx.procedures"
COGNITION: Final[str] = "agentx.cognition"
LEARNING: Final[str] = "agentx.learning"
INFRASTRUCTURE: Final[str] = "agentx.infrastructure"

SUBSYSTEMS: Final[tuple[str, ...]] = (
    CORE,
    KERNEL,
    CAPABILITIES,
    HIVE,
    PROCEDURES,
    COGNITION,
    LEARNING,
    INFRASTRUCTURE,
)

# --------------------------------------------------------------------------
# Exact allowed dependency edges between the canonical top-level subsystems.
#
# ``agentx.core`` is the shared-domain foundation: every other subsystem may
# build on it, and ``agentx.core`` itself imports nothing from the other
# subsystems. Concrete infrastructure is an outer implementation layer and may
# consume core contracts, but domain/kernel code must not depend outward on it.
# The Trusted Kernel is the only authority-granting layer; the edges below only
# record which subsystems may be clients of its boundary.
#
# This is deliberate and intentionally narrow. Exceptions to these edges are
# architecture decisions and must be approved by the Technical Lead; they must
# not be added ad hoc by an implementation task.
# --------------------------------------------------------------------------

ALLOWED_ARCHITECTURE_EDGES: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        # Shared domain contracts are the inward foundation for every outer
        # subsystem, including concrete infrastructure adapters.
        (KERNEL, CORE),
        (CAPABILITIES, CORE),
        (HIVE, CORE),
        (PROCEDURES, CORE),
        (COGNITION, CORE),
        (LEARNING, CORE),
        (INFRASTRUCTURE, CORE),
        # Governed implementations may depend on the authority boundary.
        (CAPABILITIES, KERNEL),
        # Procedure execution is routed through the kernel, never around it.
        (PROCEDURES, KERNEL),
        # Reasoning/planning is a client of trusted authority and of the
        # outcome verification contract provided by the kernel.
        (COGNITION, KERNEL),
    }
)

__all__ = [
    "ALLOWED_ARCHITECTURE_EDGES",
    "CAPABILITIES",
    "COGNITION",
    "CORE",
    "HIVE",
    "INFRASTRUCTURE",
    "KERNEL",
    "LEARNING",
    "PROCEDURES",
    "SUBSYSTEMS",
]
