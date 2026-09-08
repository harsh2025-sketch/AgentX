# C4.06 — Repair Validation Evidence Contract

C4.06 defines the smallest deterministic **data boundary** for validating one
specific proposed repair against explicit typed criteria, and the immutable
evidence vocabulary that carries the result.

C4.06 answers: *Has this specific proposed repair been validated against the
explicit required criteria, according to the typed evidence supplied?*

It does NOT answer: *Which repair should AgentX perform?* and it does not
generate, apply, shadow-execute, authorize, or promote a repair.

## Structural distinction

> **VALIDATION_EVIDENCE != EXECUTION AUTHORITY**

A validated repair is still subject to governed execution/application later.
The contract keeps the following distinctions structural, not merely
documented:

> validated != authorized · validated != executed · validated != applied ·
> validated != selected · validated != safe-to-run · validated !=
> procedure-activated

A `RepairCandidate` (C4.04) means only that a *possible repair category may be
considered*. This module never upgrades a candidate into authority.

## Canonical contracts

`agentx.core.repair_validation` exposes:

- `RepairValidationCriterion`
- `RepairValidationOutcome`
- `RepairValidationDisposition`
- `RepairValidationTarget`
- `RepairValidationEvidence`
- `RepairValidationReport`
- `evaluate_repair_validation`
- `CANONICAL_REPAIR_VALIDATION_CRITERIA`
- `CANONICAL_REPAIR_VALIDATION_OUTCOMES`
- `CANONICAL_REPAIR_VALIDATION_DISPOSITIONS`
- `DEFAULT_REQUIRED_REPAIR_VALIDATION_CRITERIA`
- `REPAIR_VALIDATION_SCHEMA_VERSION` (currently `1`)
- bounds: `MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS`,
  `MAX_REPAIR_VALIDATION_REQUIRED_CRITERIA`,
  `MAX_REPAIR_VALIDATION_DETAIL_LENGTH`,
  `MAX_REPAIR_VALIDATION_REFERENCE_LENGTH`,
  `MAX_REPAIR_VALIDATION_NODE_ID_LENGTH`
- `RepairValidationValidationError`
- `RepairValidationDeserializationError`
- `UnsupportedRepairValidationSchemaVersionError`

There is no store, no service, no planner, no policy engine, no model client,
no shadow runner, and no patch generator.

## Validation criterion vocabulary

`RepairValidationCriterion` is closed and stable. Every member is a typed check
that can be represented with structured evidence. Free-text criteria never
become authority-bearing conditions. Human-readable description is inert
documentation only.

| Criterion | Meaning |
| --- | --- |
| `STRUCTURAL_VALIDITY` (`"structural_validity"`) | The proposed repair is structurally well-formed against the typed target. |
| `TARGET_BINDING` (`"target_binding"`) | Evidence is bound to the exact procedure identity, revision, and (where applicable) node. |
| `PRECONDITION_PRESERVATION` (`"precondition_preservation"`) | Required preconditions remain representable/compatible under the proposal. |
| `POSTCONDITION_COMPATIBILITY` (`"postcondition_compatibility"`) | Expected postconditions remain compatible with the proposal. |
| `REGRESSION_FREE` (`"regression_free"`) | Explicit regression-free evidence was recorded (still not execution). |
| `VERIFICATION_EVIDENCE` (`"verification_evidence"`) | Explicit verification evidence was recorded (consumes prior facts; does not run verification). |
| `SCOPE_COMPATIBILITY` (`"scope_compatibility"`) | The proposal remains inside the declared applicability/scope boundary. |

## Evidence model

`RepairValidationEvidence` is one immutable typed evidence item:

| Field | Type | Notes |
| --- | --- | --- |
| `repair_reference` | `str` | Stable opaque external reference for the proposed repair. Not a second `ProcedureId`. Not a patch-object dependency (Worker-11 independent). |
| `procedure_id` | `ProcedureId` | Exact canonical procedure identity. |
| `procedure_revision` | `int` | Exact positive revision (first revision is 1). |
| `procedure_node_id` | `str \| None` | Optional exact graph-local node identity (same string convention as C4.02). |
| `criterion` | `RepairValidationCriterion` | Typed criterion; never inferred from text. |
| `outcome` | `RepairValidationOutcome` | `PASSED` / `FAILED` / `INSUFFICIENT_EVIDENCE` / `NOT_APPLICABLE`. Never a bare bool. |
| `evidence_reference` | `str` | Opaque reference to the supporting fact/record. |
| `evaluated_at` | `datetime` | Caller-supplied timezone-aware instant, normalized to UTC. |
| `detail` | `str \| None` | Optional bounded inert annotation; never authority-bearing. |

`PASSED` on one criterion never means the repair is globally validated.

## Result / disposition vocabulary

Per-criterion outcomes (`RepairValidationOutcome`):

- `PASSED`
- `FAILED`
- `INSUFFICIENT_EVIDENCE`
- `NOT_APPLICABLE`

Report-level dispositions (`RepairValidationDisposition`):

- `VALIDATED` — every **required** criterion has explicit `PASSED` evidence and no required failure/conflict.
- `FAILED` — at least one required criterion has explicit `FAILED` evidence (including PASSED+FAILED conflict).
- `INSUFFICIENT` — at least one required criterion is missing or only insufficient/N/A.
- `NOT_APPLICABLE` — reserved closed member for future explicit N/A report cases; aggregation of required criteria does not currently emit it as a success path.

## Fail-closed aggregation

Aggregation is **fail-closed** and deterministic:

`evaluate_repair_validation(...)` is a pure deterministic transform:

1. Reject malformed inputs. Reject an empty `required_criteria` set (never a success bypass).
2. Keep only evidence whose `repair_reference` and target binding **exactly** match the report target. Foreign / wrong-revision / wrong-node / wrong-repair evidence is ignored and never validates.
3. Collapse duplicate identical evidence so flooding cannot manufacture independent proof.
4. For each required criterion:
   - any `FAILED` (including PASSED+FAILED conflict) => criterion failed;
   - else any `PASSED` => criterion passed;
   - else missing / only insufficient or N/A => criterion insufficient.
5. Report disposition:
   - any required failure => `FAILED`;
   - else any required insufficient/missing => `INSUFFICIENT`;
   - else every required criterion passed => `VALIDATED`.

No majority voting. No confidence averaging. No probabilistic score. Text saying
`"passed=true"` / `"validated=true"` / `"permission=ADMIN"` is irrelevant.

## Required vs optional criteria

The default required set is a **non-empty safe minimum**:

- `STRUCTURAL_VALIDITY`
- `TARGET_BINDING`
- `PRECONDITION_PRESERVATION`
- `POSTCONDITION_COMPATIBILITY`

Callers may supply an explicit non-empty subset/superset within the closed
vocabulary (bounded). Optional (non-required) evidence is retained when
target-bound but never upgrades a missing required criterion. An explicit
failure on a non-required criterion does not by itself fail the report.

`required_criteria=()` is always rejected.

## Target binding

Every report binds to exact:

- `ProcedureId`
- positive integer `procedure_revision`
- and where relevant exact `procedure_node_id`

No `latest`, `*`, `any`, wildcard, prefix, or case-insensitive fuzzy matching.
Evidence for procedure revision N must not validate N+1. If the target
changes, prior evidence becomes historical only.

## No shadow execution / no patch dependency

This is C4.06. Worker-18 owns C4.07 shadow repair. This module MUST NOT:

- execute original or modified procedure
- run Capability / AgentLoop / interpreter
- write files, launch process, invoke model, invoke research
- touch OS/browser

It consumes already-produced evidence only.

Worker-11 may independently implement a future repair patch proposal contract.
This module does **not** depend on it. The stable `repair_reference` is an
opaque external identity so a future integration layer can bind a repair-patch
object without changing validation semantics.

## Authority boundary

Validation never grants:

- Permission
- AuthorityContext
- ActionGate approval
- RiskLevel downgrade
- ResourceEnvelope increase
- EmergencyStop reset
- Task success
- Procedure activation / status change

Hostile strings remain inert inside optional `detail` fields.

## Bounds

Hard caps:

- criteria count (`MAX_REPAIR_VALIDATION_REQUIRED_CRITERIA` = 16)
- evidence count (`MAX_REPAIR_VALIDATION_EVIDENCE_ITEMS` = 64)
- detail size (`MAX_REPAIR_VALIDATION_DETAIL_LENGTH` = 4096)
- reference / node id length (256)

Reject negative limits, zero where invalid, bool-as-int, and unbounded
collections.

## Determinism

No clock reads, randomness, database, filesystem, network, model, research,
subprocess, thread, or sleep. Caller supplies timestamps. Same inputs => same
report (including byte-identical canonical JSON).

## Serialization

Schema version `1`. Exact field set. Deterministic canonical JSON
(`sort_keys=True`, compact separators, `ensure_ascii=False`, `allow_nan=False`).
Unknown fields rejected. UTC timestamps with microsecond `Z` suffix. Closed
enum decoding. No custom `object_hook`. No dynamic code. Deep immutability via
frozen dataclasses.

## Persistence decision

**None.** C4.06 is a pure `agentx.core` domain contract with no store, no
SQLite access, and **no migration**. No migration number was consumed; the
highest landed migration remains v8.

## Dependencies

**Zero new runtime dependencies.** The module imports only `agentx.core.ids`
from AgentX plus the standard library (`json`, `collections.abc`,
`dataclasses`, `datetime`, `enum`, `typing`). It imports no
`agentx.kernel` / `capabilities` / `hive` / `procedures` / `cognition` /
`learning` / `infrastructure` surface, and nothing from repair-candidate,
patch, or shadow-repair toolchains.

## Relation to C4.01–C4.04

C4.01–C4.04 remain untouched. This module does not re-implement diagnosis,
localization, taxonomy, or candidate derivation. It validates one repair
*proposal reference* against typed evidence for an exact procedure target.
Future integration may bind a C4.04 candidate or a C4.05 patch object through
`repair_reference` without changing validation semantics.
