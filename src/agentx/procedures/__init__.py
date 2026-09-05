"""Ownership boundary: ``agentx.procedures``.

Canonical responsibility: Procedure Graph IR/runtime and skill lifecycle.
Procedures represent scope/preconditions and are candidates until verified and
gated; they must not bypass kernel policy.

Day-3 A3.01 owns the canonical Procedure Graph IR surface here, in
``agentx.procedures.graph``: the finite, directed (possibly cyclic) graph of
typed nodes plus typed edges and a single entry point, with deterministic,
versioned, strict serialization. The IR is DATA only — it performs no side
effects and reaches no authority or runtime subsystem.

A3.02 adds the first node-family semantics on top of that IR, in
``agentx.procedures.nodes``: typed ACTION / OBSERVE / VERIFY contracts that are
read from and written to the opaque ``ProcedureNode.params`` mapping. They are
DATA only — ACTION describes a requested capability action without executing it
or holding authority, OBSERVE describes expected evidence without claiming it
was obtained, and VERIFY encodes a verification requirement that structurally
cannot express a verdict. The remaining node families
(REASON/RESEARCH/ROLLBACK/SUBPROCEDURE/END) are owned by later tasks
(A3.04-A3.05).

A3.03 adds the typed node-family DATA contracts for the BRANCH, TRANSFORM, and
WAIT node families here, in ``agentx.procedures.branch``,
``agentx.procedures.transform``, and ``agentx.procedures.wait``. Each contract
is strictly validated, inert data with no interpreter, no execution, no
waiting, and no authority; it embeds in the A3.01 graph through
``ProcedureNode.params`` (the graph stays opaque and is not redesigned).
BRANCH conditions are inert descriptors, TRANSFORM arguments are inert
JSON-compatible data, and WAIT is a declarative requirement plus an optional
canonical-duration timeout bound. ACTION/OBSERVE/VERIFY remain owned by A3.02
in ``agentx.procedures.nodes``.

The C2.03 durable storage contract remains inward in ``agentx.core.procedures`` and
``agentx.infrastructure.procedure_store``, which persist the graph opaquely as a
``CANONICAL_JSON`` :class:`agentx.core.procedures.ProcedurePayload`.
"""
