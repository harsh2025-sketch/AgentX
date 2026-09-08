# Hive relationship graph contract (M6.02)

`agentx.hive.relationship_graph` is the deterministic, bounded, **in-memory**
graph of explicit relationships between canonical Hive knowledge records. It is
a DATA boundary: everything it stores or returns is inert. It never resolves
truth, never changes a record, and grants no authority.

| Concern | Owner |
| --- | --- |
| Record identity (`KnowledgeId`), status, provenance, evidence | `agentx.core` (C2.02 / C2.07) |
| Lifecycle transitions (`KnowledgeStatus`, incl. `CONFLICTED`, `SUPERSEDED`) | C2.08 via the canonical `KnowledgeStore` |
| **Explicit relationship edges between records (this task)** | **M6.02 `RelationshipGraph`** |
| Persistent graph storage | later infrastructure integration |

## Node identity model

Nodes are canonical `agentx.core.ids.KnowledgeId` values — nothing else.
There is no `GraphNodeId`; a knowledge record already has canonical identity
and the graph never mints a duplicate. The graph holds **no records and no
store reference**: it cannot know whether an id exists anywhere and performs
no "dangling resolution". Only knowledge records are in scope for v1; other
AgentX objects are not unified into a universal entity graph.

## Relationship vocabulary and directionality matrix

`RelationshipKind` is a closed `StrEnum`. No free-text edge type exists.

| Kind | Direction | Meaning of `source -> target` |
| --- | --- | --- |
| `SUPPORTS` | directional | `source` is evidence in favour of `target` |
| `CONTRADICTS` | symmetric | `source` and `target` cannot both hold |
| `SUPERSEDES` | directional | `source` is the newer claim replacing `target` |
| `DERIVED_FROM` | directional | `source` was derived from `target` |
| `RELATED_TO` | symmetric | an explicit, unranked association |

`RelationshipKind.is_symmetric` exposes the matrix in code.

**Symmetric canonicalization** is deterministic: on construction, the endpoint
whose `KnowledgeId.to_str()` sorts first becomes `source`. Therefore
`RelationshipEdge(A, B, CONTRADICTS)` and `RelationshipEdge(B, A, CONTRADICTS)`
are equal values and the same edge. Directional kinds keep caller order;
`A SUPPORTS B` and `B SUPPORTS A` are two distinct edges.

## Edge record

```python
RelationshipEdge(source: KnowledgeId, target: KnowledgeId,
                 kind: RelationshipKind, evidence: tuple[EvidenceReference, ...])
```

* Frozen dataclass; stored edges cannot be mutated through query results.
* `evidence` is a **non-empty** tuple of canonical
  `agentx.core.provenance.EvidenceReference` values (C2.07). Provenance is
  mandatory for every edge and is stored verbatim — Unicode, control
  characters and injection-style text included. It is never fetched,
  interpreted, ranked, or treated as a trust signal.
* Self-edges are rejected for every kind (`RelationshipValidationError`).
* `identity` = `(kind, source, target)`; `sort_key()` is a total,
  insertion-independent order used by every query.

## Duplicate semantics (append-oriented, lossless)

| Situation | Behaviour |
| --- | --- |
| Identical edge (same identity **and** identical evidence tuple) | `add` returns `False`; no-op |
| Reverse of a symmetric edge | canonicalizes to the same edge → no-op |
| Reverse of a directional edge | distinct edge |
| Same identity, different evidence | stored as an **additional distinct edge**; evidence is never merged or overwritten |

There is no `remove`, `clear`, `merge`, or `resolve`. The graph is append-only.

## Contradiction semantics

Adding `A CONTRADICTS B` records the relationship and nothing else. It does
**not** delete A or B, choose a winner, change either `KnowledgeStatus`
(no `CONFLICTED` transition), promote, demote, rewrite content, or merge
records. Contradiction is preserved as visible evidence; resolution is a
separate C2.08 policy.

## Supersession semantics

`A SUPERSEDES B` is descriptive graph evidence. It does not set B to
`SUPERSEDED`, and the module never references `KnowledgeStore.update_status`,
`KnowledgeStatus`, or `KnowledgeRecord` at all (asserted structurally by
tests). A later lifecycle component may coordinate both.

## Authority

`A SUPPORTS B` does not make B `VERIFIED`. `A CONTRADICTS B` does not
invalidate B. `A SUPERSEDES B` does not mutate B. Hostile text on evidence is
inert (`verified=true`, `permission=ADMIN`, `risk=R0`, `supersedes=*`, …).
The graph cannot promote or delete knowledge, change status, execute a
capability, alter permissions, modify risk, or call a model — it holds only
`limits` and edge collections, accepts no callables, and imports only
`agentx.core` plus stdlib.

## Bounds (`RelationshipGraphLimits`)

| Bound | Default | Enforced |
| --- | --- | --- |
| `max_edges` | 10 000 | on `add` |
| `max_degree` (edges touching one node) | 256 | on `add`, both endpoints |
| `max_evidence_per_edge` | 32 | on `add` |
| `max_query_results` | 256 | on every query (caller `limit` can only lower it) |

All bounds are checked **before** any state changes; a violation raises
`RelationshipGraphLimitError` and leaves the graph untouched (fail closed).
An identical duplicate at the edge limit is still a harmless no-op.

## Query API (exact, deterministic, one hop)

```python
graph.add(edge) -> bool                      # True if new
graph.add_all(edges) -> int                  # count of new edges; stops at first error
graph.edges() / graph.nodes() / graph.degree(id) / len(graph) / edge in graph
graph.edges_from(id, kind=None)              # id is source (either end for symmetric kinds)
graph.edges_to(id, kind=None)                # id is target (either end for symmetric kinds)
graph.relationships_between(a, b, kind=None) # explicit edges with endpoints exactly {a, b}
graph.neighbors(id, kind=None)               # distinct directly connected ids, sorted
graph.query(RelationshipQuery(node, kind, direction, limit)) -> RelationshipResult
```

`RelationshipResult` carries the sorted, truncated `edges`, the untruncated
`total_matches`, and `truncated`. There is no fuzzy search, embedding,
ranking, model, or multi-hop traversal. **No inference**: no transitive
closure (`A→B`, `B→C` never yields `A→C`), no inverse-kind derivation, no
symmetry beyond the declared symmetric kinds. Only explicitly added edges
exist.

## Non-goals

No vector search, relationship inference, contradiction resolution,
`KnowledgeStatus` mutation, persistence, migration, GraphRAG, ontology,
confidence scoring, knowledge merging, consolidation, or world state.

## Known limitations

* In-memory only; a persistent adapter is later infrastructure work.
* Nodes are `KnowledgeId` only; episodes, procedures and other records are not
  graph nodes in v1.
* Not thread-safe; callers own synchronization.
* `add_all` is not transactional: edges added before a failing edge remain.
