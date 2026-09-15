# AX-120–124 security notes

- Knowledge, provenance, evidence, confidence state, contradiction edges, supersession edges, and scope are inert data.
- No completion code imports or mutates the Permission Engine, Action Gate, risk policy, Emergency Stop, ResourceBudget, capability execution loop, or Task lifecycle.
- Confidence/revalidation evidence cannot authorize an action or mark a task successful.
- Contradiction preserves both claims and never chooses a winner.
- Supersession preserves historical data and rejects cycles/self-replacement through the existing canonical store.
- Protected scope retrieval requires structured non-empty scope metadata; free text cannot widen scope.
- Global records require an explicit typed inclusion policy.
- Revalidation history is append-only and restart-safe through the existing EventJournal.
- Replay and graph traversal are explicitly bounded and fail closed rather than silently returning partial state.
