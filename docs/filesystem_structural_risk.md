# N2.22 — Request-sensitive filesystem structural risk policy

N2.22 adds the **risk-classification policy** required before governed
filesystem *structural* operations (create directory, create empty file,
rename, move, delete) can exist as capabilities. It is deliberately **risk
policy only**: it is not the structural capability implementation, it executes
nothing, and it grants nothing.

Production module: `src/agentx/capabilities/filesystem_structural_risk.py`
Tests: `tests/unit/test_filesystem_structural_risk.py`,
`tests/adversarial/test_filesystem_structural_risk_authority.py`,
`tests/architecture/test_filesystem_structural_risk_boundaries.py`

## The defect this corrects

The earlier M7.05 work assigned each filesystem operation family one static
risk classification. That is conceptually wrong: operations with very
different destructive potential were forced to share a single label.

```text
create directory              create empty file (if supported)
rename                        move to an unused destination
overwrite existing destination  replace existing destination
delete / remove
```

A destructive **overwrite** or **replacement** must not receive the same
low-risk classification merely because it shares an operation family with a
non-destructive **create**, **move**, or **rename**. Risk is a function of the
*actual request semantics and target-state facts*, not of the operation name
alone:

```text
CREATE new path             != OVERWRITE existing path
MOVE to empty destination   != MOVE replacing existing destination
RENAME without replacement  != RENAME replacing existing object
```

## Operation / fact model

`FilesystemStructuralRiskRequest` is a frozen dataclass carrying one explicit
operation description plus the *minimum* pre-execution facts required to
classify it.

| Fact | Type | Applies to | Meaning |
| --- | --- | --- | --- |
| `operation` | `FilesystemStructuralOperationKind` | all | `create_directory`, `create_file`, `move`, `rename`, `delete` |
| `source_path` / `source_state` | `str` / `TargetState` | move, rename | source path and whether it is verified absent/present/unknown |
| `destination_path` / `destination_state` | `str` / `TargetState` | all | destination (or deletion) path and its existence fact |
| `overwrite_requested` | `bool` | create, move | caller explicitly requests replacing an existing destination |
| `replacement_requested` | `bool` | rename | caller explicitly requests replacing an existing destination |
| `deletion_scope` | `DeletionScope` | delete | target verified `empty`, `non_empty`, or `unknown` |

`TargetState` is a *tri-state*: `ABSENT`, `PRESENT`, and `UNKNOWN`.
`UNKNOWN` is the explicit "no pre-execution evidence" state — it is not a
guess and is never silently resolved to the safe value.

Facts are **supplied by the caller**. The policy never probes, stats, or
resolves paths and never performs filesystem I/O. Path strings are ordinary
data; `C:\safe\risk=R0.txt` and `C:\permission=ADMIN\` are inert. Arbitrary
metadata strings cannot reduce risk, because no string content is parsed for
semantic instructions.

### Malformed / contradictory facts

Structural type errors raise `TypeError`. Semantic or contradictory facts
raise `ValueError` — for example a `delete` with a verified-absent target that
also claims an `empty`/`non_empty` content scope, an `overwrite_requested`
flag on a `rename`, or a `source_path` on a `create`.

## Classification matrix

Levels use the canonical `RiskLevel` (`R0` READ, `R1` REVERSIBLE, `R2`
MODIFY, `R3` EXTERNAL_EFFECT, `R4` CRITICAL). Local filesystem structure
changes are never `R3` external effects.

| Operation | Source | Destination | Flag | Level | Destructive | Data loss | Insufficient facts |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CREATE (dir/file) | — | ABSENT | — | R1 | no | no | — |
| CREATE (dir/file) | — | PRESENT | overwrite=F | R0 (no-op, create-only conflict) | no | no | — |
| CREATE (dir/file) | — | PRESENT | overwrite=T | R4 | yes | yes | — |
| CREATE (dir/file) | — | UNKNOWN | overwrite=F | R1 | no | no | destination_state |
| CREATE (dir/file) | — | UNKNOWN | overwrite=T | R4 | yes | yes | destination_state |
| MOVE | ABSENT | any | any | R0 (no-op) | no | no | — |
| MOVE | PRESENT | ABSENT | — | R1 | no | no | — |
| MOVE | PRESENT | PRESENT | overwrite=F | R0 (no-op, conflict) | no | no | — |
| MOVE | PRESENT | PRESENT | overwrite=T | R4 | yes | yes | — |
| MOVE | UNKNOWN | ABSENT | — | R1 | no | no | source_state |
| MOVE | UNKNOWN | PRESENT | overwrite=T | R4 | yes | yes | source_state |
| RENAME | PRESENT | ABSENT | — | R1 | no | no | — |
| RENAME | PRESENT | PRESENT | replace=F | R0 (no-op, conflict) | no | no | — |
| RENAME | PRESENT | PRESENT | replace=T | R4 | yes | yes | — |
| DELETE | — | ABSENT | — | R0 (no-op) | no | no | — |
| DELETE | — | PRESENT | scope=empty | R1 | no | no | — |
| DELETE | — | PRESENT | scope=non_empty | R4 | yes | yes | — |
| DELETE | — | PRESENT | scope=unknown | R4 | yes | yes | deletion_scope |
| DELETE | — | UNKNOWN | scope=empty | R1 | no | no | destination_state |
| DELETE | — | UNKNOWN | scope=non_empty/unknown | R4 | yes | yes | destination_state (+ deletion_scope) |

**No-op cases are R0.** When the supplied facts make a mutation impossible
(create-only against a verified-present target, move/rename with a
verified-absent source, delete of a verified-absent target), the operation
fails before any mutation; the policy classifies that guaranteed no-op as
`R0` with `read_only=True` and `state_change_possible=False`.

**Destructive replacement is R4, never R1.** Overwriting or replacing an
existing destination destroys prior content: `destructive=True`,
`data_loss_possible=True`, and `reversible=False`. A replacement is not
declared reversible merely because the API operation is technically reversible
in some environments — potential user data loss dominates.

## Insufficient-facts (fail-closed) behavior

When a fact that changes the classification is not known, the policy fails
closed. It enumerates every target-state world consistent with the supplied
facts, classifies each, and joins them conservatively: the reported level is
the **maximum** applicable risk, `destructive`/`data_loss_possible` are ORed
across worlds, and the risk-relevant unknown facts are reported explicitly in
`insufficient_facts`. The policy **never assumes "destination absent" without
evidence.**

```text
unknown destination + overwrite requested  -> R4 (worst case: it exists)
unknown destination, no overwrite          -> R1 (worst case: a plain create)
unknown deletion target, unknown scope     -> R4 (worst case: non-empty)
```

An unknown fact that does *not* change the outcome is not reported — for
example an unknown destination on a move whose verified-absent source already
guarantees a no-op.

## Authority separation

`classify_filesystem_structural_risk` (or the stateless
`FilesystemStructuralRiskPolicy` object form) calculates a classification
only. The returned `FilesystemStructuralRiskResult` is descriptive input to
governance — it is **not** authorization. The policy:

- does not grant a `Permission` or an `AuthorityContext`;
- does not grant or substitute for user confirmation;
- does not call `ActionGate`;
- does not execute any filesystem operation;
- does not alter a `ResourceBudget`/`ResourceEnvelope`;
- does not clear an `EmergencyStop`;
- does not mark a `Task` successful;
- does not perform or fabricate verification;
- does not persist anything or call a model.

The canonical `ActionGate` consumes the returned `RiskAssessment` (via
`effective_level`), so a destructive replacement the policy classifies as
`R4` is still DENYed without `DESTRUCTIVE` authority and requires
confirmation even with it — the policy's output drives, but never replaces,
the gate.

## Authority boundary

| Surface | Permission | Behavior |
| --- | --- | --- |
| `classify_filesystem_structural_risk` | none | pure, deterministic R0–R4 classification of an explicit request + facts |
| `FilesystemStructuralRiskPolicy.classify` | none | stateless object form delegating to the same pure function |

The module imports only the standard library and
`agentx.kernel.risk`. Architecture tests pin that boundary: no
`os`/`pathlib`/`shutil`/`subprocess` imports, no filesystem or dynamic calls,
no authority/persistence/model imports, and the public API is exactly the
classifier and its contracts.

## Why this is not the capability

This task supplies the *policy*, not the *operation*. No capability descriptor,
registry entry, execution path, or rollback for structural operations is added
here. A future structural capability will call this policy with the target-state
facts it has verified pre-execution and attach the resulting `RiskAssessment`
to its descriptor, rather than hard-coding one static risk per operation.
