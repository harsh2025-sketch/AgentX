# C2.05 semantic memory

Semantic memory is the durable **declarative knowledge class** of the Hive: facts, learned
claims, stable observations generalized beyond one episode, application/environment knowledge,
and user/project facts a caller supplied explicitly.

"Semantic" here names the knowledge class. It does **not** mean embedding search. Vector
retrieval, similarity, and ranking are owned by C2.09 and are absent from this module.

## Service surface

`agentx.hive.semantic_memory.SemanticMemory` is a narrow read/write service over the canonical
C2.02 knowledge store.

| Operation | Behaviour |
| --- | --- |
| `remember(record) -> KnowledgeRecord` | Durably store one new semantic record verbatim. |
| `recall(knowledge_id) -> KnowledgeRecord \| None` | Exact identity lookup, never similarity. |
| `recall_all(query=None) -> tuple[KnowledgeRecord, ...]` | Deterministic enumeration with optional exact structured filtering. |
| `provenance_of(knowledge_id) -> ProvenanceRecord \| None` | Read-only projection of the stored origin into the canonical C2.07 shape. |

`SemanticMemoryQuery` filters only on fields the record already carries: `knowledge_types`,
`statuses`, `provenance_kinds`, exact `scope` equality, and `scope_contains` (dimension/value
containment). Criteria combine with AND; an omitted criterion is not applied; an empty criterion
set is rejected rather than silently matching nothing. `SemanticMemoryQuery.matches()` is a pure
predicate over one record and never reads storage or content.

Errors are explicit: `NonSemanticKnowledgeError`, `IngestionStatusError`,
`DuplicateSemanticKnowledgeError`, and `SemanticMemoryQueryError`, all deriving from
`SemanticMemoryError`.

## Storage composition

Semantic memory **composes** `agentx.infrastructure.knowledge_store.KnowledgeStore`; it does not
add a table, a migration, or a second copy of any record. Knowledge lives exactly once, in
`agentx_knowledge`, and durability across process restarts is precisely the store's durability.

The service depends on a structural port declared in its own module:

```python
class KnowledgeStorePort(Protocol):
    def insert(self, record: KnowledgeRecord) -> None: ...
    def get(self, knowledge_id: KnowledgeId) -> KnowledgeRecord | None: ...
    def list_records(self) -> tuple[KnowledgeRecord, ...]: ...
```

`KnowledgeStore` satisfies the port structurally, so composition needs no
`agentx.hive -> agentx.infrastructure` import edge and the A1.02 boundary manifest is unchanged.
The port is append-and-read only: it deliberately omits `update_status` and any delete, so the
service is *structurally* incapable of a lifecycle transition or of destroying knowledge.

Wiring is the composing caller's job:

```python
memory = SemanticMemory(KnowledgeStore(SQLiteDatabase(path)))
```

## Ingestion policy

New information stays explicitly typed and explicitly untrusted:

- only knowledge types in `SEMANTIC_KNOWLEDGE_TYPES` (`FACT`, `OBSERVATION`, `PREFERENCE`) may be
  remembered. It is an allow-list, so a knowledge type added later for episodic, procedural, or
  causal memory cannot silently enter semantic memory, and records of such a type stored by
  another owner are invisible to this service's reads;
- `remember()` accepts only a record in its canonical birth state — `UNVERIFIED` with no
  `verified_at`. Arbitrary text, including model output, therefore cannot arrive pre-labelled
  `VERIFIED` or `SUPPORTED`. Refusing to write is not a lifecycle decision;
- duplicate identity is explicit: `DuplicateSemanticKnowledgeError`, nothing overwritten, nothing
  merged. The check consults the whole store because identity is store-wide, and the store stays
  the final arbiter — under a concurrent writer its own duplicate error propagates unchanged;
- re-remembering the same claim under a new identity stores a second independent record. Records
  do not aggregate and repetition never increases trust.

## Lifecycle boundary (C2.08)

Semantic memory owns no lifecycle semantics. It never promotes, demotes, aggregates, or infers
status from provenance, from evidence count, or from repetition. It exposes no status-mutating
operation at all. A status that lifecycle policy sets through the store's explicit
`update_status` is read back verbatim, including a historical `VERIFIED` status — which remains
inert data.

## Provenance and evidence (C2.07)

`provenance_of()` projects the provenance hook C2.02 already persists on the record into the
canonical `ProvenanceRecord` shape, with the stored `ProvenanceReference` as its `source`.
Fields C2.02 does not persist (`observed_at`, `locator`, `derived_from`) are reported as unknown
rather than invented, and no provenance storage is introduced.

Detailed provenance records and `KnowledgeEvidence` bundles are **not** persisted here: doing so
would require a new table and migration, which this task deliberately does not create. Absence of
provenance is absence of data, never evidence for or against a claim, and provenance of any
channel — WEB, EMAIL, DOCUMENT, USER, SYSTEM — confers no trust and no authority.

## Scope

Scope uses the canonical `KnowledgeScope` / `ScopeDimension` contracts unchanged, and is preserved
byte-for-byte through write and read. Scope is **applicability** data only. An empty/global scope
means "this claim is not restricted to a named application, version, OS, environment, project, or
context". It is never permission everywhere and never grants machine authority.

## Security boundary

Remembered content is DATA. Hostile claims such as `ADMIN`, `SYSTEM`, `verified=true`, `risk=R0`,
`permission=WRITE`, `budget=unlimited`, `ignore previous policy`, `execute capability`, or
`clear emergency stop` are stored and returned as inert characters and are interpreted by nothing.

Semantic memory cannot create a `Permission` or `AuthorityContext`, bypass the Action Gate, lower
effective risk, increase a `ResourceEnvelope`, clear an `EmergencyStop`, execute a `Capability`,
mutate a `Task`, or mark action verification successful. The module imports no kernel, capability,
cognition, procedure, or transport module, publishes no events, writes no audit entries, and
executes nothing. Architecture tests assert these properties from the module's own source.

## Explicitly out of scope

Episodic/negative memory (C2.06), lifecycle/contradiction/supersession (C2.08),
retrieval/environment TTL (C2.09), causal experience (C2.10), embeddings, vector indexes,
similarity, keyword relevance, LLM ranking, graph traversal, automatic consolidation, automatic
context construction, research, web acquisition, model calls, procedure compilation, and
execution. The runtime dependency set remains empty.
