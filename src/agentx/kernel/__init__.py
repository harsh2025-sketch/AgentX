"""Ownership boundary: ``agentx.kernel``.

Canonical responsibility: trusted authority boundary. Permissions, risk,
budgets, capability gating, and audit/security policy live here. The kernel is
the only layer that may grant authority.

Adaptive components must never own their own authority boundary. The boundary
manifest allows ``agentx.capabilities`` and ``agentx.cognition`` to be clients
of this package; it forbids ``agentx.learning`` and ``agentx.infrastructure``
from importing it.

Status: not implemented. This package deliberately contains no code yet.
"""
