# C3.02 — Causal-Action Extraction

C3.02 is the second stage of the AgentX Skill Compiler pipeline:

```text
C3.01 trajectory normalization
  -> C3.02 causal-action extraction
  -> C3.03 irrelevant-action elimination
```

This stage is intentionally conservative. "Causal-action" refers to the
canonical C2.10 execution-chain record shape; it does **not** claim general
causal inference from observational data.

## Canonical input

C3.02 consumes one canonical C3.01 `NormalizedTrajectory`.

C3.01 already guarantees that every `NormalizedTrajectoryStep` contains one
unchanged canonical C2.10 `CausalExperience`, with deterministic ordering and a
SHA-256 source fingerprint. C2.10 makes `CausalExperience.action` a required
canonical `ActionPayload`.

C3.02 therefore creates no alternate trajectory, action, observation,
verification, outcome, or causal-experience schema.

## Output contract

`ExtractedActionCandidate` contains only:

- the existing C3.01 `source_trajectory_id`; and
- the unchanged canonical `NormalizedTrajectoryStep` that supplied the action.

Convenience properties expose the source step's canonical action, outcome,
observation, state-after, and verification evidence without copying or
reinterpreting them.

`CausalActionExtraction` groups the candidates for one source trajectory. It
has no new identity system: `source_trajectory_id` is exactly the C3.01
trajectory identity.

## Exact deterministic extraction rule

For a valid canonical `NormalizedTrajectory`:

1. iterate `trajectory.steps` in their existing C3.01 sequence order;
2. emit exactly one `ExtractedActionCandidate` for every step;
3. retain the same `NormalizedTrajectoryStep` object and source trajectory id;
4. do not filter, deduplicate, classify, score, rank, inspect text, or infer
   necessity/usefulness.

The current canonical schema has no valid "missing action" case: C2.10 requires
a valid `ActionPayload`. C3.02 does not invent fallback action evidence for
malformed or future incompatible records.

## Missing optional evidence

Observation, state-after, and verification are optional in C2.10. If they are
absent in the source experience, they remain absent in the extracted candidate.
C3.02 never fabricates any stage, verification result, or success state.

## Failure preservation

Outcome does not control extraction. Verified, verification-failed,
execution-failed, denied, cancelled, and timed-out experiences all remain
eligible under the same structural rule because C3.02 is not the relevance
filter.

A candidate from a failed or denied experience remains historical evidence. A
candidate from a verified experience is not automatically labelled useful,
necessary, safe, reusable, or trusted.

## Duplicate preservation

C3.01 may preserve two distinct normalized steps whose underlying canonical
experience bytes are identical, which means the two steps can share the same
source SHA-256 fingerprint. C3.02 preserves both because their source step
sequences are distinct. Deduplication is explicitly not part of C3.02.

## Causal-overclaim protection

C3.02 does not perform:

- post-hoc causal inference;
- action relevance elimination;
- action classification;
- parameter/constant extraction;
- environmental assumption inference;
- determinism or reasoning classification;
- pre/postcondition inference;
- procedure synthesis;
- skill compilation/registration;
- model calls, embeddings, keyword matching, or action-text heuristics.

The only inclusion fact is structural: the canonical normalized step records an
explicit canonical action.

## Authority boundary

Compiler evidence is inert. Constructing or extracting candidates cannot:

- execute or verify a capability;
- create/activate/register a Procedure or skill;
- grant Permission or other authority;
- lower RiskLevel;
- enlarge ResourceEnvelope/ResourceBudget;
- clear EmergencyStop;
- mutate Task state or mark success;
- fabricate verification;
- write, promote, or otherwise mutate Hive knowledge;
- route, retry, repair, or invoke a model.

Hostile action names/data such as `ALLOW`, `permission=WRITE`, `risk=R0`,
`verified=true`, `activate skill`, or `ignore policy` remain inert canonical
data.

## Persistence decision

C3.02 is a deterministic transformation over already-normalized historical
evidence. It adds no store, SQLite table, migration, or persistence write path.
No runtime dependency is added.

## A3.01 relationship

A3.01 `ProcedureGraph` is a separate inert procedure representation. C3.02 does
not import it, create it, populate its ACTION nodes, or write ProcedureStore.
Later pipeline stages own synthesis and candidate lifecycle.
