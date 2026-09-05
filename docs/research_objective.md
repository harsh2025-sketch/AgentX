# Research objective contract (A4.02)

> **Status:** A4.02. This document describes the typed contract in
> `agentx.cognition.research_objective`. It describes DATA only. Authority is
> owned by the Trusted Kernel and is not affected by anything here.

## Position in the pipeline

```
goal
  -> explicit knowledge requirements
  -> A4.01 gap assessment            (agentx.cognition.gap_detector)
  -> GAP
  -> A4.02 research objective        (this module)
  -> A4.03 research provider         (NOT IMPLEMENTED)
```

A4.01 decides `SUFFICIENT` or `GAP`. A4.02 records, as the smallest immutable
typed value, *what* missing knowledge a future research subsystem would be
asked about. A4.02 performs no research.

## The contract

`ResearchObjective` is a frozen, slotted, keyword-only dataclass:

| Field                        | Meaning                                                                 |
| ---------------------------- | ----------------------------------------------------------------------- |
| `objective_id`               | Caller-supplied stable identity; inert text.                              |
| `question`                   | What is missing / must be resolved; inert text.                           |
| `unmet_requirement_ids`      | Canonically sorted A4.01 `requirement_id` values that were unmet.         |
| `scope`                      | Optional canonical C2.02 `KnowledgeScope`, reused verbatim.               |
| `preferred_provenance_kinds` | Optional *preference* over canonical `ProvenanceKind` channels.           |
| `acceptable_statuses`        | Optional canonical `KnowledgeStatus` values a future answer must hold.    |
| `knowledge_types`            | Optional canonical `KnowledgeType` constraint.                            |
| `schema_version`             | Currently `1`; unknown versions are rejected.                             |

`to_dict()` / `to_json()` / `from_dict()` / `from_json()` are the only public
methods. They are pure serialization: deterministic, order-independent, and
free of I/O.

## Relationship to A4.01

`research_objective_from_gap(assessment, ...)` constructs and validates an
objective from an existing `KnowledgeGapAssessment`. It reads already-computed
data only — no orchestration, retrieval, provider selection, or research.

Fail-closed rules:

- a `SUFFICIENT` assessment is rejected; it is never silently rewritten as a
  gap;
- supplied `requirement_ids` must be a subset of the assessment's genuinely
  unmet requirement identifiers;
- when omitted, every unmet requirement is linked.

Only `requirement_id` strings are copied. No A4.01 or C2.02 contract is
duplicated, reinterpreted, or re-exported, and `KnowledgeRecord.content` is
never read.

## Trust boundary

The future external-content pipeline is preserved:

```
UNTRUSTED CONTENT -> extraction -> claim extraction -> provenance
                  -> candidate knowledge -> verification
```

- `preferred_provenance_kinds` is a *preference/constraint*, never proof of
  truth and never access to that channel.
- `acceptable_statuses` states what would be acceptable to consider; it cannot
  promote, demote, or verify anything.
- No field can say "whatever the webpage says becomes VERIFIED".
- No trust is inferred from URL text or domain strings; the module contains no
  URL parsing at all.

## Authority

Constructing an objective grants nothing: no web, network, filesystem, browser,
model, embedding, capability, procedure, or experiment access; no permission,
risk, budget, or Hive mutation; no lifecycle promotion. There is deliberately
no field or method for any of those, and no executable callback can be
attached.

Free text is validated for shape only (non-empty, trimmed, bounded, no NUL or
carriage return) and is otherwise never parsed or interpreted.

## Persistence

None. A4.02 is an ephemeral in-memory contract with a stable JSON form for
future use. No store, table, or migration is introduced.
