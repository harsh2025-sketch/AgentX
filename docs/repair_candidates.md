# C4.04 — Repair Candidate Contract

C4.04 defines the smallest deterministic **data boundary** for representing conservative
repair candidates derived from an explicit canonical failure diagnosis, and the immutable
record that carries one.

C4.04 answers: *What possible repair category/action may be considered based on the explicit
structured diagnosis?*

It does NOT answer: *Which repair should AgentX perform?* Selection, approval, and execution
are owned by other subsystems and later tasks. C4.04 never repairs, patches, rewrites code,
mutates a procedure or `ProcedureStore`, retries, executes a capability or task, shells out,
spawns a subprocess, touches the network or a browser, invokes a model, does research,
compiles or activates skills, grants `Permission`, creates an `AuthorityContext`, bypasses
`ActionGate`, lowers risk, widens a budget, clears `EmergencyStop`, transitions a `Task`,
claims verification, or mutates Hive.

## Canonical contracts

`agentx.core.repair_candidates` exposes:

- `RepairCandidateKind`
- `RepairCandidate`
- `derive_repair_candidates`
- `CANONICAL_REPAIR_CANDIDATE_KINDS`
- `REPAIR_CANDIDATE_SCHEMA_VERSION` (currently `1`)
- `RepairCandidateValidationError`
- `RepairCandidateDeserializationError`
- `UnsupportedRepairCandidateSchemaVersionError`

There is no store, no service, no planner, no policy engine, no model client, and no
selector.

## A candidate is a hypothesis, never a verdict

A repair candidate records only that a *category may be considered*. The contract keeps the
following distinctions structural, not merely documented:

> candidate != correct · candidate != safe · candidate != selected · candidate != authorized ·
> candidate != executed · candidate != verified

`RepairCandidate` carries no field, method, or serialized key named `selected`,
`authorized`, `executed`, `verified`, `applied`, `safe`, or `correct` — and payloads that try
to smuggle such keys are rejected as unknown fields. The record does not rank candidates,
does not score them, and does not order the vocabulary in any way. Which candidate (if any)
AgentX may act on is decided elsewhere: kernel policy stays the only authority, and patch
generation/validation/rollback/budgets stay in C4.05+.

## Relation to C4.01–C4.03 (diagnosis linkage)

C4.01 records *what kind of failure* was observed; C4.02 records *where evidence points*;
C4.03 records *what explicit diagnostic facts are known* about a node-localized failure.
C4.04 takes exactly one typed `FailureDiagnosis` (C4.03) as its source of justification and
embeds it **by value, untouched**:

- the embedded diagnosis preserves the C4.01 `FailureClassification` and the C4.02
  `PROCEDURE_NODE` `FailureLocalization` byte-for-byte (provenance round-trips are tested);
- the candidate itself stores no copied summary, no re-derived subject fields, and no
  duplicated identity: the subject procedure/node are readable only through the embedded
  diagnosis, so a candidate can never disagree with its provenance;
- the C4.01 category is deliberately **not consulted** by derivation. A category classifies
  the observed failure; it is not a repair instruction. Mapping `PERMISSION` → "acquire
  permission" or `TRANSIENT` → "retry" would be an inference this contract never performs,
  and it would smuggle policy into a data record.

## The smallest justified vocabulary

`RepairCandidateKind` is closed and stable. The requirement was the *smallest closed
repair-candidate vocabulary justified by the canonical failure categories and the diagnosis
contracts* — so the vocabulary has exactly two members:

| Kind | Meaning | Justified by |
| --- | --- | --- |
| `UNKNOWN` (`"unknown"`) | The structured diagnosis does not justify naming any more specific candidate category. Fail-closed default; always valid. | Every diagnosis is representable, including ones whose evidence is thin. |
| `NODE_DEFINITION_REVISION` (`"node_definition_revision"`) | A revision of the localized procedure node's own definition/implementation may be *considered*. Considering is not doing: C4.05 owns patch generation, C4.06/C4.07 validation/shadow, C4.08 version replacement/rollback. | The only explicit conclusion the closed C4.03 vocabulary admits (`NODE_IMPLICATED`), which itself requires at least one explicit structured `DiagnosticEvidence` item. |

Deliberately absent (and why): `GRANT_PERMISSION`, `RETRY`, `RERUN`, `RE-SENSING`,
`ROLLBACK`, `REWRITE_CODE`, `ACTIVATE_PROCEDURE`, `PROMOTE_KNOWLEDGE`, `DO_NOTHING` /
`SUPPRESS`, and any other "obvious" repair category. None of these can be justified by a
typed fact that C4.01–C4.03 records actually carry — the diagnosis vocabulary has no
structured fact that says "a retry would help", "a prior version exists to roll back to", or
"repair should be abandoned". Inventing such members would fabricate repair intent the
canonical diagnosis never contained. Vocabulary growth is an architecture decision for the
task that lands the structured fact justifying it, not something a data contract guesses at.

## Evidence rule: free text fabricates nothing

Repair candidates may be constructed only from explicit structured canonical evidence.
`derive_repair_candidates` reads exactly two things from its input record:

1. the typed `DiagnosticConclusion` member (`UNKNOWN` → one `UNKNOWN` candidate;
   `NODE_IMPLICATED` → one `NODE_DEFINITION_REVISION` candidate), and
2. the length/positions of the explicit `evidence` tuple (linkage, nothing else).

It never inspects — and no code path here can be influenced by — free-text exception
messages, stack traces, tracebacks, webpage content, model-generated explanations, hostile
strings, arbitrary user text, or naming heuristics. `"permission denied"` appearing in a
summary proves nothing about permissions and never yields a candidate category here; only a
canonical structured diagnosis states what the failure is. A payload that merely *spells*
`"node_definition_revision"` in text is still inert; a kind is a typed enum member or a
strictly decoded canonical string, never a keyword match.

`derive_repair_candidates` also refuses to accept anything that is not a typed
`FailureDiagnosis`: dicts, JSON strings, exception objects, and stores are rejected
fail-closed rather than "interpreted".

## UNKNOWN semantics

`UNKNOWN` is a first-class, always-valid candidate kind: it records "this contract names no
more specific category for that diagnosis". It is **not** a decision that no repair exists,
is impossible, or may not be attempted, and it is not a suppression, prohibition, or
escalation instruction. Other subsystems (retry policy, escalation, human approval,
research) are untouched by its presence. Conversely, a diagnosis with an `UNKNOWN`
conclusion *and* evidence does not promote itself into a specific candidate: evidence never
upgrades its own status here. Deserialization of an unrecognized kind string is rejected,
never silently downgraded to `UNKNOWN` — that would fabricate a candidate the data never
contained.

## Provenance and duplicate handling

Every candidate preserves its full provenance chain: candidate → exact embedded
`FailureDiagnosis` → C4.01 classification / C4.02 localization / explicit
`DiagnosticEvidence` items (canonical error-code references, C2.06 identities, execution
chain correlation, episode, task).

Evidence linkage is by **position into the embedded diagnosis' evidence tuple**
(`supporting_evidence_indices`), validated to be distinct, in-range, and strictly
increasing:

- two materially distinct evidence items are two distinct links — even if they are
  byte-identical, they are cited as `(0, 1)`, so duplicates originating from distinct
  diagnostic evidence are never silently collapsed;
- re-citing the same position twice adds no provenance and is rejected, which is the only
  collapse the contract performs, justified by the canonical identity semantics themselves;
- derivation cites *every* evidence item backing the conclusion, in order — candidates
  cannot cherry-pick a subset that hides inconvenient provenance;
- an `UNKNOWN` candidate cites nothing: no evidence links to an unasserted justification.

Construction is deterministic: the same typed inputs always produce equal records and
byte-identical canonical JSON. No random ranking, no model scoring, no time-derived
ordering (`proposed_at` is caller-supplied, never read from a clock).

## Record shape

`RepairCandidate` is a frozen, slotted, keyword-only dataclass with structural equality and
hashing:

| Field | Type | Notes |
| --- | --- | --- |
| `kind` | `RepairCandidateKind` | Required, typed enum member; never inferred from text. |
| `diagnosis` | `FailureDiagnosis` | Required embedded C4.03 record, preserved by value — the full provenance chain. |
| `supporting_evidence_indices` | `tuple[int, ...]` | Distinct, in-range, strictly increasing positions; empty exactly for `UNKNOWN`; non-empty for any specific kind. |
| `proposed_at` | `datetime` | Required, timezone-aware, normalized to UTC. Record-keeping only. |
| `schema_version` | `int` | Must equal `1`. |

Invariant enforced in `__post_init__` (so direct construction cannot bypass it either): a
`NODE_DEFINITION_REVISION` candidate requires the embedded diagnosis' conclusion to be the
explicit `NODE_IMPLICATED` member. A specific kind without that structured justification is
rejected fail-closed.

Read-only helper: `is_unknown` reports the fail-closed `UNKNOWN` kind.

## Serialization

`to_dict()` / `to_json()` / `from_dict()` / `from_json()` provide deterministic canonical
serialization, exactly like C4.01–C4.03:

- keys sorted, compact separators, `ensure_ascii=False`, `allow_nan=False`;
- timestamps are UTC ISO-8601 with microseconds and a `Z` suffix;
- the same value always produces byte-identical JSON; every canonical kind round-trips;
- decoding is strict and fail-closed: exact field set, integer schema version equal to `1`,
  canonical kind values only, the nested diagnosis re-validated by C4.03's own canonical
  decoder (invalid nested payloads are rejected, never repaired), integer link arrays only,
  and the cross-field invariants above;
- no object hooks, no executable types, no pickle-like extensions.

## Authority and execution boundary

A repair-candidate record cannot grant permission, create an `AuthorityContext`, bypass the
`ActionGate`, lower risk, widen a budget, clear `EmergencyStop`, transition a `Task`, claim
or fabricate verification, activate a procedure, mutate Hive, or mutate any store. It is
inert data: adversarial tests drive kernel objects before and after the full
construct/serialize/deserialize lifecycle and assert every guard is unchanged, and they
prove free text and hostile strings cannot flip a kind. The record also performs no file,
network, subprocess, or model I/O of any kind.

## Persistence decision

**None.** C4.04 is a pure `agentx.core` domain contract with no store, no SQLite access, and
**no migration**. Nothing in the candidate vocabulary requires durable storage: candidates
are constructed, compared, and serialized in memory, and callers that already persist
diagnostic evidence keep owning their own storage. No migration number was consumed; the
highest landed migration remains v8 (C2.06 negative-experience store).

## Dependencies

**Zero new runtime dependencies.** The module imports only `agentx.core.failure_diagnosis`
from AgentX (which already owns and re-validates everything consumed) plus the standard
library (`json`, `collections.abc`, `dataclasses`, `datetime`, `enum`, `typing`). It imports
no `agentx.kernel`/`capabilities`/`hive`/`procedures`/`cognition`/`learning`/
`infrastructure` surface, no third-party package, and nothing from the repair-execution
toolchain. The module introduces no competing identifier or error hierarchy and no new
reference fields.

## Task-numbering note

The docstrings of C4.01–C4.03 were written under an earlier plan that reserved the label
"C4.04" for "environment-change detection". The Technical Lead's current task order assigns
C4.04 to this repair-candidate contract; environment-change detection remains unlanded and
keeps no number reserved here. This module deliberately leaves the earlier docstring text
untouched rather than rewriting upstream contracts.
