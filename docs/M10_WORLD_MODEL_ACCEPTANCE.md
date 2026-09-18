# M10 World Model / Environment Adaptation Acceptance

## Scope

Milestone M10 covers **AX-406 through AX-435 (30 tasks)**. This campaign started
from canonical main:

- `STARTING_MAIN_SHA=509f13d44a42f1803e22c85b0602083bfa1e1183`
- baseline strict audit: **16/30 VERIFIED**
- baseline missing block: **AX-422..AX-435**
- canonical main later advanced only through the proprietary-license integration
  (`5eb3c35598bddf6142523a8a0e34c23abcbb7f67`); that history was explicitly
  merged into the M10 branch before acceptance continued.

This document records implementation and reproducible acceptance evidence. A task
is not promoted merely because code exists; the final ledger is updated only
after second-pass audit and exact-head canonical CI.

## Architecture

M10 preserves the existing AgentX split:

- `agentx.core.world_state` is the inert provider-neutral state contract.
- `agentx.world_model` owns current environment entities, provenance,
  freshness, lazy caching, invalidation, applications, tasks and Hive links.
- `agentx.capabilities.windows.screen_capture` is a read-only Windows
  observation capability. It declares `Permission.READ`, R0 risk, one bounded
  machine action and no rollback because it does not mutate the host.
- `agentx.capabilities.windows._screen_native` is the isolated lazy-`ctypes`
  GDI/monitor seam. No native library is loaded at import time.
- `agentx.perception` converts governed frames into canonical
  `PerceptionObservation` state and performs structured-first target grounding.
- Long-term Hive/memory evidence stays distinct from current world state. The
  world model remains evidence, never authority.

No `WorldModel2`, M10 executor, test-only production store, dependency, OCR
package, CV package or ambient watcher was introduced.

## Observation and provenance model

A Windows frame records:

- immutable `ScreenFrameId`;
- environment and surface identity;
- UTC capture time;
- virtual-desktop physical-pixel bounds;
- row stride and SHA-256 pixel digest;
- bounded BGRA frame data retained only in a bounded in-memory observation
  buffer;
- each display's stable observed device name, bounds, work area, primary flag and
  effective DPI.

`build_perception_observation()` converts that frame into the existing
`PerceptionObservation` contract with a canonical `ObservationMetadata`
provenance/freshness envelope. Structured UIA/DOM evidence references are
preserved alongside a screen-frame evidence reference.

Screen text/pixels, DOM text, UIA names, window titles and filenames remain data.
None can create permissions, alter risk, enlarge a budget, clear cancellation,
or manufacture verification.

## Freshness and cache behavior

`LazyWorldStateCache` keeps its existing explicit TTL/invalidation model. M10
adds optional provider-side freshness validation before a nominally fresh cache
hit is reused. Providers that do not supply a validator keep the original fast
fresh-hit path.

The native filesystem provider supplies a cheap real-OS validator based on:

- existence/accessibility;
- entity type;
- size;
- `st_mtime_ns`.

Therefore a one-hour cached filesystem fact can still be rejected immediately
when the external filesystem changes. Validation does not update the cache; a
mismatch marks the entry stale and the normal provider re-observation path
refreshes it.

Acceptance proves:

1. fresh unchanged resource -> `FRESH_HIT`;
2. external write outside WorldModel -> mismatch detected -> invalidation ->
   fresh provider observation;
3. external delete -> old existence rejected -> observed missing state;
4. resource recreation -> missing state rejected -> observed existing state;
5. unrelated sibling resource remains a fresh hit.

This is selective invalidation, not global cache clearing.

## Screen capture and display semantics

The production Win32 provider reads the virtual desktop through:

- `GetSystemMetrics` virtual-screen coordinates;
- `EnumDisplayMonitors` / `GetMonitorInfoW`;
- `GetDpiForMonitor` when available, with bounded system-DPI fallback;
- memory-DC `BitBlt` + `GetDIBits`.

All GDI handles/DCs are released on every return path. Capture is bounded by a
defensive pixel limit. Retained frames are additionally bounded by frame count
and total bytes.

Coordinates remain virtual-desktop physical pixels, including negative
coordinates on monitors left/above the primary display. `DisplayObservation`
provides explicit logical/physical DPI conversion; no implicit 96-DPI assumption
is applied to non-96-DPI displays.

The Windows-host acceptance suite exercises the real provider on the canonical
GitHub Windows runner when `AGENTX_M6_REAL_HOST=1`.

## Grounding and evidence ranking

The grounding lifecycle is:

```text
governed frame observation
 -> canonical perception observation
 -> structured target search
 -> if uniquely supported: proposal
 -> else, if explicitly allowed and approximate geometry exists:
      bounded OCR-free visual fallback
 -> ambiguity: refuse to select
 -> before use: require same current frame + freshness window
```

Structured evidence always outranks visual evidence. The visual fallback uses
bounded local pixel contrast only. It does **not** OCR text, infer instructions,
or turn pixels into control-plane state.

A `VisualTargetProposal` contains only frame identity, region identity,
geometry, confidence, evidence kind/references and observation time. It contains
no permission, risk, budget, authorization or verification field.

## Environment-change acceptance

`tests/integration/test_m10_environment_adaptation_acceptance.py` uses real
temporary filesystem resources. The mutation is performed directly by the OS
filesystem path, outside WorldModel/cache APIs. The test never calls
`cache.invalidate()`, never sets `stale=True`, and never informs the provider
what changed.

Representative chain:

```text
real path state A
 -> observe + cache
 -> external write/delete/recreate
 -> provider-side freshness mismatch
 -> affected entry invalidated
 -> independent stat/readback
 -> world state B
 -> unchanged sibling remains reusable
```

The restart test uses two real Python process boundaries. Process A observes and
terminates. The environment changes while stopped. Process B starts with no
ephemeral cache restored, so it cannot blindly trust process-A current-state
objects.

## Governed runtime acceptance

`tests/integration/test_m10_screen_capture_governed.py` composes the production
contracts through:

```text
CapabilityRegistry
 -> ActionGate
 -> authority
 -> EmergencyStop
 -> ResourceBudget
 -> WindowsScreenCaptureCapability
 -> capability verification
 -> retained exact frame
 -> provenance-normalized PerceptionObservation
 -> WorldModel
 -> PerceptionGrounder
```

It separately proves:

- explicit READ authority permits the read;
- missing authority denies before the native surface is touched;
- zero machine-action budget denies before capture;
- cancellation before execution does not touch the native surface;
- final capability success is a separate verification verdict.

Recovery/perception does not receive a second budget or a permission upgrade.

## Layout-change recovery and perception benchmark

`tests/benchmarks/test_m10_perception_benchmarks.py` records two deterministic
benchmarks:

- layout change: an old proposal is tied to frame A, frame B changes layout, the
  frame-A proposal is rejected as stale, and one fresh grounding pass rebinds to
  the moved target;
- perception accuracy: 12 labeled structured-grounding cases with distractors
  are measured against exact ground-truth bounds. The test computes
  `correct / total`; it does not hard-code a success boolean without measuring.

These benchmarks measure their fixture population only; they are not claims of
general real-world vision accuracy.

## Failure/security coverage

Applicable coverage includes:

- fresh hit and cache miss;
- TTL/invalidation semantics from the existing world-model suite;
- real external change, delete and recreation;
- selective sibling reuse;
- structured/visual conflict ordering;
- visual ambiguity refusal;
- stale/replaced screen rejection;
- provider/native failure as explicit Result failure;
- malformed frame geometry/digest rejection;
- hostile environment text remaining inert;
- restart without stale cache restoration;
- permission denial;
- budget exhaustion;
- cooperative cancellation;
- bounded frame retention;
- existing ActionGate, emergency-stop, deadline and verifier regression suites.

Unknown/error/stale remain distinct states. Observation failure is never silently
converted to verified absence.

## Task-by-task second-pass map

| Task | Requirement | Production evidence | Acceptance evidence |
| --- | --- | --- | --- |
| AX-406 | structured Windows observation foundation | existing Windows/UIA/world contracts | existing world-state/integration audit |
| AX-407 | UIA snapshot foundation | canonical UIA tree | existing UIA/world tests |
| AX-408 | DOM snapshot foundation | canonical browser DOM | existing browser/world tests |
| AX-409 | screen/perception representation | `PerceptionObservation`, `ScreenBounds` | world-model + new governed frame normalization |
| AX-410 | world-state snapshot contract | `WorldStateSnapshot` | existing world-state tests |
| AX-411 | lazy world-state cache | `LazyWorldStateCache` | cache tests + real-change acceptance |
| AX-412 | environment freshness semantics | TTL + provider currentness check | expiry + real external mutation |
| AX-413 | environment invalidation evidence | cache epochs/events/provider mismatch | external mutation acceptance |
| AX-414 | application registry | `ApplicationRegistry` | existing persistence/restart-safe tests |
| AX-415 | active-window state | canonical independent foreground observation | existing world-model tests |
| AX-416 | process-state model | `ProcessState` | Windows snapshot/world tests |
| AX-417 | browser-state model | browser session/page state | browser navigation invalidation tests |
| AX-418 | filesystem-context state | `FilesystemState` + native provider | real write/delete/recreate tests |
| AX-419 | device-state model | `DeviceState` | existing device invalidation tests |
| AX-420 | task-state world binding | `TaskWorldBinder` | existing task invalidation tests |
| AX-421 | Hive/world relationship linkage | `WorldHiveLinkage` | existing durable-link tests |
| AX-422 | screen-capture provider | `WindowsScreenCapture` + capability + native seam | unit, governed integration, real Windows host |
| AX-423 | screen-frame identity | `ScreenFrameId`, digest/topology/time identity | identity-change tests |
| AX-424 | visual-region representation | canonical `PerceptionRegion` | structured/visual grounding tests |
| AX-425 | OCR-free primary visual grounding | `PixelContrastRegionDetector` + structured-first grounder | visual fallback unit acceptance |
| AX-426 | visual fallback router | `PerceptionGrounder` | structured-first/fallback-disabled/fallback tests |
| AX-427 | visual target proposal | `VisualTargetProposal` | proposal identity/evidence tests |
| AX-428 | structured-vs-visual evidence ranking | `GroundingEvidenceKind` + explicit rank | structured-over-visual test |
| AX-429 | visual ambiguity handling | `GroundingStatus.AMBIGUOUS` | equal structured target refusal |
| AX-430 | stale-screen rejection | frame-bound proposals + age window | replaced-frame and age rejection |
| AX-431 | display/DPI normalization | `DisplayObservation` conversions | 96/144 DPI tests |
| AX-432 | multi-monitor awareness | virtual bounds + per-display geometry | negative-coordinate two-monitor tests |
| AX-433 | layout-change recovery benchmark | frame-aware rebinding path | deterministic recovery benchmark |
| AX-434 | perception accuracy benchmark | measured benchmark harness | 12 ground-truth cases |
| AX-435 | world-model milestone acceptance | composed runtime/world/perception path | full M10 + repository CI |

## Validation commands

Canonical quality gate runs:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy
python scripts/task_ledger.py
AGENTX_M6_REAL_HOST=1 python -m pytest -q tests/windows
python -m pytest
```

Targeted M10 suites:

```bash
python -m pytest -q tests/unit/test_world_model.py
python -m pytest -q tests/unit/test_m10_screen_perception.py
python -m pytest -q tests/integration/test_m10_environment_adaptation_acceptance.py
python -m pytest -q tests/integration/test_m10_screen_capture_governed.py
python -m pytest -q tests/adversarial/test_m10_world_model_authority.py
python -m pytest -q tests/benchmarks/test_m10_perception_benchmarks.py
python -m pytest -q tests/windows/test_m10_screen_capture_real_host.py
```

## Known limitations

- The native capture implementation is Windows-specific by definition; unsupported
  hosts return the canonical unsupported-platform error.
- The visual fallback is intentionally not OCR and is not a semantic vision
  model. Without structured semantics it requires an approximate region hint;
  otherwise it refuses rather than inventing meaning.
- Frame retention is in-memory and bounded. Restart intentionally discards
  current-screen pixels; persistent historical memory must not masquerade as
  current environment state.
- The 12-case accuracy benchmark is a deterministic contract benchmark, not a
  population-level claim about arbitrary desktop UIs.

## Final acceptance recording

The final section is updated only after exact-head CI. The PR must remain open
and unmerged for Product Owner review.
