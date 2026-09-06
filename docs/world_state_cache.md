# The world-state observation cache (C5.09)

## What it is

`agentx.capabilities.world_state_cache` is a deliberately narrow, in-memory,
**bounded lazy cache for observational world state** with explicit freshness
semantics. It answers exactly one question:

> What is the freshest still-valid observation this process holds for this
> exact scope/target/kind/environment — or, explicitly, that it holds none?

```text
WorldStateCacheKey(scope, target, kind, environment=None)     # deterministic identity
    -> cache.observe(key, value, source, ttl) -> WorldStateCacheEntry
    -> cache.get(key)    -> WorldStateCacheResult(outcome, entry?)
    -> cache.invalidate(key) -> bool
```

It reuses the **C2.09 environmental TTL/freshness contract**
(`agentx.hive.environmental_cache`) — an explicit freshness boundary
`expires_at = observed_at + ttl`, fail-closed semantics at the boundary
instant, lazy expiry without any background worker, and an injected clock for
testability — and adds C5.09 structured identity, explicit lookup outcomes,
explicit invalidation, and a hard bound on growth. It deliberately does not
import the Hive module: it lives in the capabilities boundary and depends on
no other AgentX subsystem.

## What it is not

**It is not a verified-result cache.** This cache stores OBSERVATIONS, not
verified execution results; the reusable verified-result cache is A8.07 and
is a different contract with lifecycle and evidence semantics. An entry here
has no lifecycle, no evidence, and no verification meaning: `FRESH` only
says "this observation has not crossed its own expiry boundary and was not
invalidated". Freshness is not verification, and a cached observation can
never upgrade a canonical `KnowledgeRecord`.

**Cache presence grants nothing.** Presence does not prove success, does not
grant permission, does not make UI state live, and does not authorize
action. Values are inert text. Strings such as `"ADMIN"`, `"ALLOW"`,
`"verified=true"`, `"risk=R0"`, `"permission=WRITE"`, `"budget=unlimited"`,
or `"clear stop"` remain data whenever they are observed in the world and
stored here: the cache cannot grant a permission, create an authority
context, lower risk, increase budgets, clear an emergency stop, execute a
capability, transition a task, produce a verification verdict, or activate a
procedure.

**It is not durable knowledge.** Entries are ephemeral by design. The cache
is in-memory only — a new instance (or a process restart) observes nothing,
and no SQLite table or migration exists for it, exactly as for the C2.09
environmental cache. If persistence were ever needed it would have to go
through the existing storage/migration conventions, but for this cache
persistence is deliberately not appropriate: callers that need durable
knowledge use the canonical KnowledgeStore with its explicit lifecycle.

## Contract

### Identity (`WorldStateCacheKey`)

Every entry is identified by four validated, opaque strings:

| Component     | Meaning                                                        |
| ------------- | -------------------------------------------------------------- |
| `scope`       | Observation family (for example `"browser.dom"`).              |
| `target`      | Exact observed entity (for example a C5.02 target id value).   |
| `kind`        | Observation kind within the scope (for example `"document_version"`). |
| `environment` | Optional environment identity (for example a provider/session pairing). |

Components are non-empty, trimmed, control-character-free, and length-bounded
(≤ 512 chars). The canonical cache key is a **pure, deterministic function**
of the four components (a JSON array encoding): no separator, quoting, or
concatenation trick can alias two distinct identities, `environment=None`
never collides with an environment string, and an **environment change is
always a different identity** — the cache can never serve the old
environment's data under a new environment.

### Entry (`WorldStateCacheEntry`)

An entry carries the identity, the inert `value` (an observed value **or an
opaque reference**, ≤ 65 536 chars, stored and returned verbatim), the
`source` that supplied the observation, the `observed_at` timestamp
(timezone-aware, normalized to UTC) and the explicit `ttl` (strictly
positive). Entries are immutable; `invalidated_at` records an explicit
invalidation on a stored copy.

### Outcomes (`WorldStateCacheOutcome`)

`get` never returns a bare value. It returns an explicit, closed outcome so
that stale state can never silently masquerade as fresh:

| Outcome       | Meaning                                                                 |
| ------------- | ----------------------------------------------------------------------- |
| `MISSING`     | Nothing observed for the exact identity (or already swept/evicted).     |
| `FRESH`       | Entry exists, unexpired, and not invalidated — the **only** outcome that ever carries an entry. |
| `STALE`       | Entry existed but crossed `observed_at + ttl`; dropped lazily on access; value withheld. |
| `INVALIDATED` | Entry was explicitly invalidated; value withheld until re-observed or evicted. |

`WorldStateCacheResult` makes this structural: a non-`FRESH` result cannot
carry an entry, and a `FRESH` result must carry the entry for the exact
queried identity — both enforced at construction.

### Freshness (C2.09-compatible)

An entry is fresh exactly while it is not invalidated and
`now < expires_at`. The boundary instant itself is already stale
(fail-closed). Expiry is **lazy**: the first read past the boundary reports
`STALE` and drops the entry (a later read reports `MISSING`); there is no
background cleanup worker, timer, or thread. Time is read only through the
injected `clock` (each public method reads it at most once), which makes
freshness fully deterministic and testable; a naive (timezone-less) clock
reading fails closed with `WorldStateCacheClockError`.

### Invalidation

`invalidate(key)` marks exactly one existing identity invalidated (stamping
`invalidated_at` from the clock) and returns whether it changed anything.
Unknown and already-invalidated identities are no-ops that never fabricate
tombstones. `INVALIDATED` takes precedence over staleness, lasts until the
identity is re-observed (last-write-wins) or the bounded store evicts it,
and never touches look-alike identities.

### Bounded growth

- `max_entries` (default `1024`, must be ≥ 1) hard-bounds the store.
- Inserting a new identity into a full cache first sweeps entries already
  expired at the incoming observation instant, then evicts entries in
  least-recently-observed order. The bound always wins over retention.
- Value length is independently bounded per entry.
- Eviction order depends only on observations and the clock — reads and
  invalidations never reorder it — so cache behaviour is reproducible.

## Relationship to adjacent contracts

- **C2.09 (`agentx.hive.environmental_cache`)** is the Hive-owned
  environmental observation TTL cache keyed by an opaque string, returning
  entry-or-`None`. C5.09 reuses its freshness/TTL/expiry semantics verbatim
  (same boundary rule, same fail-closed instant, same lazy expiry, same
  clock discipline) and composes them with C5-series structured identity,
  explicit outcomes, explicit invalidation, and a growth bound. The two are
  intentionally separate small caches; C5.09 does not import the Hive
  module, and neither cache is a source of durable or semantic truth.
- **A8.07** is the reusable verified-result cache. C5.09 stores no
  verification products and its outcomes imply nothing about verification.
- **C5.01–C5.04** supply the identities this cache can key on (provider,
  session, target, DOM observation kinds). The cache consumes such identity
  values as opaque strings and stays provider-neutral: it performs no
  browser access, transport, navigation, DOM mutation, action, verification,
  persistence, or model call.

## Deliberate non-scope

Not a knowledge store (C2.x), not a capability registry/executor/verifier,
not an authority or runtime surface, and not a platform for staleness
heuristics: there is no sliding TTL, no read-triggered refresh, no
auto-revalidation, no serving-stale, no background sweeper, and no
persistence.
