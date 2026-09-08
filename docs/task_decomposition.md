# A6.01 — Hierarchical Task Decomposition Contract

A6.01 defines the canonical, inert **PLAN/DECOMPOSITION DATA** representation
for decomposing one high-level AgentX Task into smaller task units, and the
deterministic validation boundary through which such data — including model or
reasoner output — is accepted into canonical typed structures.

A6.01 answers: *What is the canonical shape of a decomposition, and what must
be true for decomposition data to be accepted into the runtime's typed
structures?*

It does NOT answer: *In what order should subtasks be scheduled or executed?*
*Which subtask may run next given budgets, permissions, or readiness?*
Dependency planning is A6.02; full plan validation is A6.04. A6.01 never
plans, schedules, executes, grants, or claims success.

## Canonical contracts

`agentx.core.task_decomposition` exposes:

- `DecompositionNode`
- `TaskDecomposition`
- `accept_model_proposal`
- `DECOMPOSITION_SCHEMA_VERSION` (currently `1`)
- `MAX_DECOMPOSITION_NODES` (`256`), `MAX_DECOMPOSITION_DEPTH` (`8`),
  `MAX_SUCCESS_CRITERIA` (`64`)
- `TaskDecompositionError`, `TaskDecompositionValidationError`,
  `TaskDecompositionDeserializationError`,
  `UnsupportedDecompositionSchemaVersionError`,
  `DecompositionNodeNotFoundError`

`agentx.core.ids` gains exactly one new identity type: `DecompositionId`.
There is no store, no planner, no scheduler, no executor, no policy engine,
and no model client in this contract.

## Shape

A decomposition is a **rooted tree** of nodes. Every node — including the
root — is a `DecompositionNode` carrying:

| Field              | Meaning                                                              |
| ------------------ | -------------------------------------------------------------------- |
| `task_id`          | Canonical `TaskId`; unique within the decomposition                  |
| `objective`        | Explicit, non-empty, trimmed statement of the work                   |
| `parent_task_id`   | Single parent link; `None` only for the root                         |
| `success_criteria` | Declarative completion criteria (inert text; empty allowed)          |
| `order_index`      | Canonical 0-based position among siblings                            |
| `depends_on`       | TaskIds that must complete first (ordering constraint)               |
| `metadata`         | JSON-compatible, non-authoritative context (provenance etc.)         |

The record itself carries `decomposition_id` (identity), `version`
(revision counter, `>= 1`), `root_task_id`, `created_at` (UTC), and record
`metadata`. The `nodes` tuple is stored in **canonical pre-order** (root
first; siblings ascending by `order_index`), which makes serialization a
pure function of the data.

The hierarchy is a tree, mirroring the single `parent_task_id` link of the
A1.05 Task schema: a subtask shared by multiple parents (a DAG-shaped
hierarchy) is not representable in v1 and therefore not canonical.
`depends_on` adds ordering constraints *across* the tree without ever
changing parentage.

## Validation

Construction and decoding enforce, structurally:

- no duplicate node task ids;
- a valid root: `root_task_id` present, exactly one rootless node, and that
  node is the root;
- no dangling parent reference;
- no parent-link cycle (every node reaches the root);
- bounded node count (`MAX_DECOMPOSITION_NODES`);
- bounded depth (`MAX_DECOMPOSITION_DEPTH`, root = depth 1);
- explicit sibling ordering (indices exactly `0..k-1` per sibling group)
  and the canonical pre-order node sequence;
- dependency integrity (targets present, no self-dependency, no duplicates,
  no dependency cycle);
- valid task identity (canonical `TaskId` / `DecompositionId` — raw strings
  and UUIDs are rejected);
- deterministic serialization (sorted keys, canonical node order, canonical
  UTC timestamps, no NaN).

## Model / reasoner acceptance

`accept_model_proposal(raw, *, root_task_id=None, decomposition_id=None,
version=1, created_at=None, metadata=None)` is the inert acceptance boundary
for external output. `raw` is *data* — for example the result of
`json.loads` on model text. The accepted proposal shape is exactly
`schema_version`, `root_task_id`, and `nodes`; any other field is rejected.

The boundary keeps three distinctions structural:

> model text != executable authority · model text != record identity ·
> model text != provenance

- Model text is stored verbatim as inert strings in a non-executable data
  structure. It can set no identity, grant no permission, and carry no
  secret or authority marker (reserved metadata key markers are rejected).
- Record identity (`decomposition_id`, `version`, `created_at`, record
  `metadata`) is owned by the accepting caller, never by the model.
- Expected data failures cross the boundary as
  `Result.failure(AgentXError)` with a structured `task_decomposition.*`
  code (`invalid_input`, `invalid_schema_version`, `missing_fields`,
  `unknown_fields`, `root_mismatch`, `invalid_field`, `invalid_structure`),
  category `VALIDATION`, retryability `NON_RETRYABLE`. They never raise.
  Malformed *caller-supplied* typed arguments still raise (programming
  errors), mirroring the A1.04 error policy.

## Relation to existing contracts

- **A1.05 Task schema**: reuses `TaskId` and the same field-validation and
  metadata rules (trimmed non-empty objectives, no control characters,
  JSON-compatible metadata, reserved secret/authority key markers).
  Decomposition nodes are plan data — they are not `Task` records and carry
  no `TaskStatus`.
- **A1.06 state machine**: not imported and not used. A decomposition makes
  no status claims and performs no transitions.
- **A1.04 errors/results**: rejection outcomes are `Result.failure` with
  structured `AgentXError` values; typed-contract violations raise the
  `TaskDecomposition*Error` family.
- **A1.07 ExecutionContext / A2.03 Reasoner / A2.06 Task Manager / A2.07
  Router**: not imported. The acceptance boundary consumes JSON-compatible
  data only; wiring a live `ReasonerResult` into the boundary (e.g. parsing
  its inert text content) is left to the planning layer, which remains
  responsible for deciding whether to accept a proposal at all.

## A decomposition declares nothing

The contract keeps the following distinctions structural, not merely
documented:

> decomposition != schedule · decomposition != permission ·
> decomposition != success

`DecompositionNode` and `TaskDecomposition` carry no field, method, or
serialized key named `status`, `succeeded`, `completed`, `done`,
`finished`, `is_success`, or `verified`. Success criteria are *declarations
of what would count as success*, not claims that it happened. The presence
of children never implies the parent succeeded, and building a
decomposition neither reads nor writes any `Task`'s lifecycle state. Which
subtask (if any) may execute, and when a task may be marked succeeded,
remains owned by the execution, policy, and verification layers — with
kernel policy as the only authority and verification as the only basis for
success.
