# AgentX runtime-to-UI state protocol

C7.01 adds the read-oriented, provider- and framework-neutral protocol through
which a future HUD/debug UI can *observe* AgentX runtime state. The protocol
lives in `agentx.core.ui_state` and is data only: it is not a HUD, not a web or
desktop frontend, not a transport, not an approval flow, and not the Trusted
Kernel.

## Trust model

**The UI is not a trusted kernel.** Every field of the protocol is an
*observation*. Two structural rules enforce this:

- **Closed, exact wire schema.** Every object level of the serialized
  snapshot has a closed set of exact keys, and deserialization rejects unknown
  fields fail-closed (they are never ignored). Authority-shaped fields a UI
  consumer might try to send back (`approved`, `granted`, `bypass`, `execute`,
  ...) are not in the schema and therefore cannot be admitted.
- **No mutation surface.** Every value type is frozen, the module exposes no
  setter/apply/approve/execute API, and the only transformation the module
  performs, `project_state_event`, is a pure function that returns a new
  snapshot. Structurally the module imports no authority boundary (no
  `agentx.kernel`, no `agentx.capabilities`), so it holds no path to
  permissions, gates, budgets, or execution.

Hostile UI text is inert: text fields (operation descriptors, verification
details, error messages/details) are validated (non-empty, trimmed, bounded,
control-character-free) and carried as data. Nothing in the protocol parses
any text as a command, a level, or a decision.

## Canonical reuse

| Protocol field            | Canonical owner                                                        |
| ------------------------- | ---------------------------------------------------------------------- |
| `task.task_id`            | `agentx.core.ids.TaskId` (A1.04)                                       |
| `task.status`, `task.priority` | `agentx.core.tasks.TaskStatus` / `TaskPriority` (A1.05)           |
| status transition checks  | `agentx.core.task_state.can_transition` (A1.06 matrix)                 |
| `error.*`                 | `agentx.core.errors.AgentXError` (A1.04; `cause` never carried)        |
| event channel             | `agentx.core.events.Event` / `EventType` (C1.02)                       |
| `plan.procedure_id`       | `agentx.core.ids.ProcedureId` + C2.03 `(procedure_id, revision)`       |
| `execution.level`         | mirrors `agentx.cognition.router.ExecutionLevel` (A2.07)               |
| `approval.risk_level`     | mirrors `agentx.kernel.risk.RiskLevel` (R0-R4)                         |
| `approval.permission`     | mirrors `agentx.kernel.permissions.Permission`                         |

`agentx.core` is the dependency leaf and must not import outer subsystems, so
execution-level, risk-level, and permission values are mirrored as closed
string vocabularies (exact value parity, asserted by
`tests/architecture/test_ui_state_boundaries.py`). The canonical modules
remain the authority for those concepts.

`progress` has no canonical owner yet; C7.01 defines it as a bounded
deterministic fraction in `[0.0, 1.0]` (or absent).

## Wire schema (v1)

```json
{
  "schema_version": 1,
  "task":         { "task_id": "<uuid>", "status": "running", "priority": "high" },
  "execution":    { "level": "L2_COMPILED" | null, "operation": "browser.dom.click" | null },
  "plan":         { "procedure_id": "<uuid>", "revision": 3 } | null,
  "verification": { "status": "not_started" | "passed" | "failed", "detail": "..." | null },
  "approval":     { "request_id": "<uuid>", "operation": "...", "permission": "WRITE", "risk_level": "R2" } | null,
  "error":        { "code": "a.b", "message": "...", "category": "permission", "retryability": "non_retryable", "details": {} } | null,
  "progress":     0.5 | null,
  "timestamp":    "2026-09-06T12:00:00.000000Z"
}
```

Optional sections are explicit `null` on the wire. `schema_version` is always
present; any version other than 1 fails closed with
`UnsupportedUiStateSchemaVersionError`. Serialization is canonical JSON (sorted
keys, compact separators, no NaN) with UTC ISO-8601 microsecond timestamps, so
identical state always serializes to identical bytes.

## API

```python
snapshot = UiStateSnapshot.from_task(task, timestamp=now)  # initial observation
wire = snapshot.to_json()  # deterministic JSON
restored = UiStateSnapshot.from_json(wire)  # strict validation

next_state = project_state_event(snapshot, event)  # pure projection
```

`project_state_event` takes only canonical `Event` values (never raw UI JSON)
and is closed and deterministic:

- The event must be bound to the snapshot's exact task (`event.task_id` parses
  to the snapshot's `TaskId`); otherwise `UiStateProjectionError`.
- `task.started` / `task.completed` / `task.failed` move the status to
  running / succeeded / failed, and only when the canonical A1.06 matrix
  allows the move; an illegal move raises rather than being coerced.
- `verification.completed` records the observed verdict
  (`passed` / `failed` with payload detail) and applies only while the task is
  running.
- `task.created` cannot be projected onto an existing snapshot; the initial
  snapshot is constructed from the Task itself.
- Every other event kind is an explicit no-op (returns the snapshot unchanged).

The resulting snapshot's `timestamp` is the event's canonical timestamp; all
other fields are carried through exactly.

## Deliberate non-scope

- No HUD, web frontend, desktop frontend, or visual styling.
- No transport, provider adapter, or process boundary (later C7 tasks own how
  snapshots/events reach a UI).
- No permission or approval decisions, no approval flow, no task execution, no
  event publication, no persistence.
- No runtime mutation of any kind, by design and by construction.

Owner: C7.01. Tests: `tests/unit/test_ui_state.py`,
`tests/adversarial/test_ui_state_authority.py`,
`tests/architecture/test_ui_state_boundaries.py`.
