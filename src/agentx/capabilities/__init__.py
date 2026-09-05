"""Ownership boundary: ``agentx.capabilities``.

Canonical responsibility: governed machine/browser/device capability
implementations and contracts. Capabilities are provider abstractions and they
eventually execute only through governed runtime paths.

Status: A1.08 defines the canonical Capability ABI (interface contract only)
in ``agentx.capabilities.abi``, A1.09 adds the in-process Capability
Registry in ``agentx.capabilities.registry`` (registration and exact-identity
lookup only — discovery is never authority), and A1.10 adds the first
closed-loop deterministic execution path in
``agentx.capabilities.runtime`` (one governed, verified capability execution
per run: authority -> gate -> emergency stop -> budget -> execute ->
observation -> verify -> Task state -> canonical events/audit). There is
still no Executor subsystem, Verifier subsystem, or concrete production
capability implementation in this package; demo capabilities live under
``tests/`` only.
"""
