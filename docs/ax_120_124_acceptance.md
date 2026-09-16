# AX-120–124 acceptance map

This file maps the completion branch to the campaign requirements. It is evidence metadata only; CI remains authoritative for executable gates.

| Task | Production implementation | Focused proof |
| --- | --- | --- |
| AX-120 | canonical durable contradiction store + `KnowledgeRelationshipQuery.contradiction_views` | existing integrity suite plus campaign relationship/adversarial/restart tests |
| AX-121 | canonical durable supersession store + bounded bidirectional chain traversal | existing cycle/self/duplicate tests plus campaign chain/restart/coexistence tests |
| AX-122 | `KnowledgeAssuranceMetadata` and deterministic evidence update rules | serialization, failure/staleness, authority-boundary and restart tests |
| AX-123 | explicit requested/completed revalidation lifecycle persisted in `EventJournal` | success/failure/inconclusive/environment mismatch, request requirement, duplicate/concurrency, restart and injection tests |
| AX-124 | `ScopedKnowledgeRetrieval` exact-scope fail-closed boundary | neighbor/superset/global/malformed/injection/determinism adversarial tests |

The branch intentionally does not implement AX-040, AX-409, AX-411, or AX-414–421 and does not merge or push to `main`.
