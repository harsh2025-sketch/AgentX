# C4.01 — Canonical Failure Taxonomy

C4.01 defines the single canonical vocabulary AgentX uses to say **what kind of failure** it observed, and the smallest immutable record that carries that vocabulary.

It owns the failure **representation** only. Diagnosis, localization, repair, retry, fallback, escalation, and execution are explicitly out of scope and belong to C4.02+.

## Canonical contracts

`agentx.core.failure_taxonomy` exposes:

- `FailureCategory`
- `FailureClassification`
- `CANONICAL_FAILURE_CATEGORIES`
- `FAILURE_CLASSIFICATION_SCHEMA_VERSION` (currently `1`)
- `FailureClassificationValidationError`
- `FailureClassificationDeserializationError`
- `UnsupportedFailureClassificationSchemaVersionError`

There is no store, no service, no classifier function, and no policy object.

## Taxonomy

The vocabulary is closed and stable. Each member describes *what kind of thing went wrong*, as history. No member is ordered above another, and no member implies a remedy.

| Category | Meaning (historical, descriptive) |
| --- | --- |
| `TRANSIENT` | A momentary, non-structural disturbance was observed (timeout, temporary unavailability). |
| `PRECONDITION` | A required precondition for the attempt did not hold. |
| `ENVIRONMENT` | The machine/OS/configuration/installed-software state did not match what the attempt needed. |
| `PERMISSION` | The attempt lacked the authority required to proceed. |
| `DEPENDENCY` | An external dependency failed or was unavailable. |
| `UI_CHANGE` | An observed user-interface surface no longer matched expectations. |
| `API_CHANGE` | An observed programmatic interface no longer matched expectations. |
| `CAPABILITY` | The capability was missing, unusable, or failed on its own terms. |
| `PROCEDURE` | The stored procedure being followed was wrong, stale, or inapplicable. |
| `KNOWLEDGE` | The knowledge relied on was missing, wrong, or inapplicable. |
| `PLAN` | The plan/decomposition itself was unsound for the goal. |
| `VERIFICATION` | Verification of the outcome did not pass. |
| `UNKNOWN` | Evidence does not justify any more specific class. Fail-closed default. |

Serialized values are the lowercase member values (`"ui_change"`, `"api_change"`, …).

### Classification is not authority

A classification is historical/diagnostic information. Recording one never:

- executes, retries, repairs, or suppresses anything;
- changes Task state;
- grants or revokes a `Permission`;
- alters `ActionGate`, `RiskLevel`, `ResourceEnvelope`, or `EmergencyStop`;
- invokes a model, the Reasoner, research, or a capability;
- mutates Hive knowledge or `KnowledgeStatus`;
- activates, deactivates, patches, or versions a `Procedure`.

Historical failure is **not** permanent prohibition:

- a `CAPABILITY` classification does not ban that capability from future attempts;
- a `PERMISSION` classification does not grant the missing permission;
- a `VERIFICATION` classification does not manufacture verification success;
- a `PROCEDURE` classification does not patch the procedure.

### No inference, no confidence

`FailureCategory` must be supplied as a typed enum member. The contract never derives a category from arbitrary text: a summary containing `"permission denied"`, `"API"`, `"verified=true"`, or `"repair=approved"` is inert string data and produces no category, no authority, and no decision. There is no probability, confidence, score, weighting, or ranking anywhere in the contract — deliberately.

### `UNKNOWN` fails closed

`UNKNOWN` is a first-class, always-valid classification and is the correct choice whenever evidence does not justify a more specific class. Two rules keep it honest:

1. Constructing an `UNKNOWN` classification never fabricates a specific diagnosis, and `is_unknown` reports it explicitly.
2. Deserialization of an **unrecognized** category string is rejected with `FailureClassificationDeserializationError`; it is never silently downgraded to `UNKNOWN`, because that would invent a classification the data did not contain.

## Record shape

`FailureClassification` is a frozen, slotted, keyword-only dataclass:

| Field | Type | Notes |
| --- | --- | --- |
| `category` | `FailureCategory` | Required typed enum member. |
| `summary` | `str` | Required, non-empty, trimmed, ≤ 512 chars, no control characters. |
| `classified_at` | `datetime` | Required, timezone-aware, normalized to UTC. |
| `detail` | `str \| None` | Optional, trimmed, ≤ 4096 chars, no control characters other than tab/newline/carriage return. |
| `error_code` | `str \| None` | Optional reference to a canonical `AgentXError.code`. |
| `task_id` | `TaskId \| None` | Optional canonical identity reference. |
| `episode_id` | `EpisodeId \| None` | Optional canonical identity reference. |
| `negative_experience_id` | `NegativeExperienceId \| None` | Optional canonical C2.06 evidence reference. |
| `correlation_id` | `UUID \| None` | Optional canonical execution-chain correlation identity. |
| `schema_version` | `int` | Must equal `1`. |

Equality and hashing are structural (frozen dataclass). Every field is immutable; attribute assignment raises.

## Reuse of landed contracts

C4.01 introduces **no** competing error hierarchy and **no** new identifier type:

- `error_code` references the canonical A1.04 `agentx.core.errors.AgentXError` code and is validated by that module's own canonical code rule, so the two cannot drift.
- `TaskId`, `EpisodeId`, and `NegativeExperienceId` come from `agentx.core.ids`.
- The optional `correlation_id` is the same execution-chain UUID identity already used by C2.06 negative experience and C2.10 causal experience records.
- C2.06 explicitly deferred the failure vocabulary to C4.01 and kept an opaque `FailureReference.reason_code`. C4.01 does **not** rewrite that landed record: a classification links to remembered evidence through `negative_experience_id`. No C2.06 row changes, and no persisted schema is touched.
- `ErrorCategory` (A1.04) is a *transport/error-shape* taxonomy for `AgentXError` (validation, not_found, conflict, internal, …). It is not a runtime-failure diagnosis vocabulary and is deliberately neither replaced nor mechanically mapped here; any mapping between the two would be inference, which C4.01 does not perform.

## Serialization

`to_dict()` / `to_json()` / `from_dict()` / `from_json()` provide deterministic canonical serialization:

- keys are sorted, separators are compact, `ensure_ascii=False`, `allow_nan=False`;
- timestamps are UTC ISO-8601 with microsecond precision and a `Z` suffix;
- the same value always produces byte-identical JSON;
- every canonical category round-trips exactly;
- decoding is strict and fail-closed: exact field set, integer schema version equal to `1`, canonical category values only, valid non-nil UUID identity references, and no object hooks or executable types.

## Persistence decision

**None.** C4.01 is a pure `agentx.core` domain contract with no store, no SQLite access, and **no migration**. Nothing in the failure vocabulary itself requires durable storage: the record is constructed, compared, and serialized in memory, and callers that already persist evidence (episodes, negative experience, events) keep owning their own storage. No migration number was consumed, so the next migration slot remains free for the task that genuinely needs it.

## Boundary

The module is an inward `agentx.core` leaf. It imports only `agentx.core.errors` and `agentx.core.ids` from AgentX, plus the standard library (`json`, `collections.abc`, `dataclasses`, `datetime`, `enum`, `typing`, `uuid`). It does not import `agentx.kernel`, `agentx.capabilities`, `agentx.cognition`, `agentx.hive`, `agentx.procedures`, `agentx.learning`, or `agentx.infrastructure`, and it uses no dynamic import, subprocess, socket, sqlite3, pickle, or filesystem primitive. Zero new runtime dependencies.
