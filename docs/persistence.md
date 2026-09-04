# AgentX SQLite persistence foundation

C1.05 provides only the local SQLite substrate that later AgentX storage owners can build on.
It does not implement the event journal, Hive storage, episodes, procedures, tasks, audit records,
or a generic repository layer.

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

The persistence layer does not import or load global AgentX configuration. A future composition
root is responsible for deriving the database path from `AgentXConfig.data_dir` and passing it in.

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
changing schema. Ordinary caller transactions use `BEGIN` and therefore retain SQLite's normal
deferred locking behavior.

## SQLite settings

Every opened connection applies these settings:

| Setting | Choice | Reason |
| --- | --- | --- |
| `PRAGMA foreign_keys` | `ON` | Foreign-key constraints must be enforced per connection. |
| `PRAGMA busy_timeout` | `5000` ms by default | Give short local lock contention time to clear instead of failing immediately. |
| `PRAGMA journal_mode` | `WAL` | Allows local readers to continue while a writer commits and is suitable for future journal-heavy workloads. |
| `PRAGMA synchronous` | `FULL` | Prefer durability over premature write-throughput optimization. |
| `row_factory` | `sqlite3.Row` | Stable named-column access without an ORM. |

WAL mode may create `-wal` and `-shm` sidecar files next to the main database while it is active.
Those files are part of SQLite's normal WAL operation and must not be treated as unrelated
temporary files. Tests validate the configured journal mode but do not depend on sidecar timing or
presence.

## Migrations

Migrations are a small ordered list embedded in Python source, so they do not depend on the current
working directory or external migration files.

Migration rules:

1. versions are consecutive integers starting at 1;
2. migration names are unique and non-empty;
3. startup reads the persistence migration metadata;
4. already-applied migrations are validated and skipped;
5. pending migrations are applied in version order;
6. each migration is one atomic transaction;
7. a failed migration is rolled back and its version is not recorded;
8. a database whose recorded version is newer than this code supports is rejected.

The migration metadata itself is created by migration 1. SQLite transactional DDL means failure
while creating or recording that first migration rolls the metadata table back as part of the same
transaction.

C1.05 intentionally keeps this mechanism private and small rather than introducing a general
migration framework.

## Schema owned by C1.05

The fresh database contains exactly one AgentX-owned table:

`agentx_schema_migrations`

It records:

- migration `version`;
- migration `name`;
- UTC application timestamp.

No higher-level persistence schema is pre-created. Event journal, Hive, episode, procedure, audit,
task, capability, user, and model schemas belong to their respective owning tasks.

## Failures

The module defines narrow persistence exceptions only:

- `PersistenceError`;
- `PersistencePathError`;
- `DatabaseOpenError`;
- `DatabaseConfigurationError`;
- `TransactionError`;
- `TransactionStateError`;
- `MigrationError`;
- `UnsupportedSchemaVersionError`.

These do not attempt to become the global AgentX error hierarchy. Integration with the future core
Result/error contract belongs to its owning integration task.

## Concurrency assumptions

AgentX currently uses connection-per-unit-of-work semantics. A `sqlite3.Connection` is not shared
across arbitrary threads, and C1.05 does not disable sqlite3's default same-thread safety check.

SQLite/WAL still has a single-writer model. The busy timeout handles short lock contention; it is
not a scheduler and does not guarantee fairness. Long-running transactions should be avoided.
Future subsystems should open their own short-lived connections through `SQLiteDatabase` rather
than retaining a process-global connection.

The foundation assumes a local filesystem. Network-shared database files and distributed
multi-device writers are outside C1.05.
