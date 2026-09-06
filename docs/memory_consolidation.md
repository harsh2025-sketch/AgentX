# C6.04 Memory Consolidation

Conservative consolidation of accumulated Hive memory. Hive memory classes
include semantic, episodic, procedural, causal, negative and environmental
memory. Consolidation reduces redundant representation while retaining evidence
and auditability.

## Conservative Rules

- Never turn repetition into verification automatically.
- Never discard contradictory evidence.
- Never erase negative experience merely because success later occurred.
- Never lose provenance.
- Never destroy source history needed for audit.
- Prefer linking/superseding over destructive deletion.

## Implemented

- candidate selection (grouping by exact type+content+scope+status)
- compatibility/equivalence checks (deterministic, exact, filtered)
- consolidated representation/result (canonical + superseded + source refs)
- source references (frozenset of KnowledgeId)
- provenance union (distinct ProvenanceReference set)
- status handling (verbatim preservation, no inflation)
- supersession/archive linkage where canonical (KnowledgeSupersession)
- idempotence (existing supersessions recognised, no duplicate creation)
- bounded batch behavior (caller-controlled batch_size, deterministic truncation)
- deterministic consolidation is preferred; no model calls

## Out of Scope

- C6.05 salience policy
- C6.06 revalidation

## Storage

Knowledge consolidation uses the canonical `agentx_knowledge_supersessions`
table (migration v7, C2.08). Duplicate groups keep the earliest
`(created_at, knowledge_id)` as canonical; later duplicates are marked
`SUPERSEDED` via `KnowledgeSupersession`. All source rows remain durably
stored and retrievable; superseded history is excluded from default
retrieval but exposed when explicitly requested. Provenance union is the
distinct set of `ProvenanceReference` values across the group; source
history preserves the original per-record provenance.

Negative experience, episodic, procedural, causal and environmental stores
are conservatively preserved: consolidation never deletes a
`NegativeExperienceRecord`, successful history never erases prior failure,
and scope/environment differences keep groups distinct.

## API

Core (pure, no I/O):
`agentx.core.memory_consolidation` — `knowledge_consolidation_key`,
`are_knowledge_records_equivalent`, `select_knowledge_candidates`,
`check_knowledge_compatibility`, `build_consolidated_knowledge_groups`,
`MemoryConsolidation`.

Hive (durable):
`agentx.hive.memory_consolidation` — `HiveMemoryConsolidation`,
`KnowledgeStoreConsolidationPort`, `HiveConsolidationResult`,
`consolidate_memory`.

Re-exports:
`agentx.hive.consolidation`, `agentx.infrastructure.memory_consolidation`.

## Tests Covered

- duplicate claims (group + supersede earliest wins, earliest canonical)
- same claim different provenance (union preserved, both sources retained)
- conflicting claims (different content same scope => not grouped)
- negative experience (preserved)
- different scopes (scope exact equality, not grouped)
- different environments (ENVIRONMENT dimension, not grouped)
- verified vs unverified (status part of key, not grouped)
- idempotence (second call creates nothing, groups empty)
- restart (supersessions persist, second call idempotent)
- no confidence inflation (UNVERIFIED stays UNVERIFIED, no promotion)
- audit preservation (superseded rows remain gettable, supersession edges)
