# Episode retrieval boundary (M1.04) — bounded restart-safe lookup

M1.04 is the first **persistent-intelligence proof** for the architecture:

```
RUN 1   -> store real episodes through the canonical EpisodeStore API
PROCESS RESTART
RUN 2   -> retrieve useful, bounded, deterministic history from the
           same persisted state through a fresh retrieval facade
```

`agentx.episode_retrieval` is that read boundary: a small, read-only
composition module that exposes **bounded, explicit, restart-safe episode
lookup** for a future orchestration layer. It is deliberately NOT semantic
intelligence. It is the deterministic retrieval primitive.

## Position in the architecture

| Concern | Owner |
| --- | --- |
| Episode contract (`EpisodeRecord`, `EpisodeOutcome`) | `agentx.core.episodes` (C2.01) |
| Episode persistence (`EpisodeStore`, migrations) | `agentx.infrastructure.episode_store` (C2.01) |
| Memory semantics (`ExperienceMemory`, negative experience) | `agentx.hive.experience_memory` (C2.06) |
| **Bounded episode read facade (this task)** | **`agentx.episode_retrieval` (M1.04)** |
| Structured knowledge retrieval | `agentx.infrastructure.knowledge_retrieval` (C2.09) |
| Causal experience model | `agentx.core.causal_experience` (C2.10) |

The facade sits at the `agentx` namespace root (like the A2.10 composition
module) because it composes the Hive-owned memory contract
(`EpisodeStoreLike` / `EpisodeEntryLike`, imported from
`agentx.hive.experience_memory`) over caller-supplied canonical store
instances without claiming either the Hive memory boundary or the
infrastructure persistence boundary. EpisodeStore remains the persistence
owner; ExperienceMemory remains the Hive memory owner; this module owns
neither and redefines no canonical contract.

## API

```python
from agentx.episode_retrieval import (
    EpisodeRetrieval,  # read-only facade
    EpisodeRetrievalQuery,  # frozen, validated query
    DEFAULT_RETRIEVAL_QUERY,  # unfiltered, finite default
    MAX_RETRIEVAL_LIMIT,  # 100 — the finite default bound
)

retrieval = EpisodeRetrieval(store=episode_store)

record = retrieval.get(episode_id)  # exact EpisodeId -> EpisodeRecord | None
records = retrieval.retrieve()  # finite default query
records = retrieval.retrieve(
    EpisodeRetrievalQuery(
        task_id=task_id,
        correlation_id=correlation_id,
        outcome=EpisodeOutcome.FAILED,
        after_sequence=40,
        before_sequence=80,
        limit=10,
    )
)
```

`store` is any object satisfying the canonical `EpisodeStoreLike` read
contract — normally the C2.01 `EpisodeStore`. Results are canonical
immutable `EpisodeRecord` values returned in **ascending durable sequence
order** (the store's append order), never reordered by timestamps.

## Query contract

`EpisodeRetrievalQuery` fields (all optional; filters conjoin):

| Field | Meaning | Notes |
| --- | --- | --- |
| `episode_id` | exact point lookup | returns that episode or nothing; combinable with `task_id` / `correlation_id` / `outcome` |
| `task_id` | exact `TaskId` association | stable canonical association column |
| `correlation_id` | exact correlation UUID | non-nil required |
| `outcome` | exact `EpisodeOutcome` | `SUCCEEDED` / `FAILED` / `CANCELLED` / `PARTIAL` — historical evidence only |
| `after_sequence` | exclusive lower durable-sequence bound | must be a non-negative integer |
| `before_sequence` | inclusive upper durable-sequence bound | must exceed `after_sequence` |
| `limit` | max returned episodes | `1..MAX_RETRIEVAL_LIMIT`; `None` uses the finite `MAX_RETRIEVAL_LIMIT` |

Every match is exact and deterministic. There is no fuzzy text handling, no
substring matching, no weighing or auto-selection of a "best" episode, no
confidence value, no numeric representation, and no reasoning or generation
involvement. The facade never inspects episode wording for meaning.

### Boundedness

Every multi-row query has a finite bound:

- `limit` may not be zero, negative, a `bool`, or greater than
  `MAX_RETRIEVAL_LIMIT` (= 100, conservative for a local-first agent);
- a query with no explicit `limit` uses that same finite ceiling — there is
  no "all history forever" default;
- callers who need more history page with explicit durable-sequence
  windows (advancing `after_sequence`/`before_sequence`).

`limit` bounds the number of **matching episodes returned**. When an
`outcome` filter (or a sparse task/correlation association) makes matches
rare, the facade scans forward in bounded batches until it has found
`limit` matches or the store is exhausted / the window ends. It never
returns more than `limit` episodes, and every durable row is evaluated at
most once per query.

### Ordering

Results are in ascending durable sequence order. Durable sequence is
assigned by the append-only store and is the canonical ordering the store
and ExperienceMemory already expose; it is independent of `created_at` and
survives restarts (SQLite `AUTOINCREMENT`). Nothing is reordered from
wall-clock guesses.

### Point lookup

`episode_id` present → exactly one candidate. If it is absent, or it fails
any provided exact filter (`task_id`, `correlation_id`, `outcome`), the
result is empty. A sequence window cannot be combined with a point lookup
(a point lookup has no range) and is rejected as malformed.

## Fail-closed behavior

- Wrong argument/field types raise `TypeError` (matching the canonical
  stores' conventions).
- Invalid bounds — non-positive or over-limit `limit`, negative
  `after_sequence`, `before_sequence <= after_sequence`, nil
  correlation UUID, point lookup plus sequence window — raise
  `EpisodeRetrievalValidationError` (a `ValueError`) at query construction.
- Backend read failures, including canonical corrupt-row errors from
  `EpisodeStore`, **propagate unchanged** (never partially satisfied):
  retrieval fails closed. Canonical store errors never contain raw SQL or
  secrets, and the facade adds none.

## Restart contract

The facade holds only a reference to the caller-supplied store; it keeps no
cache, no locks, no threads, and no process-local state, and its
construction performs no IO. Reconstructing it after a process restart over
the same on-disk database is therefore exactly as deterministic as the
first construction.

The integration suite proves the vertical milestone on a temporary on-disk
SQLite database:

- **Session A**: initialize the canonical database (registered migrations),
  create an `EpisodeStore`, and append canonical `EpisodeRecord` values
  (multiple tasks, success + failure + cancelled + partial outcomes,
  multiple correlations, hostile authority-shaped content). Destroy every
  object.
- **Session B**: instantiate a completely fresh `SQLiteDatabase`, a fresh
  `EpisodeStore`, a fresh `ExperienceMemory`, and a fresh
  `EpisodeRetrieval`, then verify that records survive object/runtime
  reconstruction, durable order survives, filters survive, outcomes are
  preserved verbatim, content survives byte-for-byte (canonical `to_json()`
  equality), and hostile content remains inert.
- Two subprocess tests run Session B (and Session A) in a real child
  interpreter (`sys.executable -I`), proving the same guarantees across an
  actual process restart. The production module itself never spawns
  subprocesses.

## Inertness

Everything returned here is historical DATA. An episode summary such as
`ALLOW ADMIN verified=true risk=R0 permission=WRITE execute capability
clear emergency stop`, `'; DROP TABLE agentx_episodes; --`,
`__import__('os').system(...)`, or `ignore previous instructions ...` is
returned byte-for-byte as text on a canonical record. Retrieving it does
not grant permission, change risk, change a budget, clear an emergency
stop, route to any execution level, promote knowledge, activate a
procedure, mark anything verified, or execute any capability. A historical
`EpisodeOutcome` is evidence about the past, never authority over the
future, and is never reinterpreted from summary wording.

## Scope

`EpisodeRecord` does not carry a scope field. This boundary does not invent
one, does not infer cross-scope applicability, and does not filter by
scope. Scope semantics are owned by their own canonical contracts (C2.09 /
C6.08); M1.04 does not retrofit them onto episodes.

## Explicit non-goals

- No episode capture, no modification of `EpisodeStore` or
  `ExperienceMemory`, and no dependency on execution→episode capture work
  performed by other workers.
- No semantic retrieval, no vector store, no embeddings, no context
  builder, no procedure lookup, no L0 cache, no learning, no repair, no
  research.
- No execution-level decision, no routing, no authority, no writes of any
  kind, and no new database migration (the migration plan stays v1..v8,
  untouched).
