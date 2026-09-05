"""Ownership boundary: ``agentx.procedures``.

Canonical responsibility: Procedure Graph IR/runtime and skill lifecycle.
Procedures represent scope/preconditions and are candidates until verified and
gated; they must not bypass kernel policy.

Day-3 A3.01 owns the canonical Procedure Graph IR surface here, in
``agentx.procedures.graph``: the finite, directed (possibly cyclic) graph of
typed nodes plus typed edges and a single entry point, with deterministic,
versioned, strict serialization. The IR is DATA only — it performs no side
effects and reaches no authority or runtime subsystem. Node-family semantics
(ACTION/VERIFY/BRANCH/REASON/...) are owned by later tasks (A3.02-A3.05); the
C2.03 durable storage contract remains inward in ``agentx.core.procedures`` and
``agentx.infrastructure.procedure_store``, which persist the graph opaquely as a
``CANONICAL_JSON`` :class:`agentx.core.procedures.ProcedurePayload`.
"""
