"""SQLite persistence foundation for local-first AgentX storage.

This module owns only connection, transaction, and schema-migration mechanics.
Higher-level stores define their own schemas in their owning tasks.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
    "DatabaseConfigurationError",
    "DatabaseOpenError",
    "MigrationError",
    "PersistenceError",
    "PersistencePathError",
    "SQLiteDatabase",
    "TransactionError",
    "TransactionStateError",
    "UnsupportedSchemaVersionError",
    "transaction",
]

_DEFAULT_BUSY_TIMEOUT_MS: Final = 5_000
_MIGRATION_TABLE: Final = "agentx_schema_migrations"


class PersistenceError(Exception):
    """Base exception for the AgentX SQLite persistence foundation."""


class PersistencePathError(PersistenceError, ValueError):
    """Raised when a database path violates the explicit-path contract."""


class DatabaseOpenError(PersistenceError):
    """Raised when the database file or its parent directory cannot be opened."""


class DatabaseConfigurationError(PersistenceError):
    """Raised when required SQLite connection settings cannot be established."""


class TransactionError(PersistenceError):
    """Raised when an explicit transaction cannot begin, commit, or roll back."""


class TransactionStateError(TransactionError):
    """Raised when a caller requests unsupported nested transaction behavior."""


class MigrationError(PersistenceError):
    """Raised when schema migration state is invalid or a migration fails."""


class UnsupportedSchemaVersionError(MigrationError):
    """Raised when a database schema is newer than this AgentX build supports."""


@dataclass(frozen=True, slots=True)
class _Migration:
    version: int
    name: str
    statements: tuple[str, ...]


_MIGRATIONS: Final[tuple[_Migration, ...]] = (
    _Migration(
        version=1,
        name="create_persistence_metadata",
        statements=(
            """
            CREATE TABLE agentx_schema_migrations (
                version INTEGER PRIMARY KEY CHECK (version > 0),
                name TEXT NOT NULL UNIQUE,
                applied_at_utc TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,
        ),
    ),
    _Migration(
        version=2,
        name="create_event_journal",
        statements=(
            """
            CREATE TABLE agentx_event_journal (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE CHECK (length(event_id) > 0),
                event_json TEXT NOT NULL CHECK (length(event_json) > 0),
                recorded_at_utc TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,
        ),
    ),
    # C2.02 knowledge store. Migration numbering above 2 is provisional while
    # concurrent store tasks (e.g. C2.01 EpisodeStore) integrate: resequencing
    # this entry's version at integration is a two-line, conflict-free change
    # because every store migration is identified by name, not by number.
    _Migration(
        version=3,
        name="create_knowledge_store",
        statements=(
            """
            CREATE TABLE agentx_knowledge (
                knowledge_id TEXT PRIMARY KEY CHECK (length(knowledge_id) > 0),
                created_at_utc TEXT NOT NULL CHECK (length(created_at_utc) > 0),
                record_json TEXT NOT NULL CHECK (length(record_json) > 0)
            )
            """,
        ),
    ),
    _Migration(
        version=4,
        name="create_episode_store",
        statements=(
            """
            CREATE TABLE agentx_episodes (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                episode_id TEXT NOT NULL UNIQUE CHECK (length(episode_id) > 0),
                task_id TEXT CHECK (task_id IS NULL OR length(task_id) > 0),
                correlation_id TEXT
                    CHECK (correlation_id IS NULL OR length(correlation_id) > 0),
                episode_json TEXT NOT NULL CHECK (length(episode_json) > 0),
                recorded_at_utc TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,
            """
            CREATE INDEX agentx_episodes_task_sequence_idx
            ON agentx_episodes (task_id, sequence)
            """,
            """
            CREATE INDEX agentx_episodes_correlation_sequence_idx
            ON agentx_episodes (correlation_id, sequence)
            """,
        ),
    ),
    # C2.03 procedure store. Concurrent store tasks share this migration plan:
    # resequencing this entry's version at integration is a two-line,
    # conflict-free change because every store migration is identified by name,
    # not by number. (C2.02/C2.01 numbered theirs provisionally above v2; the
    # episode store has since landed as the canonical v4, so the procedure
    # store takes the next available version, 5.)
    # The status column is deliberately opaque text (not a CHECK-bounded
    # vocabulary): the canonical ProcedureStatus vocabulary is owned by
    # agentx.core.procedures and must be extendable without SQL surgery.
    _Migration(
        version=5,
        name="create_procedure_store",
        statements=(
            """
            CREATE TABLE agentx_procedures (
                procedure_id TEXT NOT NULL CHECK (length(procedure_id) > 0),
                revision INTEGER NOT NULL CHECK (revision > 0),
                created_at_utc TEXT NOT NULL CHECK (length(created_at_utc) > 0),
                status TEXT NOT NULL CHECK (length(status) > 0),
                record_json TEXT NOT NULL CHECK (length(record_json) > 0),
                PRIMARY KEY (procedure_id, revision)
            )
            """,
        ),
    ),
    _Migration(
        version=6,
        name="create_artifact_and_audit_stores",
        statements=(
            """
            CREATE TABLE agentx_artifacts (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id TEXT NOT NULL UNIQUE CHECK (length(artifact_id) > 0),
                task_id TEXT CHECK (task_id IS NULL OR length(task_id) > 0),
                correlation_id TEXT
                    CHECK (correlation_id IS NULL OR length(correlation_id) > 0),
                artifact_json TEXT NOT NULL CHECK (length(artifact_json) > 0),
                recorded_at_utc TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,
            """
            CREATE INDEX agentx_artifacts_task_sequence_idx
            ON agentx_artifacts (task_id, sequence)
            """,
            """
            CREATE INDEX agentx_artifacts_correlation_sequence_idx
            ON agentx_artifacts (correlation_id, sequence)
            """,
            """
            CREATE TABLE agentx_audit_records (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                audit_id TEXT NOT NULL UNIQUE CHECK (length(audit_id) > 0),
                task_id TEXT CHECK (task_id IS NULL OR length(task_id) > 0),
                correlation_id TEXT
                    CHECK (correlation_id IS NULL OR length(correlation_id) > 0),
                audit_json TEXT NOT NULL CHECK (length(audit_json) > 0),
                recorded_at_utc TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )
            """,
            """
            CREATE INDEX agentx_audit_records_task_sequence_idx
            ON agentx_audit_records (task_id, sequence)
            """,
            """
            CREATE INDEX agentx_audit_records_correlation_sequence_idx
            ON agentx_audit_records (correlation_id, sequence)
            """,
        ),
    ),
    _Migration(
        version=7,
        name="create_knowledge_integrity",
        statements=(
            """
            CREATE TABLE agentx_knowledge_contradictions (
                first_knowledge_id TEXT NOT NULL,
                second_knowledge_id TEXT NOT NULL,
                PRIMARY KEY (first_knowledge_id, second_knowledge_id),
                CHECK (first_knowledge_id < second_knowledge_id),
                FOREIGN KEY (first_knowledge_id)
                    REFERENCES agentx_knowledge (knowledge_id) ON DELETE RESTRICT,
                FOREIGN KEY (second_knowledge_id)
                    REFERENCES agentx_knowledge (knowledge_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE TABLE agentx_knowledge_supersessions (
                replacement_knowledge_id TEXT NOT NULL,
                superseded_knowledge_id TEXT NOT NULL,
                PRIMARY KEY (replacement_knowledge_id, superseded_knowledge_id),
                CHECK (replacement_knowledge_id <> superseded_knowledge_id),
                FOREIGN KEY (replacement_knowledge_id)
                    REFERENCES agentx_knowledge (knowledge_id) ON DELETE RESTRICT,
                FOREIGN KEY (superseded_knowledge_id)
                    REFERENCES agentx_knowledge (knowledge_id) ON DELETE RESTRICT
            )
            """,
            """
            CREATE INDEX agentx_knowledge_supersessions_superseded_idx
            ON agentx_knowledge_supersessions (superseded_knowledge_id)
            """,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class SQLiteDatabase:
    """Factory for explicit, short-lived SQLite connections.

    Each ``connection()`` call opens a distinct connection, configures required
    PRAGMAs, applies pending persistence migrations, and closes the connection
    when the context exits.
    """

    path: Path
    busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS

    def __post_init__(self) -> None:
        if not self.path.is_absolute():
            raise PersistencePathError(
                f"SQLite database path must be absolute to avoid CWD dependence: {self.path}"
            )
        if self.busy_timeout_ms <= 0:
            raise DatabaseConfigurationError("busy_timeout_ms must be greater than zero")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Open one migrated connection and close it deterministically."""
        connection = self._open_connection()
        try:
            _apply_migrations(connection, _MIGRATIONS)
            yield connection
        finally:
            connection.close()

    def _open_connection(self) -> sqlite3.Connection:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path,
                timeout=self.busy_timeout_ms / 1_000,
                isolation_level=None,
            )
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseOpenError(
                f"Unable to open SQLite database at {self.path}: {exc}"
            ) from exc

        try:
            _configure_connection(connection, self.busy_timeout_ms)
        except Exception:
            connection.close()
            raise
        return connection


def _configure_connection(connection: sqlite3.Connection, busy_timeout_ms: int) -> None:
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        journal_row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
        connection.execute("PRAGMA synchronous = FULL")
    except sqlite3.Error as exc:
        raise DatabaseConfigurationError(f"Unable to configure SQLite connection: {exc}") from exc

    foreign_key_row = connection.execute("PRAGMA foreign_keys").fetchone()
    if foreign_key_row is None or foreign_key_row[0] != 1:
        raise DatabaseConfigurationError("SQLite foreign-key enforcement could not be enabled")
    if journal_row is None or str(journal_row[0]).lower() != "wal":
        raise DatabaseConfigurationError("SQLite WAL journal mode could not be enabled")


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run one explicit transaction with commit-or-rollback semantics.

    Nested transactions are intentionally unsupported. Callers that need more
    complex savepoint behavior must own that decision explicitly in a later task.
    """
    if connection.in_transaction:
        raise TransactionStateError("Nested SQLite transactions are not supported")

    try:
        connection.execute("BEGIN")
    except sqlite3.Error as exc:
        raise TransactionError(f"Unable to begin SQLite transaction: {exc}") from exc

    try:
        yield connection
    except BaseException:
        try:
            connection.rollback()
        except sqlite3.Error as rollback_error:
            raise TransactionError(
                f"SQLite transaction failed and rollback also failed: {rollback_error}"
            ) from rollback_error
        raise
    else:
        try:
            connection.commit()
        except sqlite3.Error as exc:
            try:
                connection.rollback()
            except sqlite3.Error as rollback_error:
                raise TransactionError(
                    f"SQLite commit failed and rollback also failed: {rollback_error}"
                ) from rollback_error
            raise TransactionError(f"Unable to commit SQLite transaction: {exc}") from exc


def _apply_migrations(
    connection: sqlite3.Connection,
    migrations: tuple[_Migration, ...],
) -> None:
    _validate_migration_plan(migrations)
    current_version = _current_schema_version(connection)
    supported_version = migrations[-1].version if migrations else 0

    if current_version > supported_version:
        raise UnsupportedSchemaVersionError(
            f"Database schema version {current_version} is newer than supported "
            f"version {supported_version}"
        )

    _validate_applied_migrations(connection, migrations, current_version)
    for migration in migrations:
        if migration.version > current_version:
            _apply_migration(connection, migration)


def _validate_migration_plan(migrations: tuple[_Migration, ...]) -> None:
    expected_versions = tuple(range(1, len(migrations) + 1))
    actual_versions = tuple(migration.version for migration in migrations)
    if actual_versions != expected_versions:
        raise MigrationError(
            f"Migration plan must contain consecutive versions starting at 1; got {actual_versions}"
        )
    if any(not migration.name.strip() for migration in migrations):
        raise MigrationError("Migration names must be non-empty")
    if len({migration.name for migration in migrations}) != len(migrations):
        raise MigrationError("Migration names must be unique")
    if any(not migration.statements for migration in migrations):
        raise MigrationError("Each migration must contain at least one SQL statement")


def _current_schema_version(connection: sqlite3.Connection) -> int:
    try:
        metadata_exists = (
            connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = ?",
                (_MIGRATION_TABLE,),
            ).fetchone()
            is not None
        )
        if not metadata_exists:
            return 0

        rows = connection.execute(
            f"SELECT version FROM {_MIGRATION_TABLE} ORDER BY version"
        ).fetchall()
    except sqlite3.Error as exc:
        raise MigrationError(f"Unable to read migration metadata: {exc}") from exc

    if not rows:
        raise MigrationError("Migration metadata table exists but contains no migration records")

    try:
        versions = tuple(int(row["version"]) for row in rows)
    except (TypeError, ValueError) as exc:
        raise MigrationError("Migration metadata contains a non-integer version") from exc

    expected_versions = tuple(range(1, versions[-1] + 1))
    if versions != expected_versions:
        raise MigrationError(
            f"Migration metadata contains a non-consecutive version history: {versions}"
        )
    return versions[-1]


def _validate_applied_migrations(
    connection: sqlite3.Connection,
    migrations: tuple[_Migration, ...],
    current_version: int,
) -> None:
    if current_version == 0:
        return

    try:
        rows = connection.execute(
            f"SELECT version, name FROM {_MIGRATION_TABLE} ORDER BY version"
        ).fetchall()
    except sqlite3.Error as exc:
        raise MigrationError(f"Unable to validate migration metadata: {exc}") from exc

    expected_by_version = {migration.version: migration.name for migration in migrations}
    for row in rows:
        version = int(row["version"])
        if version > current_version:
            break
        expected_name = expected_by_version.get(version)
        if expected_name is not None and row["name"] != expected_name:
            raise MigrationError(
                f"Migration metadata mismatch at version {version}: "
                f"database has {row['name']!r}, code expects {expected_name!r}"
            )


def _apply_migration(connection: sqlite3.Connection, migration: _Migration) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            f"INSERT INTO {_MIGRATION_TABLE} (version, name) VALUES (?, ?)",
            (migration.version, migration.name),
        )
        connection.commit()
    except sqlite3.Error as exc:
        try:
            connection.rollback()
        except sqlite3.Error as rollback_error:
            raise MigrationError(
                f"Migration {migration.version} ({migration.name}) failed and rollback "
                f"also failed: {rollback_error}"
            ) from rollback_error
        raise MigrationError(
            f"Migration {migration.version} ({migration.name}) failed: {exc}"
        ) from exc
