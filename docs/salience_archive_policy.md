# Salience / archive policy (C6.05)

C6.05 owns the deterministic policy for deciding which Hive records remain
**active/high-salience** and which belong in the **archival/low-salience**
tier. It is a pure decision boundary: `agentx.hive.salience_policy`.

**Archive is not delete.** The decision vocabulary
(`SalienceTier`) contains exactly `ACTIVE` and `ARCHIVAL`. An `ARCHIVAL`
decision lowers salience only — the record stays durably stored,
byte-for-byte, and fully retrievable. The policy holds no store reference at
all, so it is structurally incapable of writing, rewriting, or destroying
anything.

## Position in the architecture

| Concern | Owner |
| --- | --- |
| Lifecycle transitions (`KnowledgeStatus`) | C2.08 via the canonical `KnowledgeStore` |
| Retrieval / default visibility | C2.09 retrieval |
| **Salience tier projection (this task)** | **C6.05 `SaliencePolicy`** |

An `ARCHIVAL` decision is a **projection over records, not a lifecycle
transition**. It never changes `status`, never demotes or promotes trust, and
never rewrites storage. The policy consumes only evidence that already exists
on canonical records plus caller-supplied context.

## Factors (all deterministic, all evidence-based)

| Factor | Source | Effect |
| --- | --- | --- |
| Recency | `max(created_at, verified_at)` (knowledge), `observed_at` (negative), `max(created_at, ended_at)` (episodes) | Age vs. the configured threshold |
| Reuse/reference count | Caller-supplied `SalienceEvidence.reuse_count` (live references; never inferred from content) | At/above `min_reuse_resistance` → active resistance |
| Verification/support status | `KnowledgeStatus` | Selects the configured age ladder |
| Negative/failure importance | `NegativeExperienceRecord`, `EpisodeOutcome.FAILED` | Unconditional active protection (invariant: failed approaches are remembered) |
| Supersession | `KnowledgeStatus.SUPERSEDED` | Archival history, preserved verbatim |
| Scope/environment relevance | `KnowledgeScope` vs. caller-supplied current environment | Mismatch on a shared dimension caps the threshold at `mismatched_environment_max_age` |
| Provenance quality | Presence of the provenance hook | Trusted statuses without provenance are capped at `degraded_max_age` (invariant: every claim eventually has provenance). Provenance **kind** is never ranked — channels of origin carry no trust |
| Conflict | `KnowledgeStatus.CONFLICTED` | Stays active and visible until an explicit C2.08 resolution |

## Decision order (fixed)

1. `CONFLICTED` → **ACTIVE** with `CONFLICT_VISIBLE`.
2. `SUPERSEDED` → **ARCHIVAL** with `SUPERSEDED_HISTORY`.
3. `reuse_count >= min_reuse_resistance` → **ACTIVE** with `REUSE_RESISTANCE`.
4. Otherwise the effective threshold is selected by status —
   `trusted_max_age` (PROVISIONAL/SUPPORTED/VERIFIED, capped to
   `degraded_max_age` when provenance is absent), `degraded_max_age`
   (DEGRADED), `unverified_max_age` (UNVERIFIED) — and capped to
   `mismatched_environment_max_age` on an environment mismatch. The record
   archives exactly when `age >= effective_threshold`. The boundary instant
   itself archives (fail-closed; the archival tier loses nothing, mirroring
   the C2.09 freshness boundary).
5. Episodes: `FAILED` → **ACTIVE** with `FAILURE_IMPORTANCE`; other outcomes
   archive at `episode_max_age`.
6. Negative experience: **always ACTIVE** with `NEGATIVE_IMPORTANCE`,
   regardless of age or configuration.

Every decision carries `tier`, a deterministically ordered, duplicate-free
tuple of `SalienceReasonCode` values, the UTC `evaluated_at` reading, and an
auditable `SalienceFactors` snapshot (age, effective threshold, mismatch and
provenance-cap flags, reuse count).

## Configuration

All thresholds live on the frozen, validated `SaliencePolicyConfig` — there
are no scattered magic constants. Defaults are the canonical baseline:

| Field | Default | Meaning |
| --- | --- | --- |
| `unverified_max_age` | 30 days | UNVERIFIED records archive beyond this |
| `degraded_max_age` | 90 days | DEGRADED records (and provenance-capped trusted records) |
| `trusted_max_age` | 365 days | PROVISIONAL/SUPPORTED/VERIFIED with recorded provenance |
| `mismatched_environment_max_age` | 7 days | Cap for records scoped away from the current environment |
| `episode_max_age` | 90 days | Non-failed episodes |
| `min_reuse_resistance` | 2 | Live references required to resist archival |

Validation fails closed: every duration must be strictly positive,
`min_reuse_resistance` an integer ≥ 1 (booleans rejected), and the age ladder
must be monotone (`mismatched ≤ unverified ≤ degraded ≤ trusted`).

Time is read exclusively through the injected `clock` (a callable returning a
timezone-aware datetime, normalized to UTC), so every decision is
reproducible in tests.

## Never silently deleted

Because the policy performs no writes at all, nothing can be silently
deleted — and the classes the architecture protects are additionally
hard-coded to remain active: audit evidence (episodes stay append-only;
archival is a tier, not a removal), important failures and negative
experience (`NEGATIVE_IMPORTANCE` / `FAILURE_IMPORTANCE`), contradictions
(`CONFLICT_VISIBLE`), and provenance (`PROVENANCE_ABSENT` annotates — it
never erases).

## Explicit non-goals

* **No consolidation** — records are never merged, deduplicated, or summarized.
* **No revalidation** — no status is checked, refreshed, or transitioned;
  `ARCHIVAL` is not a `KnowledgeStatus` change.
* **No model calls** — no model providers, no network, no callables except
  the clock.
* **No resource accounting** — archiving frees no space, so kernel resource
  budgets are neither consumed nor consulted here.

## Guarantees tested

Recent unverified, old verified, old superseded, important failure, negative
memory, conflict, threshold boundaries (including the exact boundary instant),
invalid configuration, determinism across instances, archive reason codes,
and no-deletion composition over a real `KnowledgeStore` are covered in
`tests/unit/test_salience_policy.py`,
`tests/adversarial/test_salience_policy_adversarial.py`, and
`tests/architecture/test_salience_policy_boundaries.py`.
