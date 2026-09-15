# AX-040 Trusted-Kernel Whole-System Security Audit

## Audit identity and decision

- Task: **AX-040 — Trusted-Kernel Whole-System Security Audit**
- Repository: `harsh2025-sketch/AgentX`
- Branch: `agent-ax040/trusted-kernel-security-audit`
- Audited canonical BASE_SHA: `760fe33e4e00e3f98b60d92ca657bec7b2cd583e`
- Audited code/test HEAD before this report commit: `c1f72fc196c9eef34a8a82a13d3480f95ea3dc14`
- Pull request: `#145`
- Final branch HEAD: the commit containing this report; record the exact PR head in the final AX-040 handoff. A Git commit cannot embed its own SHA in content that participates in that SHA, so the code/test head above is the exact auditable content boundary and the PR head is the exact final repository boundary.
- AX-040 completion decision: **BLOCKED_INFRASTRUCTURE**

The security review found and repaired one concrete Trusted-Kernel race and added missing machine-enforced whole-system proofs. No unresolved bypass was identified in the reviewed canonical source. AX-040 is nevertheless **not COMPLETE** because the required exact-head quality gate did not execute and no complete local checkout was available to independently run `pytest`, Ruff, formatting, and mypy. The GitHub Actions run is an infrastructure failure under the task's explicit rule, not a code-test failure.

## 1. Trusted-Kernel authority model

The kernel owns permission vocabulary, permission evaluation, risk floors, Action Gate decisions, Emergency Stop, resource accounting, secret wrappers/resolution protocol, and canonical security audit records. Capability discovery is not authority. Model output, research responses, browser/Windows/UI state, clipboard/file content, memory, causal experience, procedure data, repair evidence, and persisted records are data and cannot grant kernel authority by textual claims.

`AuthorityContext` is the typed externally supplied permission grant consumed by `PermissionEngine` and `ActionGate`. At the audited baseline there were no production `AuthorityContext(...)` construction calls outside the owning permission module; the runtime only accepts an already supplied context. AX-040 adds a production-tree AST guard so surrounding source cannot begin manufacturing one without failing the adversarial suite.

The runtime authority path is:

```text
external trusted composition grant
        -> AuthorityContext
        -> PermissionEngine
        -> typed RiskAssessment effective floor
        -> ActionGate
        -> EmergencyStop final admission
        -> ResourceBudget atomic check-and-consume
        -> CapabilityExecutionLoop
        -> capability/provider/native seam
        -> observation
        -> canonical verification
        -> Task success only after verification
```

## 2. Security invariants

The audit treats the following as hard invariants:

1. Free-form strings, JSON fields, model text, memory, research evidence, browser/UI/clipboard/file content and procedure parameters never become `Permission`, `AuthorityContext`, a lower `RiskLevel`, approval, or verification merely by containing authority-shaped text.
2. Non-owner production source cannot construct `AuthorityContext`.
3. Permission checks fail closed for missing or wrong-typed authority.
4. Risk is request/characteristic sensitive and uses the effective floor; caller `R0` text cannot suppress R3 external effects or R4 destructive effects.
5. R3 remains confirmation-required and R4 remains destructive-permission plus confirmation-required.
6. Privileged effects enter through the governed capability path; discovery/provider/native seams are not public authorization shortcuts.
7. Resource envelopes are finite and non-negative; atomic budget consumption prevents concurrent overspend.
8. Emergency Stop is monotonic. Stop activation and final execution admission have one serialized ordering.
9. Raw secret material is not a normal serializable/loggable value; production source contains no `SecretValue.reveal()` call.
10. Execution return, observation, model text, or procedure termination is not Task success. Canonical verification is required.
11. Memory and learning evidence remain historical/context data. Skill compilation produces `CANDIDATE`, not implicit `ACTIVE` lifecycle authority.
12. Persistence is data reconstruction, not authority reconstruction; generic executable deserialization is absent.
13. No production generic `eval`, `exec`, built-in `compile`, `__import__`, `os.system`, `importlib.import_module`, pickle/marshal load, `shell=True`, or subprocess import may appear without failing the new AX-040 guard.
14. Arbitrary hostile content may survive byte-for-byte across data paths while kernel state remains unchanged.

## 3. Audited subsystem matrix

| Surface | Authority-sensitive question | Evidence reviewed | Result |
| --- | --- | --- | --- |
| Architecture manifest | Can forbidden subsystem dependencies reach kernel/private authority? | `agentx._architecture`, `tests/architecture/test_module_boundaries.py`, subsystem placement tests | No current forbidden canonical edge observed; AX-040 adds source-level authority-construction guard |
| Permission Engine | Can strings/unknown/malformed permissions grant access? | `kernel/permissions.py`, existing unit/adversarial tests | Fail closed on missing/wrong typed authority; no wildcard/superuser path |
| AuthorityContext | Can surrounding production manufacture authority? | repo-wide construction search plus AX-040 AST proof | No baseline production constructor outside owner; regression guard added |
| Risk model | Can caller lower effective risk? | `kernel/risk.py`, Action Gate adversarial tests | Effective floor wins; external/destructive characteristics force R3/R4 |
| Action Gate | Can R3/R4 confirmation/destructive requirements be skipped? | `kernel/action_gate.py`, runtime path, capability tests | Typed decisions; non-ALLOW denies runtime execution |
| Resource budget | Can limits be negative/unlimited/reset/overspent concurrently? | `kernel/resource_budget.py`, concurrency tests | Finite validated envelope; atomic lock-backed consume; no unlimited sentinel found |
| Emergency Stop | Can stop be cleared or lose a race to execution? | `kernel/emergency_stop.py`, `capabilities/runtime.py`, repository search | No reset/clear API; one pre-existing TOCTOU repaired with serialized final admission |
| Secrets | Can secret bytes leak via repr/str/serialization/ordinary production use? | `kernel/secrets.py`, unit tests, production `.reveal()` search | Redaction and serialization rejection present; no production reveal call found |
| Audit | Can historical ALLOW/permission labels grant authority? | `kernel/audit.py`, `audit_persistence.py`, audit tests | Audit records are historical typed data; deserialized permission labels do not create AuthorityContext |
| Capability ABI / Registry | Can discovery execute or grant? | ABI/registry/runtime and placement tests | Registry resolves typed capability identity only; execution remains in governed loop |
| CapabilityExecutionLoop | Can execution precede gate/stop/budget or success precede verification? | `capabilities/runtime.py`, integration/adversarial tests | Ordered gate path; TOCTOU repaired; verified Task success remains verification-only |
| Windows/native | Can ordinary capability code directly mutate through ctypes/shell? | Windows provider/native modules, boundary tests, repo searches | ctypes isolated to explicit native seam modules; shell/subprocess bypass not found |
| UIA | Can read/target content grant authority? | `_uia_native`, UIA boundary/adversarial tests | UI data remains typed observation/target data; native seam isolated |
| Keyboard/clipboard | Can clipboard/text content authorize itself? | keyboard/text/clipboard capability contracts and adversarial tests | Content treated as untrusted data; governed capability metadata remains typed |
| Browser | Can web/DOM content change policy? | browser action/connection contracts and adversarial tests | Hostile page metadata remains data; authority remains external |
| Verification / Task | Can success text forge Task success? | runtime, verifier, agent-loop adversarial tests | Only canonical verification path reaches `TaskStatus.SUCCEEDED` |
| Hive / semantic memory | Can remembered `verified=true` or permission text become trust/authority? | `hive/semantic_memory.py`, adversarial memory tests | Ingestion starts unverified; content returned inert |
| Causal experience / learning | Can historical success claims authorize future execution? | causal experience and skill compiler chain | Historical evidence remains data; compiler validates typed evidence |
| Procedure synthesis/lifecycle | Can candidate text self-promote/activate/replace/rollback? | compiler, synthesis, promotion/replacement/repair contracts | Compiler emits candidate only; lifecycle is separate explicit canonical transaction |
| Model/reasoner | Can model text grant permission/verification/tool authority? | reasoner contracts/tests | Model output is typed content only; no direct kernel authorization path |
| Research | Can provider response authorize action? | current provider-neutral research objective/acquisition contracts | Provider response is untrusted data; current surface does not own execution authority |
| Persistence | Can stored JSON become privileged executable state? | SQLite adapters, stores, audit persistence, repo primitive scan | Explicit typed reconstruction; no pickle/eval/exec-style deserialization found |
| Event bus | Can event payloads grant authority? | core events + infrastructure bus placement | Events transport evidence/data; no kernel grant API in bus |
| Repair/lifecycle | Can repair mutate kernel policy or activate itself? | repair data, validation, patch/materializer and lifecycle contracts | Repair artifacts are data; lifecycle mutation remains explicit and separate |

## 4. Authority-source map

Observed authority creation/consumption boundary:

- `Permission` values originate only from the canonical enum in `agentx.kernel.permissions`.
- `AuthorityContext` is defined only by the permission owner. Production runtime accepts it by injection; it does not derive permissions from Task/request/model/content/persistence.
- `PermissionEngine.check()` consumes a typed `Permission` and typed `AuthorityContext | None`; missing authority denies.
- `ActionGate.evaluate()` consumes typed `GateRequest` and `AuthorityContext | None`; operation text is descriptive, not parsed for policy.
- Capability descriptors carry required permission values and typed risk assessments but cannot grant those permissions.
- Audit/persistence may reconstruct a historical `Permission` label for an audit record; that record is not an `AuthorityContext` and is not a grant.
- The AX-040 source guard rejects production calls to `AuthorityContext(...)` outside the owning module, including simple `getattr(..., "AuthorityContext")` construction.

Residual trust assumption: AgentX's Python process is not an isolation boundary against arbitrary already-executing Python code. If an attacker obtains arbitrary in-process code execution, Python introspection can defeat source-level ownership conventions. The audited threat is canonical surrounding AgentX subsystems plus attacker-controlled **data**, and AX-040 separately removes generic dynamic-code/deserialization seams that could turn that data into Python execution.

## 5. Privileged-effect map

Reviewed effect classes and their canonical seams:

- filesystem mutations: typed capability/provider contracts under `agentx.capabilities`;
- Windows process/native mutation: explicit Windows native-mutation seam, with direct ctypes confined by architecture tests;
- keyboard/text/clipboard: typed Windows capability ports;
- UI Automation: isolated UIA native seam plus typed capability layer;
- browser navigation/click/form actions: browser capability/action boundary;
- provider/network/research calls: provider-neutral cognition/research ports, not authority grants;
- procedure actions: procedure interpreters/strategies produce control/data and must still reach governed capability execution for effects.

Repo-wide searches found no production `shell=True`, `os.system`, generic subprocess import, pickle/marshal executable deserialization, or built-in eval/exec path. AX-040 codifies those current facts as a regression test rather than relying only on a one-time search.

## 6. Attack classes tested/reviewed

The reviewed suite covers or AX-040 adds evidence for:

- missing, forged, malformed and hostile permission claims;
- caller attempts to lower risk to R0;
- R3/R4 confirmation/destructive enforcement;
- hostile metadata claiming `approved=true`, `system_authorized=true`, or `skip_confirmation=true`;
- budget limit validation and concurrent consumption;
- emergency stop monotonicity and final-admission TOCTOU;
- secret string/representation/serialization leakage;
- forged verification/success claims in model, strategy, observation and procedure data;
- unverified/hostile semantic memory;
- hostile causal history and candidate compilation;
- self-promotion-shaped procedure/repair content;
- persistence corruption/malformed typed fields in existing store tests;
- forbidden architecture edges and native seam placement;
- generic dynamic execution/deserialization primitives;
- whole-system hostile-content chain:

```text
hostile external-style string
  -> KnowledgeRecord
  -> SemanticMemory durable store
  -> recall (still UNVERIFIED)
  -> CausalExperience payload/evidence
  -> learning/compiler stages
  -> synthesized procedure CANDIDATE
  -> no ProcedureStore activation
  -> kernel permission/gate/risk/budget/stop/Task/secret state unchanged
```

## 7. Vulnerabilities found

### V-AX040-01 — Emergency Stop check/use TOCTOU

**Baseline defect:** `CapabilityExecutionLoop` observed `EmergencyStop.stop_requested` before context/budget work, then later invoked `Capability.execute()` without a serialized final stop/admission handoff. A concurrent `request_stop()` could therefore linearize after the early check but before a privileged run was admitted to execution.

**Invariant affected:** after Emergency Stop activation, a new privileged run must not begin.

**Repair:** `EmergencyStop` now owns a lock shared by `request_stop()` and `try_admit_execution()`. The runtime performs the final admission after authority/context checks and immediately before budget consumption/execution. If stop wins, the Task is cancelled, `Capability.execute()` is not called, and budget usage is unchanged. If admission wins, the run is already classified as in-flight; a later stop applies to subsequent admissions.

**Regression proof added:** `_StopAtFinalAdmission` deterministically activates stop exactly when the runtime reaches the final admission. The expected outcome is `DENIED`/`CANCELLED`, zero execute/verify calls, unchanged capability state and unchanged budget.

### A-AX040-02 — Authority provenance enforcement gap

**Baseline assurance gap:** `AuthorityContext` is a public frozen typed value. The current production tree did not construct it outside `kernel.permissions`, but no machine-enforced rule made that fact regression-resistant.

**Repair:** AX-040 adds a production-tree AST proof that rejects `AuthorityContext(...)` construction outside the owner and rejects a direct `getattr(..., "AuthorityContext")` construction seam. No second Permission Engine, issuer, or gate was introduced.

**Why this is not claimed as a language-level sandbox:** Python code already executing arbitrarily inside the process could use reflection. The security boundary proven here is that canonical AgentX surrounding subsystems and untrusted **data** have no allowed construction/dynamic-execution path.

## 8. Repairs made

1. `src/agentx/kernel/emergency_stop.py`
   - added a lock shared by stop activation and final execution admission;
   - added `try_admit_execution()` as a safety-only linearization point;
   - preserved monotonic no-reset semantics.
2. `src/agentx/capabilities/runtime.py`
   - added final serialized Emergency Stop admission immediately before resource consumption;
   - fail-closed cancellation if stop wins;
   - no budget consumption and no capability execution on that denial.
3. `tests/adversarial/test_ax040_whole_system_security.py`
   - authority-construction provenance guard;
   - dangerous dynamic execution/deserialization source guard;
   - deterministic Emergency Stop TOCTOU regression;
   - hostile memory -> learning -> compiler whole-system chain.

No unrelated architecture redesign, duplicate kernel, duplicate gate, future milestone implementation, or ledger edit was made.

## 9. Tests added

`tests/adversarial/test_ax040_whole_system_security.py` adds four whole-system security tests:

- `test_only_permission_owner_can_construct_authority_context_in_production`
- `test_production_has_no_dynamic_authority_execution_primitives`
- `test_stop_activation_winning_final_admission_prevents_execution_and_budget`
- `test_grand_chain_hostile_content_remains_data_and_cannot_change_authority`

These tests supplement, rather than replace, existing unit/integration/adversarial/architecture suites for permission, risk, Action Gate, budget, Emergency Stop, secrets, runtime verification, memory, procedures, repair, model/research and Windows/browser boundaries.

## 10. Concurrency / TOCTOU assessment

- Resource consumption is already lock-backed and atomic; the existing concurrent double-spend tests cover budget oversubscription.
- Emergency Stop had the actionable TOCTOU described above; repaired with a single canonical lock/linearization point.
- Approval expiry does not have a separate mutable cached-approval mechanism in the reviewed canonical runtime; R3/R4 are represented as Action Gate decisions and non-ALLOW does not execute.
- Procedure activation/replacement is a separate lifecycle transaction; learning/compiler output cannot directly mutate it.
- Verification is local to one closed-loop run and Task transition to success occurs only after returned canonical verification evidence.

The repair intentionally does **not** invent asynchronous thread/process preemption. A run admitted before a stop is an in-flight operation; Emergency Stop prevents later admissions.

## 11. Residual risks

1. **Required quality evidence unavailable.** The exact-head GitHub Actions Windows C1.01 run `34963205954`, job `104361447904`, completed as a runner-infrastructure failure: `runner_id=0`, blank runner name, and zero steps. It therefore provides no pytest/Ruff/mypy result.
2. **No complete local checkout in this execution environment.** Direct repository cloning was unavailable, so repository-wide local gates could not be substituted for the failed Actions runner.
3. **Python process trust boundary.** Source/type/architecture proofs do not sandbox arbitrary already-running malicious Python. No current generic dynamic execution/deserialization seam was found, and AX-040 adds a regression scan for the dangerous primitives relevant to this threat.
4. **Stop is an admission boundary, not preemption.** Already admitted in-flight native operations are not forcibly killed. This is explicit policy, not a silent reset/bypass.
5. **Native/provider correctness remains capability-specific.** AX-040 proves governance boundaries; it does not prove every external operating-system/provider API is itself bug-free.

## 12. Deferred / not-currently-implemented surfaces

AX-040 did not invent prohibited future milestones. In particular, no N2.07/N2.27/N2.28 implementation was added. Current provider-neutral reasoner/research contracts were audited as data boundaries. Any future autonomous exploratory runtime, new model tool-calling surface, new provider credential adapter, plugin/dynamic-loading mechanism, or new privileged capability must re-enter the same kernel gates and should extend this audit rather than treating this document as permanent proof for code that does not yet exist.

## 13. Quality-gate evidence

### Task-focused security tests

**Result:** NOT EXECUTED in an authoritative repository checkout. The tests were committed to the PR, but the only exact-head GitHub runner executed zero steps.

### Full pytest

**Result:** NOT EXECUTED — infrastructure blocked.

Required command: `python -m pytest`

### Ruff lint

**Result:** NOT EXECUTED — infrastructure blocked.

Required command: `python -m ruff check .`

### Ruff format

**Result:** NOT EXECUTED — infrastructure blocked.

Required command: `python -m ruff format --check .`

### mypy

**Result:** NOT EXECUTED — infrastructure blocked.

Required command: `python -m mypy`

### Windows C1.01 exact-head gate

- Workflow: `C1.01 Quality Gate`
- Run ID: `34963205954`
- Job ID: `104361447904`
- Head: `c1f72fc196c9eef34a8a82a13d3480f95ea3dc14`
- Reported conclusion: `failure`
- Runner evidence: `runner_id=0`, `runner_name=""`, `steps=[]`
- Interpretation required by AX-040: **INFRA_UNAVAILABLE**, not a code failure.

Per the task instruction, this run was not repeatedly rerun.

## 14. Evidence supporting the current decision

Security evidence is strong enough to say the audited code has no **known** unresolved Trusted-Kernel bypass after the minimal repair, but it is not strong enough to say AX-040 is COMPLETE because the mandatory executable quality gates are unproven. Passing tests must not be inferred from source review, and absence of a discovered vulnerability must not be inferred merely from existing tests.

Checklist at handoff:

- [x] authority creation/consumption paths audited
- [x] Permission Engine adversarially reviewed
- [x] risk model adversarially reviewed
- [x] Action Gate adversarially reviewed
- [x] CapabilityExecutionLoop bypass search completed
- [x] resource-budget/anti-loop boundary audited to current implementation
- [x] Emergency Stop audited; one TOCTOU repaired
- [x] secrets boundary audited
- [x] verification/task-success boundary audited
- [x] memory/learning/procedure authority boundary audited
- [x] model/research boundary audited to current implementation scope
- [x] persistence/deserialization boundary audited
- [x] architecture/import security proofs extended
- [ ] grand-chain proof executable pass confirmed (test added; runner unavailable)
- [x] meaningful concurrency/TOCTOU surfaces audited
- [x] every discovered security defect repaired or documented
- [ ] focused tests confirmed passing
- [ ] full pytest confirmed passing
- [ ] Ruff lint confirmed passing
- [ ] Ruff format confirmed passing
- [ ] mypy confirmed passing
- [x] audit document committed
- [x] no unrelated architecture changes
- [x] one clean PR opened from canonical main

## 15. Explicit AX-040 completion decision

**AX040_STATUS: BLOCKED_INFRASTRUCTURE**

Do not mark AX-040 COMPLETE from this PR state. The remaining blocker is executable gate evidence, specifically an available exact-head test environment. If the same head (or a later docs-only head containing identical source/tests) receives passing `pytest`, Ruff lint, Ruff format and mypy results, this infrastructure block can be reevaluated without broadening the repair scope. If any gate exposes a security failure, the task must remain non-complete until that failure is repaired and regression-tested.
