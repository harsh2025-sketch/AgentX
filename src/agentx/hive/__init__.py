"""Ownership boundary: ``agentx.hive``.

Canonical responsibility: persistent semantic, episodic, procedural, causal,
and environmental knowledge. Hive records where each item came from
(provenance); it must not perform machine actions.

Implemented so far: ``agentx.hive.semantic_memory`` (C2.05 semantic memory
read/write service), ``agentx.hive.experience_memory`` (C2.06 episodic and
negative experience memory semantics), and ``agentx.hive.environmental_cache``
(C2.09 in-memory environmental observation TTL cache). Everything stored or
returned by Hive is inert data and grants no authority.

When a ``RetrievalScopeGuard`` from ``agentx.core.retrieval_scope`` is composed
into a Hive read service, every scoped read path additionally enforces C6.08
cross-scope retrieval protection: records restricted to scopes the caller
cannot prove never enter results.
"""
