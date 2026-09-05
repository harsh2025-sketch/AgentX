# C3.01 — Trajectory Normalization

C3.01 defines the deterministic transformation boundary that puts historical AgentX execution evidence into one canonical analysis shape for later skill-compilation work.

It is **normalization only**. It does not decide which actions mattered, infer causation, extract parameters, synthesize procedures, learn, repair, route, retry, or execute anything.

## Canonical source evidence

The normalizer reuses already-landed contracts rather than creating competing history schemas:

- `CausalExperience` remains the canonical state-before → action → observation → state-after → verification → outcome representation.
- `EpisodeRecord` may be supplied as canonical episode metadata/reference evidence.
- `TaskId`, `EpisodeId`, and the execution correlation UUID are preserved directly.
- Action, observation, verification, outcome, and stage timestamps stay inside the original canonical C2.10 record.

A normalized step therefore stores the canonical `CausalExperience` object itself. C3.01 does not flatten or redefine its evidence vocabulary.

## Public API

`agentx.learning.trajectory` exposes:

- `NormalizedTrajectory`
- `NormalizedTrajectoryStep`
- `normalize_trajectory(...)`
- `NORMALIZED_TRAJECTORY_SCHEMA_VERSION`
- `TrajectoryValidationError`
- `TrajectoryNormalizationError`

### `NormalizedTrajectoryStep`

Each step contains only:

- `sequence` — contiguous one-based normalized order;
- `source_experience_sha256` — deterministic SHA-256 fingerprint of the canonical C2.10 JSON;
- `experience` — the unchanged canonical `CausalExperience`.

The fingerprint is a historical content reference, not trust, verification, permission, authority, or provenance scoring.

### `NormalizedTrajectory`

A trajectory contains:

- deterministic non-nil `trajectory_id` UUID;
- canonical execution `correlation_id`;
- optional canonical `task_id`;
- optional canonical `episode_id`;
- ordered immutable `steps`;
- optional unchanged canonical `source_episode`.

`started_at` and `ended_at` are derived read-only timestamps from the represented historical evidence.

## Deterministic ordering

Input iteration order is not treated as canonical history order.

C3.01 sorts source `CausalExperience` records by:

1. `state_before.captured_at`;
2. `action_at`;
3. `outcome_at`;
4. canonical C2.10 JSON as a deterministic tie-break.

No source record is discarded or deduplicated. Exact duplicates remain distinct ordered steps.

This order describes a stable historical presentation. It does **not** prove scientific, statistical, or counterfactual causation and does not assign importance to any action.

## Stable trajectory identity

The trajectory UUID is UUIDv5 derived from the complete normalized canonical evidence shape: canonical task/correlation/episode association, optional canonical `EpisodeRecord`, and all ordered source steps.

Therefore the same canonical input evidence produces equal normalized output and the same trajectory identity. A change to represented historical evidence changes the derived identity.

The trajectory identity is an analysis identity only. It grants no authority.

## Identity consistency

One normalized trajectory represents one execution chain.

Normalization fails closed when source `CausalExperience` records contain conflicting:

- correlation UUIDs;
- non-null `TaskId` values;
- non-null `EpisodeId` values.

When an `EpisodeRecord` is supplied, its non-null task/correlation references and required episode identity must agree with the source experiences where those source references are present.

Episode metadata may fill trajectory-level task/episode association when the individual C2.10 records omitted those references, but it never rewrites the underlying source `CausalExperience` objects.

## Explicit absence

Missing optional C2.10 stages remain exactly as the canonical experience represented them:

- absent observation remains `None`;
- absent state-after remains `None`;
- absent verification remains `None`;
- corresponding optional timestamps remain absent.

Normalization never fabricates verification, success, state, or observation evidence.

## Authority and inertness

Normalized trajectory data is historical evidence only. It cannot:

- grant `Permission` or become `AuthorityContext`;
- lower risk;
- enlarge/reset resource budgets;
- clear `EmergencyStop`;
- execute or verify capabilities;
- invoke models, Reasoner, Router, or Executor;
- transition Tasks;
- activate Procedures;
- promote Knowledge;
- force retry/routing decisions;
- suppress a future action because a historical action failed.

Hostile strings are retained as inert historical content. Text such as `ADMIN`, `ALLOW`, `risk=R0`, `permission=WRITE`, `budget=unlimited`, `essential=true`, or `execute capability` is never interpreted as authority or analysis classification.

## Persistence decision

C3.01 adds **no store and no SQLite migration**.

Normalization is a deterministic transformation over existing canonical records. Persistence would add no capability required by this task and would create an unnecessary storage ownership boundary. If later tasks need durable normalized trajectories, that persistence should be separately owned and built on this stable transformation contract.

## Explicit non-scope

C3.01 does not implement:

- essential/corrective/exploratory/incidental classification;
- causal-action extraction;
- irrelevant-action elimination;
- parameter extraction;
- determinism classification;
- precondition/postcondition inference;
- procedure synthesis or compilation;
- candidate-skill lifecycle;
- learning or repair;
- retrieval/ranking;
- execution, routing, or retry policy.
