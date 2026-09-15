# Knowledge assurance and protected scope retrieval (AX-120–124)

This completion package extends the existing C2 knowledge architecture without adding a second knowledge database.

## Relationship integrity

Contradiction and supersession continue to be persisted by `KnowledgeStore`. `KnowledgeRelationshipQuery` adds read-only contradiction views and bounded bidirectional supersession traversal. Both historical claims remain intact; contradiction never selects a winner, and supersession never rewrites historical content.

## Evidence-backed assurance

`KnowledgeAssuranceMetadata` is non-authoritative metadata attached to a `KnowledgeId`. It records source observation time, verification/failure counts, environment validity, freshness, evidence references, last verification, contradiction count, supersession state, and a coarse confidence state. Confidence is deliberately a state rather than a probability and cannot grant permission, lower risk, bypass the Action Gate, or declare task success.

Revalidation has explicit requested and completed stages. Completed results require independently attributable evidence. Success, failure, inconclusive evidence, environment mismatch, contradiction discovery, and superseding-evidence discovery have deterministic update rules. Requests/results are stored as inert observations in the canonical append-only `EventJournal`, so the history survives restart without a competing migration or persistence layer.

## Scope isolation

The existing canonical `KnowledgeScope` currently represents application, application version, operating system, environment, project, and context. The context dimension is opaque applicability data and may carry caller-owned session/task/device/procedure/security context identifiers, but the retrieval layer never parses free text into scope authority.

`ScopedKnowledgeRetrieval` is the fail-closed boundary for scope-sensitive Hive reads:

- a non-empty canonical `KnowledgeScope` is mandatory;
- matching is exact, not subset/contains matching;
- globally scoped records are excluded by default and require the typed `GlobalKnowledgePolicy.INCLUDE` opt-in;
- malformed scopes fail before retrieval;
- record content is never inspected to infer or broaden scope;
- deterministic store ordering is preserved;
- scope remains applicability data only and grants no execution authority.

Dimensions not represented structurally by the canonical knowledge contract are not guessed from model text or provenance. A caller that needs a user/task/device/session/procedure/security distinction must encode that distinction in its canonical structured applicability context before protected retrieval. Absence of such metadata does not widen the query.
