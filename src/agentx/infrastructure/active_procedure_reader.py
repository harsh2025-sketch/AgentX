"""Bounded durable retrieval of canonical ACTIVE procedure revisions.

This is the storage-facing half of AX-172. It reads only rows whose persisted
status is ``ACTIVE`` through the canonical ``ProcedureStore`` database and
reconstructs them with the store's canonical decoder. It never promotes,
retires, executes, ranks, or grants authority.

An impossible state containing multiple ACTIVE revisions for one ProcedureId
fails closed. Picking one by timestamp/revision would silently manufacture
lifecycle policy and could resurrect stale behavior after restart.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Final

from agentx.core.procedures import ProcedureRecord, ProcedureStatus
from agentx.infrastructure.procedure_store import (
    _PROCEDURE_TABLE,
    ProcedureStore,
    ProcedureStoreStorageError,
    _decode_row,
)

__all__ = [
    "DEFAULT_ACTIVE_PROCEDURE_LIMIT",
    "MAX_ACTIVE_PROCEDURE_LIMIT",
    "ActiveProcedureIntegrityError",
    "ActiveProcedureReader",
]

DEFAULT_ACTIVE_PROCEDURE_LIMIT: Final[int] = 128
MAX_ACTIVE_PROCEDURE_LIMIT: Final[int] = 1024


class ActiveProcedureIntegrityError(ProcedureStoreStorageError):
    """Persisted lifecycle state cannot be used safely for ACTIVE reuse."""


def _require_limit(limit: object) -> int:
    if type(limit) is not int:
        raise TypeError("limit must be an int")
    if limit < 1 or limit > MAX_ACTIVE_PROCEDURE_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_ACTIVE_PROCEDURE_LIMIT}")
    return limit


@dataclass(frozen=True, slots=True)
class ActiveProcedureReader:
    """Read a bounded deterministic ACTIVE-only snapshot from ProcedureStore."""

    store: ProcedureStore

    def __post_init__(self) -> None:
        if not isinstance(self.store, ProcedureStore):
            raise TypeError("store must be a canonical ProcedureStore")

    def read(self, *, limit: int = DEFAULT_ACTIVE_PROCEDURE_LIMIT) -> tuple[ProcedureRecord, ...]:
        """Return ACTIVE revisions in deterministic identity/revision order.

        ``limit`` is a hard query bound. If more ACTIVE rows exist than the
        caller's bound, retrieval fails closed rather than returning a partial
        candidate universe that could change deterministic selection.
        """
        bounded = _require_limit(limit)
        with self.store.database.connection() as connection:
            try:
                rows = connection.execute(
                    f"SELECT procedure_id, revision, created_at_utc, status, record_json "
                    f"FROM {_PROCEDURE_TABLE} WHERE status = ? "
                    "ORDER BY procedure_id ASC, revision ASC LIMIT ?",
                    (ProcedureStatus.ACTIVE.value, bounded + 1),
                ).fetchall()
            except sqlite3.Error as exc:
                raise ProcedureStoreStorageError("Unable to read ACTIVE procedures") from exc

        if len(rows) > bounded:
            raise ActiveProcedureIntegrityError(
                "ACTIVE procedure set exceeds the explicit retrieval bound"
            )

        records = tuple(_decode_row(row) for row in rows)
        seen: set[str] = set()
        for record in records:
            if record.status is not ProcedureStatus.ACTIVE:
                raise ActiveProcedureIntegrityError(
                    "persisted ACTIVE query reconstructed a non-ACTIVE record"
                )
            identity = record.procedure_id.to_str()
            if identity in seen:
                raise ActiveProcedureIntegrityError(
                    f"procedure {identity} has multiple ACTIVE revisions; reuse refused"
                )
            seen.add(identity)
        return records
