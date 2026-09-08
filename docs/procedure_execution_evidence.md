# M3.02 — Procedure Execution Trace and Evidence Contract

M3.02 defines the single canonical, immutable vocabulary AgentX uses to describe **what
happened during one procedure run**, and the explicit evidence that supports each claim.

It is a **data contract only**. Module: `agentx.core.procedure_execution`.

M3.02 answers: *What did this run do, and what evidence backs each statement?*

It does **not** answer: *Should AgentX do it again?*, *Did the task succeed?*, or *Is this
procedure trustworthy?* Those belong to later tasks and to the Trusted Kernel.

## What this contract never does

It never executes a procedure, node, capability, model, research step, or subprocess; never
compiles, interprets, or traverses a `ProcedureGraph`; never persists, publishes, retries,
repairs, promotes, compiles skills, or schedules; never touches the filesystem, network,
database, or threads; never reads a clock (every timestamp is supplied by the caller); never
grants `Permission`, creates authority, changes `RiskLevel`, widens a budget, clears
`EmergencyStop`, or transitions a `Task`.

## Canonical contracts

`agentx.core.procedure_execution` exposes:

| Name | Role |
| --- | --- |
| `ProcedureRunRecord` | One immutable historical trace of one procedure run |
| `ProcedureStepRecord` | One immutable historical record of one step within that run |
| `ProcedureStepTransition` | The edge control actually took away from a step |
| `ProcedureExecutionEvidence` | One typed pointer to canonical evidence stored elsewhere |
| `ProcedureStepDisposition` | Closed step-level historical disposition vocabulary |
| `ProcedureRunDisposition` | Closed terminal **control-flow** disposition vocabulary |
| `ProcedureTaskVerification` | Closed, **separate** original-task verification vocabulary |
| `ProcedureEvidenceKind` | Closed evidence-reference kinds |
| `ExecutedNodeKind` / `ExecutedEdgeKind` | Closed, core-safe node/edge kind strings |
| `PROCEDURE_EXECUTION_SCHEMA_VERSION` | Serialized schema version (currently `1`) |
| `MAX_*` constants | Hard size bounds (see below) |
| `ProcedureExecutionValidationError` and subclasses | Fail-closed error taxonomy |

There is no store, no interpreter, no runner, no policy engine, and no selector.

## The truth model: five facts that never collapse into one

| Fact | Where it is recorded | What it does **not** mean |
| --- | --- | --- |
| Node executed | `ProcedureStepDisposition.EXECUTED` | Not observed, not verified, not success |
| External effect observed | `ProcedureStepRecord.observation_evidence` | Not verification |
| Node verified | `ProcedureStepDisposition.VERIFIED` + `verification_evidence` | Not task success, not authority |
| Procedure control terminated | `ProcedureRunDisposition.REACHED_END` | Not success, not verification |
| Original task verified | `ProcedureTaskVerification.TASK_VERIFIED` + `task_verification_evidence` | Not a licence to replay |

Structural consequences, enforced by the constructors (not merely documented):

- There is **no** `success`, `succeeded`, `ok`, `passed`, or `verified` field, property, or
  method anywhere in this contract, and nothing derives one.
- `VERIFIED` is **impossible** without at least one explicit verification evidence reference.
- Verification evidence is **forbidden** on every disposition except `VERIFIED` and
  `VERIFICATION_FAILED`, so "executed" can never be read as "verified".
- A run that reached `END` with every step `VERIFIED` still reports
  `task_verification == NOT_ASSESSED` unless a caller supplied task-verification evidence
  **and** a canonical `TaskId`.
- A historical `VERIFIED` step grants no future authority: the records expose no
  permission, risk, budget, replay, or execution surface at all.

## Identity, and what is referenced rather than copied

A run carries `run_id` (UUID), `(procedure_id, procedure_revision)` naming exactly which
stored C2.03 revision ran, the canonical execution-chain `correlation_id`, and an optional
canonical `TaskId`. Steps carry a zero-based `step_index` and a bounded, inert, core-safe
`node_id` string.

Large canonical objects are **never** duplicated. Observation, verification, terminal
control, and task-verification evidence are stable references only:

- `EVENT` → canonical `Event` UUID
- `CHAIN_CORRELATION` → canonical execution-chain UUID
- `EPISODE` → `EpisodeId`
- `ARTIFACT` → `ArtifactId`

Each evidence item carries exactly one reference matching its kind; foreign or extra
references are rejected. References are never dereferenced, fetched, ranked, or trusted.

## Closed vocabularies

`ProcedureStepDisposition`: `EXECUTED`, `VERIFIED`, `VERIFICATION_FAILED`,
`EXECUTION_FAILED`, `DENIED`, `CANCELLED`, `TIMED_OUT`, `REASON_REQUIRED`,
`RESEARCH_REQUIRED`, `SUBPROCEDURE_REQUIRED`, `SKIPPED_BY_BRANCH`.

`ProcedureRunDisposition`: `REACHED_END`, `HALTED_ON_STEP_FAILURE`, `DENIED`, `CANCELLED`,
`TIMED_OUT`.

`ProcedureTaskVerification`: `NOT_ASSESSED` (fail-closed default), `TASK_VERIFIED`,
`TASK_VERIFICATION_FAILED`.

`ExecutedNodeKind` / `ExecutedEdgeKind` mirror the A3.01 graph spellings as **closed
strings**, so a trace can name what kind of node ran without `agentx.core` importing the
outward `agentx.procedures` IR. A kind is never resolved back to a node definition.

## Invariants (fail closed, never silently normalized)

Construction and deserialization reject:

- a negative `step_index`, or one at/past `MAX_PROCEDURE_RUN_STEPS`;
- duplicate, gapped, or out-of-order step indices (steps must be exactly `0..n-1`);
- steps recorded out of chronological order, or lying outside the run window;
- `ended_at` before `started_at`, on a step or a run;
- naive (timezone-unaware) timestamps; aware values are normalized to UTC;
- `VERIFIED` without verification evidence;
- verification evidence on any non-verification disposition;
- `VERIFICATION_FAILED` with neither verification evidence nor an error code;
- `EXECUTION_FAILED` or `DENIED` without a canonical error code, and an error code on
  `EXECUTED`, `VERIFIED`, the `*_REQUIRED` dispositions, or `SKIPPED_BY_BRANCH`;
- observation evidence on `SKIPPED_BY_BRANCH` (that node never ran);
- `REACHED_END` without a final `END` step **and** explicit terminal control evidence;
- `HALTED_ON_STEP_FAILURE` without a final failed/denied/timed-out step;
- a `DENIED` run whose recorded steps do not end on a `DENIED` step;
- `TASK_VERIFIED` / `TASK_VERIFICATION_FAILED` without a `TaskId` and evidence, and
  task-verification evidence attached to `NOT_ASSESSED`;
- malformed, nil, or wrong-domain identities (`TaskId` where a `ProcedureId` belongs, etc.);
- `procedure_revision < 1`;
- booleans supplied where integers belong (`step_index`, `procedure_revision`,
  `schema_version`);
- non-JSON-compatible values (objects, callables), non-string or untrimmed mapping keys,
  and non-finite floats;
- anything exceeding a size bound.

Contradictory evidence is never repaired, downgraded, or upgraded: the record is rejected.

## Serialization

`to_dict()` / `to_json()` / `from_dict()` / `from_json()` follow the existing core
convention (C2.03, C2.10, C4.01–C4.04):

- deterministic output: sorted keys, compact separators, stable across calls;
- explicit `schema_version`, with a distinct
  `UnsupportedProcedureExecutionSchemaVersionError`;
- exact field validation — both missing **and** unknown fields are rejected;
- `allow_nan=False` on output; `NaN` / `Infinity` literals are rejected on input;
- duplicate JSON object keys are rejected (a pairs hook that builds a plain `dict`; there is
  no `object_hook`, no dynamic type construction, no pickle, no YAML);
- hostile strings are preserved verbatim as inert data;
- JSON-compatible values are deep-frozen (`MappingProxyType` / `tuple`) on construction, so
  a decoded record cannot be mutated through the mapping it came from.

## Hard bounds

| Constant | Value |
| --- | --- |
| `MAX_PROCEDURE_RUN_STEPS` | 512 |
| `MAX_EVIDENCE_REFERENCES` | 16 per evidence collection |
| `MAX_BINDING_SNAPSHOT_ITEMS` | 32 top-level keys |
| `MAX_BINDING_SNAPSHOT_DEPTH` | 4 |
| `MAX_BINDING_SNAPSHOT_BYTES` | 4096 |
| `MAX_RUN_METADATA_ITEMS` | 16 |
| `MAX_RUN_METADATA_BYTES` | 2048 |
| `MAX_NODE_ID_LENGTH` | 128 |
| `MAX_ERROR_CODE_LENGTH` | 128 |

No collection is unbounded and there is no "unlimited" escape hatch.

## Boundary

`agentx.core.procedure_execution` imports only the standard library plus
`agentx.core.ids` and `agentx.core.tasks` (the shared `JsonValue` alias). It never imports
`agentx.kernel`, `agentx.capabilities`, `agentx.cognition`, `agentx.learning`,
`agentx.procedures`, `agentx.hive`, or `agentx.infrastructure`, so `agentx.core` remains the
dependency leaf. The boundary manifest (`agentx/_architecture.py`) is untouched and no
package export was edited: the contract is imported by module path.

## Explicit non-goals

No procedure execution or interpreter, no trace persistence or store, no change to
`EpisodeRecord`, `ProcedureGraph`, `ProcedureRecord`, or `Task`, no `VerificationResult`, no
capability outcome type, no skill compilation, no candidate validation or promotion, no
migrations, and no package-export changes.

## Tests

- `tests/unit/test_procedure_execution_contract.py` — vocabulary, valid shapes, ordering,
  timestamps, truth-model separation, dispositions, bounds, serialization round trips.
- `tests/adversarial/test_procedure_execution_contract_adversarial.py` — forged
  `verified=true` and authority strings, malformed identities, oversized payloads, NaN,
  bool-as-int, duplicate encoded keys, object/callable smuggling, mutation attempts, and the
  absence of any execution or authority surface.
- `tests/architecture/test_procedure_execution_contract_placement.py` — core-only imports,
  no outward subsystem imports, no duplicate Task/Procedure/Episode schema, no persistence,
  no execution methods, no dynamic code, manifest and package exports untouched.
