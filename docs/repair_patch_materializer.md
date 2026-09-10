# N2.15 — Procedure Repair Patch Materializer

N2.15 implements the pure, deterministic outer composition step that turns an
already accepted canonical `RepairPatchProposal` into a **new inert Procedure
candidate revision**.  Its only implementation is
`agentx.repair_patch_materializer`.

```python
from agentx.repair_patch_materializer import materialize_repair_patch

materialized = materialize_repair_patch(source=source_record, patch=proposal)
candidate_record = materialized.candidate
```

The caller supplies the exact source `ProcedureRecord` and the exact canonical
proposal. Acceptance is an external precondition: the current
`RepairPatchProposal` contract intentionally carries no approval/selection
field, and N2.15 does not add one.

## Supported patch kind

The landed `agentx.core.repair_patch` vocabulary is closed. N2.15 supports
exactly its one concrete operation:

| Patch kind | Materialization behaviour |
| --- | --- |
| `NODE_DEFINITION_REPLACEMENT` | Parses `proposed_definition` as one complete canonical `ProcedureNode`, requires its ID to equal the exact target node ID, replaces that one node, keeps graph connectivity unchanged, and reconstructs the canonical `ProcedureGraph`. |

There is no generic patch language. In particular, materialization has no
partial field updates, node addition/deletion, edge mutation, source-code
diffs, shell/Python operations, rollback, status update, or activation path.
Unsupported kinds fail closed.

## Exact source/revision binding

Before materializing anything, the function requires all of the following:

1. `source` is a canonical `ProcedureRecord` whose payload is
   `CANONICAL_JSON` containing a structurally valid `ProcedureGraph`.
2. The patch target ProcedureId exactly equals the source ProcedureId.
3. The patch target revision exactly equals the source revision. A proposal
   bound to an older revision is rejected when another source revision is
   supplied.
4. The target node exists in that graph.
5. The proposed definition is a complete canonical `ProcedureNode`, and its
   node ID exactly equals the target node ID.
6. The replacement graph passes the existing canonical `ProcedureGraph`
   structural checks.

Malformed node payloads, unknown/unsupported patch kinds, wrong ProcedureId,
wrong source revision, missing nodes, and graph-invalid results all raise
`RepairPatchMaterializationError`. The function does not reconcile, retarget,
or guess.

## New-candidate semantics and provenance

The return value is the frozen `MaterializedProcedureCandidate` wrapper. It
contains:

- `source_procedure_id` — exact source ProcedureId;
- `source_revision` — exact source revision;
- `repair_patch` — the complete canonical proposal by value, including its
  embedded `RepairCandidate` diagnostic/evidence provenance; the landed patch
  contract has no separate patch ID to duplicate;
- `candidate` — the newly constructed `ProcedureRecord` revision.

The candidate:

- retains the source ProcedureId;
- uses exactly `source revision + 1`;
- preserves source scope;
- serializes a deterministically ordered canonical `ProcedureGraph` with only
the target node definition changed;
- takes the proposal's caller-supplied `proposed_at` instant as its deterministic
creation time;
- is created through `ProcedureRecord.create`, so its status is exactly
  `CANDIDATE` and `updated_at` is `None`.

The wrapper and the underlying ProcedureRecord are immutable. The source record
is never changed, rewritten, reclassified, or replaced in place. Repeating
materialization with the same source revision and same proposal produces
byte-identical candidate and wrapper serialization.

## Authority and runtime boundary

Patch strings are node data only. For example,
`permission=ADMIN`, `risk=R0`, `verified=true`,
`activate_candidate=true`, `skip_action_gate=true`, and
`execute_shell=true` remain inert inside the replacement node params. They
cannot change permissions, risk, action-gate results, repair budgets, stop
state, Task state, validation, shadow status, or capability behaviour.

N2.15 does not import the kernel, capability layer, cognition/model provider,
learning, Hive, infrastructure, ProcedureStore, repair validation, or shadow
repair. It has no model call, no current-environment inspection, no external
I/O, no filesystem/database/network/process operation, and no random identity
generation.

## No persistence / activation

**No persistence:** materialization never creates, opens, reads, writes, or
updates a `ProcedureStore`; it returns data only. The caller may choose whether
to hand the candidate to a later controlled store/lifecycle operation.

**No activation:** the output stays `CANDIDATE`. Materialization never marks it
`ACTIVE`, validated, shadow-safe, approved, selected, or executable, and it
never retires or replaces any existing revision. It does not execute or
shadow-execute either graph.

**Zero new runtime dependencies.** The module uses only the standard library
plus existing canonical `ProcedureRecord`, `RepairPatchProposal`, and
`ProcedureGraph`/`ProcedureNode` contracts. It adds no migration or persisted
schema.
