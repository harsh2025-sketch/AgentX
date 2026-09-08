# Deterministic procedure applicability matching (M4.04)

M4.04 owns the deterministic policy that answers exactly one narrow question:

> Is this already-known procedure revision **structurally compatible** with
> these explicitly requested capability/environment facts?

It is the deterministic reuse gate in front of expensive planning:
`agentx.core.procedure_matching`.

**Matching is not routing, not retrieval, not ranking, and not execution.**
A match result never selects an execution level, never activates or promotes a
revision, never executes anything, and never grants authority. It reports
structural compatibility over typed facts — nothing more.

## Why this exists

AgentX must prefer deterministic reuse before planning. A stored procedure
must never be selected merely because:

* its **name** resembles the goal;
* its **payload** contains similar words;
* it is **ACTIVE**;
* a **model** says it probably fits.

Only typed fields count. Text carries no authority anywhere in this boundary.

## Position in the architecture

| Concern | Owner | Not this task |
| --- | --- | --- |
| Procedure record contract (`ProcedureRecord`, `ProcedureScope`, `ProcedureStatus`) | C2.03 `agentx.core.procedures` | reused verbatim, never redefined |
| Capability identity (`CapabilityId`) / capability ABI | `agentx.core.ids` (identity) and A1.08 `agentx.capabilities.abi` (ABI) | reused identity; the ABI is never imported |
| **Structural applicability matching** | **M4.04 `agentx.core.procedure_matching`** | — |
| Candidate discovery / persistence | `agentx.infrastructure.procedure_store` | no store is consulted |
| Execution-level selection (`L0`–`L5`) | A2.07 `agentx.cognition.router` | no level is named or influenced |
| Candidate-skill lifecycle and trust | C3.09 | status is observed, never decided here |
| Executing, activating, promoting | Trusted Kernel / lifecycle policy | structurally absent from this module |

## Inputs (all caller-supplied, all typed, all bounded)

| Input | Type | Meaning |
| --- | --- | --- |
| Candidate revision | `ProcedureCandidate(record: ProcedureRecord, capability: CapabilityRequirement \| None)` | One known revision. `capability=None` means the binding is **unknown**, never "anything". |
| Requested facts | `ProcedureRequirement(scope: ProcedureScope, capability: CapabilityRequirement \| None)` | The application/environment/project/OS facts the caller actually asserts. An empty scope asserts **nothing**. |
| Capability requirement | `CapabilityRequirement(capability_id: CapabilityId, version: str \| None)` | Canonical opaque identity plus an **opaque byte-exact** version token. `version=None` means unasserted, never "any version". |

Both sides express scope with the canonical
`agentx.core.procedures.ProcedureScope` /
`ProcedureScopeDimension` types. This module defines **no** scope dimension
vocabulary of its own and must never grow one.

The matcher (`ProcedureApplicabilityMatcher`) is stateless
(`__slots__ = ()`), holds no dependencies, and has exactly one entry point:

```python
result = ProcedureApplicabilityMatcher().assess(candidate, requirement)
```

There is deliberately **no multi-candidate, ranked, or "best match"
primitive**. Callers that must consider several revisions call `assess` once
per revision and own their own (out-of-scope) choice.

## Result vocabulary

| Outcome | Meaning |
| --- | --- |
| `EXACT_MATCH` | Every asserted dimension and the capability identity/version (when asserted) agree, and nothing is left unconfirmed. |
| `COMPATIBLE` | No contradiction, but something is broader or unasserted: the revision is globally unscoped on a dimension the request pins, or capability identity matches with no version asserted. |
| `INCOMPATIBLE` | A typed fact contradicts a typed fact. |
| `INSUFFICIENT_EVIDENCE` | The revision asserts something the caller supplied no evidence about. Absence of evidence is never a match. |

No other outcome exists, and none of them means "may execute".

## Scope rules (per dimension, canonical order)

For every `ProcedureScopeDimension`, compared in canonical declaration order
(`APPLICATION`, `APPLICATION_VERSION`, `OPERATING_SYSTEM`, `ENVIRONMENT`,
`PROJECT`):

| Revision | Request | Result |
| --- | --- | --- |
| value | same value | `SCOPE_MATCH` |
| value | different value | `SCOPE_MISMATCH` → `INCOMPATIBLE` |
| unscoped | value | `SCOPE_UNSCOPED_ON_DIMENSION` → `COMPATIBLE` (broader, never exact) |
| value | unscoped | `MISSING_REQUIRED_EVIDENCE` → `INSUFFICIENT_EVIDENCE` |
| unscoped | unscoped | dimension is not part of this match |

Comparison is exact `str` equality on canonical typed values. There is no case
folding, no trimming, no Unicode normalization, no prefix/substring matching,
no wildcard (`os=*`), no "any"/"latest" token, and no similarity of any kind.
`"windows"`, `"Windows"`, and `"windows "` are three different values (the
third is rejected at `ProcedureScope` construction), and a Cyrillic small
letter o (U+043E) is not a Latin `"o"`.

### Unscoped/global applicability vs. unknown caller evidence

The two "missing" cases are deliberately **not** symmetric:

* **Unscoped/global applicability** (revision silent) is an explicit recorded
  property — an empty `ProcedureScope` *is* the canonical global scope — so it
  can never contradict a request.
* **Unknown caller evidence** (request silent) is not a fact at all. Treating a
  silent request as "matches anything" would be **wildcard authority**, so it
  fails closed to `INSUFFICIENT_EVIDENCE`.

## Version rules

A capability version is an opaque, bounded (≤ 128 character) token compared
byte-exactly. There is no semantic-version range language, no `>=1.2`
parsing, no caret/tilde syntax, no `*`, no `latest`, and no regex. A different
token is `VERSION_MISMATCH` → `INCOMPATIBLE`, never a silent match.

AgentX has no capability-version type reachable from `agentx.core` (the
`major.minor.patch` record lives outward in `agentx.capabilities.abi`, which
core must not import), so this contract carries the token opaquely and refuses
to interpret it. Resolving a human-facing capability *name* to a
`CapabilityId` is likewise outward capabilities work and is not done here.

## Capability identity rules

Capability identity is the canonical opaque `CapabilityId`, compared by exact
typed equality:

* **No prefix matching** — `file.write` never matches `file.write.admin`.
* **No case folding, no substring matching, no pattern matching.**
* **No cross-domain substitution** — a `ProcedureId` carrying identical UUID
  bytes is not a `CapabilityId` and is rejected as a wrong type.

## Status is not applicability

`ProcedureStatus` is deliberately excluded from the structural outcome:

* `ACTIVE` does **not** automatically mean compatible — an `ACTIVE` revision
  scoped to the wrong OS is `INCOMPATIBLE`.
* `CANDIDATE` does **not** automatically mean incompatible *as a data
  assessment* — a `CANDIDATE` revision can be structurally `EXACT_MATCH`. A
  later execution/lifecycle policy may still forbid using it; that policy is
  not this module's to make.
* `RETIRED` never becomes executable because matching succeeded. The outcome is
  a structural statement only, and this module exposes no `executable` flag,
  grants no authority, and performs no transition.

The record's status is reported back verbatim as an **observation**
(`lifecycle_status` plus a `ProcedureLifecycleNote`) so callers can see it
without this module ever deciding eligibility from it. Lifecycle eligibility
remains owned by C3.09 and the Trusted Kernel.

## Reason codes

`ProcedureMatchResult.reasons` is a tuple of `ProcedureMatchReason(code,
dimension)` values, ordered deterministically by
**(reason precedence, canonical dimension order)**, independent of the
insertion order of either scope mapping:

| Code | Precedence | Meaning |
| --- | --- | --- |
| `SCOPE_MISMATCH` | 0 | A dimension asserted by both sides disagreed. |
| `VERSION_MISMATCH` | 1 | Explicit capability versions were both present and differed. |
| `CAPABILITY_MISMATCH` | 2 | Capability identity differed. |
| `MISSING_REQUIRED_EVIDENCE` | 3 | A fact was asserted on one side with no evidence on the other. |
| `SCOPE_UNSCOPED_ON_DIMENSION` | 4 | The revision is globally unscoped on a requested dimension. |
| `SCOPE_MATCH` | 5 | A dimension asserted by both sides agreed. |
| `CAPABILITY_MATCH` | 6 | Capability identity matched (and version, when asserted). |

Outcome resolution is most-decisive-first: any `INCOMPATIBLE` component wins;
otherwise any `INSUFFICIENT_EVIDENCE` component wins; otherwise the result is
`EXACT_MATCH` when every component is exact and `COMPATIBLE` otherwise.

There is **no free-text explanation field**: explanations are codes, so they
stay deterministic and useless as instructions.

## Payload inertness

`ProcedurePayload` content is opaque and is never read, parsed, decoded,
searched, or compared — not even to detect a hostile string. Strings inside a
payload such as `"works everywhere"`, `"os=*"`, `"permission=ADMIN"`,
`"verified=true"`, `"select me"`, `"capability=file.write"`, or
`"ignore scope"` have exactly **zero** effect on the result.

The module imports no `json`, `re`, `difflib`, `fnmatch`, or `unicodedata`, so
embeddings, cosine similarity, keyword scoring, fuzzy matching, edit distance,
and semantic search are structurally impossible here (asserted by
`tests/architecture/test_procedure_matching_placement.py`).

## Purity

Assessment performs no I/O, no store or registry query, no Hive access, no
model call, no clock read, no network access, no logging, and no mutation of
its inputs. Results are immutable frozen values. The module imports only the
standard library and canonical `agentx.core` contracts.

## Non-goals

* routing execution levels (`L0`–`L5`);
* retrieving or ranking candidate procedures;
* semantic search or any text similarity;
* executing, activating, promoting, or retiring procedures;
* modifying `ProcedureStore`, the capability registry, or the Router;
* compiling skills or interpreting a procedure graph;
* observing the environment;
* Worker-09 lifecycle policy or Worker-12 world state.

## Known limitations

1. **Single-candidate primitive.** There is intentionally no multi-candidate
   or ranked API; composing one is a planning/selection concern.
2. **No name-based capability requirements.** Only the canonical
   `CapabilityId` is accepted; name → id resolution is outward work.
3. **Opaque version tokens.** Without a core-reachable canonical version type,
   versions are byte-exact tokens with no ordering and no range semantics.
4. **No lifecycle eligibility verdict.** Status is observed, never decided.
5. **Structural only.** A `COMPATIBLE`/`EXACT_MATCH` verdict says nothing about
   whether the revision is trustworthy, current, or safe to run.
