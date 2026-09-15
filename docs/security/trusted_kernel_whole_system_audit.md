# AX-040 Trusted-Kernel Whole-System Security Audit

## Audit identity

- Task: **AX-040 — Trusted-Kernel Whole-System Security Audit**
- Repository: `harsh2025-sketch/AgentX`
- Branch: `agent-ax040/trusted-kernel-security-audit`
- Canonical BASE_SHA: `760fe33e4e00e3f98b60d92ca657bec7b2cd583e`
- Pull request: `#145`
- Scope: current canonical AgentX security architecture only; no prohibited future milestone implementation

This report records the stable security conclusions and machine-enforced proofs. The exact final branch SHA and exact-head CI run are recorded in PR #145 and the AX-040 handoff rather than embedded here: changing this file changes the commit SHA, so embedding a supposedly final self-referential SHA or CI run would make the evidence stale immediately.

**Security conclusion:** the audit found one concrete Trusted-Kernel race and one authority-provenance assurance gap. Both are repaired. No unresolved Trusted-Kernel bypass was identified in the reviewed canonical source after those repairs.

**Completion rule:** AX-040 is COMPLETE only when the exact PR head passes the Windows C1.01 quality gate (`pytest`, Ruff lint, Ruff format, mypy). A green exact-head run is completion evidence; a runner that executes zero steps is infrastructure failure, not a pass.

## 1. Trusted-Kernel authority model

The kernel owns permission vocabulary/evaluation, risk floors, Action Gate decisions, Emergency Stop, resource accounting, secret wrappers/resolution protocol, and canonical security audit records. Discovery, metadata, model output, research responses, browser/Windows/UI state, clipboard/file content, memory, causal experience, procedure data, repair evidence, and persisted records are data; authority-shaped text in those surfaces grants nothing.

The governed effect path is:

```text
trusted composition grant
  -> AuthorityContext
  -> PermissionEngine
  -> RiskAssessment effective floor
  -> ActionGate
  -> EmergencyStop final admission
  -> ResourceBudget atomic check-and-consume
  -> CapabilityExecutionLoop
  -> capability/provider/native seam
  -> observation
  -> canonical verification
  -> Task success only after verification
```

`AuthorityContext` remains the explicit externally supplied permission grant. Production runtime code consumes it by injection and does not derive it from Tasks, requests, model content, memory, persistence, capability metadata, or historical audit records.

## 2. Security invariants

AX-040 treats these as hard invariants:

1. Free-form content cannot become `Permission`, `AuthorityContext`, a lower `RiskLevel`, approval, or verification by textual claims.
2. Non-owner production source cannot construct `AuthorityContext`.
3. Missing or malformed authority fails closed.
4. Effective risk cannot be lowered below request characteristics; R3/R4 rules remain enforced.
5. Privileged effects enter through governed capability execution, not discovery/provider metadata.
6. Resource envelopes remain finite and atomic under concurrent consumption.
7. Emergency Stop is monotonic; stop activation and final new-execution admission have one serialized ordering.
8. Secret material is not ordinary serializable/loggable data.
9. Execution return, observation, model claims, historical success, or procedure termination cannot mark a Task successful without canonical verification.
10. Memory/learning/compiler data cannot self-promote a procedure or manufacture kernel authority.
11. Persistence reconstructs typed data, not executable authority.
12. Generic dynamic execution/deserialization seams relevant to hostile persisted/content data are absent from production source.
13. Hostile content may survive byte-for-byte across data paths while kernel authority state remains unchanged.

## 3. Audited subsystem matrix

| Surface | Security question | Result |
| --- | --- | --- |
| Architecture manifest | Can forbidden subsystem edges bypass ownership? | Existing architecture tests enforce canonical edges; AX-040 adds authority-construction ownership proof. |
| Permission Engine | Can strings/malformed values grant access? | Missing/wrong-typed authority fails closed; no wildcard/superuser permission exists. |
| AuthorityContext | Can surrounding production manufacture authority? | No baseline constructor outside owner; AX-040 regression guard forbids one. |
| Risk model | Can caller text/declared R0 suppress real risk? | Effective characteristic floor wins; external/destructive characteristics force R3/R4. |
| Action Gate | Can R3/R4 confirmation/destructive requirements be skipped? | Non-ALLOW does not execute; typed decisions remain separate from metadata/content. |
| Resource budget | Can limits be enlarged/reset/overspent concurrently? | Finite validated envelope and atomic lock-backed consumption. |
| Emergency Stop | Can stop be cleared or lose final admission race? | No reset API; pre-existing TOCTOU repaired with serialized final admission. |
| Secrets | Can raw material leak through ordinary representations/serialization? | Redacted wrappers and serialization rejection remain; ordinary production carries references. |
| Audit | Can historical ALLOW/Permission labels become grants? | Audit is descriptive historical data, not authority. |
| Capability ABI/Registry | Can discovery itself execute/grant? | Registry resolves typed identity only; execution remains governed. |
| CapabilityExecutionLoop | Can execution precede gates or success precede verification? | Ordered governed path; final stop admission added; verification remains sole success path. |
| Windows/native/UIA | Can ordinary capability code bypass governed seams? | Native imports/effects remain isolated behind explicit seams and architecture tests. |
| Keyboard/clipboard/browser | Can external content authorize itself? | Content remains untrusted data; authority is external and typed. |
| Hive/semantic memory | Can remembered `verified=true` or permission text become trust? | Ingestion remains unverified; hostile content is inert. |
| Causal experience/learning | Can historical success authorize future effects? | Historical evidence remains data; compiler validates typed evidence. |
| Procedure lifecycle | Can candidate/repair text self-activate? | Compiler produces `CANDIDATE`; lifecycle mutation is separate and explicit. |
| Model/reasoner/research | Can provider/model text grant tool authority? | Output is data only; no direct kernel authorization path. |
| Persistence | Can stored content become executable privileged state? | Typed reconstruction; no generic executable-deserialization seam found. |
| Event bus | Can events grant authority? | Events carry evidence/data only. |

## 4. Authority-source map

- `Permission` is the canonical enum in `agentx.kernel.permissions`.
- `AuthorityContext` is owned by the permission module and consumed by `PermissionEngine`/`ActionGate`.
- Capability descriptors declare required permissions and risk but cannot grant them.
- Audit persistence may reconstruct a historical `Permission` label, but an audit record is not an `AuthorityContext`.
- Task priority, capability names, metadata, model output, memory records, procedure parameters, repair artifacts, and event payloads are not authority sources.
- AX-040 statically rejects production `AuthorityContext(...)` calls outside the owner, including a direct `getattr(..., "AuthorityContext")` construction seam.

Residual trust assumption: Python is not a sandbox against arbitrary already-running malicious in-process code. AX-040 proves canonical AgentX subsystem/data boundaries and rejects the generic dynamic-code/deserialization paths relevant to turning hostile data into code; it does not claim language-level process isolation.

## 5. Privileged-effect map

Reviewed effect surfaces include filesystem mutations, Windows native/process/window operations, keyboard/text/clipboard operations, UI Automation, browser actions, provider/research calls, and procedure-dispatched actions. The authority invariant is the same for each: availability/discovery/metadata is not authorization, and effects must remain behind the governed runtime path.

AX-040 adds a production-tree AST guard for generic `eval`, `exec`, built-in `compile`, `__import__`, `os.system`, `importlib.import_module`, pickle/marshal loading, `shell=True`, and subprocess imports. The guard records the current architectural fact that hostile content has no generic code-loading path.

## 6. Attack classes and whole-system chain

The review and regression suite cover:

- missing/forged/malformed permissions and authority-shaped metadata;
- caller risk downgrade attempts and R3/R4 enforcement;
- budget limit validation and concurrent double-spend resistance;
- Emergency Stop monotonicity and final-admission TOCTOU;
- secret representation/serialization leakage;
- forged verification/success claims in model, strategy, observation, history, and procedure data;
- hostile semantic memory and provenance claims;
- hostile causal history, skill compilation, and procedure activation-shaped content;
- persistence corruption/malformed typed fields;
- forbidden architecture/native seams;
- generic dynamic execution/deserialization primitives.

The AX-040 grand-chain proof carries one hostile string through real durable/data contracts:

```text
hostile content
  -> KnowledgeRecord
  -> SemanticMemory durable store
  -> recall (still UNVERIFIED)
  -> CausalExperience payload/evidence
  -> learning/compiler stages
  -> synthesized Procedure CANDIDATE
  -> no ProcedureStore activation
  -> Permission/Gate/Risk/Budget/Stop/Task/Secret state unchanged
```

## 7. Findings

### V-AX040-01 — Emergency Stop check/use TOCTOU

**Baseline defect:** `CapabilityExecutionLoop` checked `EmergencyStop.stop_requested`, performed later context/budget work, and eventually invoked `Capability.execute()` without a serialized final stop/admission handoff. A concurrent stop could therefore activate after the early check but before the privileged action began.

**Invariant:** once stop activation wins the final admission race, a new privileged run must not begin.

**Repair:** `EmergencyStop.request_stop()` and `EmergencyStop.try_admit_execution()` now share one lock. The runtime performs final admission immediately before budget consumption/execution. If stop wins, the Task is cancelled, capability execution/verification remain at zero calls, capability state is unchanged, and budget usage is unchanged. If admission wins first, the operation is already in flight; later stop activation blocks subsequent admissions.

**Regression:** `test_stop_activation_winning_final_admission_prevents_execution_and_budget` deterministically forces stop to win at the final admission point.

### A-AX040-02 — Authority provenance assurance gap

**Baseline gap:** the production tree did not construct `AuthorityContext` outside `kernel.permissions`, but that ownership fact was not machine-enforced.

**Repair:** `test_only_permission_owner_can_construct_authority_context_in_production` scans production source and rejects non-owner constructors/direct reflective construction. No second issuer, Permission Engine, or Action Gate was introduced.

This is deliberately an architectural ownership proof, not a claim that arbitrary hostile Python already executing in-process cannot use reflection.

## 8. Repairs and validation-only changes

Security repairs:

- `src/agentx/kernel/emergency_stop.py`: shared stop/admission lock and final-admission API; monotonic no-reset semantics retained.
- `src/agentx/capabilities/runtime.py`: final Emergency Stop admission immediately before resource consumption/execution; fail-closed cancellation if stop wins.
- `tests/adversarial/test_ax040_whole_system_security.py`: authority provenance, dynamic-execution guard, TOCTOU regression, and hostile grand-chain proof.

Validation-only fixes exposed once CI infrastructure resumed:

- added explicit static annotations for the new private Emergency Stop synchronization fields; behavior unchanged;
- made the existing Windows native receipt helper generic over its success payload instead of incorrectly requiring invariant `Result[object, AgentXError]`; behavior unchanged;
- applied Ruff import ordering/formatting to the AX-040 adversarial test.

No duplicate kernel/gate, architecture redesign, future milestone implementation, ledger change, or merge is part of AX-040.

## 9. New AX-040 tests

`tests/adversarial/test_ax040_whole_system_security.py` adds:

- `test_only_permission_owner_can_construct_authority_context_in_production`
- `test_production_has_no_dynamic_authority_execution_primitives`
- `test_stop_activation_winning_final_admission_prevents_execution_and_budget`
- `test_grand_chain_hostile_content_remains_data_and_cannot_change_authority`

These supplement the existing unit, integration, architecture, Windows, and adversarial suites rather than replacing canonical subsystem-specific tests.

## 10. Concurrency / TOCTOU assessment

- Budget consumption is already atomic and covered by concurrent final-unit tests.
- Emergency Stop had the actionable race; it is now linearized at final execution admission.
- No ordinary clear/reset/resume/re-arm API exists for the same stop object.
- Approval is not represented as a mutable cached bypass in the reviewed runtime; R3/R4 remain typed gate outcomes.
- Procedure activation/replacement remains a separate lifecycle transaction that learning/compiler output cannot perform directly.
- Task success is still produced only after canonical verification evidence.

Emergency Stop is an admission boundary, not asynchronous preemption. AX-040 intentionally does not invent unsafe thread/process-kill machinery for already admitted operations.

## 11. Residual risks

1. **Python process trust:** source/type/architecture proofs do not sandbox arbitrary already-running malicious Python.
2. **In-flight stop semantics:** Emergency Stop prevents new admissions after activation wins; it does not forcibly terminate an already admitted native operation.
3. **Provider/native correctness:** AX-040 proves governance boundaries, not that every external OS/provider API is bug-free.
4. **Future surfaces:** new dynamic plugin/loading mechanisms, autonomous tool-calling loops, credential providers, or privileged capabilities must re-enter these kernel gates and extend the adversarial proof set.

No residual risk above is an unresolved bypass of the currently audited Trusted-Kernel model.

## 12. Deferred / not-currently-implemented surfaces

AX-040 does not implement N2.07, N2.27, N2.28, or other prohibited future milestones. Current provider-neutral reasoner/research contracts were reviewed as data boundaries only. Future autonomous exploratory runtimes, new model tool-calling surfaces, provider credential adapters, plugin/dynamic-loading mechanisms, and new privileged capabilities require renewed security review rather than inheriting this report as proof for code that did not yet exist.

## 13. Completion evidence policy

The authoritative completion evidence is the exact-head Windows C1.01 run attached to PR #145. It must execute and pass all of:

```text
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

The final AX-040 handoff records the exact HEAD_SHA, run ID, job ID, and results. This report intentionally does not hard-code those moving identifiers so recording the evidence cannot itself invalidate the exact-head evidence with another report-only commit.
