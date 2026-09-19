# M13 — Multi-Device and Android Acceptance

Task range: **AX-486–AX-515**.

Campaign PR: **#180**  
Implementation branch: `codex/m13-multidevice-android-complete`

## Acceptance boundary

M13 extends the existing AgentX capability fabric; it does not create a second agent
or a second permission system. Device content, ADB output, Android UI text, screenshots,
provider metadata and World Model observations remain **data, never authority**.

The governed execution shape is:

```text
Task / canonical ExecutionContext
  -> routing / device applicability
  -> typed Android capability
  -> canonical Executor
  -> CapabilityExecutionLoop
  -> ActionGate / PermissionEngine / risk / budget / EmergencyStop
  -> bounded Android provider / ADB transport
  -> independent readback verification
  -> canonical outcome / World Model / causal experience
```

Providers advertise capability but cannot grant permission, lower risk, enlarge budgets,
clear cancellation/emergency stop, fabricate verification or promote a Procedure.

## Production implementation

- `src/agentx/capabilities/device.py`
  - provider-neutral device/protocol contracts retained as the canonical foundation;
  - Android is a first-class capability platform.

- `src/agentx/capabilities/device_registry.py`
  - device registry, discovery reconciliation and provider ownership;
  - identity/deduplication, capability advertisement and health derivation;
  - freshness and replay rejection;
  - restart restore invalidates prior live availability until fresh rediscovery;
  - stable device-environment identity for applicability.

- `src/agentx/capabilities/android/transport.py`
  - one bounded ADB process seam;
  - structured host argv with `shell=False`;
  - validated serial/package/component identities;
  - bounded timeout/output, cancellation/deadline observation and error translation;
  - remote tokens are quoted before crossing the ADB shell boundary.

- `src/agentx/capabilities/android/provider.py`
  - ADB device discovery and Android metadata;
  - online/offline/unauthorized states represented explicitly;
  - capability advertisement remains descriptive only.

- `src/agentx/capabilities/android/ui.py`
  - bounded UIAutomator XML parsing;
  - DTD/entity rejection;
  - semantic selection by resource id, text, content description, class, package,
    state and bounds;
  - missing/ambiguous semantic targets fail closed.

- `src/agentx/capabilities/android/runtime.py`
  - package discovery and launch-activity resolution;
  - app launch plus foreground-package readback;
  - accessibility-tree observation;
  - semantic or explicit-coordinate tap;
  - bounded text entry, swipe, Back and Home;
  - bounded screenshot PNG capture with dimensions/digest;
  - canonical Capability descriptors with existing Permission/risk policy;
  - independent postcondition verification for mutations;
  - a transport exit code alone never proves success.

- `src/agentx/device_orchestration.py`
  - explicit device requirements and fail-closed routing;
  - cross-device Task DAG bindings;
  - PC -> phone, phone -> PC and browser -> phone handoff representations;
  - handoff delegates the same canonical ExecutorRequest/ExecutionContext instead of
    minting fresh authority;
  - device-specific Procedure environment applicability;
  - cross-device causal episodes preserve Task/correlation lineage.

- `src/agentx/world_model.py`
  - existing M10 device ingestion is used for Android observations;
  - provenance/freshness and unavailable-device invalidation remain canonical;
  - World Model state never grants execution authority.

## Verification and security evidence

Dedicated M13 suites:

- `tests/unit/test_m13_multidevice_android.py`
- `tests/integration/test_m13_governed_android.py`
- `tests/adversarial/test_m13_android_security_failures.py`

The tests exercise:

- registry/discovery/health/freshness and restart invalidation;
- offline and unauthorized devices;
- duplicate identity and stale/replayed discovery;
- ambiguous multi-device routing;
- hostile shell metacharacters remaining quoted data;
- hostile Android UI/prompt-injection strings remaining inert data;
- malformed XML/DTD rejection;
- cancellation before transport invocation;
- explicit AST proof that the ADB host process boundary uses argv and `shell=False`;
- package discovery;
- launch plus independent foreground readback, including fake-success rejection;
- accessibility hierarchy parsing and semantic ambiguity;
- semantic tap and independent state verification;
- text, swipe, Back and Home;
- screenshot validation;
- mutating action without a postcondition failing verification;
- canonical READ/WRITE/EXECUTE permission enforcement through Executor/ActionGate;
- cross-device target routing and handoff through the canonical Executor;
- device-specific Procedure applicability;
- World Model device ingestion;
- cross-device causal-lineage validation.

The existing AX-040 security audit was updated narrowly: generic production subprocess
usage remains forbidden, with only the reviewed bounded M13 ADB transport admitted as
a structured-process seam. `shell=True` remains forbidden.

## Persistence / restart

The registry snapshot may preserve stable identity and descriptive state, but restored
devices are not treated as live. Fresh availability requires rediscovery. This prevents
a pre-shutdown Android connection from becoming authority after restart without current
evidence.

No live ADB transport handle, permission, approval or budget is persisted by M13.

## Real-environment evidence boundary

No authorized Android physical device or emulator reachable through ADB is available in
the campaign environment.

Therefore this campaign **does not claim real Android acceptance**.

Deterministic provider tests prove production-path semantics, failure behavior and
security properties. They do not satisfy the roadmap exit criterion requiring an
end-to-end **PC + browser + phone** workflow with verification at every device boundary.

To close the remaining environment-dependent acceptance, run on a host with:

```text
adb version
adb devices -l
```

and at least one authorized online Android target. Exercise, through the canonical
AgentX Executor/ActionGate path:

1. PC step with independent verification;
2. browser step with independent verification;
3. governed handoff to the selected Android device;
4. Android package/app/UI action using accessibility-first targeting;
5. independent Android post-state readback;
6. cancellation/emergency-stop propagation across the handoff;
7. cross-device causal recording and World Model freshness/invalidation;
8. restart/reconnect validation where applicable.

Record host OS, ADB version, Android version/model category, non-sensitive serial
category, operations performed and verification evidence.

## Second-pass task audit

| Task | Requirement | Final status | Evidence |
| --- | --- | --- | --- |
| AX-486 | device abstraction foundation | VERIFIED | existing canonical device contract |
| AX-487 | device protocol foundation | VERIFIED | existing canonical device protocol |
| AX-488 | Device Registry | VERIFIED | registry + restart/security tests |
| AX-489 | device discovery | VERIFIED | provider reconciliation + failure tests |
| AX-490 | device capability advertisement | VERIFIED | typed advertisement + availability tests |
| AX-491 | device health model | VERIFIED | derived health/freshness tests |
| AX-492 | device environment identity | VERIFIED | stable applicability identity |
| AX-493 | Android provider | VERIFIED | production provider + controlled integration |
| AX-494 | ADB transport | VERIFIED | bounded argv-only transport + adversarial proof |
| AX-495 | Android package discovery | VERIFIED | typed package parsing/replay tests |
| AX-496 | Android app launch | VERIFIED | launcher resolution + foreground verification |
| AX-497 | accessibility-tree observation | VERIFIED | bounded UI hierarchy parsing |
| AX-498 | Android semantic target resolution | VERIFIED | exact selector + ambiguity fail-closed |
| AX-499 | Android tap action | VERIFIED | typed tap + semantic center + postcondition |
| AX-500 | Android text entry | VERIFIED | bounded/redacted typed input + verification |
| AX-501 | Android swipe action | VERIFIED | bounded gesture + verification |
| AX-502 | Android back/home actions | VERIFIED | typed key navigation + verification |
| AX-503 | Android state verification | VERIFIED | independent foreground/UI readback |
| AX-504 | Android screen fallback | VERIFIED | bounded PNG/dimension/digest evidence |
| AX-505 | Android risk policy | VERIFIED | existing canonical risk assessment |
| AX-506 | Android permission mapping | VERIFIED | canonical descriptor permissions + ActionGate |
| AX-507 | cross-device Task DAG | VERIFIED | explicit per-node device requirements |
| AX-508 | device-target routing | VERIFIED | health/capability/freshness + ambiguity failure |
| AX-509 | PC -> phone handoff | VERIFIED | canonical handoff representation/executor |
| AX-510 | phone -> PC handoff | VERIFIED | canonical handoff representation |
| AX-511 | browser -> phone handoff | VERIFIED | canonical handoff representation |
| AX-512 | device-specific Procedure applicability | VERIFIED | environment-scoped matcher evidence |
| AX-513 | cross-device causal episode | VERIFIED | canonical causal-lineage composition |
| AX-514 | PC/browser/phone workflow benchmark | BLOCKED | real Android target/environment unavailable |
| AX-515 | multi-device milestone acceptance | BLOCKED | AX-514 real-device exit criterion unresolved |

Strict M13 acceptance is therefore **28/30 VERIFIED (93.33%)**, with exactly two
environment-dependent blockers and no remaining locally implementable task recorded as
NOT_IMPLEMENTED.

## Canonical CI

The final PR head must pass C1.01 after this document and ledger reconciliation.
That gate includes runtime-only installation, Ruff, Ruff format, strict mypy, the
600-task ledger validator, Windows-host acceptance, real headless-Chrome M7 acceptance,
and the complete pytest suite.

The PR must remain open after exact-head green CI; merge/post-merge main validation is a
separate integration campaign.
