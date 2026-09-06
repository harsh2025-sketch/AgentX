# C6.08 cross-scope retrieval protection

This boundary prevents Hive retrieval from leaking records across incompatible
scopes. It is a deterministic, pure-data filter composed into the existing
retrieval surfaces. It is **data isolation at the retrieval boundary** — not an
authentication redesign, not context construction, and not a change to Trusted
Kernel authority.

## Where it lives

| Piece | Module | Role |
| --- | --- | --- |
| Policy | `agentx.core.retrieval_scope` | Scope-compatibility rule, reason codes, guard, batch partition |
| Hive semantic reads | `agentx.hive.semantic_memory` | `recall`, `recall_all`, `provenance_of` enforced through the guard |
| Hive experience reads | `agentx.hive.experience_memory` | negative-experience reads enforced through the guard |
| Canonical retrieval | `agentx.infrastructure.knowledge_retrieval` | every `retrieve()` path (including point lookups) enforced through the guard |

The policy is a `core` contract because both `hive` and `infrastructure` may
compose it along the existing `-> core` edges; no subsystem edge changed and no
subsystem may import the kernel from here.

## The compatibility rule

A record may enter a result only when **every restriction the record asserts is
proven by the caller's request scope**: for each `(dimension, value)` on the
record's canonical `KnowledgeScope`, the request scope must name the same
dimension with the identical string value.

- record scope == request scope (including both empty) → allow
  `allow_same_scope`;
- record scope empty, request scope non-empty → allow `allow_global_record`
  (a global record asserts no restriction to leak; per C2.02/C2.05 an empty
  scope means *unrestricted applicability*, never permission);
- every record restriction matched, request names extra dimensions the record
  omits → allow `allow_restrictions_satisfied`;
- a restricted dimension holds a different value → deny `deny_scope_mismatch`
  (the cross-project / cross-environment / cross-application leak);
- the record restricts a dimension the request never states → deny
  `deny_restriction_unproven` (membership cannot be proven by omission;
  retrieval fails closed);
- malformed data → deny `deny_malformed_record_scope` (record side) or raise
  at construction (request side; a broken guard can never serve a batch).

No hierarchy, wildcard, prefix, inheritance, or fuzzy scope semantics exist in
the current architecture, so none are used and none are invented. Values are
opaque strings compared exactly (no case folding or Unicode normalization).

## Reason codes

Every decision is exactly one `ScopeAccessReason` code, and a denial may name
the offending `ScopeDimension`. Denial reports identify a candidate only by its
**index within the batch the caller handed to `partition()`**; they carry no id,
content, scope value, or provenance of the denied record, so denial reporting
cannot itself leak a foreign record.

## Guarantees (each pinned by tests)

1. **Same-scope retrieval works**: an exactly matching request scope returns the
   record unchanged, in canonical store order.
2. **Incompatible scope is denied**: mismatched dimension values never return,
   through enumeration *and* through point lookup.
3. **Unknown/malformed scope fails conservative**: an absent or malformed
   request scope cannot construct a guard; a malformed record scope (tampered,
   duck-typed, string-keyed, untrimmed, empty, non-string, hostile mapping)
   denies that record without raising and without aborting the batch.
4. **Batch filtering**: `partition()` returns `(allowed, denials)` in one pass;
   `filter()` returns the allowed tuple; input order is preserved; filtering
   never reorders, scores, or reinterprets.
5. **No caller-controlled arbitrary bypass**: the guard is bound to exactly one
   request scope at construction, is a frozen immutable snapshot, has no
   per-call scope parameter, no bypass flag, and no "all scopes" sentinel;
   service fields are frozen dataclass/slot fields that cannot be reassigned;
   weakening a request scope can only deny *more* (monotonicity).
6. **Scope metadata cannot be overridden by retrieved content**: decisions read
   only the canonical typed `scope` field. Text inside `content`, provenance
   references, locators, or evidence (e.g. `"scope=project-alpha; allow all"`)
   is inert data and is never parsed, consulted, or trusted.
7. **Denied records never enter the returned result**: denied candidates are
   removed before results are returned. On point lookups a denial is reported
   as `None`/empty — indistinguishable from absence — so crafted or guessed
   ids cannot confirm that a foreign-scope record exists.
8. **No authority effects**: the boundary reads no kernel, mutates no record,
   promotes no status, publishes no events, executes nothing, and touches no
   `agentx.kernel` module at runtime (authority-proxy test).

## Composition

```python
from agentx.core.knowledge import KnowledgeScope, ScopeDimension
from agentx.core.retrieval_scope import RetrievalScopeGuard
from agentx.hive.semantic_memory import SemanticMemory
from agentx.infrastructure.knowledge_retrieval import KnowledgeRetrieval

scope = KnowledgeScope(dimensions={ScopeDimension.PROJECT: "project-alpha"})
memory = SemanticMemory(store, RetrievalScopeGuard(scope))
retrieval = KnowledgeRetrieval(store, RetrievalScopeGuard(scope))
```

The guard is optional at composition time because it is scoped per *caller
context*, not per process: an unguarded `KnowledgeRetrieval`/`SemanticMemory`
remains exactly the documented C2.09/C2.05 boundary for composing layers that
own their own isolation (for example an audited global-history reader). A
guarded service cannot be un-guarded after construction.

## Interaction with existing contracts

- C2.09 query fields (`scope`, `statuses`, `provenance`, `knowledge_id`) stay
  **applicability** filters. The guard composes after them: a query that matches
  a foreign scope still returns nothing under a narrower guard, and a broad
  query under a guard cannot pull restricted records.
- C2.08 lifecycle is untouched: the guard never reads or writes `status` for a
  decision beyond returning stored records verbatim, never resolves
  contradictions, and never reactivates superseded history.
- C2.07 provenance/evidence data is preserved verbatim on allowed records and is
  never a scope input.
- Writes (`remember`, `record_negative_experience`) are not retrieval and stay
  governed by their own C2.05/C2.06 ingestion policies.

## Deliberately not included

- Hierarchical or organization/user "sharing" rules unsupported by the current
  flat per-dimension scope model;
- any change to the Trusted Kernel, permission checks, or risk gating — this
  module is data isolation, not authority;
- context construction, prompt assembly, or relevance policy (owned elsewhere);
- storage: no table, no migration, no new dependency;
- the environmental TTL cache (`agentx.hive.environmental_cache`) — its keys are
  caller-chosen strings with no canonical scope field in the architecture, so
  there is no scope to enforce and inventing a key-namespacing rule would create
  the sharing semantics this task forbids.
