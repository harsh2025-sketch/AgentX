"""M16 production/release composition helpers.

This module is a composition root, not a second AgentLoop. It initializes the
canonical configuration and SQLite persistence foundations, applies the
canonical migration plan through :class:`SQLiteDatabase`, and performs the
existing read-only recovery assessment before a release runtime may continue.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agentx.infrastructure.config import AgentXConfig, load_config
from agentx.infrastructure.persistence import PersistenceError, SQLiteDatabase
from agentx.infrastructure.recovery import (
    PersistenceRecoveryInspector,
    RecoveryAssessment,
    RecoveryDisposition,
)

__all__ = [
    "ReleaseStartupError",
    "ReleaseStartupReport",
    "initialize_release_state",
]


class ReleaseStartupError(PersistenceError):
    """Raised when durable state is not safe enough to start the release runtime."""


@dataclass(frozen=True, slots=True)
class ReleaseStartupReport:
    """Bounded, non-secret release startup evidence."""

    config: AgentXConfig
    database_path: Path
    schema_version: int
    recovery: RecoveryAssessment
    read_only: bool


def initialize_release_state(
    config_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    data_dir: Path | None = None,
) -> ReleaseStartupReport:
    """Initialize canonical local state and fail closed on unsafe durable state."""
    overrides: dict[str, object] | None = None
    if data_dir is not None:
        resolved = data_dir.expanduser().resolve()
        overrides = {"data_dir": resolved}

    config = load_config(config_path, environ=environ, overrides=overrides)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    database_path = config.data_dir / "agentx.sqlite3"
    database = SQLiteDatabase(database_path)

    with database.connection() as connection:
        row = connection.execute(
            "SELECT MAX(version) AS version FROM agentx_schema_migrations"
        ).fetchone()
        schema_version = 0 if row is None or row["version"] is None else int(row["version"])

    recovery = PersistenceRecoveryInspector(database).assess()
    if recovery.disposition in {
        RecoveryDisposition.BLOCK_STARTUP,
        RecoveryDisposition.INSUFFICIENT_EVIDENCE,
    }:
        raise ReleaseStartupError(
            f"durable-state startup blocked: {recovery.disposition.value}"
        )

    return ReleaseStartupReport(
        config=config,
        database_path=database_path,
        schema_version=schema_version,
        recovery=recovery,
        read_only=recovery.disposition is RecoveryDisposition.DEGRADED_READ_ONLY,
    )
