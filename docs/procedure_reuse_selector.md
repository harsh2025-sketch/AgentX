# Deterministic procedure reuse selector (N2.11)

`agentx.procedure_reuse_selector` selects from an explicit, bounded sequence of
canonical `ProcedureCandidate` values. It does not retrieve candidates from a
store. The caller supplies the `ProcedureRequirement` containing the typed
applicability evidence.

## Policy

1. Only records with `ProcedureStatus.ACTIVE` are eligible. Candidate,
   retired, and malformed inputs are never normal reuse results.
2. Applicability is assessed by the existing M4.04
   `ProcedureApplicabilityMatcher`; this module does not duplicate scope or
   capability matching.
3. Exact M4.04 matches are preferred as a categorical deterministic basis over
   broader compatible matches. This is not a score or a trust ranking.
4. Equal applicable candidates return `AMBIGUOUS`. In particular, two active
   revisions are not resolved by revision number, timestamps, payload text, or
   input order. Duplicate identical supplied records collapse to one; conflicting
   records sharing an identity fail closed as ambiguous.
5. The selected result preserves the exact `ProcedureId` and revision supplied
   by the caller.

`NO_MATCH`, `SELECTED`, and `AMBIGUOUS` are inert selection outcomes. Selection
never executes or activates a procedure, invokes a capability, changes task or
kernel authority state, mutates a `ProcedureStore`, or fabricates success.
Procedure payload, names, and metadata are opaque and cannot influence policy.

The implementation is stateless and pure: it performs no model call, embedding,
random choice, clock read, persistence, network, or filesystem operation.
