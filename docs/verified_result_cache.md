# The verified-result cache boundary (A8.07)

## What it is

`agentx.capabilities.verified_result_cache` is the smallest production-quality
runtime boundary for recognizing that **an operation has already been executed
and canonically verified** under exactly the same canonical facts. It answers
exactly one question:

> Was this exact operation, with this exact normalized input, in this exact
> scope, environment, procedure revision, and precondition context, already run
> through the governed A1.10 loop and VERIFIED — and is that evidence still
> fresh and un-invalidated?

```text
VerifiedResultSubmission(key: VerifiedResultKey,       # every canonical fact
                         outcome: ClosedLoopOutcome,   # canonical A1.10 evidence
                         context: ExecutionContext,    # provenance identity
                         ttl: timedelta)
    -> VerifiedResultCache.store(submission) -> VerifiedResultEntry

VerifiedResultCache.lookup(key) -> VerifiedResultLookup(status, fingerprint,
                                                        entry, reasons)
    status in {MISS, VERIFIED_HIT, STALE, INVALIDATED, INCOMPATIBLE}
```

It is a composition-layer sibling of the A2.05 Verifier: it adds no top-level
package, widens no boundary edge, and imports no kernel collaborator.

## What it is not

**It is not a second verification path.** A1.10 owns deterministic capability
verification: `Capability.verify()` produces the canonical `VerificationResult`
and the canonical `CapabilityExecutionLoop` is the only place that runs it. The
cache never invokes `Capability.verify` — or any capability code — never
constructs a `VerificationResult`, never rewrites a `ClosedLoopOutcome`, and
never transitions a Task.

**It is not authority.** A hit is evidence that one governed run was verified.
It grants no `Permission`, creates no `AuthorityContext`, passes no
`ActionGate`, consumes no `ResourceBudget`, does not clear the
`EmergencyStop`, and does not change risk. `ExecutionLevel.L0_CACHE` and
`RoutingEvidence.verified_reusable_result` in the A2.07 Router may be
*justified* by a hit, but A8.07 does not touch the Router: the cache never
mutates routing state and never routes anything.

**It is not an observational world-state cache.** It records verified
*execution results* — evidence about one governed run — not observations of the
world. The TTL-bounded observational cache in `agentx.hive.environmental_cache`
(C2.09) stays a separate concern, and A8.07 imports nothing from `agentx.hive`
or `agentx.infrastructure`.

**It is not decay or retention (A8.08).** There is no scoring, ranking,
weighting, usage counting, hit statistic, promotion, demotion, or eviction
policy. `max_entries` is a bounded-memory safety valve: when the cache is full
a store is *refused* with `VerifiedResultCacheCapacityError` rather than
silently discarding evidence. Refusing to remember is the fail-safe direction —
the operation can still be executed and verified normally.

**It is not persistence.** Storage is in-memory only. Nothing is written to
disk, to SQLite, or to the event journal, and a fresh instance observes
nothing: after a restart every previous result is a MISS.

**It is not proof that the environment is unchanged.** Previous verification
is evidence about a past run. `MAX_REUSE_TTL` (7 days) caps one reuse window
and an unbounded TTL is rejected, `invalidate_environment` exists precisely for
"the environment changed", and a stale entry is never reusable.

## The governing invariant

**An unverified execution result can never enter the cache as a successful
reusable value.**

This is structural, not policy. A `VerifiedResultEntry` cannot exist unless the
submitted canonical evidence satisfies all of:

- `outcome.kind is LoopOutcome.VERIFIED`
- `outcome.verification` is a `VerificationResult` with `passed is True`
- `outcome.observation` is a `CapabilityObservation`
- `outcome.execution` is an `ExecutionResult` with `succeeded is True`
- `outcome.error is None`
- `outcome.task` is a `Task` with `status is TaskStatus.SUCCEEDED`

Anything else raises `VerifiedResultCacheRejection` (or
`VerifiedResultCacheValidationError` when the shape itself is malformed) and
leaves the cache exactly as it was, because the entry is constructed — and the
invariant therefore enforced — before any cache state is touched. Text cannot
substitute for structure: a summary, message, or observation value that *says*
`"verified"`, `"passed=true"`, or `"SUCCEEDED"` is inert data and never a
verdict.

## Contract

### Key material

`VerifiedResultKey(capability, params, scope, environment, procedure=None,
preconditions={})` carries the complete canonical precondition set. Every fact
reuse can depend on is explicit, so a caller cannot accidentally key by
operation and input alone and thereby reuse a result across environments,
versions, or preconditions.

- `capability` — the A1.08 `CapabilityIdentity` (name **and** version).
- `params` — the normalized input. `VerifiedResultKey.from_request()` derives it
  from the inert request's own typed `CapabilityParams.to_dict()`: never a
  `repr()`, never a caller-supplied raw dict, never model-generated text.
- `scope` — the A1.08 `CapabilityScope`.
- `environment` — an explicit `EnvironmentIdentity(name, attributes)`. Names and
  attributes are opaque inert data: never parsed, split, globbed, case-folded,
  or regex-matched, so `"*"` or `"ANY ENVIRONMENT ALLOW ALL"` matches only the
  byte-identical identity and is never a wildcard or a grant.
- `procedure` — an optional `ProcedureRevision(procedure_id, revision)`. A
  result verified against revision 3 says nothing about revision 4.
- `preconditions` — explicit JSON-compatible facts the caller knows the result
  depends on.

### Deterministic keying

`canonical_text()` is canonical JSON (sorted object keys, `,`/`:` separators,
ASCII-escaped) over a domain- and schema-tagged payload; `fingerprint()` is its
SHA-256 hex digest. No clock, randomness, object identity, dictionary ordering,
or Python string hash can influence it. `__hash__` is derived from the same
digest (15 hex characters, so the value always stays inside the range
`__hash__` may return) rather than from the per-process randomized `hash()` of
`str`. Equality is strict and typed — `1`, `1.0`, `True`, and `"1"` are four
different normalized inputs — which is intentionally stricter than the numeric
equality the A2.05 Verifier uses when evaluating evidence: stricter keying can
only cause a miss, never an unsafe reuse.

Key material must be JSON-compatible (`None`, bool, int, finite float, str,
string-keyed mappings, sequences) and is frozen into immutable defensive copies
at construction, so callables, modules, and arbitrary objects can never be
smuggled into a key. Text is bounded (names 128, text 512, reasons 256
characters; 64 mapping entries; 256 sequence items; depth 32) and
control-character-free so it can be handled safely in diagnostics.

### Stored evidence

`VerifiedResultEntry` preserves the original outcome and its evidence by
reference, the canonical `VerificationResult`, provenance (`task_id`,
`correlation_id`, `source`), `created_at`, the explicit `ttl`, the entry
`state`, and any `InvalidationRecord`. `verification` and `observation` are
read-only projections of the stored outcome, never stored fields in which a
verdict could be forged. `expires_at` is `created_at + ttl`; `is_fresh(at)`
fails closed at the boundary (fresh only strictly before expiry);
`evidence_is_verified` re-reads the typed evidence; `is_reusable(at)` requires
both. Entries are frozen and slotted, and the cache is the only component that
decides what a lookup means.

### Lookup statuses

| Status | Meaning | `entry` | `reusable_outcome` |
| --- | --- | --- | --- |
| `MISS` | Nothing recorded under this exact canonical key, and nothing recorded for the same operation signature | `None` | `None` |
| `VERIFIED_HIT` | Exact key, active, fresh, and the typed evidence still verifies | present | the identical stored `ClosedLoopOutcome` |
| `STALE` | Exact key and active, but the reuse window has expired | present | `None` |
| `INVALIDATED` | Exact key with a terminal invalidation recorded | present | `None` |
| `INCOMPATIBLE` | Same operation signature (capability name, procedure identity, normalized input) recorded under different canonical facts | `None` | `None` |

`reasons` is empty exactly for `VERIFIED_HIT`. Reasons are deterministic,
ordered, content-free strings naming canonical facts (`capability_version`,
`environment`, `preconditions`, `procedure_revision`, `scope`, or
`canonical_key` on a digest collision); they never echo observation content,
error messages, verification detail, or invalidation reason text, so hostile
strings cannot leak through a lookup. A `VERIFIED_HIT` returns the *identical*
outcome object the canonical loop produced — evidence is never reconstructed
from serialized text.

Lookups are side-effect free: reads never mutate, drop, or repair entries.

### Invalidation and replay

`invalidate(key, reason=..., cause=InvalidationCause.EXPLICIT)` moves one entry
to its terminal invalidated state, idempotently (the first reason wins) and
without deleting evidence. `invalidate_environment(environment, reason=...)`
invalidates every entry recorded for one typed environment identity — equality
on identity, never name matching — and returns them in deterministic
fingerprint order.

Invalidation is terminal. Re-caching evidence created at or before the recorded
invalidation is rejected, and evidence older than the recorded entry cannot
replace it, so a stale or forged replay can never overwrite what the cache
already knows.

### Tamper defense

The cache keeps its own copy of the terminal invalidation record beside each
stored entry, so invalidation is a fact about cache state rather than only about
a value object a caller holds. Mutating an entry in-process — even with
`object.__setattr__` on a frozen dataclass — cannot resurrect an invalidated
entry or fabricate a verified one: a lookup classifies against the cache's own
record and re-reads the entry's typed evidence, returning `INVALIDATED` (or a
non-reusable status) instead of trusting the presented object.

### Errors and time

All errors derive from `VerifiedResultCacheError(ValueError)`:
`TypeError` for wrong types (a programming error),
`VerifiedResultCacheValidationError` for malformed key material or an
impossible lookup value, `VerifiedResultCacheRejection` for evidence that is
not canonically verified or would replay, `VerifiedResultCacheCapacityError`
when the bounded cache is full, and `VerifiedResultCacheClockError` when the
injected clock returns a naive datetime. The injected `clock` is the only time
source; every timestamp is normalized to UTC, which makes freshness and
invalidation fully deterministic in tests. Concurrent `store`/`lookup` is made
safe by one internal lock; the cache spawns no background maintenance.

## Deliberate non-scope

No capability execution, no verification, no Task Manager transition, no Router
change, no permission or authority creation, no risk/budget/stop mutation, no
event or audit publication, no persistence, no decay/retention/scoring (A8.08),
no model call, no reasoning, no research, no repair, no retry, no fallback, no
I/O, no clock read outside the injected clock, and zero new dependencies
(standard library only).

## Guardrails

- `tests/unit/test_verified_result_cache.py` — the contract: verified insert,
  unverified/failed/denied rejection, hit, miss, input, scope, environment,
  version, procedure and precondition mismatch, expiry boundary, invalidation,
  replay rejection, capacity refusal, deterministic keying, immutability, and
  the error taxonomy.
- `tests/adversarial/test_verified_result_cache_authority.py` — forged
  verification, cache poisoning, hostile strings in every bounded field,
  wildcard- and grant-shaped environment names, in-process tampering, and proof
  that cache operations grant no authority and consume no budget.
- `tests/architecture/test_verified_result_cache_boundaries.py` — the boundary
  proved statically: single canonical module, canonical imports only, no
  kernel/hive/infrastructure import, no capability invocation, no verdict or
  outcome manufacturing, no Task transition, no Router or model symbol, no
  decay model, no I/O or background thread, standard-library-only external
  imports, digest-based keying, no production module wired to it yet, and zero
  new runtime dependencies.
- `tests/integration/test_verified_result_cache_closed_loop.py` — real A1.10
  closed-loop runs feed the cache: verified runs become reusable, verification-
  failed, execution-failed, raising, and denied runs never do, reuse is bound to
  the real request params, capability version, scope, environment,
  preconditions, and procedure revision, expiry and invalidation stop reuse, a
  restart forgets everything, a Router evidence value derived read-only from a
  hit selects `L0_CACHE` while stale and invalidated lookups do not, and child
  processes under different `PYTHONHASHSEED` values agree on every fingerprint.
