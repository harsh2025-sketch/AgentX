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
"""
