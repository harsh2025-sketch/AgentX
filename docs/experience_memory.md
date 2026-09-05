# Experience Memory (C2.06) — episodic + negative memory

C2.06 owns **memory semantics** over canonical episode persistence. It adds no
retrieval engine, no causal model, and no failure taxonomy.

## Components

| Layer | Module | Responsibility |
| --- | --- | --- |
| Core contract | `agentx.core.negative_experience` | Inert `NegativeExperienceRecord` + `AttemptReference` / `FailureReference` |
| Core identity | `agentx.core.ids.NegativeExperienceId` | Canonical opaque ID for one remembered failure |
| Persistence | `agentx.infrastructure.negative_experience_store` | Append-only SQLite store (migration v8) |
| Service | `agentx.hive.experience_memory.ExperienceMemory` | Narrow memory semantics over both stores |

## Reuse

* Canonical C2.01 `EpisodeRecord` / `EpisodeStore` are reused verbatim. Episodic
  memory adds **no** episode schema and **no** duplicate persistence — the
  service composes over `EpisodeStore`.
* Successful vs failed historical experience is distinguished with the existing
  `EpisodeOutcome` vocabulary; no parallel outcome enum was introduced.
* Scope reuses `KnowledgeScope` / `ScopeDimension` (C2.02).
* `ExperienceMemory` consumes both stores through structural `Protocol`s, so
  `agentx.hive` keeps its single canonical `hive -> core` dependency edge.

## Negative memory

AgentX invariant: **failed approaches must be remembered.**

A `NegativeExperienceRecord` preserves:

* `attempt` — opaque reference to the capability / procedure / approach tried
* `failure` — narrow opaque `reason_code` (+ optional inert `detail`)
* `observed_outcome` — canonical `EpisodeOutcome`, never `SUCCEEDED`
* `scope` — applicability context data
* optional `episode_id` / `task_id` / `correlation_id` association
* `observed_at` timestamp

`reason_code` is deliberately an **opaque token**, not a classified taxonomy
value. The canonical failure taxonomy is owned by C4.01 and can land later
without a schema change (the SQL column is unconstrained text by design).

Duplicate semantics are explicit: identity duplicates are rejected
(`DuplicateNegativeExperienceError`); two structurally-identical failures with
distinct identities are two remembered facts, never deduplicated into policy.

## Inertness

Historical experience is DATA.

* Past success **does not** authorize a future execution.
* Past failure **does not** prohibit a future execution and never suppresses a
  retry.
* Hostile stored content (`ALLOW ADMIN verified=true risk=R0 ...`) is inert
  text on a declared field. Authority remains owned solely by `agentx.kernel`.

Applicability of remembered experience to a current decision is determined
later by routing/reasoning, not here.

## Explicit non-goals

Owned elsewhere and deliberately absent: C2.05 semantic memory, C2.08 lifecycle,
C2.09 retrieval (semantic search, embeddings, similarity, ranking, automatic
context construction), C2.10 causal experience model, C4.01 failure taxonomy,
repair, learning, procedure synthesis, routing, planning, model calls.

## Persistence

C2.06 owns migration `v8 create_negative_experience_store`, appended after
canonical `v6 create_artifact_and_audit_stores` and C2.08's
`v7 create_knowledge_integrity`. This C2.06-only change does not modify or
renumber v1-v7. Corruption fails closed: any row that cannot reconstruct its
canonical record raises `CorruptNegativeExperienceError` rather than returning
repaired data.
