"""Conservative, read-only durable-state recovery and integrity assessment (M6.03).

AgentX is persistent: restart behavior matters. This module is the V1
*assessment* boundary for durable state. It answers one question for a caller
that is about to start AgentX against an existing database::

    Is the durable state readable, consistent enough to continue, partially
    degraded, or does it require operator intervention?

It answers that question as typed, inert DATA and performs **no repair**:

- It never DELETEs, UPDATEs, REWRITEs, VACUUMs, DROPs, or ALTERs.
- It never repairs rows, rolls the database back, restores backups, skips
  history silently, or invents replacement records.
- It never executes content found in stored records and never interprets
  stored strings as policy, health claims, permissions, or instructions.
- It never migrates the schema. When the applied schema version is older than
  this build supports, the assessment says so and notes that the canonical
  ``SQLiteDatabase`` connection applies pending migrations as its normal
  documented startup behavior — the inspector itself stays read-only.

Read-only first principle
-------------------------

Record payloads are validated **only through canonical public store APIs**
(``KnowledgeStore.get``, ``EpisodeStore.read``, ``EventJournal.read`` and the
other canonical stores). Identity enumeration and row counts use plain
``SELECT`` statements so probing is finite; payload bytes are never decoded by
hand in this module, and a row the canonical store cannot decode is reported
as corrupt — never converted into "missing" or "empty".

CORRUPT != MISSING. DENIED != MISSING. UNREADABLE != EMPTY.

Closed vocabularies
-------------------

- :class:`RecoveryCheckKind` — every issue the inspector can raise.
- :class:`RecoveryDisposition` — the assessment verdict: ``HEALTHY``,
  ``DEGRADED_READ_ONLY``, ``BLOCK_STARTUP``, ``INSUFFICIENT_EVIDENCE``.
- :class:`StartupRecommendation` — a typed advisory for a future composition
  root: ``CONTINUE``, ``CONTINUE_DEGRADED``, ``STOP_AND_ESCALATE``.

``HEALTHY`` means the checks performed passed. It never claims that every byte
on disk is globally perfect: bounded scans are reported honestly through
:class:`StoreProbeResult` (``scan_completed`` is ``False`` and a note explains
why when the per-store bound was reached or a probe had to stop).

Dispositions are infrastructure recommendations only. This module never
clears an EmergencyStop, never starts AgentX, and grants no authority.

Bounded scanning
----------------

All content probing is finite: ``max_records_per_store`` caps how many rows
are decoded per store and ``max_issues`` caps how many issues are enumerated
(the remainder is counted in ``suppressed_issue_count``). Schema/structure
checks touch only the ``sqlite_schema`` metadata table and the canonical
migration table; row counts are ``COUNT(*)`` aggregates.

Failure of the inspector
------------------------

Unexpected I/O or database failures fail closed: :meth:`PersistenceRecoveryInspector.assess`
returns an explicit ``INSUFFICIENT_EVIDENCE`` assessment (never ``HEALTHY``)
carrying an issue of kind :attr:`RecoveryCheckKind.INSPECTION_FAULT` or the
appropriate storage issue. :class:`KeyboardInterrupt`/:class:`SystemExit` are
never swallowed (only ``Exception`` is classified).

Concurrency
-----------

No background threads, no online self-healing, no write transactions: the
inspector opens short-lived read connections and reuses the canonical
connection/transaction semantics of the stores it probes. It is intended for
controlled startup use.

Schema registry
---------------

``_MIGRATION_REGISTRY`` mirrors the canonical migration plan owned by
``agentx.infrastructure.persistence`` (version, name, and the tables each
migration creates). It is read-only metadata used to (a) detect migration
gaps and name mismatches the canonical engine forbids, (b) know which tables
must exist at the applied schema version, and (c) decide which store probes
are safe to run. The architecture tests assert this mirror stays in sync with
the canonical ``_MIGRATIONS`` plan; ``docs/persistence_recovery.md`` explains
how to update it when the canonical plan grows.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from agentx.core.ids import KnowledgeId, ProcedureId
from agentx.infrastructure.artifact_store import (
    ArtifactStore,
    ArtifactStoreStorageError,
    CorruptArtifactRecordError,
)
from agentx.infrastructure.audit_store import (
    AuditStore,
    AuditStoreStorageError,
    CorruptAuditRecordError,
)
from agentx.infrastructure.episode_store import (
    CorruptEpisodeError,
    EpisodeStore,
    EpisodeStoreStorageError,
)
from agentx.infrastructure.event_journal import (
    CorruptJournalEntryError,
    EventJournal,
    EventJournalStorageError,
)
from agentx.infrastructure.knowledge_store import (
    CorruptKnowledgeRecordError,
    KnowledgeStore,
    KnowledgeStoreStorageError,
)
from agentx.infrastructure.negative_experience_store import (
    CorruptNegativeExperienceError,
    NegativeExperienceStore,
    NegativeExperienceStoreStorageError,
)
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.procedure_store import (
    CorruptProcedureRecordError,
    ProcedureStore,
    ProcedureStoreStorageError,
)

__all__ = [
    "PersistenceRecoveryInspector",
    "RecoveryAssessment",
    "RecoveryCheckKind",
    "RecoveryDisposition",
    "RecoveryIssue",
    "StartupRecommendation",
    "StoreProbeResult",
    "startup_recommendation_for",
]

_MIGRATION_TABLE: Final = "agentx_schema_migrations"
_DEFAULT_MAX_RECORDS_PER_STORE: Final = 250
_DEFAULT_MAX_ISSUES: Final = 100
_DETAIL_LIMIT: Final = 240
_IDENTITY_LIMIT: Final = 128


class RecoveryCheckKind(StrEnum):
    """Closed vocabulary of issues the recovery inspector can report.

    Values are stable dotted strings; semantics are documented per member.
    New kinds may only be added deliberately by the owning task.
    """

    DATABASE_FILE_MISSING = "database.file_missing"
    """The database file does not exist, so no durable state could be assessed."""

    DATABASE_UNREADABLE = "database.unreadable"
    """The database file exists but cannot be opened or read (I/O failure,
    "file is not a database", or a broken header)."""

    MIGRATION_METADATA_UNREADABLE = "migration.metadata_unreadable"
    """The migration metadata table exists but could not be read."""

    MIGRATION_HISTORY_INVALID = "migration.history_invalid"
    """Migration history violates a rule the canonical engine forbids:
    non-consecutive versions, an empty metadata table, a non-integer version,
    or a recorded name that does not match the canonical plan."""

    SCHEMA_VERSION_NEWER_THAN_SUPPORTED = "migration.schema_newer_than_supported"
    """The applied schema version is newer than this build supports. The
    canonical engine raises ``UnsupportedSchemaVersionError`` for this state."""

    EXPECTED_TABLE_MISSING = "structure.expected_table_missing"
    """A table owned by canonical persistence at the applied schema version is
    missing."""

    STORE_READ_FAILED = "store.read_failed"
    """A canonical store read failed at the storage layer (not a decode
    failure); that store's content could not be fully assessed."""

    CORRUPT_RECORD = "store.corrupt_record"
    """A canonical store decode rejected a persisted row. The row is reported,
    never deleted, rewritten, or silently skipped."""

    INSPECTION_FAULT = "inspection.fault"
    """The inspector itself hit an unexpected error. The assessment fails
    closed with insufficient evidence."""


class RecoveryDisposition(StrEnum):
    """Closed verdict vocabulary for a recovery assessment.

    - ``HEALTHY``: the checks performed passed. This never proves global
      byte-level perfection; scope is documented by the probes and notes.
    - ``DEGRADED_READ_ONLY``: durable state is readable but contains corrupt
      records. Nothing was repaired; the affected data must be treated as
      read-only until an operator decides.
    - ``BLOCK_STARTUP``: durable state is known to be unsafe to continue with
      (impossible migration history, an unsupported newer schema, or missing
      expected structure).
    - ``INSUFFICIENT_EVIDENCE``: the inspector could not gather enough
      evidence to classify the state (unreadable database, storage-layer read
      failures, a missing file, or an inspection fault).
    """

    HEALTHY = "HEALTHY"
    DEGRADED_READ_ONLY = "DEGRADED_READ_ONLY"
    BLOCK_STARTUP = "BLOCK_STARTUP"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class StartupRecommendation(StrEnum):
    """Typed advisory recommendation accompanying a disposition.

    This is an infrastructure recommendation only: it does not clear an
    EmergencyStop, does not start AgentX, and grants no authority.
    """

    CONTINUE = "CONTINUE"
    CONTINUE_DEGRADED = "CONTINUE_DEGRADED"
    STOP_AND_ESCALATE = "STOP_AND_ESCALATE"


def startup_recommendation_for(disposition: RecoveryDisposition) -> StartupRecommendation:
    """Map a disposition to its advisory startup recommendation."""
    if disposition is RecoveryDisposition.HEALTHY:
        return StartupRecommendation.CONTINUE
    if disposition is RecoveryDisposition.DEGRADED_READ_ONLY:
        return StartupRecommendation.CONTINUE_DEGRADED
    return StartupRecommendation.STOP_AND_ESCALATE


@dataclass(frozen=True, slots=True)
class RecoveryIssue:
    """One typed, deterministic issue found by the inspector.

    ``detail`` never contains record payload content: canonical decode errors
    carry only identities and safe diagnostic text, and every free-text field
    is length-capped. ``store`` names the canonical store/category (for
    example ``"knowledge_store"``); it is ``None`` for database/schema-level
    issues. ``identity`` names the affected row when known (for example an
    ``episode_id``, a ``knowledge_id``, or ``procedure <id> revision <n>``)
    and is ``None`` when no safe identity is available.
    """

    kind: RecoveryCheckKind
    store: str | None
    identity: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class StoreProbeResult:
    """Honest outcome of probing one canonical store's durable rows.

    ``rows_present`` is the raw ``COUNT(*)`` of the store table when it is
    readable; ``0`` when the table is empty or when the count was unreadable
    (an unreadable count always forces ``scan_completed`` to ``False`` with an
    explanatory ``note`` — an unknown row count is never treated as zero
    rows). ``rows_checked`` is the number of rows that decoded successfully
    through canonical store APIs; corrupt rows are not counted there but
    surface as :attr:`RecoveryCheckKind.CORRUPT_RECORD` issues.
    ``scan_completed`` is ``True`` only when coverage is proven (the scan
    reached the durable end or visited every counted row); otherwise it is
    ``False`` and ``note`` explains why — a bounded scan never claims full
    coverage.
    """

    store: str
    rows_present: int
    rows_checked: int
    scan_completed: bool
    note: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryAssessment:
    """Immutable result of one recovery assessment.

    ``issues`` is deterministically ordered (store, kind, identity, detail)
    and capped at the inspector's ``max_issues``; any further detected issues
    are counted in ``suppressed_issue_count``. ``notes`` records honest scope
    statements (fresh database, pending migrations, deferred probes).
    """

    disposition: RecoveryDisposition
    startup_recommendation: StartupRecommendation
    issues: tuple[RecoveryIssue, ...]
    suppressed_issue_count: int
    probes: tuple[StoreProbeResult, ...]
    notes: tuple[str, ...]
    applied_schema_version: int | None
    supported_schema_version: int


# ---------------------------------------------------------------------------
# Canonical migration-plan mirror (read-only metadata; see module docstring).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _MigrationRegistryEntry:
    version: int
    name: str
    tables: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _TableOwner:
    table: str
    store: str


_MIGRATION_REGISTRY: Final[tuple[_MigrationRegistryEntry, ...]] = (
    _MigrationRegistryEntry(
        version=1,
        name="create_persistence_metadata",
        tables=(_MIGRATION_TABLE,),
    ),
    _MigrationRegistryEntry(
        version=2,
        name="create_event_journal",
        tables=("agentx_event_journal",),
    ),
    _MigrationRegistryEntry(
        version=3,
        name="create_knowledge_store",
        tables=("agentx_knowledge",),
    ),
    _MigrationRegistryEntry(
        version=4,
        name="create_episode_store",
        tables=("agentx_episodes",),
    ),
    _MigrationRegistryEntry(
        version=5,
        name="create_procedure_store",
        tables=("agentx_procedures",),
    ),
    _MigrationRegistryEntry(
        version=6,
        name="create_artifact_and_audit_stores",
        tables=("agentx_artifacts", "agentx_audit_records"),
    ),
    _MigrationRegistryEntry(
        version=7,
        name="create_knowledge_integrity",
        tables=("agentx_knowledge_contradictions", "agentx_knowledge_supersessions"),
    ),
    _MigrationRegistryEntry(
        version=8,
        name="create_negative_experience_store",
        tables=("agentx_negative_experiences",),
    ),
    _MigrationRegistryEntry(
        version=9,
        name="create_m12_scheduling_store",
        tables=(
            "agentx_scheduled_tasks",
            "agentx_background_runs",
            "agentx_m12_policy",
            "agentx_m12_quota",
        ),
    ),
)

#: Owner category labels for the expected-table check.
_TABLE_OWNERS: Final[tuple[_TableOwner, ...]] = (
    _TableOwner(_MIGRATION_TABLE, "persistence"),
    _TableOwner("agentx_event_journal", "event_journal"),
    _TableOwner("agentx_knowledge", "knowledge_store"),
    _TableOwner("agentx_knowledge_contradictions", "knowledge_store"),
    _TableOwner("agentx_knowledge_supersessions", "knowledge_store"),
    _TableOwner("agentx_episodes", "episode_store"),
    _TableOwner("agentx_procedures", "procedure_store"),
    _TableOwner("agentx_artifacts", "artifact_store"),
    _TableOwner("agentx_audit_records", "audit_store"),
    _TableOwner("agentx_negative_experiences", "negative_experience_store"),
    _TableOwner("agentx_scheduled_tasks", "schedule_store"),
    _TableOwner("agentx_background_runs", "schedule_store"),
    _TableOwner("agentx_m12_policy", "schedule_store"),
    _TableOwner("agentx_m12_quota", "schedule_store"),
)

_OWNER_BY_TABLE: Final[dict[str, str]] = {owner.table: owner.store for owner in _TABLE_OWNERS}

_SUPPORTED_SCHEMA_VERSION: Final[int] = max(entry.version for entry in _MIGRATION_REGISTRY)


# ---------------------------------------------------------------------------
# Issue collection (internal, bounded).
# ---------------------------------------------------------------------------


class _IssueCollector:
    """Collect issues up to a cap; count anything beyond the cap."""

    __slots__ = ("_issues", "_max_issues", "_suppressed")

    def __init__(self, max_issues: int) -> None:
        self._max_issues = max_issues
        self._issues: list[RecoveryIssue] = []
        self._suppressed = 0

    def add(
        self,
        kind: RecoveryCheckKind,
        *,
        store: str | None = None,
        identity: str | None = None,
        detail: str,
    ) -> None:
        if len(self._issues) >= self._max_issues:
            self._suppressed += 1
            return
        self._issues.append(
            RecoveryIssue(
                kind=kind,
                store=store,
                identity=_clip(identity, _IDENTITY_LIMIT) if identity is not None else None,
                detail=_clip(detail, _DETAIL_LIMIT),
            )
        )

    def issues(self) -> tuple[RecoveryIssue, ...]:
        return tuple(
            sorted(
                self._issues,
                key=lambda issue: (
                    issue.store or "",
                    issue.kind.value,
                    issue.identity or "",
                    issue.detail,
                ),
            )
        )

    @property
    def suppressed(self) -> int:
        return self._suppressed


def _clip(text: str, limit: int) -> str:
    """Length-cap free-text diagnostics so hostile stored data stays bounded."""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


# ---------------------------------------------------------------------------
# Assessment derivation.
# ---------------------------------------------------------------------------

_BLOCKING_KINDS: Final[frozenset[RecoveryCheckKind]] = frozenset(
    {
        RecoveryCheckKind.MIGRATION_HISTORY_INVALID,
        RecoveryCheckKind.SCHEMA_VERSION_NEWER_THAN_SUPPORTED,
        RecoveryCheckKind.EXPECTED_TABLE_MISSING,
    }
)

_EVIDENCE_KINDS: Final[frozenset[RecoveryCheckKind]] = frozenset(
    {
        RecoveryCheckKind.DATABASE_FILE_MISSING,
        RecoveryCheckKind.DATABASE_UNREADABLE,
        RecoveryCheckKind.MIGRATION_METADATA_UNREADABLE,
        RecoveryCheckKind.STORE_READ_FAILED,
        RecoveryCheckKind.INSPECTION_FAULT,
    }
)


def _derive_disposition(issues: tuple[RecoveryIssue, ...]) -> RecoveryDisposition:
    """Derive the closed disposition from detected issue kinds.

    Precedence is deliberate and fail-closed: any known-blocking condition
    dominates; otherwise any evidence gap dominates (so an unreadable store is
    never hidden behind a list of other corrupt rows); otherwise corrupt
    records yield a degraded verdict; otherwise the assessment is healthy.
    """
    kinds = frozenset(issue.kind for issue in issues)
    if kinds & _BLOCKING_KINDS:
        return RecoveryDisposition.BLOCK_STARTUP
    if kinds & _EVIDENCE_KINDS:
        return RecoveryDisposition.INSUFFICIENT_EVIDENCE
    if RecoveryCheckKind.CORRUPT_RECORD in kinds:
        return RecoveryDisposition.DEGRADED_READ_ONLY
    return RecoveryDisposition.HEALTHY


def _assessment(
    issues: tuple[RecoveryIssue, ...],
    suppressed: int,
    probes: tuple[StoreProbeResult, ...],
    notes: tuple[str, ...],
    applied_schema_version: int | None,
    *,
    supported_schema_version: int = _SUPPORTED_SCHEMA_VERSION,
) -> RecoveryAssessment:
    disposition = _derive_disposition(issues)
    return RecoveryAssessment(
        disposition=disposition,
        startup_recommendation=startup_recommendation_for(disposition),
        issues=issues,
        suppressed_issue_count=suppressed,
        probes=probes,
        notes=notes,
        applied_schema_version=applied_schema_version,
        supported_schema_version=supported_schema_version,
    )


def _empty_assessment(
    issues: tuple[RecoveryIssue, ...],
    suppressed: int,
    notes: tuple[str, ...],
    applied_schema_version: int | None,
) -> RecoveryAssessment:
    return _assessment(issues, suppressed, (), notes, applied_schema_version)


# ---------------------------------------------------------------------------
# Inspector.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PersistenceRecoveryInspector:
    """Conservative read-only integrity assessor for durable AgentX state.

    The inspector takes an explicit, canonical :class:`SQLiteDatabase` handle
    (the same factory the stores use); it creates no global state. Content is
    validated exclusively through canonical public store APIs; schema and
    structure checks use read-only ``SELECT`` statements on the canonical
    metadata tables.
    """

    database: SQLiteDatabase
    max_records_per_store: int = _DEFAULT_MAX_RECORDS_PER_STORE
    max_issues: int = _DEFAULT_MAX_ISSUES

    def __post_init__(self) -> None:
        if not isinstance(self.database, SQLiteDatabase):
            raise TypeError("database must be a canonical SQLiteDatabase")
        if not isinstance(self.max_records_per_store, int) or isinstance(
            self.max_records_per_store, bool
        ):
            raise ValueError("max_records_per_store must be an integer")
        if self.max_records_per_store <= 0:
            raise ValueError("max_records_per_store must be a positive integer")
        if not isinstance(self.max_issues, int) or isinstance(self.max_issues, bool):
            raise ValueError("max_issues must be an integer")
        if self.max_issues <= 0:
            raise ValueError("max_issues must be a positive integer")

    # -- Public entry point -------------------------------------------------

    def assess(self) -> RecoveryAssessment:
        """Assess durable state at ``self.database.path`` without repairing it.

        Returns an immutable :class:`RecoveryAssessment`. Storage failures
        fail closed into ``INSUFFICIENT_EVIDENCE``; the inspector never
        returns ``HEALTHY`` after an unexpected storage failure and never
        swallows :class:`KeyboardInterrupt`/:class:`SystemExit`.
        """
        path = self.database.path
        if not path.is_file():
            collector = _IssueCollector(self.max_issues)
            collector.add(
                RecoveryCheckKind.DATABASE_FILE_MISSING,
                detail=f"No database file exists at {path}; there is no durable state to assess",
            )
            notes = (
                "No database file exists. Run this assessment only when durable state is "
                "expected; a legitimate first run has no database yet.",
            )
            return _empty_assessment(
                collector.issues(),
                collector.suppressed,
                notes,
                applied_schema_version=None,
            )

        try:
            connection = self._open_read_connection()
        except (OSError, sqlite3.Error) as exc:
            collector = _IssueCollector(self.max_issues)
            collector.add(
                RecoveryCheckKind.DATABASE_UNREADABLE,
                detail=f"Unable to open database at {path}: {exc}",
            )
            return _empty_assessment(
                collector.issues(),
                collector.suppressed,
                ("The database file exists but could not be opened for reading.",),
                applied_schema_version=None,
            )

        try:
            with connection:
                return self._assess_with_connection(connection)
        except Exception as exc:  # fail closed; BaseException is never caught
            collector = _IssueCollector(self.max_issues)
            collector.add(
                RecoveryCheckKind.INSPECTION_FAULT,
                detail=f"Recovery inspection failed unexpectedly: {type(exc).__name__}: {exc}",
            )
            return _empty_assessment(
                collector.issues(),
                collector.suppressed,
                ("The inspection itself failed; durable state could not be assessed.",),
                applied_schema_version=None,
            )

    def _open_read_connection(self) -> sqlite3.Connection:
        """Open one read-only-assessment connection without touching schema.

        Deliberately does NOT run the canonical connection PRAGMAs or
        migrations: the inspector must not mutate the database (in particular
        ``PRAGMA journal_mode`` writes the database header, so it is never
        executed here). ``PRAGMA query_only`` makes any accidental write fail
        on this connection.
        """
        connection = sqlite3.connect(
            str(self.database.path),
            timeout=self.database.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    # -- Migration + structure assessment -----------------------------------

    def _assess_with_connection(self, connection: sqlite3.Connection) -> RecoveryAssessment:
        collector = _IssueCollector(self.max_issues)
        notes: list[str] = []

        metadata_exists = self._table_exists(connection, _MIGRATION_TABLE)
        if metadata_exists is None:
            collector.add(
                RecoveryCheckKind.DATABASE_UNREADABLE,
                detail="Unable to read sqlite_schema metadata",
            )
            return _empty_assessment(
                collector.issues(),
                collector.suppressed,
                ("Database schema metadata could not be read.",),
                applied_schema_version=None,
            )

        if not metadata_exists:
            # A database with no migration metadata is either a fresh empty
            # database or one whose migration table was destroyed while store
            # tables remain. The canonical engine cannot migrate the latter;
            # the former is the legitimate pre-first-open state.
            store_tables_present = self._canonical_store_tables_present(connection)
            if store_tables_present is None:
                collector.add(
                    RecoveryCheckKind.DATABASE_UNREADABLE,
                    detail="Unable to read sqlite_schema metadata",
                )
                return _empty_assessment(
                    collector.issues(),
                    collector.suppressed,
                    ("Database schema metadata could not be read.",),
                    applied_schema_version=None,
                )
            if store_tables_present:
                collector.add(
                    RecoveryCheckKind.MIGRATION_HISTORY_INVALID,
                    detail=(
                        "Migration metadata table is missing while canonical store tables "
                        "exist; the canonical engine cannot migrate this database"
                    ),
                )
                return _empty_assessment(
                    collector.issues(),
                    collector.suppressed,
                    (),
                    applied_schema_version=0,
                )
            notes.append(
                "Database file exists but no AgentX schema is applied; the canonical "
                "first connection will apply all migrations as normal startup behavior."
            )
            return _assessment(
                collector.issues(),
                collector.suppressed,
                (),
                tuple(notes),
                applied_schema_version=0,
            )

        applied = self._assess_migration_history(connection, collector)
        if applied is None:
            return _empty_assessment(
                collector.issues(),
                collector.suppressed,
                tuple(notes),
                applied_schema_version=None,
            )
        if applied > _SUPPORTED_SCHEMA_VERSION:
            collector.add(
                RecoveryCheckKind.SCHEMA_VERSION_NEWER_THAN_SUPPORTED,
                detail=(
                    f"Applied schema version {applied} is newer than supported version "
                    f"{_SUPPORTED_SCHEMA_VERSION}"
                ),
            )
            return _empty_assessment(
                collector.issues(),
                collector.suppressed,
                ("Database schema is from a newer build; store content was not assessed.",),
                applied_schema_version=applied,
            )

        self._assess_expected_structure(connection, applied, collector)
        if applied < _SUPPORTED_SCHEMA_VERSION:
            notes.append(
                f"Applied schema version {applied} is older than supported version "
                f"{_SUPPORTED_SCHEMA_VERSION}; store content probes are deferred because "
                "this assessment is read-only and the canonical connection applies pending "
                "migrations as normal startup behavior."
            )
            return _assessment(
                collector.issues(),
                collector.suppressed,
                (),
                tuple(notes),
                applied_schema_version=applied,
            )

        probes = self._probe_stores(connection, collector)
        return _assessment(
            collector.issues(),
            collector.suppressed,
            probes,
            tuple(notes),
            applied_schema_version=applied,
        )

    def _assess_migration_history(
        self, connection: sqlite3.Connection, collector: _IssueCollector
    ) -> int | None:
        """Validate migration history exactly as the canonical engine would.

        Returns the applied schema version, or ``None`` when the history is
        unreadable or invalid (the matching issue was already added).
        """
        try:
            rows = connection.execute(
                f"SELECT version, name FROM {_MIGRATION_TABLE} ORDER BY version"
            ).fetchall()
        except sqlite3.Error as exc:
            collector.add(
                RecoveryCheckKind.MIGRATION_METADATA_UNREADABLE,
                detail=f"Unable to read migration metadata: {exc}",
            )
            return None

        if not rows:
            collector.add(
                RecoveryCheckKind.MIGRATION_HISTORY_INVALID,
                detail="Migration metadata table exists but contains no migration records",
            )
            return None

        try:
            versions = [int(row["version"]) for row in rows]
        except (TypeError, ValueError):
            collector.add(
                RecoveryCheckKind.MIGRATION_HISTORY_INVALID,
                detail="Migration metadata contains a non-integer version",
            )
            return None

        applied = versions[-1]
        expected = list(range(1, applied + 1))
        if versions != expected:
            collector.add(
                RecoveryCheckKind.MIGRATION_HISTORY_INVALID,
                detail=(
                    f"Migration metadata contains a non-consecutive version history: {versions}"
                ),
            )
            return None

        expected_by_version = {
            entry.version: entry.name
            for entry in _MIGRATION_REGISTRY
            if entry.version <= _SUPPORTED_SCHEMA_VERSION
        }
        for row in rows:
            version = int(row["version"])
            if version > _SUPPORTED_SCHEMA_VERSION:
                continue
            expected_name = expected_by_version.get(version)
            recorded_name = str(row["name"])
            if expected_name is not None and recorded_name != expected_name:
                collector.add(
                    RecoveryCheckKind.MIGRATION_HISTORY_INVALID,
                    store="persistence",
                    detail=(
                        f"Migration metadata mismatch at version {version}: database has "
                        f"{recorded_name!r}, code expects {expected_name!r}"
                    ),
                )
                return None
        return applied

    def _assess_expected_structure(
        self, connection: sqlite3.Connection, applied: int, collector: _IssueCollector
    ) -> None:
        """Verify every table the applied schema version owns is present."""
        expected_tables: set[str] = set()
        for entry in _MIGRATION_REGISTRY:
            if entry.version <= applied:
                expected_tables.update(entry.tables)
        for table in sorted(expected_tables):
            present = self._table_exists(connection, table)
            if present is True:
                continue
            store = _OWNER_BY_TABLE.get(table, "persistence")
            if present is None:
                collector.add(
                    RecoveryCheckKind.DATABASE_UNREADABLE,
                    detail=f"Unable to read sqlite_schema metadata while checking {table!r}",
                )
                continue
            collector.add(
                RecoveryCheckKind.EXPECTED_TABLE_MISSING,
                store=store,
                detail=(
                    f"Expected table {table!r} (owned by {store}) is missing at "
                    f"applied schema version {applied}"
                ),
            )

    # -- Store content probes ------------------------------------------------

    def _probe_stores(
        self,
        connection: sqlite3.Connection,
        collector: _IssueCollector,
    ) -> tuple[StoreProbeResult, ...]:
        """Probe each canonical store through its public API, bounded."""
        probes: list[StoreProbeResult] = []
        journal = EventJournal(self.database)
        episodes = EpisodeStore(self.database)
        artifacts = ArtifactStore(self.database)
        audit = AuditStore(self.database)
        negative = NegativeExperienceStore(self.database)
        knowledge = KnowledgeStore(self.database)
        procedures = ProcedureStore(self.database)

        def probe_sequence(
            *,
            store_label: str,
            table: str,
            read: Callable[..., tuple[object, ...]],
            corruption_types: tuple[type[Exception], ...],
            storage_types: tuple[type[Exception], ...],
            identity_attr: str,
        ) -> None:
            present = self._table_exists(connection, table)
            if present is None:
                collector.add(
                    RecoveryCheckKind.DATABASE_UNREADABLE,
                    detail=f"Unable to read sqlite_schema metadata while checking {table!r}",
                )
                return
            if not present:
                return
            probes.append(
                self._probe_sequence_store(
                    store_label=store_label,
                    rows_present=self._count_rows(connection, table),
                    read=read,
                    corruption_types=corruption_types,
                    storage_types=storage_types,
                    identity_attr=identity_attr,
                    collector=collector,
                )
            )

        def probe_id_store(
            *,
            table: str,
            probe: Callable[[int | None], StoreProbeResult],
        ) -> None:
            present = self._table_exists(connection, table)
            if present is None:
                collector.add(
                    RecoveryCheckKind.DATABASE_UNREADABLE,
                    detail=f"Unable to read sqlite_schema metadata while checking {table!r}",
                )
                return
            if not present:
                return
            probes.append(probe(self._count_rows(connection, table)))

        probe_sequence(
            store_label="event_journal",
            table="agentx_event_journal",
            read=journal.read,
            corruption_types=(CorruptJournalEntryError,),
            storage_types=(EventJournalStorageError,),
            identity_attr="event_id",
        )
        probe_id_store(
            table="agentx_knowledge",
            probe=lambda rows_present: self._probe_knowledge_store(
                rows_present=rows_present, store=knowledge, collector=collector
            ),
        )
        probe_id_store(
            table="agentx_procedures",
            probe=lambda rows_present: self._probe_procedure_store(
                rows_present=rows_present, store=procedures, collector=collector
            ),
        )
        probe_sequence(
            store_label="episode_store",
            table="agentx_episodes",
            read=episodes.read,
            corruption_types=(CorruptEpisodeError,),
            storage_types=(EpisodeStoreStorageError,),
            identity_attr="episode_id",
        )
        probe_sequence(
            store_label="artifact_store",
            table="agentx_artifacts",
            read=artifacts.read,
            corruption_types=(CorruptArtifactRecordError,),
            storage_types=(ArtifactStoreStorageError,),
            identity_attr="artifact_id",
        )
        probe_sequence(
            store_label="audit_store",
            table="agentx_audit_records",
            read=audit.read,
            corruption_types=(CorruptAuditRecordError,),
            storage_types=(AuditStoreStorageError,),
            identity_attr="audit_id",
        )
        probe_sequence(
            store_label="negative_experience_store",
            table="agentx_negative_experiences",
            read=negative.read,
            corruption_types=(CorruptNegativeExperienceError,),
            storage_types=(NegativeExperienceStoreStorageError,),
            identity_attr="negative_experience_id",
        )
        return tuple(probes)

    def _probe_sequence_store(
        self,
        *,
        store_label: str,
        rows_present: int | None,
        read: Callable[..., tuple[object, ...]],
        corruption_types: tuple[type[Exception], ...],
        storage_types: tuple[type[Exception], ...],
        identity_attr: str,
        collector: _IssueCollector,
    ) -> StoreProbeResult:
        """Windowed canonical probe over an append-ordered store.

        Rows are visited in ascending durable sequence order, one canonical
        read at a time. A corrupt row whose durable sequence is still readable
        is reported and skipped so scanning can continue past it; a corrupt
        row whose identity is unreadable stops the probe (reported honestly)
        because the canonical API cannot address it.
        """
        attempts = 0
        checked = 0
        cursor = 0
        note: str | None = None
        aborted = False
        exhausted = False

        while attempts < self.max_records_per_store:
            try:
                entries = read(after_sequence=cursor, limit=1)
            except Exception as exc:
                if isinstance(exc, corruption_types):
                    attempts += 1
                    sequence = getattr(exc, "sequence", 0)
                    raw_identity = getattr(exc, identity_attr, None)
                    identity = raw_identity if isinstance(raw_identity, str) else None
                    if (
                        isinstance(sequence, int)
                        and not isinstance(sequence, bool)
                        and (sequence > cursor)
                    ):
                        collector.add(
                            RecoveryCheckKind.CORRUPT_RECORD,
                            store=store_label,
                            identity=identity,
                            detail=str(exc),
                        )
                        cursor = sequence
                        continue
                    collector.add(
                        RecoveryCheckKind.CORRUPT_RECORD,
                        store=store_label,
                        identity=identity,
                        detail=f"{exc} (durable identity unreadable)",
                    )
                    aborted = True
                    note = (
                        f"Probe stopped after sequence {cursor}: a corrupt row has an "
                        "unreadable durable identity; remaining rows were not assessed"
                    )
                    break
                if isinstance(exc, storage_types):
                    collector.add(
                        RecoveryCheckKind.STORE_READ_FAILED,
                        store=store_label,
                        detail=str(exc),
                    )
                    aborted = True
                    note = (
                        "Canonical store read failed at the storage layer; content was "
                        "not fully assessed"
                    )
                    break
                if isinstance(exc, (ValueError, TypeError)):
                    collector.add(
                        RecoveryCheckKind.CORRUPT_RECORD,
                        store=store_label,
                        detail=(
                            f"Canonical decode raised {type(exc).__name__} for a row at or "
                            f"after sequence {cursor}; row identity is unreadable"
                        ),
                    )
                    aborted = True
                    note = (
                        f"Probe stopped after sequence {cursor}: a corrupt row with an "
                        "unreadable durable identity; remaining rows were not assessed"
                    )
                    break
                raise
            if not entries:
                exhausted = True
                break
            attempts += 1
            checked += 1
            entry = entries[0]
            sequence = getattr(entry, "sequence", 0)
            if isinstance(sequence, int) and not isinstance(sequence, bool) and sequence > cursor:
                cursor = sequence
            else:
                aborted = True
                note = (
                    f"Probe stopped at sequence {cursor}: a returned row has no usable "
                    "durable sequence; remaining rows were not assessed"
                )
                break

        return self._finish_sequence_result(
            store_label, rows_present, checked, attempts, exhausted, aborted, note
        )

    def _probe_knowledge_store(
        self,
        *,
        rows_present: int | None,
        store: KnowledgeStore,
        collector: _IssueCollector,
    ) -> StoreProbeResult:
        """Probe knowledge rows through canonical ``KnowledgeStore.get``."""
        raw_ids = self._enumerate_text_ids(
            "agentx_knowledge", "knowledge_id", order_columns="created_at_utc, knowledge_id"
        )
        checked = 0
        attempts = 0
        aborted = False
        note: str | None = None
        for raw_id in raw_ids:
            if attempts >= self.max_records_per_store:
                break
            attempts += 1
            try:
                knowledge_id = KnowledgeId.parse(raw_id)
            except ValueError:
                collector.add(
                    RecoveryCheckKind.CORRUPT_RECORD,
                    store="knowledge_store",
                    identity=raw_id,
                    detail=f"Stored knowledge_id {raw_id!r} is not a canonical KnowledgeId",
                )
                continue
            try:
                record = store.get(knowledge_id)
            except CorruptKnowledgeRecordError as exc:
                identity = getattr(exc, "knowledge_id", None)
                collector.add(
                    RecoveryCheckKind.CORRUPT_RECORD,
                    store="knowledge_store",
                    identity=identity if isinstance(identity, str) else None,
                    detail=str(exc),
                )
                continue
            except KnowledgeStoreStorageError as exc:
                collector.add(
                    RecoveryCheckKind.STORE_READ_FAILED,
                    store="knowledge_store",
                    detail=str(exc),
                )
                aborted = True
                note = (
                    "Canonical store read failed at the storage layer; content was "
                    "not fully assessed"
                )
                break
            if record is not None:
                checked += 1
        return self._finish_sequence_result(
            "knowledge_store", rows_present, checked, attempts, False, aborted, note
        )

    def _probe_procedure_store(
        self,
        *,
        rows_present: int | None,
        store: ProcedureStore,
        collector: _IssueCollector,
    ) -> StoreProbeResult:
        """Probe procedure rows through canonical ``ProcedureStore.get``."""
        pairs = self._enumerate_procedure_pairs()
        checked = 0
        attempts = 0
        aborted = False
        note: str | None = None
        for raw_procedure_id, revision in pairs:
            if attempts >= self.max_records_per_store:
                break
            attempts += 1
            try:
                procedure_id = ProcedureId.parse(raw_procedure_id)
            except ValueError:
                collector.add(
                    RecoveryCheckKind.CORRUPT_RECORD,
                    store="procedure_store",
                    identity=raw_procedure_id,
                    detail=(
                        f"Stored procedure_id {raw_procedure_id!r} is not a canonical ProcedureId"
                    ),
                )
                continue
            try:
                record = store.get(procedure_id, revision)
            except CorruptProcedureRecordError as exc:
                procedure_identity = getattr(exc, "procedure_id", None)
                identity: str | None = None
                if isinstance(procedure_identity, str):
                    identity = (
                        f"procedure {procedure_identity} revision {getattr(exc, 'revision', '?')}"
                    )
                collector.add(
                    RecoveryCheckKind.CORRUPT_RECORD,
                    store="procedure_store",
                    identity=identity,
                    detail=str(exc),
                )
                continue
            except ProcedureStoreStorageError as exc:
                collector.add(
                    RecoveryCheckKind.STORE_READ_FAILED,
                    store="procedure_store",
                    detail=str(exc),
                )
                aborted = True
                note = (
                    "Canonical store read failed at the storage layer; content was "
                    "not fully assessed"
                )
                break
            if record is not None:
                checked += 1
        return self._finish_sequence_result(
            "procedure_store", rows_present, checked, attempts, False, aborted, note
        )

    def _finish_sequence_result(
        self,
        store_label: str,
        rows_present: int | None,
        checked: int,
        attempts: int,
        exhausted: bool,
        aborted: bool,
        note: str | None,
    ) -> StoreProbeResult:
        """Derive one probe result with honest completion semantics.

        Completion requires either a proven-exhausted scan or (when the row
        count is known) having visited every counted row. A scan truncated by
        the per-store bound never claims full coverage.
        """
        if aborted:
            return StoreProbeResult(store_label, rows_present or 0, checked, False, note)
        if exhausted:
            return StoreProbeResult(store_label, rows_present or 0, checked, True, note)
        if rows_present is None:
            return StoreProbeResult(
                store_label,
                0,
                checked,
                False,
                note=(
                    f"Bounded scan: first {attempts} rows assessed and the row count was "
                    f"unreadable (per-store bound {self.max_records_per_store}); remaining "
                    "rows were not assessed"
                ),
            )
        if attempts >= rows_present:
            return StoreProbeResult(store_label, rows_present, checked, True, note)
        return StoreProbeResult(
            store_label,
            rows_present,
            checked,
            False,
            note=(
                f"Bounded scan: first {attempts} of {rows_present} rows assessed "
                f"(per-store bound {self.max_records_per_store}); remaining rows were "
                "not assessed"
            ),
        )

    # -- Read-only raw helpers -----------------------------------------------

    def _enumerate_text_ids(
        self, table: str, id_column: str, *, order_columns: str
    ) -> tuple[str, ...]:
        """Enumerate identity columns (never payload bytes) within the bound."""
        limit = self.max_records_per_store
        with self._read_connection() as connection:
            rows = connection.execute(
                f"SELECT {id_column} FROM {table} ORDER BY {order_columns} ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(str(row[id_column]) for row in rows)

    def _enumerate_procedure_pairs(self) -> tuple[tuple[str, int], ...]:
        """Enumerate ``(procedure_id, revision)`` primary keys within the bound."""
        limit = self.max_records_per_store
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT procedure_id, revision FROM agentx_procedures "
                "ORDER BY procedure_id ASC, revision ASC LIMIT ?",
                (limit,),
            ).fetchall()
        pairs: list[tuple[str, int]] = []
        for row in rows:
            revision = row["revision"]
            if isinstance(revision, int) and not isinstance(revision, bool):
                pairs.append((str(row["procedure_id"]), revision))
        return tuple(pairs)

    def _count_rows(self, connection: sqlite3.Connection, table: str) -> int | None:
        """Return the table row count; ``None`` when the count is unreadable."""
        try:
            row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        value = row[0]
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return None

    def _table_exists(self, connection: sqlite3.Connection, table: str) -> bool | None:
        """Return whether *table* exists; ``None`` when sqlite_schema is unreadable."""
        try:
            row = connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
        except sqlite3.Error:
            return None
        return row is not None

    def _canonical_store_tables_present(self, connection: sqlite3.Connection) -> bool | None:
        """Return whether any canonical store table exists (metadata absent case)."""
        names = tuple(owner.table for owner in _TABLE_OWNERS if owner.table != _MIGRATION_TABLE)
        placeholders = ",".join("?" for _ in names)
        try:
            row = connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE type = 'table' "
                f"AND name IN ({placeholders}) LIMIT 1",
                names,
            ).fetchone()
        except sqlite3.Error:
            return None
        return row is not None

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a short-lived read-only assessment connection."""
        connection = sqlite3.connect(
            str(self.database.path),
            timeout=self.database.busy_timeout_ms / 1_000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        try:
            yield connection
        finally:
            connection.close()
