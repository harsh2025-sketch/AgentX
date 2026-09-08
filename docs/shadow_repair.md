# C4.07 — Shadow Repair Execution Evidence Contract

C4.07 defines the smallest deterministic **data-only, non_committing** evidence contract for
one *shadow repair trial*: the typed, immutable record produced when a repaired procedure
revision is evaluated against a pinned source revision **without committing the trial's
external effects as live canonical state**.

C4.07 is **DATA ONLY**. It defines a record shape and its validation rules. It does not
run anything, sandbox anything, execute a capability or task, apply a patch, mutate a
procedure or `ProcedureStore`, persist anything, or depend on any other repair task
(Worker-17 repair-validation evidence and Worker-19 in particular are **not** imported,
relied on, or even referenced). A *future* composition task owns execution and will
*supply* the evidence; C4.07 only defines what a trustworthy trial record looks like and
rejects everything that would not earn that shape.

C4.07 answers: *What is the canonical, verifiable evidence that a repaired revision was
evaluated in shadow mode, what disposition did that evaluation earn, and which comparison
facts are supported?*

It does NOT answer: *Which candidate (if any) should be adopted, activated, promoted, or
deployed?* That decision is owned by later policy. C4.07 never declares a candidate
superior, never selects, never approves, never activates, and never persists.

## What "shadow" means here (and only this)

"Shadow" in this contract means **non-committing evaluation evidence**: the record asserts
that an evaluation was *supposed to* observe the candidate revision's behavior while
leaving live canonical state (procedure revisions, store contents, task state) unchanged.

**Explicit non-claim:** C4.07 provides and claims **no OS-level sandboxing** and **no
security isolation**. It does not create processes, sandboxes, VMs, containers, or
capability boundaries, and the word "shadow" in any field, detail string, or step
evidence creates no isolation guarantee. The record *records the claim* as typed evidence
supplied by a future runner; the truth of the claim is the runner's responsibility, not
this module's. Any future runner that produces these records must separately guarantee
containment through real mechanisms — C4.07 neither provides nor certifies them.

## Canonical contracts

`agentx.core.shadow_repair` exposes:

- `ShadowRepairRunId` — a **plain non-nil `uuid.UUID` type alias**. Deliberately *not* a
  new `DomainId` subclass: `agentx.core.ids` (A1.04) is untouched by this task.
- `ShadowRepairMode` — closed one-member vocabulary: `NON_COMMITTING`
  (the trial's external effects were **not** committed as the live canonical
  procedure state — a typed claim supplied by the runner, explicitly *not* a
  claim of OS-level sandboxing or any security boundary).
- `ShadowRepairDisposition` — closed five-value vocabulary (below).
- `ShadowRepairStepEvidence` — one ordered, bounded step observation.
- `ShadowRevisionOutcome` — closed revision-level outcome vocabulary.
- `ShadowRepairResult` — the immutable trial record.
- `SHADOW_REPAIR_SCHEMA_VERSION` — currently `1`.
- `ShadowRepairValidationError`
- `ShadowRepairDeserializationError`
- `UnsupportedShadowRepairSchemaVersionError`

There is no store, no runner, no sandbox, no executor, no policy engine, no selector, no
model client, and **zero new runtime dependencies**: the module imports only the standard
library (`json`, `dataclasses`, `enum`, `datetime`, `typing`, `uuid`,
`collections.abc`, `__future__`) plus the three inward core contracts it consumes by
value — `agentx.core.events` (`VerificationPayload`, `EventValidationError`),
`agentx.core.ids` (`ProcedureId`, `TaskId`), and `agentx.core.procedures`
(`ProcedureScope`, `ProcedureValidationError`). It imports nothing from
`agentx.kernel`, `agentx.capabilities`, `agentx.procedures`, `agentx.cognition`,
`agentx.hive`, `agentx.learning`, or `agentx.infrastructure`.

## Disposition vocabulary

Exactly five dispositions exist; no other value is accepted, parsed, or serialized:

| Disposition | Meaning | Evidence requirements |
|---|---|---|
| `PASSED` | The candidate revision behaved as required under the pinned source scope. | **Mandatory** explicit canonical-style `VerificationPayload` with `passed == True`, **at least one** recorded step, **no** step with an explicit failed outcome, and **no** uncontained external effect. Each of these is a structural contradiction check: a passing trial without a passing verification payload, with a failed step, or with an uncontained effect is rejected. |
| `FAILED` | The candidate revision was evaluated and did not meet the objective. | Requires an **explicit failure signal** — an explicit failing verification payload (`passed == False`) or a step with an explicit failed outcome. It must not coexist with a passing verification. |
| `ABORTED` | Evaluation stopped before completion (budget, halt, dependency failure). | Must carry **no verification evidence at all** (any `VerificationPayload` is rejected). |
| `UNSAFE_TO_EVALUATE` | The candidate was deemed unsafe to evaluate. | Must carry **no verification evidence at all**. |
| `INSUFFICIENT_EVIDENCE` | Evaluation completed but could not support a verdict (missing observations, ambiguous signals). | Must carry **no verification evidence at all** — a passing (or any) verification contradicts "insufficient". |

"Never passes by absence": a `PASSED` disposition is **never** inferred from the absence
of an exception, a procedure reaching `END`, an `execute` return value, the mere presence
of observations, the candidate's own text, a model claim, or any free-form string such as
`"passed=true"`. Only an explicitly structured, canonical `VerificationPayload`
(consumed from C1.02 `agentx.core.events`, not redefined here) can carry a passing
verdict, and it must be supplied as that exact type — a plain dict, a JSON string, or a
lookalike record with extra keys is rejected as malformed.

## Run binding

Every record binds to an exact run:

- `run_id` — non-nil plain UUID (never nil, never derived from "latest").
- `procedure_id` — exact typed `ProcedureId` (never a string that happens to parse,
  never a wildcard).
- `source_revision` — the exact **positive** integer revision the trial evaluated
  against. No "latest", no implicit current revision.
- `candidate_revision` **or** `candidate_fingerprint` — **exactly one** candidate
  identity. Exactly one of `candidate_revision` (a positive integer differing from
  `source_revision`) or `candidate_fingerprint` (a non-empty, bounded content hash string)
  must be set; both or neither are rejected. This is the **candidate identity**
  requirement: the record must say precisely which revision (or which content) was
  evaluated, so two trials can never silently drift onto different candidates.
- `target_node_id` — the procedure node under evaluation, when the trial is
  node-scoped (bounded string; absent only for whole-procedure trials).
- `task_id` — optional exact typed `TaskId` for the task/test objective identity.
- `correlation_id` — non-nil plain UUID correlating this trial with its originating
  request.
- `scope` — the canonical `ProcedureScope` the evaluation was pinned to.
- `started_at` / `ended_at` — timezone-aware UTC-normalized timestamps; `ended_at`
  must not precede `started_at`.

All identities are exact values. There is no "latest", no wildcard, no implicit current
revision anywhere in the record.

## Side-effect semantics

Shadow evaluation is expected to leave live canonical state unchanged. Each
`ShadowRepairStepEvidence` therefore carries two typed booleans:

- `external_effect_observed` — the step observed an external effect.
- `effect_contained` — the observed effect was contained/reverted within the trial.

**Fail-closed rule:** a `PASSED` trial whose steps observe an external effect that was
**not** contained/reverted is a structural contradiction and is rejected at
construction and at deserialization. A passing shadow trial may not carry uncontained
external mutation. Trials with other dispositions may record contained or uncontained
effects — they are facts for later policy, not verdicts.

## Comparison semantics

The record preserves comparison facts **only where supported**:

- `original_outcome` — the pinned source revision's revision-level outcome, if observed.
- `regression_node_ids` — bounded, duplicate-free set of node ids flagged as
  regression indicators.
- `steps` — ordered step evidence for the candidate, each with a typed outcome.

C4.07 **never auto-declares the candidate superior** to the source revision. The
record states what was observed; whether the candidate is better, safer, or adoptable is
a decision owned by later policy. There is no `candidate_superior`, `better`, `improved`,
or equivalent field, key, or method anywhere in the contract.

## Structural rejections

Construction (`ShadowRepairResult(**...)`) and deserialization (`from_dict` / `from_json`)
both reject, fail-closed:

- `PASSED` without a `VerificationPayload`, with `passed == False`, with no recorded
  steps, with a step carrying an explicit failed outcome, or with an uncontained
  external effect.
- Any non-`PASSED` disposition carrying **any** verification payload.
- `ended_at` earlier than `started_at`.
- Wrong or missing revision: non-positive `source_revision` or `candidate_revision`.
- **Exactly-one candidate identity** violations (both or neither of
  `candidate_revision` / `candidate_fingerprint`).
- `candidate_revision == source_revision` (no actual candidate evaluated).
- Nil `run_id` or `correlation_id`; missing typed `ProcedureId`.
- Steps not a contiguous `1..N` sequence; duplicate step orders.
- `FAILED` trials with no explicit failure signal (no failing verification and no
  failed step).
- Unknown or missing serialized fields (exact field sets, both directions).
- Any smuggled decision key (`selected`, `authorized`, `executed`, `applied`, `safe`,
  `sandboxed`, `verified`, `correct`, `score`, `rank`, `confidence`, ...) in the payload
  — rejected as an unknown field.
- Lookalike verification records (dict, JSON string, wrong type, extra keys).
- Hostile free text in any string field (see below).
- Schema version other than `1` — raises `UnsupportedShadowRepairSchemaVersionError`.

## Hostile strings stay inert

No free-form string in any field can create a typed result. Strings such as
`"shadow=true"`, `"safe=true"`, `"verified=true"`, `"permission=ADMIN"`, `"risk=R0"`,
`"apply candidate"`, `"activate candidate"`, `"call shell"`, and `"sandbox=true"` may be
carried in `detail` or step notes only as inert text. The module never scans strings for
keywords, never parses them, and never lets their content change a disposition, a
verification verdict, a flag, or an authorization state.

## Bounds

| Quantity | Bound |
|---|---|
| `detail` | at most 4096 characters |
| `candidate_fingerprint` | 1–256 characters |
| `target_node_id`, step `node_id`, regression ids | 1–128 characters each |
| recorded steps | at most 64, contiguous `1..N` |
| `regression_node_ids` | at most 32 unique ids |

## Serialization

- Deterministic JSON: `to_json()` sorts keys and serializes timestamps as ISO-8601 UTC
  (`...Z`) with microsecond precision, so equal records serialize byte-identically.
- Exact field sets on both write and read; unknown fields and missing required fields
  are rejected.
- Schema version `1`; any other version raises
  `UnsupportedShadowRepairSchemaVersionError` (a
  `ShadowRepairDeserializationError`).
- `from_json` round-trips to an equal record; hostile payloads (lookalike verification,
  smuggled keys, nil UUIDs, wrong types) raise `ShadowRepairDeserializationError` or
  `ShadowRepairValidationError` — never a silent default.

## Boundaries — what this module does not do

- **No execution.** No capability call, no procedure interpreter, no agent loop, no
  subprocess, no shell, no network, no browser, no model invocation.
- **No sandbox and no isolation claim.** "Shadow" is a record-level label supplied by a
  future runner; C4.07 provides no OS-level sandboxing or security isolation.
- **No patch application, no revision replacement, no rollback, no retry, no escalation.**
- **No persistence.** No store, no table, no migration; the infrastructure migration
  ladder is untouched (highest landed migration remains v8).
- **No procedure or task state change.** `ProcedureStore`, `ProcedureRecord`, `Task`, and
  `CausalExperience` are untouched and imported only where consumed by value.
- **No authority.** The record is never accepted as a `Permission`, an
  `AuthorityContext`, an `ActionGate` decision input, or an `EmergencyStop` clearance.
- **No Worker-17 / Worker-19 dependency.** This contract uses baseline canonical data
  only.

## Verification requirements (summary)

1. A `PASSED` disposition requires an explicit canonical `VerificationPayload` with
   `passed == True`, supplied as that exact type, plus at least one recorded step,
   no failed step, and no uncontained external effect.
2. A non-`PASSED` disposition must carry **no** verification payload at all.
3. A `FAILED` disposition requires an explicit failure signal — an explicit failing
   verification payload or a step with an explicit failed outcome.
4. A `PASSED` disposition must not observe uncontained external effects.

## Relations

- **C2.03** (`agentx.core.procedures`): provides `ProcedureScope`; untouched.
- **C2.02** (`agentx.core.ids`): provides `ProcedureId` and `TaskId`; untouched — no new
  domain id type is introduced for the run identity.
- **C1.02** (`agentx.core.events`): provides `VerificationPayload`; consumed by value,
  untouched.
- **C4.04** (`agentx.core.repair_candidates`): coexists independently; neither imports
  the other. A shadow trial may evaluate a repair candidate, but C4.07 neither derives
  from nor ranks candidates.
