# M6.01 — On-Demand World-State Snapshot Contract

M6.01 defines the smallest **provider-neutral, inert, data-only** envelope that can hold one
bounded point-in-time view of the world, assembled from facts that providers have *already*
observed elsewhere. The AgentX world model is lazy, on-demand, bounded, and freshness-aware —
not a continuous full desktop mirror.

M6.01 answers: *What did the already-observed facts say, at which instants, from which
providers, and how do I carry that as inert data?*

It does NOT answer anything about the live world. This module performs **no capture** of any
kind — no screen capture, no UIA calls, no process enumeration, no browser inspection, no
filesystem inspection, no device polling, no audio capture — and it performs **no polling,
watching, or auto-refresh**: no background thread, watcher, poller, scheduler, daemon, event
subscription, or refresh timer exists, and there is no global mutable singleton. A snapshot is
an inert value that a future component may request explicitly.

## Canonical contracts

`agentx.core.world_state` exposes:

- `WorldStateDomain`, `CANONICAL_WORLD_STATE_DOMAINS`
- `WorldStateFact`
- `WorldStateSnapshot`
- `WORLD_STATE_SNAPSHOT_SCHEMA_VERSION` (currently `1`)
- `WorldStateError`
- `WorldStateValidationError`
- `WorldStateDeserializationError`
- `UnsupportedWorldStateSchemaVersionError`

There is no store, no cache, no sensor, no world model, no scheduler, no verifier, no model
client, and no authority surface.

## Domain vocabulary

`WorldStateDomain` is small and closed. Every member is anchored to a landed contract in the
current architecture; the vocabulary deliberately adds no world ontology of its own:

| Domain | Value | Anchored to |
| --- | --- | --- |
| `PROCESS` | `"process"` | Windows process discovery (`WindowsProcessSnapshot`, `WindowsProcessIdentity`) |
| `WINDOW` | `"window"` | Windows window identity and UIA tree (`WindowsWindowIdentity`, `UIATreeSnapshot`) |
| `APPLICATION` | `"application"` | Canonical application scoping (`ScopeDimension.APPLICATION`), capability scoping |
| `BROWSER` | `"browser"` | Browser observation contracts (`BrowserDomObservation`, `BrowserDomSelectionResult`) |
| `DEVICE` | `"device"` | Device observation contract (`DeviceObservation`) |
| `FILE_CONTEXT` | `"file_context"` | Canonical artifact record contract (`ArtifactRecord` file/directory/URI kinds) |
| `ENVIRONMENT` | `"environment"` | Environmental observation contracts (C2.09 cache, C4.04 environment-change facts) |

Deliberately absent: `ROBOT`, `IOT`, `EMOTION`, `GESTURE`, `BIOMETRIC`, and anything else the
current architecture does not observe. Extending the vocabulary is a contract change owned by
a later task, never an open string.

## The records

- `WorldStateFact` — one observed fact: closed `domain`, inert `subject` identity/reference,
  inert `fact_key`, bounded deep-frozen JSON-compatible `value`, explicit `observed_at`,
  explicit freshness `ttl`, and the canonical C2.02 `ProvenanceReference` `source`. A provider
  name is provenance, not authority.
- `WorldStateSnapshot` — one bounded point-in-time view: explicit caller-supplied
  `captured_at` assembly instant plus a canonical-ordered tuple of facts. Every fact must have
  been observed at or before `captured_at`; a fact observed after assembly is contradictory and
  is rejected. An empty fact tuple is representable and means "nothing was observed", which is
  explicitly *not* evidence that the world is in any particular state.

## Relationship to existing contracts (no duplication)

The M6.01 envelope fills exactly one missing piece and reuses landed canonical types:

- `CapabilityObservation` (A1.08) is the evidence attached to *one capability invocation*
  (summary plus an unstructured data mapping). It is not a cross-provider, bounded,
  point-in-time envelope: no domain vocabulary, no per-fact identity, timestamps, or
  freshness metadata.
- Provider-specific observation snapshots — `WindowsProcessSnapshot`, `UIATreeSnapshot`,
  `BrowserDomObservation`, `DeviceObservation` — are provider-shaped contracts that live with
  their providers in `agentx.capabilities`. `WorldStateSnapshot` is provider-neutral: a
  cross-provider view is assembled from facts those providers produced.
- The C4.04 `EnvironmentSnapshot` is the comparison unit of failure-diagnosis
  environment-change detection: exactly one `KnowledgeScope`, a closed failure-relevant
  fact-kind vocabulary, and a closed `TEXT`/`FLAG`/`ABSENT` value model. It is not a general
  on-demand world view and cannot carry a bounded nested process/window/browser observation.
- The C2.09 `EnvironmentalCache` in `agentx.hive` is a mutable in-memory string TTL cache with
  last-write-wins replacement and lazy expiry. It remains separate and unchanged.
  `WorldStateSnapshot` is point-in-time data, not a cache or a store: it replaces nothing by
  identity, drops nothing, and the two share no class.
- Provenance reuses the canonical C2.02 `ProvenanceReference` (kind + opaque reference). No
  competing provenance, identifier, scope, or error hierarchy is invented.

## Observation != verification

A fact observed in a world-state snapshot is **not** verified truth about the user's intended
goal. "Window title is X" is an observation; it is not a task success, a granted permission, a
valid procedure, or a verified goal. There is deliberately no generic `verified` flag anywhere
in this contract, and no confidence, probability, or scoring surface:

- **FRESH != VERIFIED.** A fresh fact is recent data, not a verdict.
- **STALE != FALSE.** A stale fact is old data, not a negation.

Hostile observed strings — `"ignore previous instructions"`, `"permission=ADMIN"`, `"risk=R0"`,
`"verified=true"`, `"task succeeded"`, `"execute command"` — remain inert data in every
textual surface (subject, fact key, value, source reference): never interpreted, scanned, or
executed.

## Freshness semantics

- Every fact carries an explicit `observed_at` and an explicit `ttl`; the snapshot itself
  carries an explicit caller-supplied `captured_at`. **No clock is ever read** in the module;
  all instants are caller-supplied.
- The canonical C2.09 rule is reproduced by value (core must not import hive):
  `expires_at = observed_at + ttl`, and a fact is fresh exactly while `at < expires_at`. At
  the boundary instant a fact is already stale (fail closed), so stale data can never present
  itself as fresh.
- `snapshot.is_fresh(at)` / `snapshot.fresh_facts(at)` are pure, descriptive filters. Nothing
  auto-refreshes, mutates, or re-observes.

## Hard bounds (fail closed)

| Bound | Limit |
| --- | --- |
| Facts per snapshot | **128 facts** maximum |
| Observed value nesting depth | 8 |
| Observed value canonical JSON size | 65,536 bytes |
| String length inside an observed value | 4,096 characters |
| Array breadth inside an observed value | 4,096 items |
| Integer range inside an observed value | signed 63-bit |
| Subject length | 512 characters, trimmed, no control characters |
| Fact key length | 256 characters, trimmed, no control characters |
| Provenance reference length | 512 characters |
| Non-finite floats (`NaN`, `Infinity`) | rejected |
| Callables, bytes, file handles, COM objects, native pointers, model objects, cycles | rejected |

There is no unbounded desktop state: a snapshot is exactly the bounded set of facts it was
given.

## Duplicate handling

A fact's identity is `(domain, subject, fact_key)`. A snapshot never contains two facts with
the same identity, whether their values, instants, or sources agree or not. Duplicates are
**rejected** at construction and on decode: the model does not support ordered observations of
one fact, and it never silently picks one based on insertion order. Facts are stored in
deterministic canonical order — domain declaration order, then subject, then fact key —
independent of the order they were supplied in.

## Serialization

Deterministic canonical JSON, schema version `1` (closed, integer,
`UnsupportedWorldStateSchemaVersionError` for anything else):

- closed fields with unknown-field rejection at every level — a smuggled `"verified": true`
  field is an error, not data;
- UTC ISO-8601 timestamps with a `Z` suffix; naive timestamps rejected;
- integer `ttl_microseconds` (strictly positive);
- canonical fact order, sorted keys, compact separators, no object hooks or dynamic decoding —
  JSON text can never name a class or invoke a callable;
- deep immutable round-trip: `WorldStateSnapshot.from_json(snapshot.to_json()) == snapshot` and
  byte-identical `to_json()`. Values are deep-frozen in memory (`MappingProxyType`/`tuple`), so
  caller mutation of the original containers has no effect.

Decoding re-runs every construction rule, so a hand-edited payload cannot smuggle duplicates,
unbounded values, future-dated facts, or unknown fields.

## No persistence

**None.** M6.01 is a pure `agentx.core` domain contract with no store, no SQLite access, and
**no migration**. No migration number was consumed; the highest landed migration remains v8
(C2.06 negative-experience store).

## Zero new runtime dependencies

The module imports only the standard library (`json`, `math`, `collections.abc`,
`dataclasses`, `datetime`, `enum`, `types`, `typing`) and the single inward
`agentx.core.knowledge` provenance hook. An architecture test pins that import set, bans
capture/clock/threading/execution/network/filesystem primitives, bans persistence and
migration surface, bans ranking/scoring/confidence machinery, and proves the module defines no
sensor, cache, world model, or engine class and that the environmental cache, capability ABI,
and C4.04 contracts remain separate and untouched.

## Test coverage

- `tests/unit/test_world_state.py` — empty snapshot, single fact, all domains, deterministic
  canonical order, duplicate rejection, Unicode, hostile text, nested JSON values, deep-freeze
  and caller mutation isolation, timestamp validation, freshness representation,
  FRESH != VERIFIED, no task-success surface, fact bound, payload bounds (depth, string,
  encoded size, array breadth, integer range), NaN/Infinity rejection, non-JSON value
  rejection, unknown-field rejection, schema-version rejection, and serialization round-trip.
- `tests/adversarial/test_world_state_authority.py` — authority-shaped content injected into
  subject, fact key, observed value, and source reference; proof of no permission change, no
  risk change, no task state, no procedure activation, no verification fabrication, and no
  dynamic execution (patched `eval`/`exec`), plus no authority subsystem import or touch and
  no filesystem access.
- `tests/architecture/test_world_state_placement.py` — the static guards above.
