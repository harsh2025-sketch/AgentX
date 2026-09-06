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
A8.01 implements the durable append-only StrategyPerformanceStore in
``agentx.infrastructure.strategy_performance_store`` while the canonical
measured strategy-performance record contract lives inward in
``agentx.core.strategy_performance``.
C7.07 implements the generic bounded event-watcher framework in
``agentx.infrastructure.event_watcher`` over the canonical Event contract, the
EventBus, and the persistent EventJournal. A watcher match is data only.
This package initializer deliberately remains declarative and side-effect free.
"""
