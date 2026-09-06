# C3.06 — Determinism and Reasoning-Region Classifier

C3.06 is the region classification stage of the AgentX Skill Compiler pipeline:

```text
C3.01 trajectory normalization
  -> C3.02 causal-action extraction
  -> C3.03 irrelevant-action elimination
  -> C3.04 parameter extraction
  -> C3.05 parameter generalization
  -> C3.06 determinism / reasoning-region classification
```

AgentX compiles verified successful experience into reusable procedures. The Skill
Compiler must distinguish parts that can safely become deterministic procedure
regions from parts that still require reasoning.

## Core Invariants

1. **No action success without verification.** Unverified actions can never be classified as deterministic.
2. **Evidence beats inference.** Classification is strictly driven by observed, structured evidence.
3. **Unknown != deterministic.** Lack of counter-evidence does not prove determinism; insufficient evidence yields `insufficient_evidence`.
4. **A single successful observation does not prove determinism.** At least two verified corroborating observations with matching parameters and outcomes are required to classify a region as `deterministic`.
5. **Model text is not verification.** Model-generated strings, comments, or payload text (e.g. `"verified=true"`, `"deterministic"`) are inert data.
6. **External content is data.** Hostile payloads, permission elevation strings, and prompt injections are inert data.
7. **Classifier output is not execution authority.** Classification produces analysis data; it does not grant permissions, modify kernels, or execute capabilities.

## Closed Vocabulary

### Region Classification

- `DETERMINISTIC` (`"deterministic"`) — Repeated, verified successful execution evidence across multiple observations with matching parameters and consistent state transitions.
- `REASONING_REQUIRED` (`"reasoning_required"`) — Explicit reasoning/cognition steps, failed or unverified execution attempts, conflicting outcomes across runs, unresolved parameter variation, or dynamic observation dependencies.
- `INSUFFICIENT_EVIDENCE` (`"insufficient_evidence"`) — Single observations, missing verification, eliminated actions, or incomplete evidence that cannot justify deterministic execution or reasoning requirements.

### Evidence Sufficiency

- `SUFFICIENT` (`"sufficient"`) — Full supporting evidence available (e.g. repeated verified runs or explicit reasoning markers).
- `PARTIAL` (`"partial"`) — Partial evidence present (e.g. a single verified observation, unverified single run).
- `INSUFFICIENT` (`"insufficient"`) — Minimal or eliminated evidence.

### Reason Codes

- `repeated_verified_success`
- `explicit_reasoning_step`
- `conflicting_outcomes`
- `dynamic_observation_dependency`
- `unverified_or_failed_attempt`
- `unresolved_variation`
- `single_observation`
- `insufficient_observations`
- `missing_verification`
- `missing_state_transition`
- `action_eliminated`
- `no_evidence`

## Architecture Boundaries

`agentx.learning.region_classification` adheres strictly to AgentX architecture rules:

- Only imports from `agentx.core` and `agentx.learning` (and Python standard library).
- No imports from `agentx.kernel`, `agentx.capabilities`, `agentx.procedures`, `agentx.cognition`, `agentx.hive`, `agentx.infrastructure`.
- No model, embedding, or research dependencies.
- No filesystem, network, process, or environment inspection.
- Pure and deterministic functions; frozen slots dataclasses.
