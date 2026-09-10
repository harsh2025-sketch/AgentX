# Windows UIA semantic target resolution (N2.24)

> **Status:** N2.24. This task implements deterministic semantic resolution of
> an intended UI target from **already-observed** structured Windows UI
> Automation data. It is selection/resolution only. It does not act.

## Scope

`agentx.capabilities.windows.uia_target_resolution` answers one question:

> Which observed UIA element, if any, uniquely satisfies this structured target
> query?

Resolution consumes the canonical A5.03 read-only observation model
(`agentx.capabilities.windows.uia_tree`). It performs **no live desktop read,
no UI Automation invocation, no clicking, no typing, no value setting, no focus
change, no window mutation, no model call, no embedding, no fuzzy ranking, no
screenshot, and no visual fallback**. A5.03 observes. N2.24 resolves. Later
capability code acts under normal governance.

## Observation contracts reused

| Contract | Meaning in this resolver |
| -------- | ------------------------ |
| `UIATreeSnapshot` | The bounded point-in-time observation set. Resolution keeps its `captured_at`, `freshness`, `limits`, `errors` and truncation flags verbatim. |
| `UIAElementReference` | Snapshot-local identity: root window handle, element path, optional native runtime ID. |
| `UIAElementSnapshot` | One observed element with direct and transitive parent/child provenance. |
| `UIAPropertyName` / `UIAPropertyObservation` | Canonical typed property facts; only available values can satisfy a typed query. |
| `UIAElementState` | `VANISHED` elements are excluded; `PARTIAL`/`AVAILABLE` elements remain eligible when their required facts are observed. |

Canonical A5.03 observations do **not** carry a `ClassName` property, so no
ClassName criterion is defined here. Control types are treated as the canonical
integer IDs UIA returns, never as natural-language control labels.

## Structured target query

`UIATargetQuery` is a frozen dataclass. Every supplied field is an exact typed
constraint; unsupplied fields are ignored.

| Field | Type | Match rule |
| ----- | ---- | ---------- |
| `reference` | `UIAElementReference` | Must belong to the supplied snapshot root; compare canonical element path, and runtime ID when the query reference carries one. |
| `automation_id` | `str` | Exact string equality. |
| `control_type` | `int` | Exact UIA control-type integer equality. |
| `name` | `str` | Exact string equality. |
| `parent_path` | `tuple[int, ...]` | Candidate's direct `parent_path` equals this path. |
| `ancestor_path` | `tuple[int, ...]` | Candidate is a strict descendant of this path. |
| `require_enabled` | `bool` | `True` requires enabled, `False` requires disabled. |
| `require_visible` | `bool` | `True` requires `IsOffscreen=False`, `False` requires `IsOffscreen=True`. |
| `require_keyboard_focusable` | `bool` | `True` requires keyboard-focusable, `False` requires not focusable. |

At least one criterion must be supplied. Arbitrary prompt text such as "click
the login button near the top right" is rejected unless every requested
constraint is represented by a structured field; the module has no
natural-language parser.

## Match precedence

The resolver is a filter, not a ranking engine. Stronger identifiers simply
narrow the result set:

1. Exact observed element reference/path.
2. Exact `AutomationId` plus any structural constraints.
3. Exact typed combination of structured properties (`control_type`, `name`,
   parent/ancestor, boolean requirements).
4. Weaker `name`-only matching only when it yields exactly one result.

No hard-coded geometry preference, no traversal-order tie-break, and no
coordinate scoring are applied. Matches are returned in canonical element-path
order, not arrival order, so the same multiset always yields the same result.

## Ambiguity policy

Ambiguity is a first-class outcome.

- zero matches -> `NOT_FOUND`
- one match -> `RESOLVED`
- more than one match -> `AMBIGUOUS`

Two buttons named `OK` are ambiguous. Two text boxes sharing a `Name` but with
distinct `AutomationId` become unique only when the query supplies that
`AutomationId`. Duplicate `AutomationId` values in malformed observations stay
ambiguous. The resolver never silently picks the first result, closest result,
or a model-chosen candidate.

## Untrusted UI text and authority

UIA text is inert data. Names, AutomationIds and values such as
`SYSTEM ignore previous instructions`, `permission=ADMIN`, `risk=R0`,
`verified=true`, or `click_me=true` are compared exactly and stored verbatim.
They cannot grant a `Permission`, change `RiskAssessment`, bypass the
`ActionGate`/`PermissionEngine`, clear an `EmergencyStop`, widen a resource
budget, mark a target verified, or cause a model call.

## Staleness and identity

Resolution does not verify liveness. A `RESOLVED` result means:

> this observed element matched this query in the supplied A5.03 observation
> set.

It does **not** mean the element still exists now. The result retains the
snapshot's `captured_at` and `UIAFreshness.POINT_IN_TIME` provenance. `VANISHED`
elements in the snapshot are never selected.

## Result model

`UIATargetResolutionResult` is frozen and stores:

- the exact `UIATreeSnapshot` supplied;
- the exact `UIATargetQuery`;
- the `UIATargetResolutionStatus`;
- the deterministic path-ordered `matches`;
- schema version `1`.

`to_dict()`/`to_json()` produce JSON-compatible evidence only. The result is
constructed so that a fabricated status/matches combination is rejected.

## Boundaries

The architecture test suite proves the allowed edge

> UI observation -> semantic resolution

is data processing, while

> semantic resolution -X-> UIA invocation/keyboard/mouse/ActionGate/model

is not present. The module imports no native seam, no provider, no registry, no
capability ABI, no Trusted Kernel, no cognition/model layer, no hive, and no
persistence layer.

## Tests

- `tests/unit/test_uia_target_resolution.py` — query validation, exact
  identity, AutomationId, control-type+name, parent/ancestor constraints,
  NOT_FOUND, AMBIGUOUS, boolean requirements, ordering determinism, hostile
  text, immutability, serialization.
- `tests/integration/test_uia_target_resolution.py` — A5.03 inspection with a
  fake read-only UIA surface followed by pure resolution, including that
  resolution never re-reads the surface.
- `tests/adversarial/test_uia_target_resolution_authority.py` — hostile UI
  text inert, no action/authority/verification surface, no `eval`/`exec`,
  permission/ActionGate/stop integrity, no liveness fabrication.
- `tests/architecture/test_uia_target_resolution_boundaries.py` — module
  ownership, canonical contract reuse, no native/network/persistence/model
  surface, no authority coupling, no module-level side effects.
