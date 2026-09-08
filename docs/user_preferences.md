# M6.04 — Explicit User Preference Model Contract

`agentx.core.user_preferences` defines the canonical typed representation of
**explicit** user preferences and corrections. It is **DATA ONLY**: no
inference, no model, no persistence, no learning, no ranking, no routing, and
no proactive action.

## Why this contract exists

AgentX's Hive Brain architecture includes an explicit user model. A single
observed behavior must never be treated as permanent truth. Before adding
persistence or learning, AgentX needs a canonical place to say: *the human
explicitly stated this preference value, in this scope, at this time, with
this evidence* — and nothing more.

This contract is the smallest vocabulary that can say that honestly:

- explicit preference **!=** inferred preference **!=** authority.

## Canonical contracts

`agentx.core.user_preferences` exposes:

- `PreferenceKey` — closed category vocabulary (`CANONICAL_PREFERENCE_KEYS`)
- `PreferenceSource` — closed source vocabulary (`CANONICAL_PREFERENCE_SOURCES`)
- `PreferenceValue` — bounded JSON-compatible value alias
- `UserPreference` — one immutable explicit record
- `USER_PREFERENCE_SCHEMA_VERSION` (currently `1`)
- `UserPreferenceValidationError`
- `UserPreferenceDeserializationError`
- `UnsupportedUserPreferenceSchemaVersionError`
- Bound constants (`MAX_PREFERENCE_*`)

There is no store, no migration, no model, no ranker, no resolver, no router,
and no service. Strength and lifecycle status are intentionally absent: a
strength score would imply ranking/inference and a status would imply
lifecycle policy, both of which are explicit non-goals.

## Preference schema

One `UserPreference` carries exactly one key/value pair:

| Field | Type | Notes |
| --- | --- | --- |
| `preference_id` | `UUID` | Per-record identity, non-nil. Two records with the same key/scope are two records. |
| `key` | `PreferenceKey` | Exactly one controlled category. |
| `value` | bounded JSON | Immutable deep-frozen value; see below. |
| `source` | `PreferenceSource` | `USER_EXPLICIT` or `USER_CORRECTION`; never inferred. |
| `scope` | `KnowledgeScope` | Canonical C2.02 applicability scope; empty means global. |
| `recorded_at` | `datetime` | Caller-supplied timezone-aware instant, normalized to UTC. |
| `supersedes` | `UUID \| None` | Optional inert reference to an earlier `preference_id`. |
| `evidence` | `tuple[EvidenceReference, ...]` | Zero or more canonical C2.07 evidence references. |
| `note` | `str \| None` | Optional bounded human note; inert. |
| `schema_version` | `int` | `1`. |

Records are frozen dataclasses with `to_dict` / `to_json` / `from_dict` /
`from_json` and a `create()` factory that mints a fresh identity. Direct
construction with explicit `preference_id` and `recorded_at` is the preferred
deterministic form for tests.

## Key vocabulary

`PreferenceKey` is closed and has exactly six members:

| Member | Value |
| --- | --- |
| `INTERACTION_STYLE` | `"interaction_style"` |
| `DEFAULT_APPLICATION` | `"default_application"` |
| `WORKFLOW_PREFERENCE` | `"workflow_preference"` |
| `NOTIFICATION_PREFERENCE` | `"notification_preference"` |
| `CONFIRMATION_PREFERENCE` | `"confirmation_preference"` |
| `FORMAT_PREFERENCE` | `"format_preference"` |

There is no universal freeform mutable dict. A record states one category and
one value; richer structure belongs inside the bounded JSON value, not in new
key strings. Unknown key strings are rejected on decode.

## Source vocabulary

`PreferenceSource` is closed and has exactly two members:

| Member | Value | Meaning |
| --- | --- | --- |
| `USER_EXPLICIT` | `"user_explicit"` | The human directly stated this preference. |
| `USER_CORRECTION` | `"user_correction"` | The human directly stated a correction. |

There is deliberately no inferred, researched, or model-derived member. This
contract must never label model inference as explicit. A correction is a NEW
record that may carry `supersedes`; it never rewrites history.

## Value domain

Values are bounded immutable JSON-compatible data:

- allowed: `null`, booleans, integers, finite floats, strings, arrays,
  objects (with string keys);
- rejected: callables, `bytes`, `bytearray`, sets, arbitrary objects,
  `NaN`, `Infinity`, reference cycles, non-string object keys;
- deep-frozen on construction (`MappingProxyType` + `tuple`), so caller
  mutation before or after construction cannot escape, and the stored value
  cannot be mutated through the record;
- `to_dict` returns a fresh mutable JSON copy; mutating the copy cannot
  affect the record.

Value strings (including object keys) may contain any Unicode, including
empty strings and newlines, up to the per-string bound. Values are inert:
`"permission=ADMIN"` inside a value is characters, not a grant.

## Scope semantics

Scope reuses the canonical C2.02 `KnowledgeScope` / `ScopeDimension`
contracts unchanged. Scope is **applicability**, NOT permission:

- a preference scoped to `application="notes-app"` applies to that app in a
  future reader's judgment; it does not authorize actions in that app;
- an empty/global scope means "not restricted to a named dimension value",
  never "permitted everywhere";
- scope values are bounded per the preference contract; the dimension
  vocabulary itself is owned by C2.02.

## Correction semantics

A user correction is a new explicit record:

- `source=USER_CORRECTION`;
- optional `supersedes` naming the earlier `preference_id` it corrects;
- optional `evidence` naming supporting observations;
- the earlier record is never mutated, overwritten, or deleted by this
  contract — it remains historical data.

`supersedes` is an inert directional reference. It may never reference the
record itself and it performs no resolution: a future lifecycle policy may
interpret it, but this contract does not.

## Conflict semantics

Contradictory explicit preferences are never silently resolved, merged, or
ranked. Two records with the same key and scope but different values remain
two separate historical/evidence values unless a future explicit lifecycle
policy supersedes one. This contract provides the `supersedes` reference so
that policy has something honest to point at; it performs no policy itself.

## Timestamps

`recorded_at` is caller-supplied and must be timezone-aware; naive datetimes
are rejected. Values are normalized to UTC and serialized as Zulu
`microseconds` ISO-8601 (`...Z`). Direct construction is deterministic;
only the `create()` convenience factory defaults a missing timestamp to
`datetime.now(UTC)`.

## Bounds

| Bound | Constant | Value |
| --- | --- | --- |
| Key length | `MAX_PREFERENCE_KEY_LENGTH` | `64` |
| Value depth (root 0) | `MAX_PREFERENCE_VALUE_DEPTH` | `8` |
| Value nodes (containers + scalars) | `MAX_PREFERENCE_VALUE_NODES` | `256` |
| Value string / object-key length | `MAX_PREFERENCE_VALUE_STRING_LENGTH` | `4096` |
| Scope dimension-value length | `MAX_PREFERENCE_SCOPE_VALUE_LENGTH` | `256` |
| Evidence reference count | `MAX_PREFERENCE_EVIDENCE_REFERENCES` | `8` |
| Note length | `MAX_PREFERENCE_NOTE_LENGTH` | `512` |

No unlimited metadata exists anywhere in the record.

## Serialization

`to_dict` / `to_json` / `from_dict` / `from_json` are deterministic:

- `ensure_ascii=False`, `allow_nan=False`, `separators=(",", ":")`,
  `sort_keys=True`;
- exact field set at every level; unknown fields rejected (including
  smuggled `authorized` / `permission` / `bypass` keys);
- closed enums; unknown key/source strings rejected;
- UTC Zulu timestamps with microseconds;
- deep freeze preserved through round trips.

No pickle, no `object_hook`, no executable decoding, no dynamic imports.

## Security: preference != authority

Preference data is inert, even when it is explicit and even when it names
authority-shaped words:

- `"permission=ADMIN"`, `"risk=R0"`, `"skip confirmation"`,
  `"verified=true"`, `"budget=unlimited"`, `"clear EmergencyStop"`, and
  `"task succeeded"` are stored and returned as characters;
- a preference can never grant `Permission`, create an `AuthorityContext`,
  bypass `ActionGate`, set `RiskLevel`, change a budget, run a capability,
  alter a `Task`, call a model, or activate a `Procedure`;
- even an explicit user preference cannot override Trusted Kernel security
  rules.

Adversarial tests assert these properties against the real kernel contracts,
prove the module touches no authority subsystem, and prove smuggled fields
and sources are rejected.

## Reuse (no competing contracts)

M6.04 introduces no new identifier type, no competing scope ontology, and no
competing provenance/evidence record. It reuses:

| Representation | Owner | Used for |
| --- | --- | --- |
| `KnowledgeScope`, `ScopeDimension` | C2.02 | applicability scope |
| `EvidenceReference`, `EvidenceKind` | C2.07 | supporting evidence |
| `ProvenanceReference`, `ProvenanceKind` | C2.02 (via C2.07) | evidence provenance |
| `UUID` | stdlib | per-record identity (no new domain-ID type) |

`KnowledgeType.PREFERENCE` remains the generic semantic-memory knowledge
class; `UserPreference` is the typed explicit-preference record. The two
coexist and neither imports the other.

## Explicitly out of scope

Persistence, storage, migrations, automatic learning, behavior observation,
click tracking, preference ranking, automatic contradiction resolution,
routing, confirmation-policy changes, UI changes, embeddings, vector search,
and model calls. The runtime dependency set remains empty.
