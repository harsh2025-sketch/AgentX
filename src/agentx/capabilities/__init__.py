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
observation -> verify -> Task state -> canonical events/audit), and A2.04 adds
the narrow Executor boundary in ``agentx.capabilities.executor`` (a typed
``ExecutorRequest`` delegated to the canonical A1.10 loop — it adds no
authority, no verification, no retry, and no routing). A2.05 adds the narrow
Verifier boundary in ``agentx.capabilities.verifier`` (evaluates an
already-produced canonical A1.10 outcome against an explicit deterministic
verification requirement — it never invokes ``Capability.verify``, never
manufactures a ``VerificationResult``, never rewrites a ``ClosedLoopOutcome``,
and fails closed on missing evidence). There is still no Task Manager, Router,
agent loop, or concrete production capability implementation in this package;
demo capabilities live under ``tests/`` only.

A5.01 adds the Windows provider boundary in
``agentx.capabilities.windows`` (deterministic provider identity, explicit
platform facts/support evaluation, and contribution of already-constructed
Windows-scoped capabilities into a caller-owned A1.09 registry). It implements
no Windows automation and performs no import-time platform detection or
registration; availability remains descriptive and never authority.
"""
