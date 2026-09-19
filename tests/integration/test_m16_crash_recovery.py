"""M16 real abrupt-process crash/restart recovery proof."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.recovery import PersistenceRecoveryInspector, RecoveryDisposition

_CHILD = r"""
import os
import sys
from pathlib import Path
from agentx.infrastructure.persistence import SQLiteDatabase

path = Path(sys.argv[1])
database = SQLiteDatabase(path)
with database.connection() as connection:
    connection.execute("BEGIN")
    connection.execute(
        "INSERT INTO agentx_event_journal (event_id, event_json) VALUES (?, ?)",
        ("m16-uncommitted-event", '{"event":"uncommitted"}'),
    )
    os._exit(73)
"""


def test_abrupt_process_exit_does_not_commit_partial_state_and_restart_is_healthy(
    tmp_path: Path,
) -> None:
    path = tmp_path / "crash.sqlite3"
    with SQLiteDatabase(path).connection():
        pass

    result = subprocess.run(
        [sys.executable, "-I", "-c", _CHILD, str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 73

    restarted = SQLiteDatabase(path)
    with restarted.connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM agentx_event_journal "
            "WHERE event_id = 'm16-uncommitted-event'"
        ).fetchone()[0]
    assert count == 0
    assert (
        PersistenceRecoveryInspector(restarted).assess().disposition
        is RecoveryDisposition.HEALTHY
    )
