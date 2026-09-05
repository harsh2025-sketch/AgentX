"""Ownership boundary: ``agentx.procedures``.

Canonical responsibility: Procedure Graph IR/runtime and skill lifecycle.
Procedures represent scope/preconditions and are candidates until verified and
gated; they must not bypass kernel policy.

Status: not implemented. This package deliberately contains no code yet. The
Procedure Graph canonical IR (ACTION/VERIFY/BRANCH/REASON node semantics and
interpretation) remains owned by its Day-3 task (A3.01). C2.03's durable
procedure-record storage lives inward, mirroring the C2.02 knowledge-record
split: the canonical record contract is ``agentx.core.procedures`` and the
SQLite store is ``agentx.infrastructure.procedure_store``.
"""
