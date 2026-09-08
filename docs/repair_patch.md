# M5.02 — Repair Patch Proposal Contract

M5.02 defines the smallest deterministic **data boundary** for representing **one inert
proposed change** to one procedure node, and the immutable record that carries it.

Canonical C4.04 ([`repair_candidates.md`](repair_candidates.md), `agentx.core.repair_candidates`)
answers *"what category of repair MAY be considered?"* and deliberately generates nothing.
M5.02 answers the next, still purely representational question:

> *If a node-definition revision were proposed, what exactly would the proposal say, and what
> exactly is it bound to?*

It does **not** answer *"is this change correct/safe/allowed?"* and it never performs the change.

## What this contract is not

M5.02 contains **no generation algorithm, no model, no application, no validation, no
execution**. Nothing here:

generates a patch · calls an LLM · applies a patch · modifies a procedure · touches
`ProcedureStore` · creates a revision · validates a payload against a procedure-graph schema ·
shadow-executes anything · selects or ranks proposals · implements a repair budget (C4.09) ·
adds a migration · grants a `Permission` · creates an `AuthorityContext` · bypasses an
`ActionGate` · lowers a `RiskLevel` · widens a `ResourceEnvelope` · clears an `EmergencyStop` ·
transitions a `Task` · claims verification · mutates Hive.

## Canonical contracts

`agentx.core.repair_patch` exposes:

- `RepairPatchKind`
- `RepairPatchProposal`
- `CANONICAL_REPAIR_PATCH_KINDS`
- `REPAIR_PATCH_PROPOSAL_SCHEMA_VERSION` (currently `1`)
- `RepairPatchProposalValidationError`
- `RepairPatchProposalDeserializationError`
- `UnsupportedRepairPatchProposalSchemaVersionError`
- the payload bounds: `MAX_PATCH_PAYLOAD_DEPTH`, `MAX_PATCH_PAYLOAD_COLLECTION_SIZE`,
  `MAX_PATCH_PAYLOAD_VALUES`, `MAX_PATCH_PAYLOAD_STRING_LENGTH`, `MAX_PATCH_PAYLOAD_KEY_LENGTH`,
  `MAX_PATCH_PAYLOAD_INT_MAGNITUDE`, `MAX_PATCH_PAYLOAD_JSON_BYTES`, `MAX_TARGET_NODE_ID_LENGTH`

There is **no module-level function at all** — not a generator, not an applier, not a
validator, not a selector. The module is data plus validation of that data.

## A proposal is never a verdict

The contract keeps these distinctions structural, not merely documented:

> proposal != valid · proposal != safe · proposal != selected · proposal != authorized ·
> proposal != applied · proposal != verified · proposal != active revision

`RepairPatchProposal` carries no field, property, method, or serialized key named `approved`,
`verified`, `safe`, `authorized`, `applied`, `selected`, `valid`, `active`, `score`, `rank`,
or `confidence`, and encoded payloads that try to smuggle such **top-level** keys are rejected
as unknown fields. Whether a proposal may ever be applied is decided elsewhere: kernel policy
remains the only authority, and patch validation/shadow repair/version replacement remain
later tasks.

## Schema (v1)

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | `int` (`1`) | Explicit canonical schema version; anything else is rejected. |
| `kind` | `RepairPatchKind` | Closed vocabulary; currently only `NODE_DEFINITION_REPLACEMENT`. |
| `candidate` | `RepairCandidate` | The originating canonical C4.04 candidate, embedded **by value**, untouched. |
| `target_procedure_id` | `ProcedureId` | Exact canonical procedure identity. No wildcard, no name lookup. |
| `target_revision` | `int >= 1` | The exact stored revision the proposal was written against. |
| `target_node_id` | `str` | Graph-local node identity string (bounded, trimmed, printable). |
| `proposed_definition` | JSON object | Bounded, deep-frozen, inert replacement node definition. |
| `proposed_at` | aware `datetime` | Caller-supplied instant, normalized to UTC. |

Serialized form (`to_dict`/`to_json`) uses exactly those eight keys, sorted, compact
separators, `allow_nan=False`, UTC timestamps rendered as `...Z`.

## Supported patch kinds

| Kind | Meaning | Justified by |
| --- | --- | --- |
| `NODE_DEFINITION_REPLACEMENT` (`"node_definition_replacement"`) | The proposal carries a complete inert replacement definition for exactly one localized procedure node of exactly one target revision. "Replacement" is the shape of the proposal, never an act. | The only concrete repair family canonical C4.04 can name: `RepairCandidateKind.NODE_DEFINITION_REVISION`. |

Deliberately absent (and why): arbitrary source-code patches, textual diffs/hunks, shell or
PowerShell patches, Python patches, kernel modifications, permission changes, risk changes,
budget changes, procedure activation/rollback, and architecture changes. None of them can be
justified by a typed fact that the canonical candidate/diagnosis chain carries, and each would
smuggle authority or execution into a data record. Vocabulary growth belongs to the task that
lands the structured fact justifying it.

## Target binding rules

A proposal is bound to an exact subject, and the binding is checked against its own
provenance:

1. `target_procedure_id` **must equal** `candidate.diagnosis.procedure_id`;
2. `target_node_id` **must equal** `candidate.diagnosis.procedure_node_id`;
3. `target_revision` must be a positive integer (first revision is `1`).

Consequences, all enforced and tested:

- no wildcard procedure id, no `"*"`, no `"latest"`, no `"current"`, no implicit revision;
- no fuzzy node lookup, no name matching, no re-targeting, no reconciliation;
- a proposal written against revision 3 is a *different record* from one written against
  revision 4 (different value, different canonical JSON), so it can never silently apply to
  another revision;
- the contract performs no existence check — it never asks a store whether the procedure,
  revision, or node exists. Existence is an execution-time concern of the outer layer, and
  asking would turn a pure data contract into a store client.

The `target` property returns the `(procedure_id, revision, node_id)` triple already stored on
the record. It resolves nothing.

## Provenance rules

- The originating canonical `RepairCandidate` is embedded **by value** and preserved
  byte-for-byte, carrying with it the C4.03 diagnosis, the C4.02 `PROCEDURE_NODE`
  localization, and the C4.01 classification.
- A caller cannot claim structured justification without that chain: `candidate` must be a
  canonical `RepairCandidate` instance (a dict, a JSON string, a bare diagnosis, or a kind
  enum is rejected).
- **Fail closed on `UNKNOWN`:** a `RepairCandidateKind.UNKNOWN` candidate names no repair
  family, so it can never justify a concrete node-definition patch. Only
  `NODE_DEFINITION_REVISION` justifies `NODE_DEFINITION_REPLACEMENT`.
- **No proposer identity field.** `agentx.core` owns no canonical agent/actor identity type,
  and a free-text `proposed_by` string would be an unverifiable, spoofable identity claim
  ("the model said so" is not provenance). Justification comes from the candidate chain. If a
  canonical actor identity ever lands, adding it is a schema-version decision, not a guess
  made here.

## Payload: untrusted, bounded, inert

`proposed_definition` is a **JSON-compatible payload**, never a parsed procedure graph. Core
must not depend on `agentx.procedures`, so the module imports no `ProcedureGraph`,
`ProcedureNode`, ACTION/branch class, or interpreter, and it never parses, schema-checks,
compiles, imports, or executes the payload. An outer composition layer that legitimately owns
both sides may validate the payload against the canonical Procedure Graph contracts later —
that validation is exactly why a proposal is not "valid" here.

Accepted: JSON objects, arrays, strings, finite numbers, booleans, and `null`.

Rejected, fail-closed: `NaN`, `Infinity`, `-Infinity` (both as Python floats and as the JSON
extension tokens on decode), `bytes`/`bytearray`/`memoryview`, callables, sets, `Decimal`,
`datetime`, domain identifiers, arbitrary objects, non-string keys, empty keys, keys or
strings with control characters, cyclic object graphs, duplicate JSON object keys, and an
empty payload (a proposal that proposes nothing is not a proposal).

Bounds (all exported constants):

| Bound | Value |
| --- | --- |
| `MAX_PATCH_PAYLOAD_DEPTH` | `8` (the payload object itself is depth 1) |
| `MAX_PATCH_PAYLOAD_COLLECTION_SIZE` | `256` entries per object/array |
| `MAX_PATCH_PAYLOAD_VALUES` | `1024` total values |
| `MAX_PATCH_PAYLOAD_STRING_LENGTH` | `4096` characters |
| `MAX_PATCH_PAYLOAD_KEY_LENGTH` | `128` characters |
| `MAX_PATCH_PAYLOAD_INT_MAGNITUDE` | `2**53 - 1` |
| `MAX_PATCH_PAYLOAD_JSON_BYTES` | `65536` bytes of canonical JSON |
| `MAX_TARGET_NODE_ID_LENGTH` | `256` characters |

Cyclic graphs terminate on the depth bound instead of recursing, so a hostile object graph
cannot exhaust the stack.

### Security posture

The payload is **untrusted data with zero authority**. A payload containing
`"permission=ADMIN"`, `"risk=R0"`, `"verified=true"`, `"import os"`, `"subprocess.run(...)"`,
`"clear emergency stop"`, SQL, PowerShell, a tool-call JSON object, or a prompt injection is
stored, compared, and re-serialized as inert data — nothing more. The module performs no
`eval`, `exec`, `compile`, `__import__`, `importlib`, `pickle`, `subprocess`, shell,
filesystem, database, or network operation, and adversarial tests assert that constructing,
serializing, and decoding such payloads leaves the permission engine, action gate, emergency
stop, task state, and procedure records completely unchanged.

## Immutability and determinism

- The record is a frozen, slotted, keyword-only dataclass.
- Payload mappings are deep-frozen into read-only proxies and payload arrays into tuples, so
  mutating the caller's original structures after construction cannot mutate the proposal.
- Serialization is deterministic: the same typed inputs always produce byte-identical records
  and byte-identical canonical JSON (sorted keys, compact separators, UTC `...Z` timestamps).
- Decoding is strict: exact field set, explicit `schema_version`, closed kind vocabulary,
  unknown fields rejected, duplicate JSON keys rejected, no type coercion (`"3"` is not `3`,
  `True` is not `1`), and every construction-time rule (target binding, provenance, bounds) is
  re-applied.

## Persistence decision

**Nothing here is persisted.** M5.02 adds no table, no store, no repository, no migration, and
no schema-ladder entry; the migration ladder is untouched (highest landed version remains the
C2.06 negative-experience store, v8). A proposal is a value that an outer layer may hold,
compare, or hand on. Persisting proposals — if ever needed — is a separate, explicitly
justified task.

**Zero new runtime dependencies.** The module imports only `agentx.core.ids`,
`agentx.core.repair_candidates`, and the standard library (`json`, `math`, `dataclasses`,
`datetime`, `enum`, `types`, `typing`, `collections.abc`). Architecture tests prove the whole
transitive import closure stays inside `agentx.core`.

## Known limitations (deliberate)

- The payload's *meaning* is unchecked here: a syntactically valid JSON object that is not a
  well-formed procedure node is accepted as data and must be rejected later by whoever owns
  the Procedure Graph contract.
- No existence check is performed on the target procedure, revision, or node.
- No proposer/actor identity is recorded (see "Provenance rules").
- Proposals are not hashable, because the deep-frozen payload uses read-only mapping proxies;
  they are compared by value instead.
- `agentx/core/__init__.py` is not modified by this task, so the package docstring's module
  inventory does not yet mention `repair_patch`; that file is owned elsewhere.
