"""Ownership boundary: ``agentx.infrastructure``.

Canonical responsibility: configuration, events, persistence adapters, and
other non-domain plumbing. This is a technical support layer; it is not an
authority boundary and must not depend on domain subsystems.

C1.02 implements the canonical event contract in ``agentx.infrastructure.events``.
This package initializer deliberately remains declarative and side-effect free.
"""
