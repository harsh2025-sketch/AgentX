"""Ownership boundary: ``agentx.hive``.

Canonical responsibility: persistent semantic, episodic, procedural, causal,
and environmental knowledge. Hive records where each item came from
(provenance); it must not perform machine actions.

Implemented so far: ``agentx.hive.semantic_memory`` (C2.05 semantic memory
read/write service), ``agentx.hive.experience_memory`` (C2.06 episodic and
negative experience memory semantics), ``agentx.hive.environmental_cache``
(C2.09 in-memory environmental observation TTL cache), and
``agentx.hive.salience_policy`` (C6.05 deterministic salience/archive-tier
policy; archive is not delete). Everything stored, returned, or decided by
Hive is inert data and grants no authority.
"""
