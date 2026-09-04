"""Ownership boundary: ``agentx.kernel``.

Canonical responsibility: trusted authority boundary. Permissions, risk,
budgets, capability gating, and audit/security policy live here. The kernel is
the only layer that may grant authority.

Adaptive components must never own their own authority boundary. The boundary
manifest allows ``agentx.capabilities`` and ``agentx.cognition`` to be clients
of this package; it forbids ``agentx.learning`` and ``agentx.infrastructure``
from importing it.

Status: C1.07 adds explicit permission/authority contracts and the deterministic
Action Gate. Risk classification remains separate descriptive input; gate
evaluation itself never executes actions or approval flows.
"""
