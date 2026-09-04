"""Ownership boundary: ``agentx.kernel``.

Canonical responsibility: trusted authority boundary. Permissions, risk,
budgets, capability gating, and audit/security policy live here. The kernel is
the only layer that may grant authority.

Adaptive components must never own their own authority boundary. The boundary
manifest allows ``agentx.capabilities`` and ``agentx.cognition`` to be clients
of this package; it forbids ``agentx.learning`` and ``agentx.infrastructure``
from importing it.

Status: C1.08 adds explicit resource envelopes, deterministic budget evaluation,
and process-local atomic accounting. Resource allowance remains separate from
permission/action authorization and never executes work.
"""
