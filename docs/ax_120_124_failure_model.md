# AX-120–124 failure model

- Missing relationship endpoints fail explicitly through the canonical store/query errors.
- Duplicate contradiction/supersession edges remain explicit canonical store failures.
- Supersession cycles/self-links remain rejected by the existing C2.08 writer.
- Relationship traversal is finite and fails if its configured node bound is exceeded.
- Assurance replay is finite and fails if the configured journal replay bound is exceeded; partial assurance is never returned as complete.
- Revalidation completion without a persisted request fails explicitly.
- Revalidation completion is idempotent by durable event identity; duplicate concurrent completion cannot increment verification twice.
- Journal write failure leaves prior durable history unchanged; restart reconstructs only committed evidence.
- Scope retrieval rejects empty/malformed protected scopes and untyped global broadening.
