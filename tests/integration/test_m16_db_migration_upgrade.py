"""M16 durable-state upgrade acceptance."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agentx.infrastructure.persistence import (
    SQLiteDatabase,
    _MIGRATIONS,
    _apply_migrations,
)


def test_v8_database_upgrades_to_current_without_destroying_existing_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "upgrade.sqlite3"
    connection = sqlite3.connect(path)
    try:
        _apply_migrations(connection, _MIGRATIONS[:8])
        payload = '{"event_id":"preserved-event","source":"m16-upgrade"}'
        connection.execute(
            "INSERT INTO agentx_event_journal (event_id, event_json) VALUES (?, ?)",
            ("preserved-event", payload),
        )
        connection.commit()
    finally:
        connection.close()

    with SQLiteDatabase(path).connection() as upgraded:
        versions = upgraded.execute(
            "SELECT version FROM agentx_schema_migrations ORDER BY version"
        ).fetchall()
        preserved = upgraded.execute(
            "SELECT event_json FROM agentx_event_journal WHERE event_id = ?",
            ("preserved-event",),
        ).fetchone()
        scheduler = upgraded.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name = 'agentx_scheduled_tasks'"
        ).fetchone()

    assert [int(row["version"]) for row in versions] == list(
        range(1, len(_MIGRATIONS) + 1)
    )
    assert preserved is not None
    assert preserved["event_json"] == payload
    assert scheduler is not None

    with SQLiteDatabase(path).connection() as reopened:
        repeated = reopened.execute(
            "SELECT version, name FROM agentx_schema_migrations ORDER BY version"
        ).fetchall()
    assert len(repeated) == len(_MIGRATIONS)
