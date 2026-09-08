# M4.01 — Procedure Synthesis Candidate Builder

M4.01 is the missing compiler stage between the canonical learning pipeline
and the canonical procedure subsystem. It turns verified, structured learning
evidence into a **procedure candidate**: a canonical `ProcedureGraph` plus an
explicit typed synthesis report.

```text
C3.01 trajectory normalization
  -> C3.02 causal-action extraction
  -> C3.03 irrelevant-action elimination
  -> C3.04 parameter extraction
  -> C3.05 parameter generalization
  -> C3.06 region classification
  -> M4.01 procedure synthesis candidate builder
```

## Why top-level composition

The canonical architecture does NOT allow `agentx.learning -> agentx.procedures`,
so no canonical subsystem may legally read learning outputs and produce
procedure data. Per the same ruling that placed the A2.10 agent loop, the
builder lives at the `agentx` namespace root
(`src/agentx/procedure_synthesis.py`), which the boundary checker treats as
"not a subsystem". The architecture manifest (`agentx/_architecture.py`) is
NOT modified, and neither the learning nor the procedures package is touched.

## Inputs

`synthesize_procedure_candidate` takes six canonical typed upstream outputs,
all keyword-only, plus caller-supplied identity:

- `NormalizedTrajectory` (C3.01)
- `CausalActionExtraction` (C3.02)
- `IrrelevantActionAnalysis` (C3.03)
- `ParameterExtraction` (C3.04)
- `ParameterGeneralization` (C3.05)
- `RegionClassificationAnalysis` (C3.06)
- `procedure_id: ProcedureId` + `revision: int` (caller-supplied; no UUIDs or
  timestamps are generated internally)
- optional `bounds: SynthesisBounds` (defaults to `DEFAULT_BOUNDS`)

Freeform natural-language instructions are never accepted as compiler input.
Human-readable text inside canonical types (action names, data strings) is
preserved verbatim as inert data. Wrong Python types fail closed with
`TypeError`, including subclass lookalikes (exact-type checks).

## Evidence agreement

All six analyses must agree on one source trajectory: identical
`source_trajectory_id`, matching step coverage, matching source-experience
fingerprints, and a consistent derivation chain
(`parameters.source_analysis is elimination`,
`generalization.source_extraction is parameters`). Every step fingerprint is
recomputed from the canonical experience JSON. Disagreement fails closed to
`EVIDENCE_MISMATCH`; a fingerprint that does not match its experience fails
closed to `FABRICATED_UPSTREAM_OBJECT`.

## Synthesis rules

Only what evidence supports is synthesized:

- **Deterministic regions** map to canonical `ACTION` graph nodes carrying the
  observed action name/data verbatim as inert params, plus the canonical
  parameter mapping below. No capability version is invented, so no A3.02
  capability-identity claim is made.
- **Reasoning-required regions** map to canonical `REASON` nodes built with
  the existing `ReasonNodeSpec` contract. They are never presented as
  deterministic.
- **`RESEARCH` is never emitted.** The current C3.06 vocabulary has no
  research-required classification, and research is never inferred from text.
- **Branches are never emitted.** No current upstream typed evidence expresses
  branching, and condition expressions are never invented from correlations.
- **Parameters** come only from `OBSERVED_VARIATION` groups (generalized, with
  verbatim observed values in observation order). `OBSERVED_SAME_VALUE` groups
  are preserved as constants. Single-observation fields are preserved verbatim
  with an explicit warning and no constant/parameter claim.
- **Excluded actions** are exactly the upstream `ELIMINATE` decisions
  (`DENIED` before capability execution), each carried with its canonical
  reason. Nothing else is dropped.
- The graph is a linear `NEXT` chain over the retained actions in source
  order, terminated by a plain canonical `END` node without a payload (legal
  A3.01 structure on its own; no A3.05 payload claim is made).

## Candidate status

The output is `CANDIDATE / UNVALIDATED`, always:

- never `ACTIVE`: construction success grants nothing (`SynthesisResult`
  rejects any status other than `CANDIDATE`/`None` at the type level);
- never `VERIFIED`: a verified trajectory proves that trajectory worked in its
  original context, not that the generalized procedure works under new
  parameters or environment. No `VERIFY` node is ever emitted and no verdict
  field exists anywhere in the report.

## No execution, no authority

The synthesizer builds DATA. It never runs the graph, calls a Capability /
AgentLoop / model / research provider, touches filesystem / browser / Windows,
runs subprocesses, persists, or activates anything. Candidate metadata cannot
grant permission, risk override, gate bypass, resource increase, stop clearing,
or task success; hostile action text remains inert. The module imports only
`agentx.core`, `agentx.learning`, `agentx.procedures`, and an inert stdlib
surface (`hashlib`, `json`, `re`, `collections`, `dataclasses`, `enum`,
`typing`, `uuid`).

## Determinism

Identical canonical inputs produce identical outputs: no randomness,
wall-clock, UUID generation, model output, or environment probing. Node ids
(`step-001`, …, `end`), parameter names (`p000_<action>_<field>`), edge order,
warnings, and serialization are all deterministic functions of the evidence.

## Bounds

`SynthesisBounds` caps source actions (64), synthesized nodes (128), edges
(256), generalized parameters (64), and serialized metadata bytes (65,536,
applied to both the candidate report and any rejection report). Any excess
fails closed to `LIMIT_EXCEEDED`.

## Report

`SynthesisResult` carries the outcome (`candidate` / `rejected`), caller
identity, source trajectory id, the candidate graph (or `None`), included
actions with node mapping, excluded actions with canonical reasons,
generalized parameters, reasoning-required regions (reused canonical
`ClassifiedRegion` objects), warnings/limitations, closed-vocabulary rejection
reasons with deterministic details, and the effective bounds. `to_dict` /
`to_json` are deterministic and hook-free.

## Rejection reasons

Closed `SynthesisRejectionReason` vocabulary: `no_retained_actions`,
`evidence_mismatch`, `fabricated_upstream_object`, `unknown_classification`,
`insufficient_evidence`, `unsupported_parameter_generalization`,
`limit_exceeded`, `graph_invariant_violation`, `unsupported_cycle` (reserved:
no current upstream evidence can express cycles, and synthesis never creates
them), `malformed_evidence`.

## Non-goals

No execution-based validation, no promotion, no persistence, no Worker-08
policy, no procedure interpreter, no learning-classifier changes, no new node
families, no new graph IR, no environment inference, no autonomous skill
compiler loop, no LLM synthesis, no research, no migration.

## Known limitations

- Only linear procedures are synthesized; branching, cycles, recovery edges,
  conditions, wait/rollback/subprocedure structure have no supporting upstream
  evidence vocabulary and are therefore never produced.
- `ACTION` nodes carry evidence params, not A3.02 `ActionNodeSpec`
  capability-identity payloads, because no upstream evidence supplies a
  capability version.
- `REASON` nodes carry no input references; ordering is expressed by graph
  edges only.
- A rejection report that itself exceeds the metadata bound raises
  `SynthesisError` instead of returning, so oversized data can never be
  laundered through the rejection path.
