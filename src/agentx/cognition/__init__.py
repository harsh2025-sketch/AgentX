"""Ownership boundary: ``agentx.cognition``.

Canonical responsibility: provider-neutral reasoning, planning, and model
interfaces. Cognition produces proposals; it must not directly own machine
execution.

Implemented components include deterministic routing/escalation (A2.07/A2.08),
the Reasoner/model boundary (A2.01-A2.03), anti-loop (A2.09), the knowledge/
execution gap detector (A4.01), research objectives/acquisition/providers, and
bounded structured cognition-context construction from already-retrieved Hive
results (C6.07, :mod:`agentx.cognition.context_construction`). Everything
cognition consumes or produces is inert data: cognition invokes no authority,
and remembered strings never become instructions.
"""
