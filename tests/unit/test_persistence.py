"""Tests for the AgentX SQLite persistence foundation."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agentx.infrastructure.persistence import (
    _MIGRATIONS,
    MigrationError,
    PersistencePathError,
    SQLiteDatabase,
    TransactionStateError,
    UnsupportedSchemaVersionError,
    _apply_migrations,
    _Migration,
    transaction,
)


def _database_path(tmp_path: Path, name: str = "agentx.sqlite3") -> Path:
    return tmp_path / name


def test_create_fresh_database(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    assert not path.exists()

    with SQLiteDatabase(path).connection() as connection:
        assert path.exists()
        row = connection.execute("SELECT 1 AS value").fetchone()
        assert row is not None
        assert row["value"] == 1


def test_migration_metadata_created(tmp_path: Path) -> None:
    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        row = connection.execute(
            """
            SELECT version, name, applied_at_utc
            FROM agentx_schema_migrations
            """
        ).fetchone()

        assert row is not None
        assert row["version"] == 1
        assert row["name"] == "create_persistence_metadata"
        assert isinstance(row["applied_at_utc"], str)
        assert row["applied_at_utc"].endswith("Z")


def test_migrations_apply_in_order(tmp_path: Path) -> None:
    path = _database_path(tmp_path)
    with SQLiteDatabase(path).connection() as connection:
        migrations = (
            *_MIGRATIONS,
            _Migration(
                version=2,
                name="test_first",
                statements=(
                    "CREATE TABLE test_order (position INTEGER NOT NULL)",
                    "INSERT INTO test_order (position) VALUES (2)",
                ),
            ),
            _Migration(
                version=3,
                name="test_second",
                statements=("INSERT INTO test_order (position) VALUES (3)",),
            ),
        )

        _apply_migrations(connection, migrations)

        applied = connection.execute(
            "SELECT version FROM agentx_schema_migrations ORDER BY version"
        ).fetchall()
        positions = connection.execute("SELECT position FROM test_order ORDER BY rowid").fetchall()

        assert [row["version"] for row in applied] == [1, 2, 3]
        assert [row["position"] for row in positions] == [2, 3]


def test_reopening_current_database_is_idempotent(tmp_path: Path) -> None:
    path = _database_path(tmp_path)

    with SQLiteDatabase(path).connection() as connection:
        first = connection.execute(
            "SELECT version, name, applied_at_utc FROM agentx_schema_migrations"
        ).fetchone()
        assert first is not None
        first_values = tuple(first)

    with SQLiteDatabase(path).connection() as connection:
        rows = connection.execute(
            "SELECT version, name, applied_at_utc FROM agentx_schema_migrations"
        ).fetchall()

    assert len(rows) == 1
    assert tuple(rows[0]) == first_values


def test_transaction_commit(tmp_path: Path) -> None:
    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        connection.execute("CREATE TABLE test_commit (value TEXT NOT NULL)")

        with transaction(connection):
            connection.execute("INSERT INTO test_commit (value) VALUES ('committed')")

        row = connection.execute("SELECT value FROM test_commit").fetchone()
        assert row is not None
        assert row["value"] == "committed"


def test_transaction_rolls_back_on_failure(tmp_path: Path) -> None:
    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        connection.execute("CREATE TABLE test_rollback (value TEXT NOT NULL)")

        with (
            pytest.raises(RuntimeError, match="force rollback"),
            transaction(connection),
        ):
            connection.execute("INSERT INTO test_rollback (value) VALUES ('rolled back')")
            raise RuntimeError("force rollback")

        count = connection.execute("SELECT COUNT(*) AS count FROM test_rollback").fetchone()
        assert count is not None
        assert count["count"] == 0


def test_nested_transactions_are_rejected(tmp_path: Path) -> None:
    with (
        SQLiteDatabase(_database_path(tmp_path)).connection() as connection,
        transaction(connection),
        pytest.raises(TransactionStateError, match="Nested"),
        transaction(connection),
    ):
        pass


def test_foreign_keys_are_enforced(tmp_path: Path) -> None:
    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        connection.execute("CREATE TABLE test_parent (id INTEGER PRIMARY KEY)")
        connection.execute(
            """
            CREATE TABLE test_child (
                parent_id INTEGER NOT NULL REFERENCES test_parent(id)
            )
            """
        )

        with pytest.raises(sqlite3.IntegrityError), transaction(connection):
            connection.execute("INSERT INTO test_child (parent_id) VALUES (999)")


def test_required_connection_settings_and_row_policy(tmp_path: Path) -> None:
    with SQLiteDatabase(_database_path(tmp_path), busy_timeout_ms=2_500).connection() as connection:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()
        synchronous = connection.execute("PRAGMA synchronous").fetchone()
        metadata = connection.execute("SELECT version FROM agentx_schema_migrations").fetchone()

        assert foreign_keys is not None and foreign_keys[0] == 1
        assert busy_timeout is not None and busy_timeout[0] == 2_500
        assert journal_mode is not None and str(journal_mode[0]).lower() == "wal"
        assert synchronous is not None and synchronous[0] == 2
        assert isinstance(metadata, sqlite3.Row)


def test_newer_schema_is_rejected(tmp_path: Path) -> None:
    database = SQLiteDatabase(_database_path(tmp_path))

    with database.connection() as connection, transaction(connection):
        connection.execute(
            """
            INSERT INTO agentx_schema_migrations (version, name)
            VALUES (2, 'future_schema')
            """
        )

    with (
        pytest.raises(UnsupportedSchemaVersionError, match="newer than supported"),
        database.connection(),
    ):
        pass


def test_failed_migration_rolls_back_atomically(tmp_path: Path) -> None:
    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        migrations = (
            *_MIGRATIONS,
            _Migration(
                version=2,
                name="test_broken",
                statements=(
                    "CREATE TABLE must_rollback (value INTEGER)",
                    "INSERT INTO missing_table (value) VALUES (1)",
                ),
            ),
        )

        with pytest.raises(MigrationError, match="test_broken"):
            _apply_migrations(connection, migrations)

        table = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'must_rollback'"
        ).fetchone()
        versions = connection.execute(
            "SELECT version FROM agentx_schema_migrations ORDER BY version"
        ).fetchall()

        assert table is None
        assert [row["version"] for row in versions] == [1]


def test_connection_is_closed_after_context_exit(tmp_path: Path) -> None:
    database = SQLiteDatabase(_database_path(tmp_path))

    with database.connection() as connection:
        connection.execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_each_unit_of_work_gets_distinct_connection(tmp_path: Path) -> None:
    database = SQLiteDatabase(_database_path(tmp_path))

    with database.connection() as first, database.connection() as second:
        assert first is not second


def test_paths_with_spaces_are_supported_and_parent_created(tmp_path: Path) -> None:
    path = tmp_path / "AgentX Data With Spaces" / "local state.sqlite3"
    assert not path.parent.exists()

    with SQLiteDatabase(path).connection():
        pass

    assert path.is_file()


def test_absolute_database_path_is_not_cwd_dependent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "canonical data" / "agentx.sqlite3"
    other_directory = tmp_path / "unrelated cwd"
    other_directory.mkdir()
    monkeypatch.chdir(other_directory)

    with SQLiteDatabase(path).connection():
        pass

    assert path.is_file()
    assert not (other_directory / path.name).exists()


def test_relative_database_path_is_rejected() -> None:
    with pytest.raises(PersistencePathError, match="must be absolute"):
        SQLiteDatabase(Path("relative.sqlite3"))


def test_import_does_not_create_database_or_files(tmp_path: Path) -> None:
    working_directory = tmp_path / "import sandbox"
    working_directory.mkdir()

    result = subprocess.run(
        [sys.executable, "-I", "-c", "import agentx.infrastructure.persistence"],
        cwd=working_directory,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert list(working_directory.iterdir()) == []


def test_fresh_schema_contains_only_persistence_metadata(tmp_path: Path) -> None:
    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_schema
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

    assert [row["name"] for row in rows] == ["agentx_schema_migrations"]


def test_migration_plan_rejects_nonconsecutive_versions(tmp_path: Path) -> None:
    with SQLiteDatabase(_database_path(tmp_path)).connection() as connection:
        invalid = (
            *_MIGRATIONS,
            _Migration(version=3, name="skipped_two", statements=("SELECT 1",)),
        )

        with pytest.raises(MigrationError, match="consecutive"):
            _apply_migrations(connection, invalid)
