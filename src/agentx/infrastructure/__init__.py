"""Ownership boundary: ``agentx.infrastructure``.

Canonical responsibility: configuration, event transport, persistence adapters,
and other non-domain plumbing. This is a technical support layer; it is not an
authority boundary. Infrastructure may consume stable contracts from
``agentx.core`` but must not become the owner of shared domain contracts.

C1.03 implements the EventBus in ``agentx.infrastructure.event_bus`` while the
canonical C1.02 Event contract lives inward in ``agentx.core.events``.
C2.02 implements the durable KnowledgeStore in
``agentx.infrastructure.knowledge_store`` while the canonical knowledge record
contract lives inward in ``agentx.core.knowledge``.
C2.03 implements the durable ProcedureStore in
``agentx.infrastructure.procedure_store`` while the canonical procedure-record
contract lives inward in ``agentx.core.procedures``.
This package initializer deliberately remains declarative and side-effect free.
"""
