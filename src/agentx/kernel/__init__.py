"""Ownership boundary: ``agentx.kernel``.

Canonical responsibility: trusted authority boundary. Permissions, risk,
budgets, capability gating, and audit/security policy live here. The kernel is
the only layer that may grant authority.

Adaptive components must never own their own authority boundary. The boundary
manifest allows ``agentx.capabilities`` and ``agentx.cognition`` to be clients
of this package; it forbids ``agentx.learning`` and ``agentx.infrastructure``
from importing it.

Status: C1.06 implements the canonical risk classification contract in
``agentx.kernel.risk``. This package initializer remains declarative and
side-effect free; risk classification itself grants no authority.
"""
