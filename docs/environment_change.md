# C4.04 — Environment-Change Detection

C4.04 defines the smallest deterministic **data boundary** for deciding whether the explicit
environment evidence associated with a failure shows that a *relevant execution-environment
assumption has changed*, and the immutable record that carries that decision.

C4.04 answers: *Did relevant execution-environment assumptions change, according to the explicit
environment evidence?*

It does NOT answer: *How should we repair it?* and it does not repair anything. Environment-change
detection is **diagnosis data**. C4.04 never generates a repair, never applies or proposes a patch,
never modifies/versions/activates/rolls back a procedure or `ProcedureStore`, never executes a
capability or a task, never shells out or spawns a subprocess, never touches the network or a
browser, never researches the web, never invokes a model, never grants `Permission`, never creates
an `AuthorityContext`, never bypasses `ActionGate`, never lowers risk, never widens a budget, never
clears `EmergencyStop`, never transitions a `Task`, never mutates Hive, never claims or fabricates
verification, and never declares success. Authority belongs exclusively to `agentx.kernel`.

> **Historical C4.04 label collision.** An earlier merged task also used the identifier **C4.04**
> for the *Repair Candidate Contract* in `agentx.core.repair_candidates` (documented in
> [`repair_candidates.md`](repair_candidates.md)). That contract is **preserved untouched** —
> module, docs, tests, and its C4.04 label all remain exactly as they landed — and it remains
> valid support code. It is **not** the canonical C4.04. This document and
> `agentx.core.environment_change` are the canonical C4.04. Nothing was removed, renamed, or
> rewritten; both contracts coexist, and neither imports the other.

## Canonical contracts

`agentx.core.environment_change` exposes:

- `EnvironmentFactKind`, `CANONICAL_ENVIRONMENT_FACT_KINDS`, `CANONICAL_ENVIRONMENT_FACT_ANCHORS`
- `EnvironmentFactKey`
- `EnvironmentFactValueKind`, `EnvironmentFactValue`, `CANONICAL_ENVIRONMENT_FACT_VALUE_KINDS`
- `EnvironmentObservation`
- `EnvironmentSnapshot`
- `EnvironmentFactChange`
- `EnvironmentChangeResult`, `CANONICAL_ENVIRONMENT_CHANGE_RESULTS`
- `EnvironmentChangeReason`, `CANONICAL_ENVIRONMENT_CHANGE_REASONS`
- `EnvironmentChangeDetection`
- `detect_environment_change`
- `ENVIRONMENT_CHANGE_DETECTION_SCHEMA_VERSION` (currently `1`)
- `EnvironmentChangeValidationError`
- `EnvironmentChangeDeserializationError`
- `UnsupportedEnvironmentChangeSchemaVersionError`

There is no store, no service, no sensor, no world model, no planner, no policy engine, no model
client, and no repair selector.

## The three-way output

`EnvironmentChangeResult` is closed and has exactly three members; every call lands on exactly one:

| Result | Meaning |
| --- | --- |
| `RELEVANT_CHANGE_DETECTED` (`"relevant_change_detected"`) | At least one typed fact was observed on **both** sides with matching kind and a differing value. A statement about evidence, never a claim of causation and never a repair instruction. |
| `NO_RELEVANT_CHANGE` (`"no_relevant_change"`) | Every fact in the comparison was observed on both sides, at matching kind, with an equal value. Scoped to the facts actually compared; not a claim that the environment is unchanged in general. |
| `INSUFFICIENT_EVIDENCE` (`"insufficient_evidence"`) | **Fail-closed default.** The comparison could not be completed or could not be trusted. Records an absence of justification, not a verdict. |

Every record also carries non-empty `reason_codes` from the closed
`EnvironmentChangeReason` vocabulary, in canonical declaration order without duplicates, stating
*why* the result is what it is: `baseline_missing`, `current_missing`, `scope_mismatch`,
`no_facts_observed`, `conflicting_observations`, `future_observation`, `unordered_observations`,
`stale_baseline_observation`, `stale_current_observation`, `fact_unobserved_in_baseline`,
`fact_unobserved_in_current`, `fact_value_changed`, `all_compared_facts_unchanged`.

## Decision order (fail conservative)

1. A missing snapshot, a scope mismatch, an empty comparison, conflicting observations,
   future-dated observations, unordered observations, or a stale observation **invalidates the
   comparison as a whole** and yields `INSUFFICIENT_EVIDENCE` with the explicit reason codes. A
   change is never asserted from contradictory data, and "no change" is never asserted from data
   that could not be trusted.
2. Otherwise, any fact observed on both sides with matching kind and differing value yields
   `RELEVANT_CHANGE_DETECTED`.
3. Otherwise, any fact observed on only one side yields `INSUFFICIENT_EVIDENCE` — lost or new
   coverage is never a change.
4. Otherwise every compared fact was equal and the result is `NO_RELEVANT_CHANGE`.

## Absence is not change

A fact key that is *not observed* on one side is **never** reported as a change and never
fabricates one. Only an explicit `EnvironmentFactValueKind.ABSENT` value — a positive observation
that the canonical contract makes meaningful — can differ from another value and therefore count as
a change. Missing coverage degrades the result to `INSUFFICIENT_EVIDENCE` with an explicit
`FACT_UNOBSERVED_IN_*` reason code.

## Type-aware deterministic comparison

Values belong to a closed typed vocabulary, and comparison never coerces:

| Kind | Payload | Compared by |
| --- | --- | --- |
| `TEXT` (`"text"`) | non-empty trimmed text (identity, version string, schema/UI signature, configuration label) | exact string equality — no trimming, case folding, or version parsing |
| `FLAG` (`"boolean"`) | a real `bool` (normally availability) | boolean equality; `"true"`/`1`/`"1"` are rejected, never coerced |
| `ABSENT` (`"absent"`) | none — an explicit positive absence observation | equal only to another `ABSENT` |

Consequences that are structural, not documented intentions:

- `_text("true") != _flag(True)` — a type change on the same fact is a change, not a coercion.
- `_text("3.10.0") != _text("3.9.0")` — nothing decides which version is newer; that would be
  inference.
- Numeric values are deliberately absent from the vocabulary: numeric equality is not a
  deterministic identity comparison for environment facts.
- Facts are compared only within the same `EnvironmentFactKey` (`kind` + `subject`), so a
  different application, capability, endpoint, or selector is never compared as "the same fact".

Changed and unchanged facts are emitted in canonical fact order (declaration order of
`CANONICAL_ENVIRONMENT_FACT_KINDS`, then subject); the record **enforces** that order and rejects
hand-built records that violate it, so determinism is structural.

## Represented data

| Field | Type | Notes |
| --- | --- | --- |
| `diagnosis` | `FailureDiagnosis` | Required. The canonical C4.03 record this detection is associated with, embedded **by value** and never rewritten. |
| `result` | `EnvironmentChangeResult` | The three-way result. |
| `reason_codes` | `tuple[EnvironmentChangeReason, ...]` | Non-empty, canonical order, no duplicates. |
| `scope` | `KnowledgeScope` | The canonical environment scope of the detection. |
| `baseline` | `EnvironmentSnapshot \| None` | Prior evidence/reference; `None` is an explicit `BASELINE_MISSING` reason. |
| `current` | `EnvironmentSnapshot \| None` | Current evidence/reference. |
| `changed_facts` | `tuple[EnvironmentFactChange, ...]` | Typed before/after pairs with both observation instants. Non-empty exactly when the result is `RELEVANT_CHANGE_DETECTED`. |
| `unchanged_facts` | `tuple[EnvironmentFactKey, ...]` | Facts observed on both sides with equal values. |
| `compared_at` | `datetime` | Caller-supplied instant every freshness decision is evaluated against. The contract never reads a clock. |
| `summary` / `detail` | `str` / `str \| None` | Inert description. |
| `schema_version` | `int` | `1`. |

`EnvironmentSnapshot` carries `scope` (canonical `KnowledgeScope`), `evidence` (canonical
`EvidenceReference`), and `observations`. `EnvironmentObservation` carries the typed `fact`, the
typed `value`, `observed_at`, an explicit positive `ttl`, and canonical `ProvenanceReference`
provenance. `EnvironmentSnapshot.evidence` is **required**: a snapshot without an evidence
reference is not evidence.

Structural honesty rules enforced by the record itself:

- `RELEVANT_CHANGE_DETECTED` requires at least one explicit `EnvironmentFactChange`; changed facts
  are only representable with that result — a record cannot claim a change it cannot show.
- A fact cannot appear in both `changed_facts` and `unchanged_facts`.
- `SCOPE_MISMATCH` may only be reported when a supplied snapshot actually describes a different
  scope, and a snapshot whose scope differs from the record's scope is only representable when that
  reason is reported — so a record can neither hide a scope mismatch nor invent one.
- `EnvironmentFactChange` requires two differing values and `baseline_observed_at <=
  current_observed_at`; temporally inverted evidence is rejected as malformed.

## Freshness and timestamps

`EnvironmentObservation` reproduces the canonical C2.09 environmental-observation freshness rule
**exactly**: `expires_at = observed_at + ttl`, fresh exactly while `at < expires_at`, so the
boundary instant itself is already stale (fail closed). A unit test asserts the equality against
`agentx.hive.environmental_cache.EnvironmentalCacheEntry` for a matrix of instants. Because
`agentx.core` is an inward leaf and must not import `agentx.hive`, the rule is reused by
*reproduction* rather than by import — the same reuse-by-value discipline C4.03 applies to the
outward `ProcedureNodeId`. `ttl` is serialized as an integer number of microseconds, so
serialization is exactly round-trippable.

## Reuse of existing AgentX representations (no parallel world model)

C4.04 introduces **no** new identifier type, **no** competing error hierarchy, **no** competing
scope ontology, and **no** competing provenance/evidence record. It reuses:

| Representation | Owner | Used for |
| --- | --- | --- |
| `FailureDiagnosis` (embedding `FailureClassification` + `FailureLocalization`) | C4.03 / C4.01 / C4.02 | the failure this detection is associated with; full provenance travels by value |
| `KnowledgeScope`, `ScopeDimension` | C2.02 | *the* environment scope |
| `ProvenanceReference`, `ProvenanceKind` | C2.02 | observation provenance |
| `EvidenceReference`, `EvidenceKind` | C2.07 | prior/current evidence references |
| `observed_at` + `ttl` freshness rule | C2.09 | observation freshness |
| `ScopeDimension` / `FailureCategory` / `FailureLocationKind` member names | C2.02 / C4.01 / C4.02 | anchors of `CANONICAL_ENVIRONMENT_FACT_ANCHORS` |

`CANONICAL_ENVIRONMENT_FACT_ANCHORS` is the machine-checkable statement of that reuse: every
`EnvironmentFactKind` maps to one or more names that already exist in a landed canonical
vocabulary (`platform_identity` → `os`/`environment`/`environment`, `application_identity` →
`application`, `application_version` → `application_version`, `capability_availability` /
`capability_version` → `capability`, `api_schema` → `api_change`, `ui_structure` → `ui_change`,
`dependency_availability` → `dependency`, `context_configuration` → `project`/`context`). An
architecture test fails if any anchor is not a member of those vocabularies. The fact taxonomy
adds no environment ontology of its own.

The C4.01 `category` and the C4.03 `conclusion` are deliberately **not consulted** by detection: a
category classifies the observed failure and a conclusion is a claim about a node; neither is an
environment fact. Mapping `ENVIRONMENT` → "the environment changed" would be an inference this
contract never performs, and tests assert that no category and no conclusion can steer the result.

## Free text fabricates nothing

`detect_environment_change` performs exactly one computation: equality between two explicitly typed
values of the same typed fact key. It never inspects summaries, details, exception messages, stack
traces, tracebacks, webpage content, model output, or hostile strings, and no code path can be
influenced by them. `"the app was updated"` in a summary produces no fact, no value, and no
change; a payload that merely *spells* `"relevant_change_detected"` is still inert, because a
result is a typed enum member or a strictly decoded canonical string that the record must then be
able to *show*. Non-typed inputs (dicts, JSON strings, exception objects, stores, snapshots passed
as diagnoses) are refused fail-closed rather than "interpreted".

## Serialization

`to_dict` / `to_json` / `from_dict` / `from_json` are deterministic: `ensure_ascii=False`,
`allow_nan=False`, `separators=(",", ":")`, `sort_keys=True`, no object hooks and no executable
types. Decoding is strict and fail-closed:

- exact field set at every level (unknown fields rejected — including smuggled `authorized`,
  `selected`, `executed`, `verified`, `applied`, `repair`, `permission`, `risk`, `confidence`,
  `cause`);
- integer `schema_version` equal to `1`, with
  `UnsupportedEnvironmentChangeSchemaVersionError` for anything else;
- canonical enum strings only — unrecognized `result`, reason, fact-kind, or value-kind strings are
  rejected, never downgraded to `INSUFFICIENT_EVIDENCE` or `UNKNOWN`;
- nested C4.03 diagnosis, C2.02 scope, and C2.07 evidence validated by their own contracts;
- timezone-aware ISO-8601 timestamps and a strictly positive integer `ttl_microseconds`;
- the record-level consistency rules above are re-checked after decoding, so a hand-edited payload
  cannot claim a change, a scope mismatch, or an ordering it does not carry.

## Persistence decision

**None.** C4.04 is a pure `agentx.core` domain contract with no store, no SQLite access, and **no
migration**. Nothing in the detection vocabulary itself requires durable storage: records are
constructed, compared, and serialized in memory, and callers that already persist evidence
(episodes, negative experience, events, the ephemeral C2.09 environmental cache) keep owning their
own storage. No migration number was consumed; the highest landed migration remains v8 (C2.06
negative-experience store).

## Zero new runtime dependencies

The module imports only the standard library (`json`, `collections.abc`, `dataclasses`, `datetime`,
`enum`, `itertools`, `types`, `typing`) and five inward `agentx.core` contract modules. An
architecture test pins that import set, bans execution/network/filesystem/regex/clock primitives,
bans persistence and migration surface, bans ranking/scoring/confidence/model machinery, and proves
the module defines no sensor, world model, or engine class.

## Test coverage

- `tests/unit/test_environment_change.py` — vocabularies, anchors, typed values, freshness
  equivalence with C2.09, same environment, changed version, changed availability, explicit
  absence, type change, partial observations, new/lost coverage, missing baseline, missing current,
  stale baseline, stale current, conflicting observations, duplicated observations, future-dated
  observations, unordered snapshots, scope mismatch, empty comparison, multiple changes, canonical
  ordering, determinism, provenance round-trips, record validation, serialization strictness, and
  hostile strings.
- `tests/adversarial/test_environment_change_authority.py` — no authority, no repair, no execution,
  no sensing, no model, no network, no filesystem, no clock, no mutation of Task/Procedure/
  Knowledge/Hive, false-positive resistance, and smuggled-field rejection.
- `tests/architecture/test_environment_change_placement.py` — the static guards above, plus the
  guards proving the historical C4.04 repair-candidate contract is preserved untouched.
