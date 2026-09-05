# C2.10 — Causal Experience Model

C2.10 defines the canonical immutable representation of one historical AgentX execution attempt:

`state_before -> action -> observation -> state_after -> verification -> outcome`

The model records the causal execution chain AgentX observed. It does **not** claim statistical, scientific, or counterfactual causation. Later learning systems may reason over these records, but this contract itself performs no inference, ranking, routing, retry, planning, learning, repair, or procedure synthesis.

## Canonical contracts

`agentx.core.causal_experience` exposes:

- `CausalExperience`
- `ExperienceState`
- `CausalOutcome`
- `CAUSAL_EXPERIENCE_SCHEMA_VERSION`
- `CausalExperienceValidationError`
- `CausalExperienceDeserializationError`
- `UnsupportedCausalExperienceSchemaVersionError`

The schema version is currently `1`.

## Identity reuse

The record reuses canonical identity contracts already available inward on main:

- optional `TaskId`
- required execution/correlation `UUID` from the A1.07 `ExecutionContext` identity model
- optional `EpisodeId`

No new Task, execution, or episode identifier type is introduced.

A1.07 does not currently define a separate `ExecutionId`; correlation UUID is the canonical execution-chain identity available on main.

## Action / observation / verification reuse

C2.10 deliberately reuses the C1.02 core evidence payloads:

- `ActionPayload`
- `ObservationPayload`
- `VerificationPayload`

This is the same inward representation A1.10 already emits from Capability ABI values:

- capability request identity becomes `ActionPayload.name` via `str(request.identity)`;
- typed capability parameters become inert action data;
- `CapabilityObservation.to_dict()` becomes `ObservationPayload.value`;
- `VerificationResult` becomes `VerificationPayload`.

The C2.10 model therefore does not create duplicate capability request, observation, or verification schemas and does not introduce an illegal `agentx.core -> agentx.capabilities` dependency.

## State snapshots

`ExperienceState` is a frozen timestamped wrapper around canonical `ObservationPayload` evidence. It is not a universal mutable dictionary. The observation payload is defensively frozen by the existing C1.02 contract and remains inert JSON-compatible historical data.

State snapshots may contain hostile or misleading strings. Storage or deserialization never interprets those strings as code, policy, authority, permission, risk, budget, or verified success.

## Ordering

Chronology is explicit and validated:

- `state_before.captured_at <= action_at`
- when present, `action_at <= observation_at`
- when present, `state_after` cannot precede the preceding action/observation stage
- verification requires both an observation and an explicit `state_after` snapshot
- verification cannot precede `state_after`
- outcome cannot precede any recorded earlier stage

Optional stages and their timestamps are paired: a missing observation or verification never receives a fabricated timestamp or payload.

## Outcome semantics

`CausalOutcome` is a historical-only vocabulary:

- `VERIFIED`
- `VERIFICATION_FAILED`
- `EXECUTION_FAILED`
- `DENIED`
- `CANCELLED`
- `TIMED_OUT`

A separate inward core vocabulary is necessary because the existing A1.10 `LoopOutcome` lives outward in `agentx.capabilities`, cannot be imported by `agentx.core`, and does not distinguish cancellation from timeout.

The model enforces:

- `VERIFIED` requires an explicit `VerificationPayload(passed=True, ...)`;
- `VERIFICATION_FAILED` requires an explicit `VerificationPayload(passed=False, ...)`;
- every other outcome carries no verification evidence;
- `DENIED` cannot fabricate capability observation or post-action state.

Action, observation, and `state_after` are never sufficient to mark success.

## Authority boundary

Every causal experience is historical **data only**. A record cannot:

- grant `Permission`;
- create or strengthen `AuthorityContext`;
- bypass `ActionGate`;
- lower risk;
- enlarge/reset a resource budget;
- clear `EmergencyStop`;
- execute or verify a capability;
- invoke Executor, Reasoner, Router, or models;
- transition a Task;
- mark a future action verified;
- promote Knowledge;
- activate a Procedure;
- suppress or force a retry;
- force a routing decision.

A past verified action does not authorize repetition. A past failure does not prohibit retry.

## Persistence decision

C2.10 does **not** add a `CausalExperienceStore` or SQLite migration.

The assigned architectural purpose is the canonical data model. Existing storage tasks separate core records from persistence, and concurrent C2.08/C2.06 integration owns the next persistence migration slots. A new store is not required to establish this contract and would add unnecessary migration coupling.

Therefore C2.10 leaves the canonical migration chain untouched. If a durable causal-experience store is required later, it can persist the stable schema-v1 JSON through the canonical `SQLiteDatabase` substrate in a separately owned persistence task.

## Non-scope

C2.10 does not implement retrieval, embeddings, semantic ranking, graph traversal, causal inference, trust/confidence scoring, learning, skill compilation, candidate lifecycle, repair, research, LLM calls, planning, routing, execution, verification orchestration, Task management, or automatic policy decisions.
