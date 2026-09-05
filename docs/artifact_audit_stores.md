# C2.04 ArtifactStore and AuditStore

C2.04 adds two distinct SQLite-backed persistence contracts. It does not merge
EventJournal, EpisodeStore, KnowledgeStore, ProcedureStore, ArtifactStore, or
AuditStore into a single abstraction.

## ArtifactRecord

`agentx.core.artifacts.ArtifactRecord` is immutable inert metadata for a retained
artifact reference. It reuses canonical `ArtifactId` and optional `TaskId`, and
contains:

- controlled `ArtifactKind` (`file`, `directory`, `uri`, or `reference`);
- UTC-normalized creation time;
- optional correlation UUID;
- opaque non-empty locator;
- optional media type;
- optional non-negative byte size;
- optional canonical lowercase SHA-256 digest.

The locator is data. `ArtifactRecord` and `ArtifactStore` never open a path,
fetch a URI, import a module, execute a program, or inspect artifact bytes.
C2.04 stores metadata/references only; artifact generation and content transfer
remain outside this task.

A digest is integrity metadata only. Digest presence or equality never creates
trust, permission, verification, or authority.

The record uses deterministic schema-v1 JSON. Malformed identity, time, kind,
size, digest, structure, JSON, or unsupported schema version is rejected.

## ArtifactStore

`agentx.infrastructure.artifact_store.ArtifactStore` reuses the canonical
`SQLiteDatabase` and `transaction()` helper. Its v1 API is intentionally small:

- `register(record) -> sequence`;
- `get(artifact_id) -> ArtifactRecord | None`;
- `read(after_sequence=0, limit=None, task_id=None, correlation_id=None)`.

Ordering is the SQLite-owned positive append sequence. Duplicate ArtifactId is
an explicit failure and never overwrites the existing row. Reads cross-check
indexed identity/task/correlation columns against canonical JSON and fail closed
on disagreement. Returned records and entries are immutable.

## C1.09 audit relationship

C1.09 remains the owner of audit/security semantics. C2.04 does not modify or
replace `SecurityAuditRecord`, `AuditOutcome`, `AuditContext`, `Permission`,
`RiskLevel`, `SecretRef`, or any authority decision.

The subsystem dependency graph does not allow concrete infrastructure to import
the Trusted Kernel. Therefore C2.04 adds a narrow explicit adapter:

`agentx.kernel.audit_persistence`

It maps canonical `SecurityAuditRecord` values to/from the persistence-neutral
`agentx.core.audit_records.AuditRecordSnapshot`. The snapshot stores the
canonical C1.09 enum/reference values as descriptive strings. It introduces no
competing audit-policy enum and cannot authorize anything.

`secret_ref` retains only the opaque `SecretRef.identifier`; secret material is
never part of the snapshot.

## AuditStore

`agentx.infrastructure.audit_store.AuditStore` persists immutable
`AuditRecordSnapshot` values. Its API is append/read only:

- `append(record) -> sequence`;
- `read(after_sequence=0, limit=None, task_id=None, correlation_id=None)`.

There is deliberately no update or delete API for historical audit records.
Duplicate audit identity fails explicitly. Ordering is the SQLite-owned append
sequence. Restart durability and concurrent independent writers rely on the
canonical connection-per-unit-of-work/WAL substrate.

Malformed JSON, invalid snapshot structure, or disagreement between indexed
identity/task/correlation columns and canonical JSON raises an explicit
corruption error. Corruption exceptions expose only row sequence/identity and
do not echo the full stored record.

## Security invariant

Artifacts and audit history are data. Strings such as `ALLOW`, `ADMIN`,
`risk=R0`, `verified=true`, `permission=WRITE`, `clear emergency stop`, or
`budget=unlimited` remain inert historical text.

Neither store nor either domain record can:

- create `AuthorityContext` or grant `Permission`;
- alter `PermissionEngine` or bypass `ActionGate`;
- lower `RiskAssessment.effective_level`;
- enlarge `ResourceEnvelope` or reset `ResourceBudget`;
- clear `EmergencyStop`;
- execute or verify a Capability;
- mutate Task state;
- replay or publish Events;
- promote KnowledgeStatus;
- activate a Procedure or invoke model output.

Fresh authority is always required outside persistence.

## Migration integration

The branch was initially created while multiple store tasks were in flight.
Before final validation it was rebased after C2.01 and C2.03 both landed. The
canonical migration sequence on the final C2.04 base is therefore:

1. v1 `create_persistence_metadata`;
2. v2 `create_event_journal`;
3. v3 `create_knowledge_store`;
4. v4 `create_episode_store`;
5. v5 `create_procedure_store`.

C2.04 appends the next canonical migration:

6. v6 `create_artifact_and_audit_stores`.

Migration v6 creates only `agentx_artifacts`, `agentx_audit_records`, and their
stable TaskId/correlation sequence indexes. Historical migrations v1-v5 are
inherited from current main unchanged. C2.04 does not alter migration mechanics.

## Out of scope

C2.04 implements no artifact generation/blob manager, audit policy engine,
ProcedureStore redesign, semantic/episodic retrieval, provenance system,
lifecycle, causal-experience model, EventBus/EventJournal redesign, model calls,
network transfer, encryption/compression framework, ORM, or new runtime
dependency.
