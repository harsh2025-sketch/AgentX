"""Ownership boundary: ``agentx.core``.

Canonical responsibility: shared domain contracts and Agent Runtime primitives
that other subsystems build on.

This package is a low-level, non-authority foundation. It may hold contracts and
runtime primitives; it does not implement the Trusted Kernel, capabilities,
Hive storage, or any adaptive subsystem.

Status:

    - ``ids`` — canonical opaque domain identifiers (A1.04).
    - ``errors`` — structured error model and taxonomy (A1.04).
    - ``result`` — typed Success/Failure result abstraction (A1.04).
    - ``tasks`` — canonical immutable Task schema, controlled status and
      priority vocabulary, and deterministic serialization (A1.05). Data
      model only: the Task state machine is A1.06 and the Task Manager is
      owned by Day 2.
    - ``task_state`` — canonical Task status transition contract (A1.06):
      the explicit legal transition matrix, terminal-state semantics, and
      pure/stateless helpers to ask, validate, and derive a transitioned
      Task. Transitions only: no Task Manager, execution, scheduling,
      cancellation, or persistence.
    - ``events`` — canonical immutable Event envelope, taxonomy, payloads,
      validation, and deterministic serialization (C1.02 / A1.02b).
    - ``knowledge`` — canonical immutable KnowledgeRecord contract for
      semantic/knowledge data: identity, type, status/trust vocabulary,
      minimum provenance hook, and typed scope (C2.02). Records only: Hive
      lifecycle policy and retrieval live in ``agentx.hive`` tasks; SQLite
      persistence lives in ``agentx.infrastructure.knowledge_store``.
"""
