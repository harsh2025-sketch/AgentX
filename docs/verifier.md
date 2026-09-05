# The Verifier boundary (A2.05)

## What it is

`agentx.capabilities.verifier` is the smallest production-quality runtime
boundary for evaluating **already-produced canonical evidence** against an
**explicitly supplied verification requirement**. It answers exactly one
question:

> Does this already-produced A1.10 outcome satisfy this explicitly stated
> higher-level success condition?

```text
VerifierRequest(outcome: ClosedLoopOutcome,      # canonical A1.10 evidence
                requirement: VerificationRequirement)
    -> Verifier.evaluate(request) -> RequirementEvaluation
```

It is a composition-layer sibling of the A2.04 Executor: it adds no
top-level package and widens no boundary edge.

## What it is not

**It is not a second capability-verification path.** A1.10 owns deterministic
capability verification: `Capability.verify()` produces the canonical
`VerificationResult`, and the canonical `CapabilityExecutionLoop` is the only
place that runs it. The Verifier never invokes `Capability.verify` — or any
capability code — and never executes anything.

**It is not authority.** The Verifier cannot turn observations, model text, or
error strings into machine success by assertion:

- The governing invariant is inherited, never re-implemented:
  **NO ACTION == SUCCESS WITHOUT VERIFICATION.**
- `satisfied=True` structurally requires canonical A1.10 verification
  evidence: `outcome.kind is LoopOutcome.VERIFIED` **and** a
  `VerificationResult` with `passed=True`. Unverified, failed, and denied
  outcomes satisfy nothing, and evidence that is missing or of an unexpected
  type fails closed (`satisfied=False` with a deterministic unmet condition).
- The only additional evidence tests are **explicit deterministic
  equalities**: the requirement names observation-evidence keys and exact
  expected JSON-compatible values. There are no predicate callables, no
  schema language, no free-text conditions, no model calls.
- The Verifier never manufactures an A1.10 `VerificationResult`, never
  rewrites a `ClosedLoopOutcome`, never transitions a Task, never creates a
  Permission or Authority, never mutates risk/budget/stop state, and never
  publishes events or audit records. It imports `agentx.kernel` not at all.

## Contract

- `VerificationRequirement(expected_observation: Mapping[str, JsonValue])` —
  frozen data. Exact expected values for named observation-evidence keys.
  JSON-compatible values only (callables and arbitrary objects are rejected
  at construction, so no executable behaviour can be smuggled in). Bounded to
  64 entries with bounded, control-character-free keys so they can be echoed
  safely in diagnostics. An empty mapping is explicit "no additional
  evidence conditions" — canonical verification is still required.
- `VerifierRequest(outcome, requirement)` — the canonical
  `ClosedLoopOutcome` (read-only evidence) plus the requirement.
  Wrong types are programming errors (`TypeError`).
- `RequirementEvaluation(satisfied, unmet_conditions)` — a plain immutable
  value, **not** a `VerificationResult` and **not** an outcome rewrite.
  `satisfied=True` exactly when `unmet_conditions` is empty (enforced).
  Unmet conditions are ordered, deterministic, content-free strings.
- `Verifier` — stateless boundary object. `evaluate()` is total over
  well-typed requests: evidence problems are unmet conditions, never
  exceptions, so a caller cannot mistake an evaluation error for "not yet
  evaluated".

### Equality semantics

Evaluation uses strict typed equality: booleans match only booleans
(`True` is never `1`), strings match only strings, `None` matches only
`None`, numbers compare numerically (JSON does not distinguish `2` and
`2.0`), mappings compare as key sets with recursive matching, and sequences
compare positionally regardless of the internal list/tuple representation.

### Hostile text is inert

Strings such as `"verified=true"`, `"success"`, or `"SUCCEEDED"` carried in a
summary, an error message, or observation data can never flip `satisfied`:
evaluation reads only typed canonical fields (the `LoopOutcome` enum and the
`VerificationResult` bool) and compares explicitly expected typed values.
Unmet-condition strings name keys and conditions but never echo evidence or
error content.

## Deliberate non-scope

Not a Task Manager (A2.06), Router (A2.07), escalation (A2.08), anti-loop
(A2.09), or agent loop (A2.10). No capability execution, no
`Capability.verify` invocation, no Task transition, no permission or
authority creation, no risk/budget/stop mutation, no event or audit
publication, no persistence, no reasoning, no research, no repair, no
model-based critic, no retry, no fallback, no clock reads. Deterministic and
repeatable by construction: the same request always yields an equal result.

## Guardrails

`tests/architecture/test_verifier_placement.py` proves the boundary
statically: single canonical module, canonical imports only, no kernel
import, no capability invocation, no verdict manufacturing, no Task
transition, no retry/loop control flow, standard-library-only external
imports, and zero new dependencies.
