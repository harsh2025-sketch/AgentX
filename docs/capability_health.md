# M7.03 — Capability Availability & Health Assessment

M7.03 defines the smallest deterministic **data/assessment boundary** for turning explicit, typed,
caller-supplied evidence about exactly one capability identity into one immutable health verdict —
and the immutable record that carries that verdict.

M7.03 answers: *According to the explicit evidence supplied by the caller, is this exact capability
version currently usable?*

It does NOT answer *May we do this?* (authority) and it does NOT answer *How should we execute
this?* (routing). **Registry presence is not availability.** A provider may be unsupported on this
platform, temporarily unavailable, missing a dependency, degraded, or known healthy from explicit
probe evidence — and a registry lookup cannot tell those apart. M7.03 makes that distinction
representable, deterministically.

A capability health assessment is **diagnostic data**. M7.03 never probes a capability, never calls
a provider, never pings a server, never checks a process, never opens a file, never queries the
network, never calls a browser or a Windows API, never reads the registry, never spawns a process,
never spawns a background or periodic check, never modifies the capability registry, never reroutes
the AgentLoop, never retries or repairs a capability, never persists health, never adds a migration,
and never implements a missing-capability detector. Authority belongs exclusively to
`agentx.kernel`.

## Canonical contracts

`agentx.core.capability_health` exposes:

- `CapabilityHealthState`, `CANONICAL_CAPABILITY_HEALTH_STATES`
- `CapabilityHealthEvidenceKind`, `CANONICAL_CAPABILITY_HEALTH_EVIDENCE_KINDS`,
  `CANONICAL_CAPABILITY_HEALTH_EVIDENCE_ANCHORS`
- `CapabilityHealthFact`, `CANONICAL_CAPABILITY_HEALTH_FACTS`,
  `CANONICAL_CAPABILITY_HEALTH_EVIDENCE_FACTS`
- `CapabilityHealthReason`, `CANONICAL_CAPABILITY_HEALTH_REASONS`
- `CapabilityVersionKey`
- `CapabilityHealthSubject`
- `CapabilityHealthEvidence`
- `CapabilityHealthConflict`
- `CapabilityHealthAssessment`
- `assess_capability_health`
- `CAPABILITY_HEALTH_SCHEMA_VERSION` (currently `1`)
- `MAX_HEALTH_EVIDENCE_RECORDS` (currently `32`), `MAX_HEALTH_EVIDENCE_TTL` (currently 7 days)
- `CapabilityHealthValidationError`

There is no store, no service, no monitor, no prober, no scheduler, no policy engine, no router, and
no registry mutation.

## The five-state vocabulary

`CapabilityHealthState` is closed and has exactly five members; every assessment lands on exactly
one. No member is ordered above another, and none carries a scalar confidence, score, or
probability.

| State | Meaning |
| --- | --- |
| `UNKNOWN` (`"unknown"`) | **Fail-closed default.** The evidence does not justify a more specific verdict: no evidence, no *fresh* evidence, an untrustworthy timeline, or self-contradictory evidence. Never a claim that the capability is broken, and never a prohibition. |
| `AVAILABLE` (`"available"`) | A fresh succeeded execution **and** a fresh passed verification for this exact capability version, with no conflicting current failure. A statement about usability — never a permission. |
| `DEGRADED` (`"degraded"`) | Present but weakened: a transient or structural execution failure, a failed verification, a degraded provider or dependency, or a relevant environment change. Degraded is not prohibited. |
| `UNAVAILABLE` (`"unavailable"`) | An explicit current fact says it cannot be used right now: the provider is unavailable, or a required dependency is missing. Present-tense, and it expires with the evidence — never a permanent ban. |
| `UNSUPPORTED` (`"unsupported"`) | An explicit observation says the capability is not supported in this platform/environment. Structural, not transient, and still never a permission decision. |

## Evidence vocabulary

Evidence is **typed only**. A fact is always an explicit `CapabilityHealthFact` enum member supplied
by the caller. Nothing in this contract derives a fact from a message, an exception, a stack trace,
a detail string, or any other text: a `detail` containing `"available=true"` or `"verified=true"`
never becomes a fact. Each fact is legal for exactly one evidence channel, and that pairing is
enforced structurally by `CANONICAL_CAPABILITY_HEALTH_EVIDENCE_FACTS`; a fact outside its own
channel is a rejected input, never a coerced one.

| Evidence kind | Legal facts | Anchored to |
| --- | --- | --- |
| `PLATFORM_SUPPORT` | `PLATFORM_SUPPORTED`, `PLATFORM_UNSUPPORTED` | `EnvironmentFactKind.PLATFORM_IDENTITY`, `FailureCategory.ENVIRONMENT` |
| `PROVIDER_AVAILABILITY` | `PROVIDER_AVAILABLE`, `PROVIDER_UNAVAILABLE`, `PROVIDER_DEGRADED` | `EnvironmentFactKind.CAPABILITY_AVAILABILITY`, `FailureCategory.CAPABILITY` |
| `DEPENDENCY_AVAILABILITY` | `DEPENDENCY_SATISFIED`, `DEPENDENCY_MISSING`, `DEPENDENCY_DEGRADED` | `EnvironmentFactKind.DEPENDENCY_AVAILABILITY`, `FailureCategory.DEPENDENCY` |
| `EXECUTION_OUTCOME` | `EXECUTION_SUCCEEDED`, `EXECUTION_FAILED`, `EXECUTION_FAILED_TRANSIENT` | `FailureCategory.CAPABILITY`, `FailureCategory.TRANSIENT` |
| `VERIFICATION_OUTCOME` | `VERIFICATION_PASSED`, `VERIFICATION_FAILED` | `FailureCategory.VERIFICATION` |
| `ENVIRONMENT_CHANGE` | `ENVIRONMENT_CHANGED`, `ENVIRONMENT_UNCHANGED` | `EnvironmentChangeResult.RELEVANT_CHANGE_DETECTED`, `EnvironmentChangeResult.NO_RELEVANT_CHANGE` |

Every evidence kind is **anchored** to names that already exist in landed canonical vocabularies
(C4.01 `FailureCategory`, C4.04 `EnvironmentFactKind` / `EnvironmentChangeResult`).
`CANONICAL_CAPABILITY_HEALTH_EVIDENCE_ANCHORS` records that mapping explicitly, and an architecture
test asserts the anchors really exist in the contracts they claim to index. M7.03 adds no
observation ontology of its own and no parallel world model.

## Freshness

Availability changes over time, so every `CapabilityHealthEvidence` carries an explicit
`observed_at` and an explicit `ttl`. Freshness is the canonical C2.09 environmental-observation
rule reproduced exactly:

```
expires_at = observed_at + ttl
fresh      = at < expires_at      # strict <, fail closed
```

At the boundary instant itself the record is **already stale**, so stale data can never present
itself as fresh. The rule is reproduced by value from C2.09/C4.04 because `agentx.core` is an
inward leaf and must not import `agentx.hive` — the same reuse-by-value discipline C4.04 applies.

**The caller supplies the evaluation time.** `assess_capability_health` takes `assessed_at` and
never reads a clock, so the same typed inputs at the same supplied instant always produce a
byte-identical record.

Freshness is bounded: a TTL must be strictly positive and at most `MAX_HEALTH_EVIDENCE_TTL`
(7 days). This contract accepts no unbounded freshness claim.

### Staleness rules

Stale evidence is **excluded** from state determination and reported with the `EVIDENCE_STALE`
reason; it never invalidates fresh evidence. That is what makes both required properties hold at
once:

- **A stale success cannot prove current availability.** With every record stale the verdict is
  `UNKNOWN` with `NO_FRESH_EVIDENCE` — never `AVAILABLE`.
- **A stale failure cannot permanently prohibit a capability.** With every record stale the verdict
  is `UNKNOWN` — never `UNAVAILABLE` or `UNSUPPORTED`.

A **future-dated** record (`observed_at > assessed_at`) is different: it means the timeline itself
is untrustworthy, so the assessment is invalidated *as a whole* to `UNKNOWN` with
`EVIDENCE_FUTURE_DATED`. This mirrors the canonical C4.04 fail-conservative treatment of
future-dated observations.

## Identity and version binding

An assessment binds exactly one `CapabilityHealthSubject`: a canonical
`agentx.core.ids.CapabilityId` plus one explicit `CapabilityVersionKey` (`major.minor.patch`
integers).

- `agentx.core` is an inward leaf and must not import `agentx.capabilities`, so
  `CapabilityVersionKey` reproduces the A1.08 `CapabilityVersion` rule **by value**. It imports
  nothing outward and creates no competing identity for the capability itself.
- **There is no wildcard version, no "latest" member, and no prefix or range matching.** `1.2.3`
  and `1.2.0` are different versions. `CapabilityVersionKey.from_str` rejects `1.2`, `1.2.*`,
  `1.*.3`, `*`, and `latest`.
- **Evidence for version 1 never establishes health for version 2.** Every evidence record carries
  the subject it was observed about, and a record naming any other subject is a *rejected input*,
  not an ignored one — so one bad version invalidates the whole evidence set rather than being
  silently dropped.
- `CapabilityId` is UUID-backed, so a lookalike string is not a lookalike identity: it fails to
  parse rather than resolving to a neighbouring capability.

## Assessment rules

The decision procedure is an **ordered rule table; the first match wins**. There is no averaging,
no majority vote, no weighting, no scoring, no ranking, and no inspection of text.

1. no evidence → `UNKNOWN` (`NO_EVIDENCE`)
2. any evidence dated after `assessed_at` → `UNKNOWN` (`EVIDENCE_FUTURE_DATED`)
3. any explicitly represented conflict → `UNKNOWN` (`EVIDENCE_CONFLICTING`)
4. no *fresh* evidence → `UNKNOWN` (`NO_FRESH_EVIDENCE`, `EVIDENCE_STALE`)
5. explicit unsupported platform → `UNSUPPORTED`
6. explicit required dependency unavailable → `UNAVAILABLE`
7. fresh provider unavailable → `UNAVAILABLE`
8. fresh structural execution failure → `DEGRADED`
9. fresh transient execution failure → `DEGRADED`
10. fresh failed verification → `DEGRADED`
11. fresh degraded provider → `DEGRADED`
12. fresh degraded dependency → `DEGRADED`
13. fresh relevant environment change → `DEGRADED`
14. fresh succeeded execution **and** fresh passed verification, with no conflicting current
    failure → `AVAILABLE`
15. otherwise → `UNKNOWN` (`INSUFFICIENT_EVIDENCE`, plus the reasons for what *was* observed)

Two consequences are deliberate:

- **Execution success without verification never yields `AVAILABLE`.** This preserves the canonical
  ABI invariant `NO ACTION == SUCCESS WITHOUT VERIFICATION` at the health layer.
- **A fresh provider being up never yields `AVAILABLE`.** Provider presence is not capability
  availability, exactly as registry presence is not availability. Rule 15 returns `UNKNOWN`.

Every assessment carries a non-empty `reasons` tuple drawn from the closed
`CapabilityHealthReason` vocabulary, in canonical declaration order without duplicates, so a
verdict is always explainable by the typed facts that produced it.

### Supersession and conflicts

Within one evidence channel the **latest** fresh observation supersedes earlier ones. That
selection is justified by the canonical `observed_at` timestamp *and* by the channel semantics — a
later observation of the same channel is a newer statement about the same thing. This is what makes
"new success after old failure" yield `AVAILABLE` and "new failure after old success" yield
`DEGRADED`.

**Ties are never broken arbitrarily.** When the same channel was observed at the *same* instant
with differing facts, M7.03 emits an explicit `CapabilityHealthConflict` naming the channel, the
instant, and both facts in canonical order; the conflicted channel contributes nothing, and the
assessment fails closed to `UNKNOWN` with `EVIDENCE_CONFLICTING`. Conflicting current evidence is
therefore always represented structurally — never resolved by arrival order, insertion order, or
any other accident.

## Bounds

- At most `MAX_HEALTH_EVIDENCE_RECORDS` (32) evidence records per assessment. Exceeding the bound
  is a **rejected input**, not a silently truncated one: this contract keeps no unbounded health
  history.
- At most 1024 characters of inert `detail` per evidence record and per assessment, and at most 512
  characters of `summary`. Control characters are rejected.
- TTL is bounded to 7 days.
- `evidence_considered` on the assessment is the bounded, canonically ordered set of fresh records
  in non-conflicted channels; `stale_evidence_count` reports how many records were excluded, so
  excluded evidence stays visible rather than silently dropped.
- Evidence must arrive in **strictly increasing canonical order** (evidence kind, then fact, then
  `observed_at`, then `ttl`, then `detail`). Duplicates are rejected rather than merged, and
  unsorted input is rejected rather than silently reordered.

## Health is not authority

**`AVAILABLE` grants nothing.** A health state is not a permission, not an `AuthorityContext`, not
a clearance, and not a routing decision. It never grants a `Permission`, never widens a
`ResourceEnvelope`, never lowers a `RiskLevel`, never clears an `EmergencyStop`, and never bypasses
the `ActionGate`. **`UNAVAILABLE` never revokes a policy permission either**: health and
authorization are independent axes, and this contract touches neither. The Action Gate remains the
only authority, and it never receives a health state — passing one as authority raises `TypeError`.

Nothing on any M7.03 value can grant authority, and the records expose no `grant`, `revoke`,
`authorize`, `approve`, `allows`, `bypass`, `clear`, `execute`, `verify`, `probe`, `retry`,
`repair`, `register`, or `transition` member.

## Health is not routing

M7.03 never selects an `ExecutionLevel` and holds no router state. It produces evidence a future
router may choose to consume. The A2.07 router still owns L0-L5 selection and knows nothing about
capability health; `agentx.cognition.router` is untouched by this task.

## No probing

The caller supplies every observation. M7.03 never:

- calls a capability, invokes a provider, or executes anything;
- pings a server, opens a socket, or touches the network;
- checks a process, spawns a subprocess, or reads the Windows registry;
- opens, reads, or writes a file;
- schedules, spawns, or runs a background or periodic health check;
- reads a clock.

An architecture test pins the import set to the standard library plus three inward
`agentx.core` modules, and bans execution, dynamic-import, network, filesystem, process, regex, and
clock primitives. An adversarial test additionally replaces every outward subsystem in
`sys.modules` with a proxy that fails on any attribute access and proves nothing is touched.

## Persistence decision

**None.** M7.03 is a pure `agentx.core` domain contract with no store, no SQLite access, and **no
migration**. Health is deliberately *not* persisted by this contract: it is a present-tense
statement derived from caller-supplied evidence at a caller-supplied instant, and persisting it
would let a recorded verdict masquerade as a current one. Callers that already persist evidence
(episodes, negative experience, events, the ephemeral C2.09 environmental cache) keep owning their
own storage. No migration number was consumed; the highest landed migration remains v8 (C2.06
negative-experience store).

## Zero new runtime dependencies

The module imports only the standard library (`json`, `collections.abc`, `dataclasses`, `datetime`,
`enum`, `itertools`, `types`, `typing`) and three inward `agentx.core` contract modules
(`agentx.core.ids`, `agentx.core.failure_taxonomy`, `agentx.core.environment_change`). An
architecture test pins that import set, proves the module defines no prober, monitor, service, or
engine class, proves the evidence vocabulary is anchored to landed canonical names, proves the C2.09
freshness rule is reproduced without importing the Hive, and proves the A1.08 version rule is
reproduced without importing `agentx.capabilities`.

## Untouched by this task

`src/agentx/capabilities/abi.py`, `registry.py`, `runtime.py`, `src/agentx/cognition/router.py`,
`src/agentx/core/ids.py`, `src/agentx/_architecture.py`, `src/agentx/kernel/*`,
`src/agentx/hive/*`, `src/agentx/infrastructure/*`, `src/agentx/agent_loop.py`, every
`__init__.py`, `pyproject.toml`, and every migration are all unchanged. The new module is imported
by path; it is not re-exported from `agentx.core`.

## Test coverage

- `tests/unit/test_capability_health.py` — vocabularies and anchors, version binding and
  wildcards, freshness equivalence with C2.09, TTL bounds and the exclusive boundary, no evidence,
  supported + fresh verified success, unsupported platform, dependency unavailable, provider
  unavailable, transient failure, structural failure, failed verification, degraded provider /
  dependency, environment change, execution without verification, provider-only evidence, stale
  success, stale failure, stale mixed with fresh, new success after old failure, new failure after
  old success, same `CapabilityId` at the wrong version, duplicate evidence, unsorted evidence,
  conflicting evidence, multi-channel conflicts, hostile detail, future timestamps, invalid and
  wrong-type evidence, record and TTL bounds, determinism, immutability, and serialization.
- `tests/adversarial/test_capability_health_authority.py` — every bait string from the brief
  (`available=true`, `permission=ADMIN`, `risk=R0`, `verified=true`, `ignore failure`,
  `force router L1`, `budget=unlimited`), lookalike capability IDs, recased UUIDs, version
  spoofing across six neighbouring versions, duplicate flooding, TTL and timestamp manipulation,
  fifteen wrong evidence types, no permission grant, no Action Gate change, no risk lowering, no
  budget widening, no execution-level selection, no probing of any outward subsystem, no clock
  read, and no file creation.
- `tests/architecture/test_capability_health_placement.py` — the static guards above: placement and
  the pinned import set, the exact defined class set, no competing error or identifier hierarchy,
  vocabulary anchoring, C2.09 freshness reuse without the Hive, A1.08 version reuse without
  `agentx.capabilities`, the single pure entry point, banned execution/network/process/clock
  primitives, no probing surface, no persistence or migration surface, no ranking or scoring
  machinery, no keyword inference, and the untouched ABI/registry/runtime/router/`ids`/
  `_architecture` neighbours.
