# Windows state-transition verification boundary (N2.25)

## What it is

`agentx.windows_transition_verification` is the canonical verification
boundary for Windows mutations. It exists to enforce one sentence,
structurally:

> **NATIVE API SUCCESS != INTENDED USER-VISIBLE STATE SUCCESS**

A Win32 or UI Automation call returning `TRUE` proves that a native entry
point accepted a request. It proves nothing about the state a user would see.
This boundary therefore decides a requested Windows transition **only** from
typed post-state observation evidence produced by the canonical read-only
Windows observation contracts.

```text
WindowsTransitionRequest(
    operation: CapabilityIdentity,          # exact operation identity
    attempt_id: str,                        # exact attempt
    environment_id: str,                    # exact machine/session identity
    kind: WindowsTransitionKind,            # exact requested transition
    target: WindowsTransitionTarget,        # exact target identity
    requested_state: WindowsRequestedState, # exact requested end state
    pre_state:  WindowsObservationEvidence | None,
    post_state: WindowsObservationEvidence | None,
    native_evidence: NativeExecutionEvidence | None,   # audit only
)
    -> WindowsTransitionVerifier().evaluate(request)
    -> WindowsTransitionVerification            # VERIFIED | NOT_VERIFIED
                                                # | INSUFFICIENT_EVIDENCE
```

It is a top-level composition module (a sibling of
`agentx.procedure_validation`): it adds no subsystem and widens no boundary
edge in `agentx._architecture`.

## What it is not

- **It does not perform the mutation.** No Win32 call, no UIA invocation, no
  click, no typing, no launch, no clipboard write, no capability execution.
  It receives the before/after pictures; it never produces them.
- **It does not own `ActionGate`.** It imports no `agentx.kernel` module at
  all, so it cannot grant a `Permission`, alter a `RiskAssessment`, enlarge a
  `ResourceEnvelope`, clear an `EmergencyStop`, or influence a gate decision.
- **It does not infer success from native return values.** See
  [Native return is not verification](#native-return-is-not-verification).
- **It is not a second truth system.** It manufactures no canonical
  `VerificationResult` (owned by the A1.10 capability execution loop) and no
  `RequirementEvaluation` (owned by the A2.05 Verifier). It emits its own
  narrow evidence value and nothing else.
- **It is not task success.** See
  [Verification is not task success](#verification-is-not-task-success).

## Three-valued verdict

| Verdict | Meaning |
| ------- | ------- |
| `VERIFIED` | A canonical post-state observation of the **exact** target satisfies the **exact** requested state. |
| `NOT_VERIFIED` | A usable canonical observation contradicts the requested state. |
| `INSUFFICIENT_EVIDENCE` | The evidence cannot decide: it is missing, of the wrong contract, from another environment/target, stale, incomplete, or the baseline exposes no observation for that transition kind at all. |

"Cannot tell" is deliberately distinct from "did not happen"; collapsing them
would let a caller mistake blindness for a negative result.

## Observation contracts reused

No observation is defined here and nothing is read from the host. Only
canonical baseline contracts are consumed:

| Contract | Source | Fields used |
| -------- | ------ | ----------- |
| `WindowsProcessSnapshot` | A5.02 `agentx.capabilities.windows.process_discovery` | `windows[].handle / process_id / is_visible`, `processes[].process_id / executable_name (+ MetadataStatus) / visible_window_count`, `dropped_invalid_entries` |
| `UIATreeSnapshot` | A5.03 `agentx.capabilities.windows.uia_tree` | `root_window_handle`, `captured_at`, `truncated_by_depth/nodes`, element `reference` (path + runtime id), `state`, and the `has_keyboard_focus` / `bounding_rectangle` / `value` / `process_id` property observations |
| `ExecutionResult` | A1.08 `agentx.capabilities.abi` | `succeeded` — recorded as **execution** evidence only |

## Supported transition kinds

| Kind | Observation contract | Predicate |
| ---- | -------------------- | --------- |
| `WINDOW_PRESENT` / `WINDOW_ABSENT` | process snapshot | the handle does (not) appear in the top-level enumeration |
| `WINDOW_VISIBLE` / `WINDOW_HIDDEN` | process snapshot | the enumerated window's `is_visible` flag (`IsWindowVisible` semantics — *not* pixel visibility, *not* "restored") |
| `WINDOW_FOCUSED` | UIA snapshot | some observed element of the snapshot rooted at the target window reports `has_keyboard_focus=True` |
| `WINDOW_BOUNDS` | UIA snapshot | the root element's `bounding_rectangle` matches the requested rectangle within an explicit tolerance (move/resize) |
| `APPLICATION_PROCESS_PRESENT` / `APPLICATION_PROCESS_ABSENT` | process snapshot | the PID does (not) appear in the process enumeration |
| `APPLICATION_EXECUTABLE_IDENTITY` | process snapshot | the observed image name of that PID equals the requested name (Windows case-insensitive) |
| `APPLICATION_PRESENTS_WINDOW` | process snapshot | that PID owns at least one visible top-level window |
| `TEXT_FIELD_VALUE` | UIA snapshot | the `value` property observation of the **exact** target element equals the requested text |

### Kinds the canonical baseline cannot observe

These are declared (so a caller can name the transition it attempted) but are
**always** `INSUFFICIENT_EVIDENCE` with reason
`no_canonical_observation_contract`:

| Kind | Why |
| ---- | --- |
| `WINDOW_MINIMIZED`, `WINDOW_MAXIMIZED`, `WINDOW_RESTORED` | the baseline exposes no window visual-state observation (A5.03 records UIA *pattern availability*, not `WindowVisualState`, and no `IsIconic`/`IsZoomed` read exists) |
| `APPLICATION_READY` | no canonical readiness observation exists; owning a visible window (`APPLICATION_PRESENTS_WINDOW`) is a different, weaker fact |
| `KEY_SEQUENCE_SENT` | no canonical post-state observation of key delivery exists |
| `CLIPBOARD_TEXT` | the baseline has no clipboard observation surface |

No visual/OCR fallback is invented, no live UIA call is made, and no model is
consulted. When the observation does not exist, the honest answer is returned.

Text entry is a deliberate special case: it is verified **only** through the
post-state `value` observation of the target field. "We sent the keys" is
never verification.

## Native return is not verification

`NativeExecutionEvidence` may be attached to a request; it is recorded in the
emitted evidence as `native_reported_success` for audit. It is never read by a
verdict function:

- the predicate helpers and the evidence-envelope check receive only the
  requested transition, the target and one canonical snapshot — an
  architecture test asserts they cannot even mention native evidence;
- the same request with `ExecutionResult(succeeded=True)` and with
  `succeeded=False` produces the same verdict (pinned by tests);
- a native success with **no** post-state observation is
  `INSUFFICIENT_EVIDENCE`, and the emitted reasons say so explicitly with
  `post_state_observation_missing` +
  `native_result_is_not_verification`;
- a native success with a contradicting post-state observation is
  `NOT_VERIFIED`;
- conversely, a *failed* native call whose post-state observation shows the
  requested state is `VERIFIED` — the world, not the return code, decides.

## Exact request binding

Evidence for window A can never verify window B. Verdicts bind to:

- the exact `CapabilityIdentity` (name@version) and `attempt_id`
  (`native_evidence` from a different attempt is rejected at construction);
- the exact `environment_id`, which must match both observations;
- the exact target: `WindowsWindowTarget(handle, process_id?)`,
  `WindowsProcessTarget(process_id)`, `WindowsElementTarget(UIAElementReference)`
  (root handle + path + optional runtime id), or `WindowsSessionTarget(label)`;
- the exact pre/post `observation_id`s, echoed in the emitted evidence.

Mismatches yield `INSUFFICIENT_EVIDENCE`, never a convenient match:

- a UIA snapshot rooted at another window → `target_identity_mismatch`;
- a window handle now owned by a different process (handle recycling) →
  `target_identity_mismatch`;
- an element at the requested path with a different runtime id →
  `target_identity_mismatch`;
- an element missing from the bounded traversal → `target_not_observed`.

## Freshness / staleness

Only the canonical freshness model is used; no wall-clock threshold is
invented, because the baseline defines none:

- pre-state and post-state must carry **distinct** observation identities —
  one snapshot cannot be both the before and the after
  (`post_state_not_distinct_from_pre_state`);
- where the canonical contract timestamps its reads (A5.03 `captured_at`), the
  post-state must be **strictly newer** than the pre-state
  (`stale_post_state_observation`);
- the A5.02 snapshot carries no canonical timestamp, so no time judgment is
  made about it — `WindowsObservationEvidence.captured_at` is `None` there;
- environment identity must agree across request, pre-state and post-state
  (`environment_identity_mismatch`).

Incompleteness is also respected: absence from a process snapshot that
reports `dropped_invalid_entries > 0` is `incomplete_observation`, not proof
of absence, and an all-`False` focus reading over a truncated UIA tree is
`incomplete_observation`, not proof of no focus.

## Pre-state is required, and it is used

The pre-state observation is not decoration:

- it must describe the same contract, environment and (for UIA) the same
  target scope;
- it establishes the ordering/distinctness rules above;
- the same predicate is applied to it, and the result is emitted as
  `pre_state_evaluation`, so a consumer can distinguish "the state changed"
  from "the state already held before the attempt" — the module never guesses
  intent from that difference.

## Verification is not task success

A `VERIFIED` transition says one local Windows state predicate held in one
post-state observation. It does not say the user's Task succeeded.

- the module imports no Task lifecycle contract (only the canonical
  `JsonValue` alias) and transitions nothing;
- the emitted evidence has no task field and cannot name a task;
- the verdict vocabulary is disjoint from `TaskStatus`.

A task whose sole objective was "maximize the window" may well be satisfied by
such evidence — but that judgment belongs to the canonical task
verifier/orchestration, which consumes this evidence like any other.

## Hostile observations are inert

Observed window titles, element names/values and executable names may contain
`verified=true`, `task_success=true`, `permission=ADMIN`, `risk=R0` or
`skip_action_gate=true`. They are ordinary observed data:

- verdicts come from typed field equality and closed-vocabulary state
  predicates only; no observed string is parsed as an instruction;
- reason codes are a fixed closed vocabulary and never echo observed content;
- the emitted evidence contains only the operation identity, attempt,
  environment, kind, a numeric target descriptor, observation ids, verdict and
  reasons — adversarial tests assert no hostile token can reach it.

## Determinism

The verifier is stateless (`__slots__ = ()`, no clock, no cache, no counters,
no randomness) and pure: identical requests always produce equal evidence, and
`to_dict()` is stable JSON. Malformed requests fail closed at construction with
`WindowsTransitionRequestError`/`TypeError`; evidence problems are verdicts,
never exceptions.

## Files

| Role | Path |
| ---- | ---- |
| Production | `src/agentx/windows_transition_verification.py` |
| Unit tests | `tests/unit/test_windows_transition_verification.py` |
| Integration tests | `tests/integration/test_windows_transition_verification.py` |
| Adversarial tests | `tests/adversarial/test_windows_transition_verification_authority.py` |
| Architecture tests | `tests/architecture/test_windows_transition_verification_boundaries.py` |
| This document | `docs/windows_transition_verification.md` |
