# M10 World Model — AX-409 / AX-411 / AX-414–AX-421

`agentx.world_model` is the outer composition layer for AgentX's Milestone-10
current-environment model. It does **not** replace `agentx.core.world_state`.
The M6.01 core contract remains the inert provider-neutral snapshot envelope;
M10 composes existing Windows, browser, filesystem, device, Task, provenance,
and Hive contracts into a lazy runtime view of state that is relevant now.

The governing invariants are:

- **WORLD STATE != AUTHORITY** — a world observation grants no `Permission`,
  changes no risk, bypasses no `ActionGate`, clears no emergency stop, and
  cannot make a Task succeed.
- **OBSERVATION != VERIFICATION** — an action/native call reporting success is
  not converted into a verified state transition. Canonical verifiers remain
  separate.
- **CACHED STATE != CURRENT TRUTH** — callers receive explicit freshness and
  refresh outcomes. Stale observations are never silently returned as fresh.
- **WORLD MODEL != HIVE** — current environmental state is ephemeral or
  reconstructable unless an explicit relationship is written through the
  canonical `KnowledgeStore` boundary.

## Entity and identity model

Every current-state object has an environment-scoped `WorldEntityId`:

`environment_id + WorldEntityKind + stable value`

The closed M10 kinds are perception, application, process, window, browser
session, browser page, filesystem entity, and device. Display names, window
text, browser titles, URLs, and other untrusted labels are never primary
identity.

Identity sources are deliberately structured:

- applications use `ApplicationIdentity(environment, platform, stable_id)`;
- Windows process identity combines the observed PID with image identity so a
  changed image produces a different world identity when evidence permits;
- window identity uses the observed handle plus owning PID for that snapshot;
- browser identity reuses canonical provider/session/target identities;
- filesystem identity is the normalized absolute path in the relevant host
  path semantics;
- device identity reuses the canonical device provider/device id or a bounded
  local-host identity;
- perception identity is explicitly supplied by the future observation
  producer and is never derived from screen text.

All identities are environment-scoped. Cross-environment device/perception or
application/process/window relationships are rejected.

## Observation metadata and freshness

`ObservationMetadata` is the common evidence envelope. It carries:

- observation id;
- canonical provenance source;
- UTC observation time;
- TTL;
- environment id;
- optional device id;
- optional Task id;
- optional execution correlation id.

Freshness follows the same strict TTL shape used elsewhere in AgentX:
`FRESH` only while `at < observed_at + ttl`. Expiry or explicit invalidation is
`STALE`. Absence of trustworthy evidence is `UNKNOWN`.

Freshness is evidentiary only. `FRESH` does not mean verified, authorized, or
correct in the real environment.

## AX-409 perception representation

`PerceptionObservation`, `PerceptionRegion`, and `ScreenBounds` provide the
canonical typed representation required before later visual providers exist.
They model source/time/environment/device/surface, bounds, structured regions,
optional uncertainty confidence, Task/correlation metadata, and links to
structured UIA/DOM observations. The representation has deterministic JSON
serialization and strict malformed-input checks.

No capture provider, screenshot store, OCR pipeline, visual grounding,
fallback router, DPI normalization, or multi-monitor policy is implemented
here; those remain later M10 tasks.

Observed screen text is preserved as inert untrusted data.

## AX-411 lazy cache

`LazyWorldStateCache` is bounded, thread-safe, and on demand. It has no polling
thread and no full-desktop mirror. Providers are registered per entity kind and
are called only when a lookup requires refresh.

A lookup reports both `WorldFreshness` and `CacheRefreshState`:

- cache miss;
- fresh hit;
- stale hit;
- invalidated;
- refresh success;
- refresh failure;
- source unavailable.

If refresh fails, a previous value is returned only with `STALE`; if no prior
value exists, freshness is `UNKNOWN`. A refresh racing with invalidation/update
is discarded using a per-entity epoch so the raced observation cannot revive
known-stale state.

The cache is bounded by `max_entries`, evicts deterministically by oldest
observation then entity identity, and supports stale cleanup. It is
**ephemeral/reconstructed on restart** and is intentionally not persisted.

## AX-414 application registry

`ApplicationRegistry` models an application separately from processes,
windows, executable paths, and running instances. A record carries stable
identity, canonical/display names, executable/package ids, launch targets,
aliases, availability, process/window relationships, provenance/time/freshness,
and environment scope.

Alias lookup is bounded by the registry; ambiguous aliases return multiple
records rather than silently picking a winner. A stable identity conflicting on
canonical name fails closed. A refreshed complete Windows snapshot reconciles
missing process/window relationships and marks an application unavailable when
no current relationship remains.

`stable_snapshot_json()` persists only stable registry metadata. On restart,
`from_stable_snapshot_json()` restores those stable fields while resetting
availability and running relationships to `UNKNOWN`/empty. Current availability
must be re-observed.

Application metadata and launch targets are descriptive only; they grant no
launch permission.

## AX-415 active-window state

The world model represents active-window state with an environment-scoped
synthetic current-state key (`__active_window__`) whose value is still a
canonical `WindowState` observation. Windows discovery data supplies window and
process identity; a caller must supply a trustworthy foreground-handle
observation from the platform composition boundary. Without that evidence the
active-window cache entry is invalidated/unknown rather than guessed.

Focus-affecting governed actions invalidate only the environments associated
with the same Task binding. Native/action success never makes a requested
window active. The next independent observation is required to make active
state fresh again. Canonical N2.25 Windows transition verification remains the
verification boundary.

## AX-416 process state

A Windows process snapshot produces `ProcessState` objects containing PID,
image metadata, parent PID where observed, application link where known,
window relationships, availability, and common observation metadata.

Because A5.02 discovery is a full point-in-time process/window snapshot,
refresh reconciliation explicitly invalidates previously cached process/window
identities absent from the new snapshot. This supplies process termination,
window disappearance, and available-evidence PID-reuse handling without
pretending a PID alone is permanent identity.

Process presence never implies Task success.

## AX-417 browser state

Browser state reuses canonical browser provider/session/target and DOM
observation contracts. `BrowserSessionState` and `BrowserPageState` retain the
provider/session/target identity, current title/URL/origin, document version,
DOM observation reference, active-page relationship, timestamp/provenance, and
freshness.

A changed URL or document version invalidates the previous page observation
before the replacement is cached. Selecting a new active page stales a prior
active page in the same session. Closing an active page invalidates both the
page and the session that named it. Governed browser actions invalidate only
browser entities explicitly bound to the same Task; unrelated tabs are not
blanket-invalidated.

Web titles, URLs, query parameters, and DOM text remain inert untrusted data.

## AX-418 filesystem context

Filesystem context is lazy and task-relevant; AgentX does not index a disk.
`track_filesystem_path()` registers a normalized absolute identity and the
filesystem provider performs one `stat` on demand. Results explicitly
represent `EXISTS`, `MISSING`, or `INACCESSIBLE`, plus entity type, bounded
metadata, provenance/time/freshness, and Task/correlation metadata.

Windows paths use `ntpath.normpath` + `normcase`; other hosts use native
absolute-path normalization. A known mutation invalidates only the relevant
filesystem entity, after which refresh reconstructs current metadata. Rename or
move is represented as invalidation of the old identity plus tracking of the
new normalized identity; delete refreshes to `MISSING`.

The filesystem world model never grants filesystem permission and never reads
file contents.

## AX-419 device state

`DeviceState` composes the canonical device protocol into world state: device
identity, platform, local/remote role, availability, reported capability
availability/health, last-seen time, environment, provenance, and freshness.
The local Windows host can be represented without implementing Android.

When a device becomes unavailable, observations explicitly scoped to that
device are targeted for invalidation; unrelated environmental state is left
alone. Device claims and capability-health metadata are descriptive only and
cannot grant execution authority.

## AX-420 Task/world binding

`TaskWorldBinder` stores bounded, explicit, evidence-backed links between one
canonical `TaskId`/`ExecutionContext.correlation_id` and world entities.
Evidence is mandatory. Bindings are Task-local: one Task never inherits another
Task's entity set.

Affected entities can be removed after invalidation/mutation, and Task terminal
events remove the Task-local binding. `assemble()` returns only the bound
entities and preserves each cache lookup's `FRESH`/`STALE`/`UNKNOWN` status.
Bindings are **ephemeral runtime state**; after restart they are absent rather
than trusted without a live Task/runtime reconstruction policy.

A binding is relevance data, not permission to mutate the entity.

## AX-421 Hive/world boundary

`WorldHiveLink` is the explicit relationship between a current world entity and
a canonical `KnowledgeId`. It carries evidence, observation time, environment
scope, relationship verification status, optional contradiction/supersession
ids, and an explicit durable/transient flag.

Transient links live only in the runtime linkage object. Durable links are
stored as ordinary `KnowledgeRecord(kind=OBSERVATION)` rows through the
existing `KnowledgeStore` with derived provenance and environment scope. They
are born **UNVERIFIED**; inserting a world observation never promotes durable
knowledge to `VERIFIED`.

On restart, durable links can be reconstructed from the canonical Knowledge
store. Loading historical links never populates the live world cache, so stale
Hive history cannot overwrite a fresh or unknown current-world observation.
Contradiction/supersession references are preserved as relationship data and
never auto-resolve truth.

There is no second Hive database, event store, relationship authority, or
world-state persistence migration.

## Targeted invalidation

Known state-changing boundaries invalidate only affected evidence:

- refreshed Windows discovery stales disappeared processes/windows and removes
  affected Task bindings;
- missing trustworthy foreground evidence stales active-window state;
- focus-affecting actions stale the active-window state for Task-related
  environments;
- browser navigation/document replacement stales the previous page;
- active-page changes stale the prior active page in that session;
- page close stales the page and an owning active session;
- filesystem mutation stales the Task-bound filesystem entity;
- device unavailability stales observations scoped to that device;
- Task completion/failure removes Task-local bindings.

A generic action event without enough identity evidence is not used to perform
unsafe global invalidation. Unknown impact remains unknown/stale until a
structured observation refreshes it.

## Persistence and restart table

| State | Policy |
| --- | --- |
| Perception observations | Ephemeral/re-observed |
| Lazy cache | Ephemeral/reconstructed |
| Active window | Ephemeral/re-observed |
| Process/window current state | Ephemeral/re-observed |
| Browser current state | Ephemeral/re-observed |
| Filesystem current metadata | Ephemeral/re-observed |
| Device current availability | Ephemeral/re-observed |
| Task/world bindings | Ephemeral runtime state |
| Application stable metadata | Deterministically serializable; current availability reset on restore |
| Durable world/Hive links | Persisted in canonical `KnowledgeStore` |
| Transient world/Hive links | Ephemeral |

This split is intentional: SQLite existence is not a reason to persist transient
environmental truth.

## Failure and race semantics

Providers can fail, return no observation, return the wrong identity, or race
with invalidation. These outcomes are explicit `REFRESH_FAILURE` or
`SOURCE_UNAVAILABLE`; cached fallback is never marked fresh. Filesystem access
errors become `INACCESSIBLE`, missing paths become `MISSING`, and malformed
serialized perception/Hive data fails closed.

The cache and registries use locks around bounded mutable state. Refresh runs
outside the cache lock but commits only if its entity epoch is unchanged.
World/Hive durable insertion and in-memory linkage registration are serialized
by the linkage lock so a failed durable write does not leave a misleading live
"durable" link.

## Future extension points

Later perception providers can produce `PerceptionObservation` without changing
Task bindings, freshness, or Hive linkage. Future device platforms can map their
canonical `DeviceDescriptor` into `DeviceState` without adding Android
execution here. Screen capture, frame identity, visual grounding/fallback,
evidence ranking, DPI/multi-monitor policy, Android execution, voice,
proactivity, and self-extension remain outside this package.
