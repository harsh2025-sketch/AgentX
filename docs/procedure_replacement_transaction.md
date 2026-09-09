# Procedure Replacement Transaction

The N2.17 Procedure Version Replacement Transaction implements the atomic persistence transaction to install an already-approved procedure replacement revision.

## Responsibilities
- Receives a canonical `ProcedureReplacementDecision` that has been pre-evaluated as `ELIGIBLE` by the replacement policy.
- Receives the exact current state of the store for the active record and the target record to replace it with.
- Atomically, within a single serialized SQLite write transaction (`BEGIN IMMEDIATE`), transitions the current `ACTIVE` revision to `RETIRED`, and inserts or updates the target revision to `ACTIVE`.
- Ensures concurrency safety by verifying that the state of the active record has not mutated since the decision was made.

## Bound Requirements Checked
- **Outcome**: The decision must strictly be `ELIGIBLE`.
- **Identity & Revisions**: The supplied active record and target record must match the `ProcedureId` and revision numbers dictated by the decision.
- **Concurrent Mutations**: If the active revision has been altered (e.g. is no longer `ACTIVE`, or was updated), the transaction aborts and leaves the store untouched.
- **Atomicity**: Either both the retirement of the active revision and the promotion of the new revision happen, or neither do. No split state will be written.

This module guarantees purely mechanical, secure progression of versions without re-evaluating any replacement criteria, executing any capabilities, or altering execution authority.
