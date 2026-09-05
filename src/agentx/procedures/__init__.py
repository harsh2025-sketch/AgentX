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

A3.05 adds the typed node-family DATA contracts for the ROLLBACK,
SUBPROCEDURE, and END node families here, in ``agentx.procedures.rollback``,
``agentx.procedures.subprocedure``, and ``agentx.procedures.end``. Each
contract is strictly validated, inert data with no interpreter, no rollback
execution, no procedure invocation, no outcome, and no authority; it embeds
in the A3.01 graph through ``ProcedureNode.params`` (the graph stays opaque
and is not redesigned). ROLLBACK records an explicit typed, inert rollback
scope; SUBPROCEDURE records a canonical ``(procedure_id, revision)``
reference with inert JSON bindings and never an implicit "latest" version;
END is the smallest useful termination payload, structurally incapable of
encoding success — reaching END is never verified task success.

A3.06 adds the canonical preconditions/postconditions DATA contract here, in
``agentx.procedures.conditions``. It is representation and validation only:
it never executes, evaluates, or verifies a condition and never interprets a
procedure. One reusable requirement (:class:`~agentx.procedures.conditions.
ProcedureCondition`) covers both preconditions and postconditions; each
declares, as explicit structured data, a requirement stated against the
canonical core evidence vocabulary, with an explicit condition identity and
an optional inert evidence reference. Conditions are scoped to the whole
procedure or to individual graph nodes by canonical ``ProcedureNodeId``
reference and are carried in a minimal companion document
(:class:`~agentx.procedures.conditions.ProcedureConditions`) — a
deterministic, strictly validated, immutable wrapper that adds no node kind,
no edge, and no second graph schema, cross-checked against a graph only by
structural node-id membership (``bind_to_graph``). A precondition does not
mean satisfied; a postcondition does not mean verified, does not manufacture
a VerificationResult, and does not mark a Task SUCCEEDED (I1 stays absolute).
There is no condition DSL: statements and references are inert descriptive
data, and nothing here is evaluated, compiled, or dispatched.

The C2.03 durable storage contract remains inward in ``agentx.core.procedures`` and
``agentx.infrastructure.procedure_store``, which persist the graph opaquely as a
``CANONICAL_JSON`` :class:`agentx.core.procedures.ProcedurePayload`.
"""
