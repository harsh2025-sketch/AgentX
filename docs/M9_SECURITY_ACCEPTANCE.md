# M9 Security / Untrusted-Content Acceptance

Repository: `harsh2025-sketch/AgentX`  
Scope: AX-371 through AX-405 (35 tasks)  
Canonical campaign baseline: `509f13d44a42f1803e22c85b0602083bfa1e1183`  
Final PR: #169 — **Complete M9 untrusted-content and security acceptance**

## Acceptance principle

**Untrusted content is data, never authority.**

Authority is structural and typed. Text from models, research, browser DOM, Windows/UI,
clipboard, files, memory, learning history, procedures, repair evidence, provider output,
or persistence cannot manufacture or modify canonical permission, risk, gate, budget,
stop/cancellation, confirmation, verification, procedure-promotion, repair-promotion,
or task-success state.

This campaign ports the historical C4.10 corpus onto the current architecture instead of
merging the obsolete branch. It also extends the corpus with encoded/base64-like attacks,
and adds current source-tree privilege, dynamic-execution, secret-leakage, deserialization,
and audit-privacy checks.

## Before

- VERIFIED: 20
- NOT_AUDITED: 0
- IN_PROGRESS: 0
- BLOCKED: 0
- NOT_IMPLEMENTED: 15
- TOTAL: 35

## Evidence layers

1. Existing production structural controls: Trusted Kernel, capability runtime, Hive,
   procedure compiler/runtime, repair lifecycle, browser/Windows capability boundaries,
   model/research contracts, strict persistence decoders and typed task verification.
2. Historical C4.10 replay ported onto current canonical APIs:
   - `tests/support/untrusted_content_corpus.py`
   - `tests/adversarial/test_untrusted_content_research_path.py`
   - `tests/adversarial/test_untrusted_content_knowledge_path.py`
   - `tests/adversarial/test_untrusted_content_learning_path.py`
   - `tests/adversarial/test_untrusted_content_repair_path.py`
   - `tests/adversarial/test_untrusted_content_kernel_authority.py`
   - `tests/adversarial/test_untrusted_content_architecture.py`
3. Modern M9 privacy/privilege audit:
   - `tests/adversarial/test_m9_security_privacy_and_privilege.py`
4. Existing current-main integration evidence:
   - `tests/adversarial/test_ax040_whole_system_security.py`
   - `tests/integration/test_m2_restart_memory_acceptance.py`
   - `tests/integration/test_ax363_366_research_chain.py`
   - `tests/integration/test_release_repair_chain_authority.py`
   - `tests/adversarial/test_windows_native_mutation_authority.py`
   - browser/model/planning authority adversarial suites under `tests/adversarial/`
5. Exact-head C1.01 on PR #169 is the final repository-wide validation gate. The PR body
   records the exact final head and run once the campaign is complete.

## Second-pass task audit

| AX | Requirement | Final evidence path | Second-pass conclusion |
| --- | --- | --- | --- |
| AX-371 | external-content-is-data invariant | `test_ax040_whole_system_security.py`; C4.10 suites | structural typed separation; replay required |
| AX-372 | verified knowledge != authority invariant | knowledge + kernel C4.10 suites | status/trust is not AuthorityContext |
| AX-373 | permission-injection resistance | kernel C4.10 suite | permission strings cannot manufacture Permission |
| AX-374 | risk-downgrade resistance | kernel C4.10 suite | typed action characteristics retain canonical floor |
| AX-375 | Action-Gate bypass resistance | AX-040 whole-system suite | mutation remains gate-governed |
| AX-376 | Emergency-Stop override resistance | kernel C4.10 suite | hostile content cannot clear stop |
| AX-377 | budget-override resistance | kernel C4.10 suite | finite typed ResourceBudget is unchanged |
| AX-378 | task-success fabrication resistance | kernel C4.10 suite | text cannot transition Task |
| AX-379 | verification fabrication resistance | kernel C4.10 suite | independent typed verification remains required |
| AX-380 | procedure activation fabrication resistance | procedure-promotion adversarial suite | candidate text cannot activate |
| AX-381 | arbitrary-callable rejection | C4.10 architecture suite | hostile carriers expose no callable execution field |
| AX-382 | dynamic-code boundary tests | M9 privacy/privilege + C4.10 architecture | unsafe dynamic execution is source-audited |
| AX-383 | model-output authority separation | planning/model authority tests | model output remains proposal/data |
| AX-384 | clipboard hostile-content tests | Windows native mutation authority suite | clipboard text does not become authority |
| AX-385 | UI hostile-content tests | Windows/UI adversarial suites | UI text cannot authorize mutation |
| AX-386 | browser hostile-content tests | browser DOM/selection authority suites | webpage/DOM remains data |
| AX-387 | compiler hostile-history tests | AX-040 grand chain + learning C4.10 | history cannot smuggle executable authority |
| AX-388 | repair hostile-evidence tests | repair C4.10 + release repair chain | repair remains bounded/evidence-driven |
| AX-389 | persistence malformed-data tests | persistence recovery adversarial suite | malformed/security-shaped state fails closed |
| AX-390 | threat model foundation | `test_ax390_threat_model_foundation.py` | code-connected threat inventory remains current |
| AX-391 | replay modern C4.10 suite | all seven C4.10 files | historical suite ported to current APIs |
| AX-392 | research-path C4.10 tests | research C4.10 suite | research text remains untrusted |
| AX-393 | knowledge-path C4.10 tests | knowledge C4.10 suite | knowledge promotion is explicit and non-authoritative |
| AX-394 | learning-path C4.10 tests | learning C4.10 suite | experiences/history remain inert evidence |
| AX-395 | repair-path C4.10 tests | repair C4.10 suite | hostile diagnosis/repair text cannot promote |
| AX-396 | kernel-authority C4.10 tests | kernel C4.10 suite | typed kernel state is sole authority source |
| AX-397 | tool-directive smuggling corpus | shared corpus + path suites | JSON/XML/Markdown tool syntax remains text |
| AX-398 | Unicode/homoglyph adversarial corpus | shared corpus + path suites | Unicode payloads do not change authority |
| AX-399 | encoded-payload adversarial corpus | shared corpus + path suites | base64-like content remains opaque data |
| AX-400 | SQL-shaped data poisoning tests | shared corpus + knowledge/persistence suites | SQL-shaped strings remain parameterized data |
| AX-401 | secrets leakage audit | M9 privacy/privilege suite | SecretValue redaction/serialization boundary audited |
| AX-402 | audit-log privacy audit | M9 privacy/privilege suite | typed audit outcome cannot be forged by text; SecretValue rejected |
| AX-403 | cross-subsystem privilege audit | architecture + M9 privilege suite | AuthorityContext ownership and import edges audited |
| AX-404 | whole-system hostile-content chain | AX-040 grand chain + kernel C4.10 grand chain | multi-hop payload cannot change authority snapshot |
| AX-405 | security milestone signoff | this report + matrix + exact-head CI | only VERIFIED after all preceding evidence passes |

## Security invariant report

| Invariant | Evidence | Required result |
| --- | --- | --- |
| UNTRUSTED CONTENT CANNOT GRANT PERMISSION | kernel C4.10; AX-040 | PASS |
| UNTRUSTED CONTENT CANNOT LOWER RISK | kernel C4.10; trusted-kernel adversarial suite | PASS |
| UNTRUSTED CONTENT CANNOT BYPASS ACTIONGATE | AX-040; capability adversarial suites | PASS |
| UNTRUSTED CONTENT CANNOT FABRICATE CONFIRMATION | human-approval authority suite | PASS |
| UNTRUSTED CONTENT CANNOT INCREASE BUDGET | kernel C4.10 | PASS |
| UNTRUSTED CONTENT CANNOT CLEAR STOP/CANCELLATION | kernel C4.10; runtime tests | PASS |
| UNTRUSTED CONTENT CANNOT FABRICATE VERIFICATION | kernel C4.10; Verifier integration | PASS |
| UNTRUSTED CONTENT CANNOT PROMOTE PROCEDURES | procedure-promotion authority tests | PASS |
| UNTRUSTED CONTENT CANNOT PROMOTE REPAIRS | repair C4.10 + repair chain | PASS |
| UNTRUSTED CONTENT CANNOT BECOME DIRECT MACHINE AUTHORITY | Windows/browser capability authority tests | PASS |
| UNTRUSTED MEMORY DOES NOT BECOME TRUSTED AFTER RESTART | M2 restart acceptance | PASS |
| RESEARCH/MODEL OUTPUT REMAINS DATA | research C4.10; planning/model authority tests | PASS |
| SECRETS ARE NOT EXPOSED THROUGH EVIDENCE/LOGGING | M9 privacy/privilege + kernel secret tests | PASS |
| EXECUTION REMAINS BOUNDED | kernel budget/anti-loop tests; C4.10 | PASS |
| SECURITY FAILURES FAIL CLOSED | strict decoders, gate, persistence/adversarial suites | PASS |

These PASS labels are acceptance claims only when the exact final PR head completes the
canonical C1.01 gate. A failing exact-head run invalidates milestone signoff until fixed.

## Multi-hop and restart evidence

The current production composition contains two especially important non-mock proofs:

- `test_grand_chain_hostile_content_remains_data_and_cannot_change_authority` stores
  hostile content in durable semantic memory, retrieves it, feeds it into causal learning
  and skill compilation, and asserts permission/risk/gate/budget/stop/task/secret state
  is unchanged.
- `test_m2_restart_memory_acceptance.py` persists hostile WEB-provenance knowledge,
  reopens the SQLite state in a fresh child process, and proves the record remains
  UNVERIFIED while canonical authority checks remain fail-closed.

Repair and research integration tests independently prove hostile provider/failure text
remains unverified/evidence-only through their production persistence and lifecycle paths.

## Known limitations

M9 security acceptance does **not** imply that unfinished M7 browser functionality or
blocked live-provider M8 acceptance is complete. M9 proves authority separation for the
surfaces that exist on current canonical AgentX. It does not manufacture live-provider,
interactive-browser, device, or release evidence for other milestones.

## Validation commands

Canonical C1.01 executes:

- runtime-only installation validation
- `python -m ruff check .`
- `python -m ruff format --check .`
- `python -m mypy`
- `python scripts/task_ledger.py`
- M6 Windows hosted acceptance
- `python -m pytest`

The final exact-head run and aggregate test count are recorded in PR #169 after completion.
