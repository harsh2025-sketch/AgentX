# Durable State Recovery & Integrity Assessment (M6.03)

`agentx.infrastructure.recovery` provides a **conservative, read-only
assessment** of the AgentX SQLite durable state, performed before the
canonical runtime connects to it. It never repairs, rewrites, deletes, or
migrates anything. If durable state is unhealthy it says so, with the
evidence; it does not fix the state itself.

This document covers M6.03: what the assessor checks, its closed
vocabularies, exactly what it promises (and what it does not), and how the
tests prove it.

---

## 1. What it is

`PersistenceRecoveryInspector` is the single public entry point. It is
constructed with an explicit, injected `SQLiteDatabase` handle (no global
state, no singleton, no environment lookup):

```python
from agentx.infrastructure.persistence import SQLiteDatabase
from agentx.infrastructure.recovery import PersistenceRecoveryInspector

database = SQLiteDatabase(Path("agentx.sqlite3"))
assessment = PersistenceRecoveryInspector(
    database,
    max_records_per_store=250,  # bounded scan limit per store
    max_issues=100,  # cap on reported issues
).assess()

print(assessment.disposition)  # e.g. RecoveryDisposition.HEALTHY
print(assessment.startup_recommendation)
```

An assessment returns an immutable `RecoveryAssessment`:

| Field | Meaning |
| --- | --- |
| `disposition` | Overall health verdict (closed vocabulary, below) |
| `startup_recommendation` | Mapped from the disposition (closed vocabulary) |
| `applied_schema_version` | Highest version in `agentx_schema_migrations`, `None` if unreadable |
| `supported_schema_version` | Version this build of the assessor understands |
| `issues` | Sorted, capped tuple of `RecoveryIssue` |
| `suppressed_issue_count` | Number of issues beyond `max_issues` not listed |
| `notes` | Human-readable context that is never a health claim |
| `probes` | One `StoreProbeResult` per readable store that was scanned |

### Closed vocabularies

`RecoveryDisposition` (verdict):

| Disposition | Meaning |
| --- | --- |
| `HEALTHY` | Every check that applies to this database passed. Scope caveats below. |
| `DEGRADED_READ_ONLY` | All checks ran; at least one stored record failed canonical decode. Nothing was changed. |
| `BLOCK_STARTUP` | Migration history or required structure is invalid, or schema is newer than supported. |
| `INSUFFICIENT_EVIDENCE` | The database could not be opened/read; no verdict possible. |

`StartupRecommendation` (what a caller should do next):

| Recommendation | Meaning |
| --- | --- |
| `CONTINUE` | Healthy; connect normally. |
| `CONTINUE_DEGRADED` | Connect normally but treat affected stores as read-only/erroring per their canonical `Corrupt*Error`s. |
| `STOP_AND_ESCALATE` | Do not open for normal operation; escalate to an operator. |

`RecoveryCheckKind` (per-issue vocabulary, sorted output):

`database.file_missing`, `database.unreadable`,
`migration.metadata_unreadable`, `migration.history_invalid`,
`migration.schema_newer_than_supported`, `structure.expected_table_missing`,
`store.read_failed`, `store.corrupt_record`, `inspection.fault`.

### What HEALTHY means (scope)

`HEALTHY` means: the schema-migration history is complete and canonical for
this build, the schema's own tables are reachable, and every bounded scan
completed **with a readable row count** — each row it covered decoded
through the canonical store's own parser. It is not proof that:

- bytes not yet covered by the bounded scan are valid (see bounds below), or
- the database will never produce an error later (e.g. `query_only`-mode
  errors that only appear under writes), or
- application-level semantic health (contradictions, supersessions,
  duplicated facts) is sound. AgentX already has separate machinery for
  knowledge integrity; M6.03 does not duplicate it.

Every assessment records in `notes` which of these limits applied.

---

## 2. What it checks

### 2.1 Migration history integrity (always, if readable)

A module-local mirror of the canonical migration plan
(`_MIGRATION_REGISTRY`, kept in sync with
`agentx.infrastructure.persistence._MIGRATIONS` by an architecture test)
defines the expected `(version, name)` history and the tables each version
creates.

- **Fresh database** (file exists, zero bytes / no tables, no metadata
  table): `HEALTHY` with note that the first canonical connection will apply
  all migrations (`applied_schema_version == 0`).
- **History must be exactly `[1..N]`** with canonical names, where `N` is the
  highest applied version. Missing versions, renames, or an empty history →
  `migration.history_invalid` → `BLOCK_STARTUP`. An orphaned store table with
  no metadata table at all → `migration.history_invalid`.
- **Applied version `> supported`**: `migration.schema_newer_than_supported`
  → `BLOCK_STARTUP`; no content probes are attempted (a future schema is
  unknown territory).
- **Applied version `< supported`**: report it and check only structure
  (tables present for applied versions); content probes are deferred with a
  note — this database still needs canonical migration, which the assessor
  never performs.
- Metadata table unreadable → `migration.metadata_unreadable` →
  `INSUFFICIENT_EVIDENCE`.

The canonical engine agrees in every direction; tests assert
`UnsupportedSchemaVersionError`/`MigrationError` from the real
`SQLiteDatabase.connection()` wherever the assessor says `BLOCK_STARTUP`.

### 2.2 Store integrity (content probes, only when applied == supported)

Content probes run **only** at `applied == supported` (the schema the stores
know). Each probe requires the store's owning table to exist; a missing
table is `structure.expected_table_missing` → `BLOCK_STARTUP` for that store
and the probe is skipped, while every other store is still probed.

Seven canonical stores are covered through their canonical public APIs only:

| Store | Probe strategy |
| --- | --- |
| `event_journal` | `scan(limit=max_records)` ascending durable order |
| `episode_store` | `scan(limit=max_records)` ascending durable order |
| `knowledge_store` | primary-key enumeration + `get` per key |
| `procedure_store` | primary-key enumeration + `get` per `(procedure_id, revision)` |
| `artifact_store` | `scan(limit=max_records)` ascending durable order |
| `audit_store` | `scan(limit=max_records)` ascending durable order |
| `negative_experience_store` | `scan(limit=max_records)` ascending durable order |

A row that makes the canonical parser raise the store's own
`Corrupt*Error` is reported as `store.corrupt_record` with the row's durable
identity (the row's durable id — `event_id`, `episode_id`, `knowledge_id`,
`artifact_id`, `audit_id`, `negative_experience_id`, or `procedure <id>
revision <n>`). Corrupt rows
are **not** counted as checked; they never become "missing", never become
"healthy", and are never rewritten or deleted. Their raw bytes stay in the
database, byte for byte, and the tests prove it across restarts.

### 2.3 Bounded scans

`max_records_per_store` (default 250) caps how many rows any probe decodes.
The `StoreProbeResult` is honest about coverage:

| Field | Meaning |
| --- | --- |
| `store` | Canonical store name |
| `rows_present` | Raw `COUNT(*)` of the store's table; `0` when the table is empty **or** the count was unreadable — an unreadable count is never treated as zero rows (`scan_completed` is forced to `False` with an explanatory `note`) |
| `rows_checked` | Rows actually decoded successfully through canonical APIs |
| `scan_completed` | `True` only when coverage is proven: the scan reached the durable end or visited every counted row |
| `note` | When/why the scan stopped short |

Completion rules (fail-closed): an aborted probe is incomplete; a probe
reaching the durable end is complete; a probe that hits the cap while more
rows provably exist is incomplete and says so (`"first N of M rows assessed
– bounded by max_records_per_store"`); and a probe whose row count could not
be read never claims completion, even if it decoded `N` rows. If the scan
limit is hit **and** the row count is unreadable, the probe says the row
count was unreadable instead of guessing. Corrupt rows discovered in the
covered prefix always surface as issues even when the scan is incomplete.

### 2.4 Deterministic, capped, neutral output

- Issues are sorted by `(store, kind, identity, detail)` so two databases
  with identical content produce identical assessments regardless of
  insertion order or physical row order.
- `identity` is capped at 128 chars, `detail` at 240 chars; a
  `suppressed_issue_count` carries the overflow beyond `max_issues`.
- Stored strings are data. Nothing in a hostile string — SQL, Python,
  prompt-injection, fake `"database healthy"`/`"verified=true"`/`"permission=
  ADMIN"` claims — can execute, clear a real issue, or appear as a health
  claim in output (hostile content is still surfaced in capped `detail` text
  when describing a *corrupt* row, because operators need the evidence).

---

## 3. Fail-closed on unexpected I/O

Every unexpected failure — file missing, unreadable file, unreadable
metadata, unreadable row count, a store call raising something other than
its documented `Corrupt*Error`, an internal fault — is recorded under
`database.file_missing`, `database.unreadable`, `migration.metadata_unreadable`,
`store.read_failed`, or `inspection.fault`, and resolves to
`INSUFFICIENT_EVIDENCE` → `STOP_AND_ESCALATE`. The assessor never maps
"couldn't read" to "healthy" or "empty".

---

## 4. Constraints honored (M6.03)

- **Read-only.** The inspector opens short-lived connections with
  `PRAGMA query_only = ON`; it executes only `SELECT`/`PRAGMA` statements and
  performs no migration application. No background threads, no global state,
  no singleton, no startup composition-root changes.
- **Zero destructive recovery.** No `DELETE`/`UPDATE`/`REWRITE`/`VACUUM`/
  `DROP`/`ALTER`/`INSERT` against the durable state, ever. An architecture
  test statically forbids destructive SQL vocabulary in executed statements
  and forbids `exec`/`eval`/`compile`/`commit`/`rollback`/`executescript`.
- **Canonical stores untouched.** The existing store modules and
  `persistence.py` are unchanged; the assessor only calls their public
  contracts.
- **Corrupt ≠ missing ≠ healthy.** Three distinct outcomes, all observable
  in `issues` and `probes`.
- **Architecture.** `infrastructure.recovery` imports only stdlib plus
  canonical infrastructure store/database contracts and `agentx.core.ids`
  (for typed identity parsing). No kernel, capabilities, cognition,
  learning, hive, procedures-execution, or model/research imports. An
  architecture test proves it by parsing the module.
- **No migrations added, no migration numbering altered.**
- **`REPEATED_ASSESSMENT_IS_STABLE`:** assessing twice is side-effect free;
  the second assessment equals the first and the bytes on disk are unchanged.

---

## 5. Known limitations (by design)

1. **Assessment only.** M6.03 deliberately provides no repair path.
   `DEGRADED_READ_ONLY`/`BLOCK_STARTUP` results require operator action.
2. **Bounded coverage.** Stores larger than `max_records_per_store` are
   honestly reported as partially scanned; a corrupt row beyond the covered
   prefix will be found by raising the limit, not by this run.
3. **One-shot, not continuous.** The inspector is a pre-connect assessment;
   it does not monitor a live database.
4. **Schema mirror must be maintained.** When the canonical plan gains
   migration v9+, `_MIGRATION_REGISTRY` and `_SUPPORTED_SCHEMA_VERSION` must
   be updated; `tests/architecture/test_persistence_recovery_boundaries.py`
   fails until they are, forcing a conscious decision.
5. **Semantic integrity is out of scope.** Knowledge contradictions,
   supersession graphs, and cross-store referential consistency are separate
   concerns with their own canonical machinery.
6. **Concurrent writers.** If another process writes between probes, a
   scan can observe a torn snapshot; this is why dispositions are advisory
   and why an operator gates on the recommendation.

---

## 6. Test suite

| File | Proves |
| --- | --- |
| `tests/unit/test_persistence_recovery.py` | Dispositions and vocabulary on real temp databases; fresh/migrated/newer/gapped/renamed history; missing tables; each store's corrupt-row detection with no rewrite/delete; bound caps; issue caps; deterministic ordering across insertion orders; immutability; repeated-assessment stability; hostile strings; missing/unreadable files; constructor validation; closed recommendation mapping. |
| `tests/integration/test_persistence_recovery.py` | Full lifecycle against real temp SQLite + canonical stores: write → close → reopen → assess healthy; multi-store tamper → `DEGRADED_READ_ONLY` with identity evidence that survives restarts while canonical stores still fail closed on the corrupt rows and healthy siblings stay reachable; tampered migration history blocks after restart and stays tampered on disk. |
| `tests/adversarial/test_persistence_recovery_adversarial.py` | SQL-injection, Python-looking, prompt-injection, fake health/permission/verification strings are inert data; malformed JSON, unsupported enums, and unexpected record schema versions are corruption; unexpected schema-migration versions block startup; nothing destructive happens, no markers appear in output. |
| `tests/architecture/test_persistence_recovery_boundaries.py` | Import allowlist; static proof that executed SQL is `SELECT`/`PRAGMA` only and contains no destructive vocabulary; no `exec`/`eval`/`compile`/`commit`/`rollback`; closed vocabularies; migration mirror in sync with canonical plan; import side-effect free. |

All tests tamper only via raw SQL in test code (the states cannot be produced
through canonical APIs) and then prove **no autocorrection**: bytes on disk
before and after an assessment are identical.
