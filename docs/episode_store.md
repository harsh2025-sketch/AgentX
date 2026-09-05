# AgentX EpisodeStore

C2.01 adds durable storage for selected **episodic experience records**. An EpisodeStore is not the
EventJournal: the EventJournal is canonical ordered event history, while EpisodeStore contains only
meaningful episodes deliberately appended by a caller. No Event, EventBus subscription, or journal
replay automatically creates an episode.

## Canonical episode record

`agentx.core.episodes.EpisodeRecord` is the inert shared-domain contract. It contains only:

- canonical `EpisodeId` identity;
- optional canonical `TaskId` association;
- optional correlation UUID;
- UTC-normalized creation time;
- optional start/end experience window;
- controlled `EpisodeOutcome`;
- a concise summary;
- zero or more canonical Event UUID references.

This is intentionally smaller than the future C2.10 Causal Experience Model. C2.01 does not define
state-before/action/observation/state-after/verification causal structure.

Supporting Event UUIDs are references only. They are not foreign keys, are not required to exist in
an EventJournal, and are never replayed or executed by EpisodeStore.

## Persistence schema

C2.01 appends migration 4, `create_episode_store`, to the existing C1.05 migration sequence.
Migrations 1 and 2 remain the canonical persistence metadata and EventJournal migrations, and C2.02
owns migration 3, `create_knowledge_store`. C2.01 does not rewrite or resequence any of those
historical migrations.

`agentx_episodes` stores:

- `sequence INTEGER PRIMARY KEY AUTOINCREMENT` — durable append/iteration order;
- `episode_id TEXT NOT NULL UNIQUE` — canonical episode identity and duplicate protection;
- nullable `task_id` and `correlation_id` — stable filter dimensions;
- `episode_json TEXT NOT NULL` — canonical deterministic `EpisodeRecord.to_json()` representation;
- `recorded_at_utc TEXT NOT NULL` — SQLite insertion timestamp.

Task/correlation columns are indexes for stable lookup dimensions. Every read cross-checks them
against the canonical serialized EpisodeRecord; a mismatch is corruption, not alternate truth.

## API and ordering

`EpisodeStore` receives an explicit canonical `SQLiteDatabase`:

```python
store = EpisodeStore(database)
sequence = store.append(episode)
restored = store.get(episode.episode_id)
entries = store.read(after_sequence=sequence, task_id=episode.task_id)
```

`append(episode)` accepts only canonical EpisodeRecord values, commits atomically through the
existing `transaction(connection)` helper, and returns the SQLite-assigned durable sequence.
Duplicate `EpisodeId` values fail explicitly with `DuplicateEpisodeError` and never create a second
row.

`get(episode_id)` returns the reconstructed EpisodeRecord or `None`.

`read(after_sequence=0, limit=None, task_id=None, correlation_id=None)` returns immutable
`EpisodeEntry` values in **ascending durable sequence order**. `after_sequence` is exclusive.
Optional filtering is deliberately limited to TaskId and correlation UUID; C2.01 does not implement
Hive retrieval, embeddings, similarity search, ranking, graph traversal, or learning.

Sequence describes EpisodeStore append order only. It is independent of episode timestamps and the
EventJournal's independent sequence.

## Corruption and fail-closed behavior

EpisodeStore reconstructs rows only through `EpisodeRecord.from_json()`. It uses no pickle, eval,
exec, dynamic imports, or payload-driven object construction.

Malformed JSON, unsupported episode schema, invalid canonical fields, or disagreement between
indexed identity/association columns and serialized episode data raises `CorruptEpisodeError`.
The error exposes only the durable sequence and indexed episode identity; stored summary/body data
is not included in the error message. Canonical deserialization errors remain available as the
exception cause.

## Concurrency and durability

EpisodeStore uses C1.05 connection-per-unit-of-work semantics and SQLite WAL. Each append uses an
independent short-lived connection and one transaction. SQLite owns sequence allocation and unique
EpisodeId enforcement, so there is no process-local counter or correctness lock.

Restarting the process and constructing a new EpisodeStore over the same SQLite path preserves both
history and the next sequence. Concurrent writer tests use independent connections and no sleeps.

## Historical data is inert

Episode records describe what happened; they never authorize what may happen next. In particular,
a stored successful episode — including one whose summary says that an R4 action was previously
authorized — does not:

- become an `AuthorityContext` or grant `Permission`;
- reduce `RiskLevel` or bypass `ActionGate`;
- enlarge a resource budget;
- clear `EmergencyStop`;
- mutate Task state;
- invoke a capability, callback, EventBus subscriber, or EventJournal replay.

Fresh authority and runtime safety checks remain mandatory outside EpisodeStore.
