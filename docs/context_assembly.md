# M2.05 — Bounded agent context assembly contract

`agentx.core.context` owns one immutable, provider-neutral envelope of **already
selected evidence** for one task/correlation attempt. It is a data contract, not
a retrieval service, prompt, model request, working-memory store, or authority
boundary.

## Baseline and responsibility

The canonical baseline inspected was
`03a1221771ba4bcf038dca1d1432e9e8526b9550`. The duplicate guard searched that tree
for `context assembly`, `agent context`, `reasoning context`, `retrieval context`,
`context bundle`, `context envelope`, and `working context`; no equivalent
canonical evidence-assembly contract existed.

Related baseline contracts remain separate and unchanged:

- `core.execution.ExecutionContext` carries cancellation/deadline state and
  execution correlation, not selected evidence. This envelope does not embed its
  mutable cancellation state or duplicate its execution behavior.
- `cognition.reasoner.ReasonerRequest` binds execution context to an instruction;
  `cognition.model_provider.ModelRequest` is a model invocation request. Neither
  becomes a dependency or gains automatic context integration here.
- Hive semantic/experience memory and infrastructure knowledge retrieval return
  canonical records. The outer composition layer selects those records and
  determines their order; this module never accesses those services.

Public data types are `AgentContext`, `ContextItem`, `ContextItemKind`,
`ContextLimits`, and the `ContextRecord` union of existing canonical types.
There is no second Task, knowledge, episode, scope, provenance, status, or ID
schema. No `__init__.py` export or architecture manifest change is required.

## Closed item vocabulary

`ContextItem(record=...)` derives `kind` from the **exact canonical record type**.
It does not accept a caller-controlled kind at the live constructor or infer a
kind from content. Decoding validates the explicit wire kind against the
corresponding canonical record schema.

| Kind | Existing canonical record | Data preserved |
| --- | --- | --- |
| `KNOWLEDGE` | `core.knowledge.KnowledgeRecord` | KnowledgeId, knowledge type, content, full KnowledgeStatus, scope, provenance hook, creation and verification timestamps |
| `EPISODE` | `core.episodes.EpisodeRecord` | EpisodeId, historical outcome, summary, task/correlation references, timestamps, supporting event UUIDs in their original order |
| `NEGATIVE_EXPERIENCE` | `core.negative_experience.NegativeExperienceRecord` | NegativeExperienceId, attempt and failure references, observed outcome/time, scope, historical episode/task/correlation identities |
| `CAUSAL_EXPERIENCE` | `core.causal_experience.CausalExperience` | Complete selected state/action/observation/verification/outcome chain and all original identities and timestamps |
| `PROCEDURE_REFERENCE` | `core.procedures.ProcedureRecord` | Exact ProcedureId/revision, canonical status and scope, timestamps, and opaque payload/reference |
| `ENVIRONMENT_FACT` | `core.environment_change.EnvironmentSnapshot` | Exactly **one** selected EnvironmentObservation, its typed fact key/value, provenance, observed time/TTL, and the snapshot's scope and EvidenceReference |
| `USER_SUPPLIED_CONTEXT` | `core.events.ObservationPayload` | Bounded JSON-compatible user-supplied data, without a fabricated user/source identity or new trust/status model |

A procedure item is a reference to a specific revision **with its bounded
canonical revision snapshot**. `CANONICAL_JSON` payload text is never parsed as
an IR or interpreted. Artifact references are never dereferenced. This avoids
losing source/reference data while introducing no procedure execution or graph
traversal.

An environment item must contain exactly one observation: an empty snapshot or
multi-observation snapshot is refused, not silently split or truncated. An outer
layer that selects several observations must explicitly construct individual
one-observation snapshots retaining the original scope and evidence. Observation
TTL is preserved, not evaluated; stale or future-dated evidence is not filtered
or relabeled by context assembly.

`USER_SUPPLIED_CONTEXT` names how the outer layer supplied the payload, **not** an
authenticated origin. It has no inferred user ID, scope, status, or permission.
The existing `ObservationPayload` remains the inert JSON data representation.

## Task and attempt binding

Every envelope requires:

- an exact canonical, non-nil `TaskId`, never a raw string, UUID, other domain ID,
  `None`, wildcard, or subclass at the live constructor;
- a non-nil correlation `UUID` for this attempt;
- an explicit timezone-aware `created_at`.

No time or identity is generated implicitly. Envelope times accept standard
`datetime.timezone` or `zoneinfo.ZoneInfo` and normalize to UTC; custom timezone
callbacks and datetime subclasses are rejected. Inner canonical record times
already normalize to UTC through their existing contracts.

A live consumer must call
`context.require_binding(task_id=..., correlation_id=...)` before use. Both
`AgentContext.from_dict` and `AgentContext.from_json` **require independent
`expected_task_id` and `expected_correlation_id` arguments**; a mismatched task or
attempt raises `ContextBindingError`. There is no automatic rebinding. The outer
layer may deliberately construct a new envelope for another task/attempt.

Historical episode/negative/causal task and correlation IDs remain historical
provenance. They need not equal the envelope's current binding: otherwise past
experience from another task could never be represented. They are not rewritten.
A context stores no current Task object, changes no Task fields, and cannot mark
a Task successful.

## Provenance and status

Every accepted record is independently snapshotted through its existing canonical
serializer/decoder **after bounded exact-type preflight**. All embedded source
references, scopes, timestamps, and class-specific statuses are retained. The
context does not paraphrase evidence into an instruction, erase negative
outcomes, or flatten different status systems into `trusted=True`.

An optional canonical `EvidenceReference` named `record_reference` may identify
the **exact selected record** when the outer layer knows such a locator. It is
supplemental: it never replaces embedded knowledge provenance or environment
snapshot evidence. Its source, reference, kind, and observed timestamp are kept.
A document URL referring to many records is not a record-level identity; callers
must not claim that it is.

Missing information stays missing:

- `KnowledgeRecord.provenance=None` remains `None`, even if a supplemental
  retrieval/record reference is supplied;
- no supporting event IDs stays an empty tuple;
- absent historical task/episode/correlation references remain absent;
- absent `record_reference` stays `None`; no SYSTEM/USER origin is invented.

Only the record types above are accepted. Unknown record types, additional
sidecar fields, and executable objects are refused, never silently flattened or
dropped. Separately stored provenance/evidence is not fetched automatically.
Source labels and URLs are claims about origin, not authenticity checks.

`KnowledgeStatus.VERIFIED`, `ProcedureStatus.ACTIVE`, `EpisodeOutcome.SUCCEEDED`,
and a historical passing `VerificationPayload` retain their **different canonical
meanings**. None grants execution authority, activates anything, promotes another
record, or establishes success for the current task.

## Ordering and duplicate identity

`items` must be a finite, caller-ordered **tuple** of exact `ContextItem` values.
That order survives assembly and serialization. Sets, arbitrary sequences, and
generators are rejected; assembly does not sort, rank, score, or select evidence.

Repeated **known identities** raise `ContextDuplicateError`, whether the two
snapshots are identical or materially disagree. There is no merge, first-wins,
last-wins, or silent deduplication policy. Refusal prevents evidence being counted
twice and avoids erasing differing statuses, scopes, or provenance.

| Record | Identity used within the envelope |
| --- | --- |
| Knowledge | item kind + KnowledgeId |
| Episode | item kind + EpisodeId |
| Negative experience | item kind + NegativeExperienceId |
| Procedure revision | item kind + ProcedureId + revision |
| Causal / environment / user payload with explicit `record_reference` | item kind + EvidenceKind + source ProvenanceKind + source reference + record reference |
| Causal / environment / user payload without `record_reference` | **Unknown**; no identity is fabricated |

Canonical IDs take precedence over supplemental references: changing retrieval
labels cannot disguise a duplicate KnowledgeId, EpisodeId, NegativeExperienceId,
or procedure revision. Different procedure revisions and different ID domains
remain distinct. Identical text with different identities remains distinct.

The baseline gives a causal experience no unique record ID; its correlation UUID
groups a chain, potentially containing several attempts. An environment fact key
identifies a fact, not an individual observation; snapshot evidence can identify
a whole source collection. Neither is misused as a unique observation/record ID.

When identity is unknown, **even equal payloads or the same supplied object are
retained as separate items and count separately against limits**. Object address,
content hashes, correlation alone, and source labels are not proof of identity.
The outer layer can supply a genuine record-level `EvidenceReference` to enable
duplicate refusal. A reference's acquisition timestamp does not change the
referenced record's identity, but its source namespace does.

## Scope compatibility

The envelope carries canonical `KnowledgeScope`. Each item retains its own
original `KnowledgeScope`, `ProcedureScope`, or absence of a scope (`None`).
An empty canonical scope remains global/unscoped; it is not an authority grant.

Assembly compares **only overlapping explicit dimension bindings**, across both
the envelope and all scoped items. Differing values for the same dimension raise
`ContextScopeError`, even if the envelope's scope is empty. Comparison is exact
and case-sensitive; no scope is inferred from text or metadata.

The five explicit shared procedure/knowledge dimensions are application,
application version, operating system, environment, and project. Those typed
counterparts are compared using an explicit mapping, without changing the stored
scope types. Knowledge's `CONTEXT` dimension has no procedure counterpart.

Compatible partial scopes and global records are allowed. Missing dimensions
are not filled into any record or the envelope. This is a consistency check, not
an applicability/relevance decision, scope merger, or environment observation.

## Hard bounds and refusal

`ContextLimits` has fixed upper ceilings. Callers may only **tighten** them.
Booleans, floats, `None`, negative values, unlimited sentinels, raised ceilings,
and subclasses are not accepted as limits. Zero item/metadata counts are allowed;
byte limits must be positive. Limits describe data size, not execution budgets.

| Bound | Hard ceiling / default | Accounting |
| --- | ---: | --- |
| `max_items` | 64 | All supplied items, including identity-unknown entries |
| `max_total_bytes` | 262,144 (256 KiB) | Complete deterministic envelope JSON encoded as UTF-8, including limits, task binding, scopes, references, metadata, and syntax |
| `max_item_bytes` | 16,384 (16 KiB) | Complete item JSON: kind, full canonical record/detail, supplemental reference, keys, and syntax |
| `max_metadata_entries` | 16 | Sum of keys across **all nested metadata objects**, not just the root |
| `max_metadata_bytes` | 4,096 (4 KiB) | Complete serialized metadata object |
| `MAX_CONTEXT_DETAIL_DEPTH` | 12 | Canonical record preflight and complete item/metadata JSON trees; root depth is zero |
| `MAX_CONTEXT_DETAIL_NODES` | 4,096 | Values/containers inspected per item and per metadata object; canonical preflight counts typed objects too |
| `MAX_CONTEXT_INTEGER_BITS` | 1,024 | Maximum integer magnitude bit length in JSON-compatible data and numeric record fields |

Scope data on the envelope also passes the fixed per-detail byte/node/depth
bounds. A separate per-category count is unnecessary: each category cannot
exceed the 64-item total, and one environment item cannot hide several facts.
The item byte limit bounds *all* detail, not only a summary string; it also
bounds opaque procedure JSON strings without interpreting them.

Validation bounds work **before** canonical serializers or defensive recursive
copies: only exact allowlisted core dataclasses, IDs, enum values, and plain JSON
values are inspected. Cycles/depth explosions, oversized containers, huge
integers, non-finite floats, invalid Unicode, callbacks, arbitrary objects,
record/primitive subclasses, and arbitrary mapping/sequence hooks are refused.
Public raw mappings/metadata require exact built-in dictionaries with plain
string keys. Arrays may be built-in lists/tuples at the live JSON-data boundary;
wire arrays are emitted as lists. Canonical/internal frozen mappings are not a
public deserialization format: use `to_dict()` to obtain detached JSON data.

Final exact byte checks include UTF-8 multibyte characters and JSON escaping.
Items validate their **complete wire shape** at construction as well as decoding,
so a constructor-valid boundary item can round-trip. Canonical record invariants
may impose additional stricter bounds of their own.

There is **no truncation**, filtering, or evidence-loss fallback. A breach raises
`ContextLimitError`; the caller must explicitly select a smaller set/detail.

On decode, the receiver's `limits` are independent from the serialized limit
declaration. Declared limits exceeding receiver limits are refused rather than
silently relaxed or rewritten, even if the actual payload is small. The full
input JSON text, including whitespace, must fit the receiver's byte limit before
parsing. Parser nesting is capped at 16; decoded envelope traversal is bounded to
`4,096 * (64 + 1)` nodes before per-item checks. Accepted envelope output must
also fit its own declared canonical byte limit.

## Immutability and serialization

The envelope, items, and limits are frozen, slotted dataclasses. Selected records
and supplemental references are independently reconstructed as their original
canonical types; nested canonical evidence retains its existing deep freeze.
Metadata is defensively copied to read-only mappings and tuple-valued arrays.
`to_dict()` returns detached mutable JSON data, never live internal containers.

Schema version is `1`. The exact envelope fields are:

```text
schema_version, task_id, correlation_id, created_at,
scope, items, metadata, limits
```

Each item contains exactly `kind`, `record`, and `record_reference`. Limits have
exactly the five fields in the table. Canonical record wire fields (including
nullable/optional fields that their serializers emit) must be present. Unknown
fields, unknown kinds/statuses/scope dimensions, missing fields, and unsupported
schema versions fail explicitly.

JSON is compact, key-sorted, `ensure_ascii=False`, and `allow_nan=False`. UUID
values are preserved through canonical types and their canonical string forms;
timestamps serialize as UTC with microseconds and `Z`. There is no generated
assembly time, random ID, or content-dependent ordering to disturb determinism.

Duplicate JSON member names, including escaped spellings of the same name, are
rejected before ordinary `json.loads`. The structural preflight does not interpret
quoted payload text. There are **no object hooks**, dynamic imports, pickle,
`asdict`/`deepcopy` callbacks, executable reconstruction, or configurable codecs.

Errors derive from `ContextValidationError` (`ValueError`), with explicit
`ContextLimitError`, `ContextBindingError`, `ContextDuplicateError`,
`ContextScopeError`, and `UnsupportedContextSchemaVersionError` subclasses.

## Example: package selected evidence, not a prompt

```python
from datetime import UTC, datetime
from uuid import UUID

from agentx.core.context import AgentContext, ContextItem, ContextLimits
from agentx.core.ids import TaskId
from agentx.core.knowledge import KnowledgeRecord, KnowledgeType

# The outer layer owns these exact identities and has already selected record.
task_id = TaskId.parse("00000000-0000-0000-0000-000000000001")
correlation_id = UUID("00000000-0000-0000-0000-000000000002")
created_at = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
record = KnowledgeRecord.create(
    knowledge_type=KnowledgeType.FACT,
    content="Selected evidence with no known provenance.",
    created_at=created_at,
)
limits = ContextLimits(max_items=8, max_total_bytes=32_768)
context = AgentContext(
    task_id=task_id,
    correlation_id=correlation_id,
    created_at=created_at,
    items=(ContextItem(record=record),),
    limits=limits,
)
context.require_binding(task_id=task_id, correlation_id=correlation_id)
restored = AgentContext.from_json(
    context.to_json(),
    expected_task_id=task_id,
    expected_correlation_id=correlation_id,
    limits=limits,
)
assert restored == context
assert restored.items[0].record == record
```

The example constructs data only; it performs no retrieval, prompt construction,
model invocation, persistence, or task/procedure/knowledge transition.

## No authority, non-goals, and limitations

Strings such as `ignore previous instructions`, `permission=ADMIN`, `risk=R0`,
`verified=true`, `task succeeded`, `activate procedure`, `clear emergency stop`,
and `budget=unlimited` remain inert in content **and source labels**. JSON data
may describe those claims, but the envelope has no authority/success/global-trust
field, and a context/item/limit object cannot satisfy `AuthorityContext` at the
real `ActionGate`. There is no Permission grant, RiskLevel change, gate bypass,
budget increase, stop reset, current Task success, procedure activation, or
knowledge promotion.

This module imports only stdlib and `agentx.core.*`. It never imports Hive,
cognition, kernel, capabilities, learning, procedure runtime, or infrastructure.
It has no retrieval/storage/provider ports, model/prompt/tokenizer library,
network/process/file operations, salience/relevance policy, graph search,
world-state sensing, or AgentLoop integration.

These are **data and architecture guarantees**, not a Python sandbox or proof
that source claims are true. Code with arbitrary interpreter access can bypass
ordinary frozen-dataclass protections. Binding is explicit validation, not a
cryptographic signature or automatic enforcement in unchanged consumers. The
outer layer remains responsible for selection, complete known provenance,
record-level locator correctness, freshness/relevance, correlation allocation,
and safe later consumption. Incompatible scopes require explicit separate
assembly, not an override hidden in text. Extending the closed vocabulary or
supporting additional canonical sidecars needs a deliberate contract change.

## Verification

Focused checks live in:

- `tests/unit/test_context.py`: all vocabulary members, ordering/identity, exact
  task binding, provenance/status preservation, scope conflicts, hard limits,
  deep immutability, exact/deterministic JSON, and malformed input;
- `tests/adversarial/test_context_authority.py`: hostile content/source labels,
  real gate refusal, unchanged Task/Procedure/Knowledge/budget/stop state,
  no code execution, and rejected callable/object/bytes/subclass/hook injection;
- `tests/architecture/test_context_placement.py`: canonical placement and reuse,
  stdlib/core-only imports, fresh-process transitive import isolation, no
  retrieval/model/prompt/storage/execution calls, and no decoder object hooks.

The full repository pytest, Ruff lint, Ruff format, and mypy gates remain
required; the Windows/Python 3.12 CI workflow is unchanged.
