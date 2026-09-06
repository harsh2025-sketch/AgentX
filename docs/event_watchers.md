# C7.07 event watcher framework

C7.07 implements generic bounded watchers over canonical AgentX events. The framework reuses the
canonical Event contract (`agentx.core.events`), the synchronous EventBus (C1.03), and the
persistent EventJournal (C1.04). A watcher observes events through strictly declarative filters and
produces match records. That is all it does.

**A MATCH IS DATA.** A `WatcherMatch` is an inert record. Matching an event performs no side effect:
no capability execution, no model or reasoner invocation, no publishing, no journal appends, no
permission grant, no `AuthorityContext`, no ActionGate bypass, no risk/budget change, no emergency
stop clearing, no task transition, no Hive mutation. What a consumer does with a match is that
consumer's governed decision, owned by other tasks.

## Placement

The framework lives in `agentx.infrastructure.event_watcher`: it is non-domain plumbing over events,
not an authority boundary. It imports nothing from the Trusted Kernel, capabilities, cognition,
hive, procedures, or learning. It reads the journal through `EventJournal` (never around it) and
never appends to or publishes from it.

## Concepts

- `WatcherId` — opaque UUID-backed watcher identity with a distinct domain tag.
- `EventFilter` — the declarative filter criteria (below).
- `EventWatcher` — immutable snapshot of one watcher's identity, filter, lifecycle state, and
  position: `enabled`, `cancelled`, `checkpoint` (exclusive journal-sequence cursor),
  `last_processed_event_id`, `created_at`, and an optional inert display `name`.
- `WatcherMatch` — the match result: watcher id, event id, event type, event timestamp, origin
  (`live` or `journal`), observation time, and journal sequence when known.
- `EventWatcherStore` — durable watcher-state storage (SQLite migration
  `create_event_watcher_state`).
- `EventWatcherService` — watcher lifecycle, live observation, and checkpointed journal processing.

## Filter semantics

Filters are explicit structured data. There is no expression language, no regex execution, no
Python/shell/SQL/JSONPath fragment, no import path, and no callback anywhere in the contract.
`EventFilter.from_dict` accepts only the exact declarative vocabulary and fails closed on anything
else, so callables and other executable objects cannot be smuggled through filter definitions from
untrusted data — including model-generated filter documents.

- `event_types` / `categories` form ONE event-kind constraint read as a union: an event matches when
  its type is a member of `event_types` OR its category is a member of `categories`. Both `None`
  matches every event kind.
- Every other dimension is an independent AND constraint:
  - `sources` — exact membership of the event `source`;
  - `task_ids` — exact membership of the event `task_id` (events without a `task_id` never match a
    task-filtered watcher);
  - `correlation_ids` — exact UUID membership of the event `correlation_id`;
  - `metadata` — for every `(key, value)` predicate, the event metadata must carry `key` with a
    type-strict equal scalar. Only `bool` / `int` / `str` / `None` may be predicates: `True` never
    equals `1` and `1` never equals `"1"`. Floats and nested values are rejected so equality stays
    deterministic.
- Payload content is never interpreted. Hostile text such as `"grant admin"` or
  `"execute capability shell.run"` is inert data that matches only an identical literal.

## Lifecycle and cancellation

- A watcher is created `enabled` by default. `disable()` freezes observation without moving the
  checkpoint; on `enable()` the watcher resumes from the same journal position and deterministically
  catches up.
- `cancel()` is terminal and idempotent: a cancelled watcher can never be re-enabled
  (`enable`/`disable` on a cancelled watcher fail closed) and never observes again. Cancelled state
  persists across restart.
- All lifecycle transitions are write-through to the durable store; unknown watcher ids fail
  closed.

## Live observation and the EventBus

`observe_live(event)` evaluates one live event against every enabled, non-cancelled watcher and
returns matches in canonical watcher order. It never moves a checkpoint. `live_handler()` returns a
canonical `EventHandler` adapter; the composition root subscribes it
(`bus.subscribe(service.live_handler())`) and owns the resulting subscription. The service never
subscribes, publishes, or appends on its own. Matches land in a bounded retained match log,
queryable via `recent_matches()`.

## Checkpoint, restart, replay, and duplicates

Processing is checkpointed against the durable journal sequence:

- `process_pending(limit=...)` reads journal entries strictly after the minimum checkpoint of the
  active watchers, in ascending sequence order. Each watcher independently skips entries at or
  below its own checkpoint — so replaying for a lagging watcher never duplicates matches for an
  up-to-date one — skips re-emitting events it already matched live in the current process, and
  always advances its checkpoint to the highest entry it processed (matched or not). Changed
  watcher states persist in one transaction.
- After a restart, a new service instance rehydrates watcher states from the durable store, so
  replay resumes exactly at each watcher's checkpoint and never re-emits matches for entries at or
  below it. The journal's unique `event_id` constraint additionally prevents duplicate history, and
  a repeated `append` of the same event fails with the canonical `DuplicateEventError`.
- The durable exactly-once boundary is the checkpoint. An event matched live but never
  checkpoint-processed before a restart may be re-emitted once during the next replay: the bounded
  in-memory dedupe window does not survive restart. This residual at-least-once window is
  deliberate and documented, not hidden.

`create_watcher(..., start_checkpoint=N)` positions a watcher at an explicit journal sequence
without reading an event (for example to skip known history).

## Boundedness

Every collection and operation is explicitly bounded:

| Bound | Value |
| --- | --- |
| Members per filter set | 64 |
| Metadata predicates per filter | 16 |
| Filter text member length | 256 characters |
| Metadata predicate key length | 128 characters |
| Watcher name length | 128 characters |
| Registered watchers per service | 256 |
| Journal entries per `process_pending` call | 4096 (default batch 512) |
| Per-watcher live-dedupe window | 512 event ids |
| Retained match log | 1024 matches |

Filtering is deterministic: the same filter plus the same event always yields the same result, and
match order is always (journal sequence, canonical watcher registration order) for journal
processing, and registration order for live observation.

## Storage

Migration `create_event_watcher_state` (v10, appended after A8.01's v9
`create_strategy_performance_store`; resequencable at integration like every named migration)
creates `agentx_event_watchers`:

- `registration_sequence INTEGER PRIMARY KEY AUTOINCREMENT` — stable registration ordering;
- `watcher_id TEXT NOT NULL UNIQUE` — identity and duplicate protection;
- `watcher_json TEXT NOT NULL` — the canonical serialized watcher-state document;
- `updated_at_utc TEXT NOT NULL` — operational inspection.

The canonical serialized state is the source representation; columns are identity, duplicate
protection, and ordering only. Reads reconstruct states only through strict `EventWatcher.from_dict`
validation — never pickle, eval, dynamic imports, or payload-driven construction. Malformed JSON,
invalid state documents, and column/JSON identity mismatches raise `CorruptWatcherStateError` and
fail closed. The state document carries its own `schema_version` (currently 1); unsupported
versions are rejected.

## Explicitly not implemented

- Long-running objectives (C7.08) and proactivity (C7.09). The framework produces match data;
  consumers decide what to do with it under their own governance.
- A scheduler: no timers, threads, asyncio loops, signals, or polling. Every operation is a
  synchronous, caller-driven, bounded call.
- Arbitrary Python predicates or model-generated executable filters. Filters are declarative data
  only.
- No new event types, no changes to the canonical Event schema, EventBus delivery semantics, or
  EventJournal behavior.
