# AgentX persistent event journal

C1.04 adds durable append-only storage and deterministic replay for canonical AgentX Events.
The journal records history. It does not create authority: reading historical events never publishes
them, invokes capabilities, executes payload content, grants permissions, or mutates trusted state.

## Schema and migration

The event journal is migration 2 in the existing C1.05 SQLite migration sequence. Migration 1 is
left unchanged. Existing version-1 databases migrate forward when they next open through
`SQLiteDatabase`.

The `agentx_event_journal` table stores:

- `sequence INTEGER PRIMARY KEY AUTOINCREMENT` — durable journal ordering;
- `event_id TEXT NOT NULL UNIQUE` — canonical event identity and duplicate protection;
- `event_json TEXT NOT NULL` — the canonical `Event.to_json()` representation;
- `recorded_at_utc TEXT NOT NULL` — journal insertion time for operational inspection.

The canonical serialized Event remains the source representation. Event fields are not decomposed
into a parallel persistence schema.

## API

`EventJournal` receives an explicit `SQLiteDatabase`:

```python
journal = EventJournal(database)
sequence = journal.append(event)
entries = journal.read(after_sequence=sequence)
events = journal.replay(after_sequence=sequence)
```

`append(event)` accepts only canonical `Event` objects and returns the committed journal sequence.
The return occurs after the C1.05 transaction context has committed successfully.

`read(after_sequence=0, limit=None)` returns immutable `JournalEntry` values in ascending sequence
order. `after_sequence` is exclusive: `after_sequence=10` starts at the first row whose sequence is
greater than 10. `limit`, when supplied, must be a positive integer.

`replay(...)` applies the same boundary rules but returns only reconstructed canonical `Event`
objects. It is a read operation, not a publish operation.

## Sequence semantics

Journal sequence is the durable persistence order. It is independent of:

- `Event.timestamp`;
- `Event.correlation_id`;
- `Event.causation_id`.

SQLite owns sequence assignment through the journal table's integer primary key. There is no
process-local counter, so ordering survives restart and concurrent independent connections cannot
allocate the same sequence.

## Duplicate behavior

Duplicate append fails explicitly with `DuplicateEventError`. A repeated `event_id` never creates a
second record. The unique database constraint is the concurrency-safe source of truth for this
rule.

## Corruption behavior

Journal rows are reconstructed only through canonical `Event.from_json()` deserialization. The
journal never uses pickle, eval, exec, dynamic imports, or payload-driven object construction.

Malformed JSON, invalid canonical Event content, or a mismatch between the indexed `event_id` and
the serialized Event raises `CorruptJournalEntryError`. The error exposes the journal sequence and
indexed event ID but does not include the stored payload body. Canonical event-schema validation
errors remain available as the exception cause.

A database schema newer than the registered SQLite migration set continues to raise the existing
`UnsupportedSchemaVersionError` from C1.05 before journal reads or writes proceed.

## Transactions and durability

Each append uses a short-lived connection from `SQLiteDatabase` and the existing
`transaction(connection)` helper. One append is one transaction:

- success commits before the sequence is returned;
- statement failure rolls back;
- duplicate failure rolls back;
- commit/rollback infrastructure failures continue to use the C1.05 persistence errors.

No second persistence or migration framework is introduced.

## Concurrency

The journal follows the existing SQLite WAL/single-writer model. Each operation opens its own
connection. No process-global journal lock or connection pool is added. The configured busy timeout
handles short writer contention while SQLite serializes durable writes.

Concurrent writer tests use independent connections and verify unique monotonic committed
sequences without sleeps or in-memory sequencing.

## EventBus and authority separation

`EventBus` and `EventJournal` remain separate components:

- EventBus provides live in-process delivery;
- EventJournal provides durable historical storage.

Appending does not publish. Publishing does not automatically append. Replaying does not call
`EventBus.publish()` or subscriber code. A historical `ACTION_REQUESTED` event is data describing
what happened; it is not permission to execute that action again.

A future composition/integration task may decide how live publication and durable recording are
wired together, including ordering and failure semantics. C1.04 deliberately does not make that
decision.
