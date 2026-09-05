# AgentX SQLite persistence foundation

C1.05 provides the local SQLite substrate that AgentX storage owners build on. It owns connection,
transaction, and ordered migration mechanics; higher-level stores own their APIs and schemas.
C1.04 extends the registered migration sequence with the event-journal table, C2.02 adds the
KnowledgeStore schema, and C2.01 adds the EpisodeStore schema while keeping the C1.05 mechanics and
historical migrations unchanged.

## Database API

Persistence lives in `agentx.infrastructure.persistence` and uses only Python's standard-library
`sqlite3` module.

Create a database factory with an explicit absolute `pathlib.Path`:

```python
database = SQLiteDatabase(data_dir / "agentx.sqlite3")

with database.connection() as connection:
    with transaction(connection):
        connection.execute(...)
```

The persistence layer does not import or load global AgentX configuration. A composition root is
responsible for deriving the database path from configuration and passing it in.

Relative database paths are rejected so persistence behavior cannot silently change with the
process working directory. The database parent directory is created only when `connection()` is
explicitly entered. Importing the persistence module performs no filesystem writes.

## Connection lifetime and transactions

Each call to `SQLiteDatabase.connection()` opens a new `sqlite3.Connection`, applies required
connection settings and pending schema migrations, yields it, and closes it when the context exits.
There is no global connection, singleton, or connection pool.

Connections use `isolation_level=None`, so SQLite implicit transaction management is disabled.
Callers use the explicit `transaction(connection)` context manager when they need a transaction:

- successful exit commits;
- an exception from the body rolls back and is re-raised;
- commit failures attempt rollback and raise a persistence-specific transaction error;
- nested transactions are rejected rather than silently introducing savepoint behavior.

Schema migrations use `BEGIN IMMEDIATE` so a migration obtains the SQLite write reservation before
changing schema. Ordinary caller transactions use `BEGIN` and retain SQLite's normal deferred
locking behavior.

## SQLite settings

Every opened connection applies these settings:

| Setting | Choice | Reason |
| --- | --- | --- |
| `PRAGMA foreign_keys` | `ON` | Foreign-key constraints must be enforced per connection. |
| `PRAGMA busy_timeout` | `5000` ms by default | Give short local lock contention time to clear instead of failing immediately. |
| `PRAGMA journal_mode` | `WAL` | Allows local readers to continue while a writer commits and suits journal-heavy local workloads. |
| `PRAGMA synchronous` | `FULL` | Prefer durability over premature write-throughput optimization. |
| `row_factory` | `sqlite3.Row` | Stable named-column access without an ORM. |

WAL mode may create `-wal` and `-shm` sidecar files next to the main database while it is active.
Those files are part of SQLite's normal WAL operation and must not be treated as unrelated
temporary files.

## Migrations

Migrations remain a small ordered list embedded in Python source, independent of the current
working directory or external migration files.

Migration rules:

1. versions are consecutive integers starting at 1;
2. migration names are unique and non-empty;
3. startup reads persistence migration metadata;
4. already-applied migrations are validated and skipped;
5. pending migrations are applied in version order;
6. each migration is one atomic transaction;
7. a failed migration is rolled back and its version is not recorded;
8. a database whose recorded version is newer than this code supports is rejected.

Migration 1 creates `agentx_schema_migrations` and is unchanged from C1.05. Migration 2, owned by
C1.04, creates `agentx_event_journal` and is unchanged. Migration 3, owned by C2.02, creates
`agentx_knowledge` and is preserved unchanged. C2.01 appends migration 4,
`create_episode_store`, which creates the EpisodeStore schema. C2.01 does not renumber or rewrite
migrations 1-3. C2.02/C2.03 continue the same name-identified convention: `create_procedure_store`
landed as migration 5, and C2.04 appends migration 6 (`create_artifact_store`) and migration 7
(`create_audit_store`) as the next available versions. No landed migration is ever renumbered or
rewritten.

The mechanism remains intentionally private and small rather than becoming a generic migration
framework.

## Registered schema

The current registered AgentX tables include:

- `agentx_schema_migrations` — migration version, name, and UTC application timestamp;
- `agentx_event_journal` — C1.04 durable canonical Event history;
- `agentx_knowledge` — C2.02 durable KnowledgeStore records;
- `agentx_episodes` — C2.01 selected episodic-experience history;
- `agentx_procedures` — C2.03 durable procedure revisions;
- `agentx_artifacts` — C2.04 durable inert artifact references (metadata only; contents stay
  external and stored references are never dereferenced by storage);
- `agentx_audit_log` — C2.04 durable append-only security-audit history for the canonical C1.09
  `SecurityAuditRecord` contract (historical evidence, never authority; distinct from the
  event journal by table, identity space, and semantics).

The event-journal API is documented in `docs/event_journal.md`; EpisodeStore is documented in
`docs/episode_store.md`. Task, capability, semantic-memory, vector, graph, and model schemas
remain unimplemented.

## Failures

The foundation retains its narrow persistence exceptions:

- `PersistenceError`;
- `PersistencePathError`;
- `DatabaseOpenError`;
- `DatabaseConfigurationError`;
- `TransactionError`;
- `TransactionStateError`;
- `MigrationError`;
- `UnsupportedSchemaVersionError`.

Subsystem-specific persistence layers may define narrower errors at their boundary without turning
this module into the global AgentX error hierarchy.

## Concurrency assumptions

AgentX uses connection-per-unit-of-work semantics. A `sqlite3.Connection` is not shared across
arbitrary threads, and C1.05 does not disable sqlite3's default same-thread safety check.

SQLite/WAL still has a single-writer model. The busy timeout handles short lock contention; it is
not a scheduler and does not guarantee fairness. Long-running transactions should be avoided.
Subsystems should open short-lived connections through `SQLiteDatabase` rather than retaining a
process-global connection.

The foundation assumes a local filesystem. Network-shared database files and distributed
multi-device writers remain outside this contract.
