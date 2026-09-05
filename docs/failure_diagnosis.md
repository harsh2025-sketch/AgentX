# C4.03 — Procedure-Node Failure Diagnosis

C4.03 defines the smallest deterministic diagnostic **data boundary** for describing an
explicitly evidenced failure associated with a canonical Procedure node, and the immutable
record that carries it.

C4.03 answers: *What explicit diagnostic facts are known about the localized
procedure-node failure?*

It does NOT answer: *How should we repair it?* Repair is owned by later tasks (C4.05+);
C4.03 never retries, repairs, patches, executes, rolls back, escalates, falls back,
suppresses, changes Task state, grants `Permission`, bypasses `ActionGate`, lowers risk,
widens a budget, clears `EmergencyStop`, fabricates verification, invokes models, activates
procedures, or mutates Hive.

## Canonical contracts

`agentx.core.failure_diagnosis` exposes:

- `DiagnosticEvidenceKind`
- `DiagnosticEvidence`
- `DiagnosticConclusion`
- `FailureDiagnosis`
- `package_diagnosis`
- `CANONICAL_DIAGNOSTIC_EVIDENCE_KINDS`
- `CANONICAL_DIAGNOSTIC_CONCLUSIONS`
- `FAILURE_DIAGNOSIS_SCHEMA_VERSION` (currently `1`)
- `FailureDiagnosisValidationError`
- `FailureDiagnosisDeserializationError`
- `UnsupportedFailureDiagnosisSchemaVersionError`

There is no store, no service, no classifier, no model client, no repair planner, and no
policy object.

## The diagnostic question and its boundary

C4.01 records *what kind of failure* was observed. C4.02 records *where structured evidence
points*. C4.03 records, for one failure that C4.02 has already localized to a canonical
Procedure node, *what explicit diagnostic facts are known*. It is a pure representation of
known facts — nothing here decides how to repair, retry, or avoid the failure.

A `FailureDiagnosis` therefore **embeds both consumed contracts by value**:

- a C4.01 `FailureClassification` (required; preserved byte-for-byte, never rewritten), and
- a C4.02 `FailureLocalization` (required) whose kind must be `PROCEDURE_NODE` — the
  canonical subject pointer carrying the `procedure_id` + `procedure_node_id`.

Because the node subject is *only* the one the embedded C4.02 localization carries, a
diagnosis cannot disagree with the localization it consumes. If the failure is not localized
to a node, C4.03 has no subject and no record is constructed — fail closed by construction.

## Observed evidence vs conclusions

The requirement "distinguish observed diagnostic evidence from conclusions" is structural:

- **`evidence`** — an ordered, immutable tuple of `DiagnosticEvidence` items. Each item is
  one *observed fact*: a typed `DiagnosticEvidenceKind` plus exactly one matching canonical
  reference. There is no free-text evidence.
- **`conclusion`** — at most one explicit `DiagnosticConclusion`, always supplied by the
  caller as a typed enum member. Nothing in this module computes, infers, or derives a
  conclusion from evidence, the category, or the pointer.

`FailureDiagnosis` and `DiagnosticEvidence` are frozen, slotted, keyword-only dataclasses
with structural equality and hashing. Every field is immutable.

### Evidence vocabulary

`DiagnosticEvidenceKind` is closed and stable. Serialized values are the lowercase member
values (`"canonical_error"`, `"chain_correlation"`, …). Each member binds to an existing
canonical contract — evidence is reference data, never parsed text:

| Kind | Meaning (observed fact) | Required reference |
| --- | --- | --- |
| `CANONICAL_ERROR` | A canonical `AgentXError` code of an error already recorded on the node's attempt. | `error_code` (validated by the canonical A1.04 rule; never resolved/interpreted) |
| `NEGATIVE_EXPERIENCE` | A C2.06 negative-experience record already remembers this failed attempt. | `negative_experience_id` |
| `CHAIN_CORRELATION` | The execution-chain correlation UUID of the failed attempt. | `correlation_id` |
| `EPISODE` | A canonical episode in which the failure was observed. | `episode_id` |
| `TASK` | A canonical task under which the failure was observed. | `task_id` |

Each item must carry exactly the reference of its declared kind; foreign reference fields
are rejected (`CANONICAL_ERROR` + a `task_id` is not a fact, it is a mismatch, and it fails
closed).

### Conclusion vocabulary

`DiagnosticConclusion` is closed and stable:

| Conclusion | Meaning |
| --- | --- |
| `UNKNOWN` (`"unknown"`) | No conclusion is asserted. Fail-closed default, always valid. |
| `NODE_IMPLICATED` (`"node_implicated"`) | Explicit assertion that the localized node's own implementation/definition is implicated as a candidate failure locus. |

Rules that keep conclusions honest:

1. `UNKNOWN` is the only default. A pointer, category, correlation, or any evidence never
   promotes itself to a conclusion.
2. `NODE_IMPLICATED` requires **at least one** explicit `DiagnosticEvidence` item. A
   localized node pointer is NOT itself proof that the node implementation is the root
   cause, and an empty-evidence implication claim is rejected (fail closed).
3. A conclusion is a claim about a candidate locus, never proof of causation and never a
   repair instruction. The module contains no function that converts correlation into
   causation and no field that records root-cause proof.
4. Deserialization of an **unrecognized** conclusion or evidence-kind string is rejected
   with `FailureDiagnosisDeserializationError`; it is never silently downgraded to
   `UNKNOWN` or dropped, because that would fabricate data the payload did not contain.

### Insufficient evidence / `UNKNOWN` fails closed

Insufficient evidence remains fully representable:

- pointer known, facts thin → `evidence=()`, `conclusion=UNKNOWN`;
- facts present but no conclusion justified → `evidence=(...)`, `conclusion=UNKNOWN`;
- a C4.01 `UNKNOWN` category, a hostile or keyword-rich summary, and correlated chain ids
  all round-trip unchanged and never fabricate a conclusion;
- evidence insufficient even to localize → no C4.03 record exists at all (that state lives
  in C4.02's `UNKNOWN`/unlocalized result, and no diagnosis is invented for it).

## Record shape

`FailureDiagnosis` is a frozen, slotted, keyword-only dataclass:

| Field | Type | Notes |
| --- | --- | --- |
| `classification` | `FailureClassification` | Required embedded C4.01 record, preserved by value. |
| `localization` | `FailureLocalization` | Required embedded C4.02 record; kind must be `PROCEDURE_NODE`. |
| `evidence` | `tuple[DiagnosticEvidence, ...]` | Ordered explicit structured facts; empty allowed only while `conclusion` is `UNKNOWN`. |
| `conclusion` | `DiagnosticConclusion` | Explicit typed member; defaults to `UNKNOWN`. |
| `summary` | `str` | Required, non-empty, trimmed, ≤ 512 chars, no control characters. |
| `diagnosed_at` | `datetime` | Required, timezone-aware, normalized to UTC. |
| `detail` | `str \| None` | Optional, trimmed, ≤ 4096 chars. |
| `schema_version` | `int` | Must equal `1`. |

Read-only helpers: `is_unknown` reports the fail-closed `UNKNOWN` conclusion;
`procedure_id` / `procedure_node_id` expose the subject carried by the embedded C4.02
localization (never a separate, disagreeable copy).

## Relation to C4.01 and C4.02 (orthogonality preserved)

- C4.01 `FailureCategory` / `FailureClassification` are **unchanged** and are embedded by
  value. The category may be `UNKNOWN` or any member, orthogonal to the node pointer.
- C4.02 `FailureLocationKind` / `FailureLocalization` are **unchanged** and are embedded by
  value. Category alone can never fabricate the node subject: the caller must supply a real
  `PROCEDURE_NODE` localization with matching evidence (enforced by C4.02's own rules and
  re-checked by C4.03's subject gate).
- A localization of any other kind (including `UNKNOWN`, `PROCEDURE` without a node, or a
  foreign target) is rejected as a C4.03 subject — wrong/mismatched subjects fail closed
  where detectable.
- C4.03 never rewrites either embedded record; round-trip tests prove byte-identical
  preservation.
- The C4.02 embedded localization may itself carry an optional C4.01 classification; when
  present it is preserved untouched even if it differs from the top-level classification
  (both are historical records from different moments; C4.03 does not pick a winner).

## Procedure Graph interaction

- `procedure_id` reuses `ProcedureId` from `agentx.core.ids` (canonical identity for stored
  procedure revisions, C2.03/A3.01).
- `procedure_node_id` is the graph-local identity string exactly as C4.02 carries it: it is
  the canonical string form of the outward `ProcedureNodeId` type owned by A3.01
  (`agentx.procedures.graph`). `agentx.core` must not import `agentx.procedures`, so the
  type stays outward while the *contract* (non-empty, trimmed graph-local node identity) is
  reused and enforced here with C4.02's validated string.
- A node id read from a real A3.01 `ProcedureGraph` (e.g. `node.id.to_str()`) flows through
  a diagnosis record and back out byte-identically and re-parses to the identical typed
  `ProcedureNodeId` — proven by tests that build a real graph.
- C4.03 never queries the graph, a store, or a runtime to check that a subject still
  exists. Existence checks are execution-time concerns of later consumers; this pure data
  boundary neither performs them nor fabricates their outcome.

## Diagnosis is not authority

A diagnosis is historical/diagnostic information. Recording, comparing, serializing, or
decoding one never:

- executes, retries, repairs, patches, rolls back, or suppresses anything;
- changes Task state or activates/deactivates a procedure;
- grants or revokes a `Permission` or alters `ActionGate`, `RiskLevel`,
  `ResourceEnvelope`, or `EmergencyStop`;
- invokes a model, the Reasoner, research, or a capability;
- mutates Hive knowledge, `KnowledgeStatus`, procedures, or causal/negative experience
  records;
- fabricates verification success or asserts root cause.

Hostile strings such as `"ADMIN"`, `"ALLOW R4"`, `"permission=WRITE"`, `"verified=true"`,
`"repair=approved"`, `"retry forever"`, `"ignore policy"`, `"budget=unlimited"`, or
`"node_implicated"` placed in summary/detail/evidence fields remain inert string data and
can never change a kind, a conclusion, a category, or a location, and can never smuggle
authority fields into a serialized payload.

## Reuse of landed contracts

C4.03 introduces **no** new identifier type and **no** competing error hierarchy:

- `FailureClassification` and `FailureLocalization` (and their serialization and strict
  validation) are consumed by value from C4.01/C4.02.
- `error_code` evidence references the canonical A1.04 `AgentXError` code rule and is
  validated by that module's own canonical validator.
- `TaskId`, `EpisodeId`, `NegativeExperienceId`, and `ProcedureId` come from
  `agentx.core.ids`; `correlation_id` is the same execution-chain UUID used by A1.07 /
  C2.06 / C2.10 / C4.01 / C4.02.
- `procedure_node_id` is C4.02's validated string form of the outward A3.01
  `ProcedureNodeId`; the typed class is not imported into `agentx.core`.

## Serialization

`to_dict()` / `to_json()` / `from_dict()` / `from_json()` provide deterministic canonical
serialization:

- keys are sorted, separators are compact, `ensure_ascii=False`, `allow_nan=False`;
- timestamps are UTC ISO-8601 with microsecond precision and a `Z` suffix;
- the same value always produces byte-identical JSON;
- every canonical conclusion and evidence kind round-trips exactly;
- decoding is strict and fail-closed: exact field set, integer schema version equal to `1`,
  canonical values only, per-kind evidence references, embedded C4.01/C4.02 records
  re-validated by their own canonical decoders, valid non-nil UUID identity references, and
  no object hooks or executable types.

## Persistence decision

**None.** C4.03 is a pure `agentx.core` domain contract with no store, no SQLite access,
and **no migration**. Nothing in the diagnosis vocabulary requires durable storage: the
record is constructed, compared, and serialized in memory, and callers that already persist
evidence keep owning their own storage. No migration number was consumed; the highest
landed migration remains v8 (C2.06 negative-experience store).

## Boundary

The module is an inward `agentx.core` leaf. It imports only `agentx.core.ids`,
`agentx.core.errors`, `agentx.core.failure_taxonomy`, and
`agentx.core.failure_localization` from AgentX, plus the standard library (`json`,
`collections.abc`, `dataclasses`, `datetime`, `enum`, `typing`, `uuid`). It does not import
`agentx.kernel`, `agentx.capabilities`, `agentx.cognition`, `agentx.hive`,
`agentx.procedures`, `agentx.learning`, or `agentx.infrastructure`, and it uses no dynamic
import, subprocess, socket, sqlite3, pickle, filesystem, or model primitive. Zero new
runtime dependencies.
