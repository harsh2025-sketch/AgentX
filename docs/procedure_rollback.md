# N2.18 — Explicit procedure rollback composition

`agentx.procedure_rollback` consumes one exact caller-selected rollback target
and one canonical `ProcedureReplacementDecision` whose kind is `ROLLBACK` and
outcome is `ELIGIBLE`.

It never chooses a nearest or newest historical target. The request binds the
exact `ProcedureId`, current revision, target revision, request time and
optionally the caller's exact known revision set. The stored history is read
before mutation and is compared byte-for-byte again inside the shared write
transaction, so stale or concurrent requests fail closed.

`RETIRED` is terminal. A RETIRED target is never reactivated. Instead N2.18
creates a new contiguous CANDIDATE revision whose payload and scope are copied
exactly from the selected historical target; the original RETIRED record stays
unchanged. The shared `agentx.infrastructure.procedure_activation` seam then
atomically retires the current ACTIVE record and activates only that new
CANDIDATE. An earlier CANDIDATE target may be activated directly when it still
matches the exact stored history.

Rollback preserves revision numbers and all historical rows. It deletes
nothing, rewrites no historical payload, grants no Permission, changes no Risk
or budget, bypasses no Action Gate or EmergencyStop, executes no capability or
Procedure, and never claims verified Task success.
