# N2.17 — Forward procedure replacement transaction

`agentx.procedure_replacement_transaction` is the composition boundary for an
already-assessed canonical `FORWARD_REPLACEMENT` decision.

It does not decide eligibility, rollback, verification, promotion or execution
authority. It binds the exact `ProcedureReplacementDecision`, current ACTIVE
record and target CANDIDATE record to an exact `ProcedureStore` history
snapshot, then delegates the storage mutation to
`agentx.infrastructure.procedure_activation`.

The shared activation seam owns only serialized storage mechanics. It rechecks
the complete expected history inside the write transaction, requires the
expected record to be the sole ACTIVE revision, preserves contiguous
append-only revision identity, retires the current revision and activates the
exact CANDIDATE atomically, and verifies the final single-ACTIVE postcondition.
Any failure rolls the transaction back.

N2.17 never accepts `ROLLBACK` decisions and never activates RETIRED history.
N2.18 owns rollback composition.
