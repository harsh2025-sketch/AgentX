# M1.03 — Canonical verified execution episode capture

`agentx.execution_episode` is a narrow top-level composition adapter. It turns
already-existing canonical execution evidence into the existing
`agentx.core.episodes.EpisodeRecord` and writes that record through
`ExperienceMemory.record_episode`.

It is not an executor, verifier, policy engine, task-state owner, causal schema,
or persistence implementation.

## Inputs

`ExecutionEpisodeRequest` requires only existing canonical objects plus explicit
recording identity/time:

- the terminal canonical `Task`; its immutable `task_id` and `objective` preserve
  the original task context across canonical state transitions;
- the canonical `ExecutionContext`, carrying task and correlation identity;
- the canonical C2.10 `CausalExperience`, carrying
  `StateBefore -> Action -> Observation -> StateAfter -> Verification -> Outcome`;
- an explicit canonical `EpisodeId`;
- an explicit wall-clock `recorded_at` timestamp;
- optional canonical supporting event UUIDs.

The adapter does not manufacture missing observations, state snapshots, or
verification results. Optional/absent causal evidence remains absent in the
C2.10 object; the episode projection does not invent replacement fields.

## Truth and mapping

The episode outcome is derived only from the typed `CausalOutcome`. Summary,
objective, observation text, verification detail text, and error-like strings
are inert data and are never parsed for success.

| Canonical C2.10 outcome | Required terminal `TaskStatus` | `EpisodeOutcome` |
| --- | --- | --- |
| `VERIFIED` | `SUCCEEDED` | `SUCCEEDED` |
| `VERIFICATION_FAILED` | `FAILED` | `FAILED` |
| `EXECUTION_FAILED` | `FAILED` | `FAILED` |
| `DENIED` | `FAILED` | `FAILED` |
| `CANCELLED` | `CANCELLED` | `CANCELLED` |
| `TIMED_OUT` | `CANCELLED` | `CANCELLED` |

The smaller `EpisodeOutcome` vocabulary cannot encode every causal terminal
reason. The exact controlled C2.10 outcome label is therefore preserved in the
existing `EpisodeRecord.summary` field, followed by the canonical Task
objective. This preserves the distinction without adding a second episode
schema.

A successful episode cannot be created from text such as `"task succeeded"` or
`"verified=true"`. `CausalExperience` itself requires an explicit passing
`VerificationPayload` for `VERIFIED`, and M1.03 additionally requires the final
canonical Task to be `SUCCEEDED`.

## Identity and timestamp checks

Before persistence, M1.03 fails closed unless:

- `ExecutionContext.task_id`, `CausalExperience.task_id`, and `Task.task_id`
  match and are present;
- `ExecutionContext.correlation_id` equals
  `CausalExperience.correlation_id`;
- an already-associated `CausalExperience.episode_id`, when present, equals the
  requested `EpisodeId`;
- the Task terminal status is compatible with the typed causal outcome;
- `recorded_at` is a canonical timezone-aware timestamp and is not earlier than
  `CausalExperience.outcome_at`.

`EpisodeRecord.started_at` is the canonical C2.10
`state_before.captured_at`; `ended_at` is the canonical C2.10 `outcome_at`.
Packaging is deterministic for identical explicit inputs.

## Persistence

`ExecutionEpisodeCapture.record()` performs an explicit call to
`ExperienceMemory.record_episode()`. `ExperienceMemory` delegates to the
canonical `EpisodeStore`; M1.03 adds no SQLite code, table, migration, sidecar
file, queue, cache, singleton, or background process.

Duplicate handling is therefore exactly the existing `EpisodeStore` contract:
a repeated `EpisodeId` raises `DuplicateEpisodeError`; distinct executions are
not collapsed merely because their content is identical.

## Authority boundary

Recorded history is inert data. This module imports no Trusted Kernel component
and cannot execute a capability, grant/revoke permission, change risk, reset an
emergency stop, alter a resource budget, retry work, or mutate Task state.

A prior successful episode grants no future authority. A prior failed episode
prohibits no future action.

## A2.10 relationship

A2.10 already guarantees that its terminal `OrchestrationOutcome.task` is a
canonical terminal Task and that `SUCCEEDED` requires a verified final attempt.
M1.03 deliberately does not import or modify `agentx.agent_loop`: composition
may pass that final canonical Task into `ExecutionEpisodeRequest`, while the
per-execution truth still comes from C2.10 `CausalExperience`. This keeps Agent
Loop unaware of persistence and avoids turning aggregate orchestration status
into fabricated execution evidence.
