"""Ownership boundary: ``agentx.infrastructure``.

Canonical responsibility: configuration, event transport, persistence adapters,
and other non-domain plumbing. This is a technical support layer; it is not an
authority boundary. Infrastructure may consume stable contracts from
``agentx.core`` but must not become the owner of shared domain contracts.

C1.03 implements the EventBus in ``agentx.infrastructure.event_bus`` while the
canonical C1.02 Event contract lives inward in ``agentx.core.events``.
This package initializer deliberately remains declarative and side-effect free.
"""
