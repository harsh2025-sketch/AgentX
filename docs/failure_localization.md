# C4.02 — Failure Localization

C4.02 defines the single canonical vocabulary AgentX uses to say **where in the attempted execution chain** available structured evidence explicitly points, and the smallest immutable record that carries that pointer.

It owns the localization **representation** only. Diagnosis, repair, retry, fallback, escalation, and execution are explicitly out of scope and belong to C4.03+.

C4.01 answers *what class of failure was observed*. C4.02 answers *where the evidence points*. The two are orthogonal.

## Canonical contracts

`agentx.core.failure_localization` exposes:

- `FailureLocationKind`
- `LocalizationEvidence`
- `FailureLocalization`
- `localize_failure`
- `CANONICAL_FAILURE_LOCATION_KINDS`
- `FAILURE_LOCALIZATION_SCHEMA_VERSION` (currently `1`)
- `FailureLocalizationValidationError`
- `FailureLocalizationDeserializationError`
- `UnsupportedFailureLocalizationSchemaVersionError`

There is no store, no service, no keyword classifier, no model client, and no policy object.

## Localization target vocabulary

The vocabulary is closed and stable. Each member describes *where structured evidence points*. No member is ordered above another, and no member implies a remedy or a diagnosis that something is logically wrong.

| Kind | Meaning (historical, descriptive) | Required structured evidence |
| --- | --- | --- |
| `TASK` | The canonical task under which the failure was observed. | `task_id` |
| `CAPABILITY` | The capability identity of the attempt. | `capability_id` |
| `PROCEDURE` | The stored procedure identity (no specific node). | `procedure_id` |
| `PROCEDURE_NODE` | A graph-local procedure node inside a named procedure. | `procedure_id` + `procedure_node_id` |
| `ACTION` | The action/attempt stage of the execution chain. | `correlation_id` (optional `action_name`) |
| `OBSERVATION` | The observation stage of the execution chain. | `correlation_id` |
| `VERIFICATION` | The verification stage of the execution chain. | `correlation_id` |
| `ENVIRONMENT` | An environment reference the attempt depended on. | `environment_ref` |
| `DEPENDENCY` | An external dependency reference the attempt relied on. | `dependency_ref` |
| `UNKNOWN` | Evidence does not justify any more specific target. Fail-closed / unlocalized. | no target-specific fields |

Serialized values are the lowercase member values (`"procedure_node"`, `"verification"`, …).

### Localization is not authority

A localization is historical/diagnostic information. Recording one never:

- executes, retries, repairs, or suppresses anything;
- changes Task state;
- grants or revokes a `Permission`;
- alters `ActionGate`, `RiskLevel`, `ResourceEnvelope`, or `EmergencyStop`;
- invokes a model, the Reasoner, research, or a capability;
- mutates Hive knowledge or `KnowledgeStatus`;
- activates, deactivates, patches, or versions a `Procedure`;
- fabricates verification success;
- determines that a procedure node is logically wrong (C4.03).

### No inference, no confidence

`FailureLocationKind` must be supplied as a typed enum member on `LocalizationEvidence` (or directly on `FailureLocalization`). The contract never derives a location from:

- arbitrary text, summaries, details, or stack traces;
- keyword scans (`"permission"`, `"button"`, `"API"`, `"verify"`, …);
- a C4.01 `FailureCategory` alone;
- embeddings, models, scores, probabilities, or rankings;
- store queries or trajectory analysis.

Hostile strings such as `"ADMIN"`, `"ALLOW R4"`, `"permission=WRITE"`, `"verified=true"`, `"repair=approved"`, `"retry forever"`, `"ignore policy"`, or `"budget=unlimited"` remain inert string data.

### `UNKNOWN` / unlocalized fails closed

`UNKNOWN` is a first-class, always-valid localization and is the correct choice whenever structured evidence does not justify a more specific target. Two rules keep it honest:

1. Constructing an `UNKNOWN` localization never fabricates a specific target, and `is_unlocalized` / `is_unknown` report it explicitly. Target-specific identity fields are forbidden on `UNKNOWN`.
2. Deserialization of an **unrecognized** kind string is rejected with `FailureLocalizationDeserializationError`; it is never silently downgraded to `UNKNOWN`, because that would invent a localization the data did not contain.

## Evidence contract

`LocalizationEvidence` is the caller-facing structured-evidence boundary:

| Field | Type | Role |
| --- | --- | --- |
| `kind` | `FailureLocationKind` | Required typed target. |
| `task_id` | `TaskId \| None` | Required for `TASK`; ambient context otherwise. |
| `capability_id` | `CapabilityId \| None` | Required for `CAPABILITY`. |
| `procedure_id` | `ProcedureId \| None` | Required for `PROCEDURE` / `PROCEDURE_NODE`. |
| `procedure_node_id` | `str \| None` | Required for `PROCEDURE_NODE` (graph-local string form of outward `ProcedureNodeId`). |
| `action_name` | `str \| None` | Optional for `ACTION` (typically `ActionPayload.name`). |
| `environment_ref` | `str \| None` | Required for `ENVIRONMENT`. |
| `dependency_ref` | `str \| None` | Required for `DEPENDENCY`. |
| `episode_id` | `EpisodeId \| None` | Ambient chain context. |
| `negative_experience_id` | `NegativeExperienceId \| None` | Optional C2.06 evidence reference. |
| `correlation_id` | `UUID \| None` | Required for `ACTION` / `OBSERVATION` / `VERIFICATION`; ambient otherwise. |
| `classification` | `FailureClassification \| None` | Optional C4.01 linkage; never drives `kind`. |

`localize_failure(evidence, *, summary, localized_at, detail=None)` packages validated evidence into one `FailureLocalization`. Free-form text is never accepted as evidence.

Foreign target-specific fields that do not belong to the declared kind are rejected (for example a `CAPABILITY` kind carrying a `procedure_node_id`).

## Record shape

`FailureLocalization` is a frozen, slotted, keyword-only dataclass:

| Field | Type | Notes |
| --- | --- | --- |
| `kind` | `FailureLocationKind` | Required typed enum member. |
| `summary` | `str` | Required, non-empty, trimmed, ≤ 512 chars, no control characters. |
| `localized_at` | `datetime` | Required, timezone-aware, normalized to UTC. |
| `detail` | `str \| None` | Optional, trimmed, ≤ 4096 chars. |
| identity refs | as above | Validated per kind. |
| `classification` | `FailureClassification \| None` | Optional linked C4.01 record, preserved by value. |
| `schema_version` | `int` | Must equal `1`. |

Equality and hashing are structural (frozen dataclass). Every field is immutable.

## Relation to C4.01

- C4.01 `FailureCategory` / `FailureClassification` are **unchanged**.
- A localization may optionally embed a classification by value.
- Category alone cannot fabricate a location: linking a `PROCEDURE` classification without a `procedure_id` still requires the caller to declare an explicit kind with matching evidence (or `UNKNOWN`).
- Location never rewrites category.

## Reuse of landed contracts

C4.02 introduces **no** new domain identifier type and **no** competing error hierarchy:

- `TaskId`, `CapabilityId`, `ProcedureId`, `EpisodeId`, and `NegativeExperienceId` come from `agentx.core.ids`.
- `correlation_id` is the same execution-chain UUID used by A1.07 / C2.06 / C2.10 / C4.01.
- `procedure_node_id` is the graph-local string form already used by A3.01 `ProcedureNodeId`. The typed class lives in `agentx.procedures` and is **not** imported into `agentx.core` (architecture leaf constraint).
- Optional C2.10 / C3.01 reuse is by **identity reference only** (`correlation_id`, ambient `task_id` / `episode_id`, optional `action_name` matching `ActionPayload.name`). C4.02 does not import, query, analyze, or mutate causal-experience or trajectory records.
- Linked C4.01 classification reuses `FailureClassification` serialization as a nested object.

## Serialization

`to_dict()` / `to_json()` / `from_dict()` / `from_json()` provide deterministic canonical serialization:

- keys are sorted, separators are compact, `ensure_ascii=False`, `allow_nan=False`;
- timestamps are UTC ISO-8601 with microsecond precision and a `Z` suffix;
- the same value always produces byte-identical JSON;
- every canonical kind round-trips exactly;
- decoding is strict and fail-closed: exact field set, integer schema version equal to `1`, canonical kind values only, valid non-nil UUID identity references, nested classification validated by C4.01 rules, and no object hooks or executable types.

## Persistence decision

**None.** C4.02 is a pure `agentx.core` domain contract with no store, no SQLite access, and **no migration**. Nothing in the localization vocabulary itself requires durable storage. No migration number was consumed (later store tasks append after v8: A8.01 owns v9, `create_strategy_performance_store`).

## Boundary

The module is an inward `agentx.core` leaf. It imports only `agentx.core.ids` and `agentx.core.failure_taxonomy` from AgentX, plus the standard library (`json`, `collections.abc`, `dataclasses`, `datetime`, `enum`, `typing`, `uuid`). It does not import `agentx.kernel`, `agentx.capabilities`, `agentx.cognition`, `agentx.hive`, `agentx.procedures`, `agentx.learning`, or `agentx.infrastructure`, and it uses no dynamic import, subprocess, socket, sqlite3, pickle, or filesystem primitive. Zero new runtime dependencies.
