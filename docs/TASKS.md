# AgentX AX-001-AX-600 ledger

Generated from `docs/TASKS.json` by `python scripts/task_ledger.py --write`.

reported_status preserves the supplied 349/2/249 snapshot. VERIFIED requires separately reviewed canonical implementation, tests, and acceptance evidence; no reported checkbox implies verified acceptance.

Partial: only the explicit critical path is encoded. Empty depends_on is not a claim of independence. AX-574 remains in progress until the whole graph is reviewed.

Baseline: `72bf7059d64a38ed9512aedf420ec7b1d2552837`. User-supplied reconciliation, 2026-09-17; definitions from review-agentx-handover (4).pdf pp. 632-647.

| Milestone | Reported complete | Reported partial | Reported remaining | Verified acceptance |
| --- | ---: | ---: | ---: | ---: |
| M0 Trusted Kernel | 40 | 0 | 0 | 0 |
| M1 Agent Runtime | 41 | 2 | 2 | 45 |
| M2 Hive and Memory | 39 | 0 | 1 | 40 |
| M3 Procedure Compiler | 49 | 0 | 1 | 50 |
| M4 Learning Efficiency | 20 | 0 | 10 | 0 |
| M5 Self-Repair | 36 | 0 | 4 | 40 |
| M6 Windows Capabilities | 52 | 0 | 3 | 0 |
| M7 Browser Agent | 14 | 0 | 21 | 0 |
| M8 Models and Research | 13 | 0 | 22 | 0 |
| M9 Security | 20 | 0 | 15 | 0 |
| M10 World Model | 16 | 0 | 14 | 0 |
| M11 Voice and HUD | 2 | 0 | 23 | 0 |
| M12 Scheduling | 1 | 0 | 24 | 0 |
| M13 Multi-device and Android | 2 | 0 | 28 | 0 |
| M14 Optimization | 2 | 0 | 23 | 0 |
| M15 Self-extension | 1 | 0 | 29 | 0 |
| M16 Production and Release | 1 | 0 | 29 | 0 |

A zero verified-acceptance count means this ledger has not yet recorded a task-level
acceptance audit; it does not mean the existing implementation is absent.

| Task | Requirement | Reported baseline | Acceptance audit | Known dependencies |
| --- | --- | --- | --- | --- |
| AX-001 | Python 3.12+ project baseline | COMPLETE | NOT_AUDITED |  |
| AX-002 | src/ package layout | COMPLETE | NOT_AUDITED |  |
| AX-003 | pyproject.toml packaging | COMPLETE | NOT_AUDITED |  |
| AX-004 | CLI/version bootstrap | COMPLETE | NOT_AUDITED |  |
| AX-005 | subsystem ownership map | COMPLETE | NOT_AUDITED |  |
| AX-006 | architecture dependency manifest | COMPLETE | NOT_AUDITED |  |
| AX-007 | architecture dependency tests | COMPLETE | NOT_AUDITED |  |
| AX-008 | canonical Task identity | COMPLETE | NOT_AUDITED |  |
| AX-009 | canonical Task lifecycle | COMPLETE | NOT_AUDITED |  |
| AX-010 | ExecutionContext contract | COMPLETE | NOT_AUDITED |  |
| AX-011 | canonical error/result vocabulary | COMPLETE | NOT_AUDITED |  |
| AX-012 | permission vocabulary | COMPLETE | NOT_AUDITED |  |
| AX-013 | Permission Engine | COMPLETE | NOT_AUDITED |  |
| AX-014 | authority-context contract | COMPLETE | NOT_AUDITED |  |
| AX-015 | R0–R4 risk vocabulary | COMPLETE | NOT_AUDITED |  |
| AX-016 | deterministic risk assessment | COMPLETE | NOT_AUDITED |  |
| AX-017 | Action Gate | COMPLETE | NOT_AUDITED |  |
| AX-018 | confirmation-required semantics | COMPLETE | NOT_AUDITED |  |
| AX-019 | destructive-action gating | COMPLETE | NOT_AUDITED |  |
| AX-020 | Emergency Stop | COMPLETE | NOT_AUDITED |  |
| AX-021 | resource-envelope contract | COMPLETE | NOT_AUDITED |  |
| AX-022 | Resource Budget | COMPLETE | NOT_AUDITED |  |
| AX-023 | machine-action accounting | COMPLETE | NOT_AUDITED |  |
| AX-024 | bounded runtime budget enforcement | COMPLETE | NOT_AUDITED |  |
| AX-025 | audit-event contract | COMPLETE | NOT_AUDITED |  |
| AX-026 | durable audit plumbing | COMPLETE | NOT_AUDITED |  |
| AX-027 | secret-value abstraction | COMPLETE | NOT_AUDITED |  |
| AX-028 | secret-boundary rules | COMPLETE | NOT_AUDITED |  |
| AX-029 | capability ABI | COMPLETE | NOT_AUDITED |  |
| AX-030 | Capability Registry | COMPLETE | NOT_AUDITED |  |
| AX-031 | capability request validation | COMPLETE | NOT_AUDITED |  |
| AX-032 | capability result validation | COMPLETE | NOT_AUDITED |  |
| AX-033 | CapabilityExecutionLoop | COMPLETE | NOT_AUDITED |  |
| AX-034 | execution/verification separation | COMPLETE | NOT_AUDITED |  |
| AX-035 | event bus foundation | COMPLETE | NOT_AUDITED |  |
| AX-036 | persistence foundation | COMPLETE | NOT_AUDITED |  |
| AX-037 | SQLite migration framework | COMPLETE | NOT_AUDITED |  |
| AX-038 | runtime-only dependency gate | COMPLETE | NOT_AUDITED |  |
| AX-039 | Windows/Python 3.12 quality workflow | COMPLETE | NOT_AUDITED |  |
| AX-040 | Trusted-Kernel whole-system security audit | COMPLETE | NOT_AUDITED |  |
| AX-041 | ExecutionLevel L0–L5 vocabulary | COMPLETE | VERIFIED |  |
| AX-042 | deterministic level-selection inputs | COMPLETE | VERIFIED |  |
| AX-043 | routing-evidence contract | COMPLETE | VERIFIED |  |
| AX-044 | bounded escalation model | COMPLETE | VERIFIED |  |
| AX-045 | anti-loop policy | COMPLETE | VERIFIED |  |
| AX-046 | AgentLoop composition boundary | COMPLETE | VERIFIED |  |
| AX-047 | strategy registry | COMPLETE | VERIFIED |  |
| AX-048 | canonical runtime strategy assembly | COMPLETE | VERIFIED |  |
| AX-049 | L0 strategy adapter | COMPLETE | VERIFIED |  |
| AX-050 | L1 governed capability strategy | COMPLETE | VERIFIED |  |
| AX-051 | L2 compiled-procedure strategy | COMPLETE | VERIFIED |  |
| AX-052 | L3 guided-procedure strategy | COMPLETE | VERIFIED |  |
| AX-053 | L4 planning boundary | COMPLETE | VERIFIED |  |
| AX-054 | L5 exploratory strategy clean implementation | NOT_IMPLEMENTED | VERIFIED | AX-065, AX-364, AX-365, AX-366 |
| AX-055 | strategy-unavailable behavior | COMPLETE | VERIFIED |  |
| AX-056 | failure escalation behavior | COMPLETE | VERIFIED |  |
| AX-057 | TaskManager/runtime integration | COMPLETE | VERIFIED |  |
| AX-058 | task decomposition schema | COMPLETE | VERIFIED |  |
| AX-059 | decomposition DAG validation | COMPLETE | VERIFIED |  |
| AX-060 | decomposition readiness validator | COMPLETE | VERIFIED |  |
| AX-061 | terminal-node readiness rules | COMPLETE | VERIFIED |  |
| AX-062 | capability-target readiness | COMPLETE | VERIFIED |  |
| AX-063 | procedure-target readiness | COMPLETE | VERIFIED |  |
| AX-064 | higher-level-resolution readiness | COMPLETE | VERIFIED |  |
| AX-065 | Reasoner provider-neutral boundary | COMPLETE | VERIFIED |  |
| AX-066 | bounded Reasoner request | COMPLETE | VERIFIED |  |
| AX-067 | model-output acceptance boundary | COMPLETE | VERIFIED |  |
| AX-068 | model-output strict JSON handling | COMPLETE | VERIFIED |  |
| AX-069 | independent task verification | COMPLETE | VERIFIED |  |
| AX-070 | verification-requirement contract | COMPLETE | VERIFIED |  |
| AX-071 | requirement evaluation | COMPLETE | VERIFIED |  |
| AX-072 | execution success != task success invariant | COMPLETE | VERIFIED |  |
| AX-073 | procedure END != task success invariant | COMPLETE | VERIFIED |  |
| AX-074 | cancellation semantics | COMPLETE | VERIFIED |  |
| AX-075 | timeout semantics | COMPLETE | VERIFIED |  |
| AX-076 | denied-attempt semantics | COMPLETE | VERIFIED |  |
| AX-077 | unverified-attempt semantics | COMPLETE | VERIFIED |  |
| AX-078 | bounded strategy attempts | COMPLETE | VERIFIED |  |
| AX-079 | strategy result typing | COMPLETE | VERIFIED |  |
| AX-080 | hostile routing metadata inertness | COMPLETE | VERIFIED |  |
| AX-081 | task/runtime adversarial tests | COMPLETE | VERIFIED |  |
| AX-082 | task/runtime architecture tests | COMPLETE | VERIFIED |  |
| AX-083 | real-world single-task vertical slice | PARTIAL | VERIFIED | AX-053, AX-060, AX-069, AX-344 |
| AX-084 | cross-strategy orchestration benchmark | PARTIAL | VERIFIED | AX-083 |
| AX-085 | M1 release acceptance proof | NOT_IMPLEMENTED | VERIFIED | AX-054, AX-083, AX-084 |
| AX-086 | Episode identity | COMPLETE | VERIFIED |  |
| AX-087 | Episode record | COMPLETE | VERIFIED |  |
| AX-088 | Episode outcome vocabulary | COMPLETE | VERIFIED |  |
| AX-089 | durable EpisodeStore | COMPLETE | VERIFIED |  |
| AX-090 | monotonic durable episode sequence | COMPLETE | VERIFIED |  |
| AX-091 | execution-episode packaging | COMPLETE | VERIFIED |  |
| AX-092 | execution evidence capture | COMPLETE | VERIFIED |  |
| AX-093 | verification evidence capture | COMPLETE | VERIFIED |  |
| AX-094 | causal-experience contract | COMPLETE | VERIFIED |  |
| AX-095 | causal-outcome vocabulary | COMPLETE | VERIFIED |  |
| AX-096 | verified causal experience | COMPLETE | VERIFIED |  |
| AX-097 | failed causal experience | COMPLETE | VERIFIED |  |
| AX-098 | ExperienceMemory | COMPLETE | VERIFIED |  |
| AX-099 | restart-safe episode retrieval | COMPLETE | VERIFIED |  |
| AX-100 | bounded episode retrieval | COMPLETE | VERIFIED |  |
| AX-101 | task-based episode filtering | COMPLETE | VERIFIED |  |
| AX-102 | correlation-based episode filtering | COMPLETE | VERIFIED |  |
| AX-103 | outcome-based episode filtering | COMPLETE | VERIFIED |  |
| AX-104 | sequence-window retrieval | COMPLETE | VERIFIED |  |
| AX-105 | semantic knowledge record | COMPLETE | VERIFIED |  |
| AX-106 | provenance contract | COMPLETE | VERIFIED |  |
| AX-107 | evidence contract | COMPLETE | VERIFIED |  |
| AX-108 | KnowledgeStore | COMPLETE | VERIFIED |  |
| AX-109 | SemanticMemory | COMPLETE | VERIFIED |  |
| AX-110 | explicit knowledge lifecycle | COMPLETE | VERIFIED |  |
| AX-111 | unverified-by-default ingestion | COMPLETE | VERIFIED |  |
| AX-112 | explicit verification promotion | COMPLETE | VERIFIED |  |
| AX-113 | knowledge retrieval | COMPLETE | VERIFIED |  |
| AX-114 | environmental state contract | COMPLETE | VERIFIED |  |
| AX-115 | environmental TTL cache | COMPLETE | VERIFIED |  |
| AX-116 | environment-change detection | COMPLETE | VERIFIED |  |
| AX-117 | world-state snapshot | COMPLETE | VERIFIED |  |
| AX-118 | Hive relationship graph | COMPLETE | VERIFIED |  |
| AX-119 | explicit user preferences | COMPLETE | VERIFIED |  |
| AX-120 | contradiction relationships | COMPLETE | VERIFIED |  |
| AX-121 | supersession relationships | COMPLETE | VERIFIED |  |
| AX-122 | evidence-confidence metadata | COMPLETE | VERIFIED |  |
| AX-123 | knowledge revalidation | COMPLETE | VERIFIED |  |
| AX-124 | cross-scope retrieval protection | COMPLETE | VERIFIED |  |
| AX-125 | full restart-memory acceptance scenario | NOT_IMPLEMENTED | VERIFIED | AX-120, AX-121, AX-122, AX-123, AX-124 |
| AX-126 | ProcedureId contract | COMPLETE | VERIFIED |  |
| AX-127 | Procedure revision identity | COMPLETE | VERIFIED |  |
| AX-128 | ProcedureStatus lifecycle vocabulary | COMPLETE | VERIFIED |  |
| AX-129 | Procedure Graph IR | COMPLETE | VERIFIED |  |
| AX-130 | ACTION node | COMPLETE | VERIFIED |  |
| AX-131 | CONDITION node | COMPLETE | VERIFIED |  |
| AX-132 | END node | COMPLETE | VERIFIED |  |
| AX-133 | REASON node | COMPLETE | VERIFIED |  |
| AX-134 | RESEARCH node representation | COMPLETE | VERIFIED |  |
| AX-135 | subprocedure representation | COMPLETE | VERIFIED |  |
| AX-136 | recovery/error edges | COMPLETE | VERIFIED |  |
| AX-137 | graph structural validation | COMPLETE | VERIFIED |  |
| AX-138 | deterministic graph serialization | COMPLETE | VERIFIED |  |
| AX-139 | precondition representation | COMPLETE | VERIFIED |  |
| AX-140 | postcondition representation | COMPLETE | VERIFIED |  |
| AX-141 | deterministic interpreter | COMPLETE | VERIFIED |  |
| AX-142 | procedure execution trace | COMPLETE | VERIFIED |  |
| AX-143 | procedure execution evidence | COMPLETE | VERIFIED |  |
| AX-144 | ACTION_REQUIRED transition | COMPLETE | VERIFIED |  |
| AX-145 | REASON_REQUIRED transition | COMPLETE | VERIFIED |  |
| AX-146 | RESEARCH_REQUIRED transition | COMPLETE | VERIFIED |  |
| AX-147 | procedure run disposition | COMPLETE | VERIFIED |  |
| AX-148 | procedure task-verification separation | COMPLETE | VERIFIED |  |
| AX-149 | trajectory normalization | COMPLETE | VERIFIED |  |
| AX-150 | causal-action extraction | COMPLETE | VERIFIED |  |
| AX-151 | irrelevant-action elimination | COMPLETE | VERIFIED |  |
| AX-152 | parameter extraction | COMPLETE | VERIFIED |  |
| AX-153 | parameter generalization | COMPLETE | VERIFIED |  |
| AX-154 | determinism analysis | COMPLETE | VERIFIED |  |
| AX-155 | reasoning-region classification | COMPLETE | VERIFIED |  |
| AX-156 | skill-compiler orchestrator | COMPLETE | VERIFIED |  |
| AX-157 | verified-input compiler admission | COMPLETE | VERIFIED |  |
| AX-158 | candidate-only synthesis | COMPLETE | VERIFIED |  |
| AX-159 | Procedure candidate builder | COMPLETE | VERIFIED |  |
| AX-160 | validation evidence policy | COMPLETE | VERIFIED |  |
| AX-161 | varied-parameter validation runner | COMPLETE | VERIFIED |  |
| AX-162 | exact procedure-revision validation binding | COMPLETE | VERIFIED |  |
| AX-163 | distinct-variation minimum | COMPLETE | VERIFIED |  |
| AX-164 | partial validation failure preservation | COMPLETE | VERIFIED |  |
| AX-165 | promotion eligibility decision | COMPLETE | VERIFIED |  |
| AX-166 | explicit promotion transaction | COMPLETE | VERIFIED |  |
| AX-167 | atomic candidate activation | COMPLETE | VERIFIED |  |
| AX-168 | ACTIVE-only normal reuse | COMPLETE | VERIFIED |  |
| AX-169 | capability/procedure applicability matcher | COMPLETE | VERIFIED |  |
| AX-170 | deterministic reuse selector | COMPLETE | VERIFIED |  |
| AX-171 | ambiguous reuse rejection | COMPLETE | VERIFIED |  |
| AX-172 | restart-safe active-procedure retrieval | COMPLETE | VERIFIED |  |
| AX-173 | complete compiled L2 real workflow | COMPLETE | VERIFIED |  |
| AX-174 | complete guided L3 real workflow | COMPLETE | VERIFIED |  |
| AX-175 | end-to-end skill compilation acceptance proof | NOT_IMPLEMENTED | VERIFIED | AX-172, AX-173, AX-174 |
| AX-176 | execution metrics record | COMPLETE | NOT_AUDITED |  |
| AX-177 | elapsed-time instrumentation | COMPLETE | NOT_AUDITED |  |
| AX-178 | machine-action instrumentation | COMPLETE | NOT_AUDITED |  |
| AX-179 | model-call event instrumentation | COMPLETE | NOT_AUDITED |  |
| AX-180 | input-token accounting | COMPLETE | NOT_AUDITED |  |
| AX-181 | output-token accounting | COMPLETE | NOT_AUDITED |  |
| AX-182 | cost accounting | COMPLETE | NOT_AUDITED |  |
| AX-183 | model-ID evidence | COMPLETE | NOT_AUDITED |  |
| AX-184 | procedure-revision evidence | COMPLETE | NOT_AUDITED |  |
| AX-185 | verified-success evidence type | COMPLETE | NOT_AUDITED |  |
| AX-186 | reuse-mode vocabulary | COMPLETE | NOT_AUDITED |  |
| AX-187 | cold-run evidence | COMPLETE | NOT_AUDITED |  |
| AX-188 | warm-run evidence | COMPLETE | NOT_AUDITED |  |
| AX-189 | efficiency comparison | COMPLETE | NOT_AUDITED |  |
| AX-190 | missing-metric preservation | COMPLETE | NOT_AUDITED |  |
| AX-191 | unverified warm-run rejection | COMPLETE | NOT_AUDITED |  |
| AX-192 | failed warm-run regression rule | COMPLETE | NOT_AUDITED |  |
| AX-193 | cold-vs-warm experiment harness | COMPLETE | NOT_AUDITED |  |
| AX-194 | deterministic experiment serialization | COMPLETE | NOT_AUDITED |  |
| AX-195 | evidence non-authority invariants | COMPLETE | NOT_AUDITED |  |
| AX-196 | connect metrics to real model provider | NOT_IMPLEMENTED | IN_PROGRESS | AX-344 |
| AX-197 | execute unknown cold task | NOT_IMPLEMENTED | NOT_IMPLEMENTED | AX-083, AX-196 |
| AX-198 | collect real L4/L5 metrics | NOT_IMPLEMENTED | NOT_IMPLEMENTED | AX-197 |
| AX-199 | compile cold experience | NOT_IMPLEMENTED | NOT_IMPLEMENTED | AX-175, AX-197 |
| AX-200 | validate candidate with variations | NOT_IMPLEMENTED | NOT_IMPLEMENTED | AX-199 |
| AX-201 | promote learned procedure | NOT_IMPLEMENTED | NOT_IMPLEMENTED | AX-200 |
| AX-202 | restart AgentX | NOT_IMPLEMENTED | NOT_IMPLEMENTED | AX-125, AX-201 |
| AX-203 | solve related warm task via reuse | NOT_IMPLEMENTED | NOT_IMPLEMENTED | AX-202 |
| AX-204 | demonstrate material model-call/cost reduction | NOT_IMPLEMENTED | NOT_IMPLEMENTED | AX-198, AX-203 |
| AX-205 | publish reproducible cold-vs-warm benchmark | NOT_IMPLEMENTED | NOT_IMPLEMENTED | AX-204 |
| AX-206 | failure taxonomy | COMPLETE | VERIFIED |  |
| AX-207 | transient-failure classification | COMPLETE | VERIFIED |  |
| AX-208 | environment-unavailable classification | COMPLETE | VERIFIED |  |
| AX-209 | precondition-failure classification | COMPLETE | VERIFIED |  |
| AX-210 | capability-change classification | COMPLETE | VERIFIED |  |
| AX-211 | UI/API-change classification | COMPLETE | VERIFIED |  |
| AX-212 | verification-failure classification | COMPLETE | VERIFIED |  |
| AX-213 | failure localization | COMPLETE | VERIFIED |  |
| AX-214 | failure diagnosis | COMPLETE | VERIFIED |  |
| AX-215 | repair-candidate contract | COMPLETE | VERIFIED |  |
| AX-216 | procedure degradation detector | COMPLETE | VERIFIED |  |
| AX-217 | repair-budget policy | COMPLETE | VERIFIED |  |
| AX-218 | repair anti-loop | COMPLETE | VERIFIED |  |
| AX-219 | repair workflow orchestrator | COMPLETE | VERIFIED |  |
| AX-220 | repair proposal generation boundary | COMPLETE | VERIFIED |  |
| AX-221 | node-definition replacement patch | COMPLETE | VERIFIED |  |
| AX-222 | repair patch materializer | COMPLETE | VERIFIED |  |
| AX-223 | immutable source preservation | COMPLETE | VERIFIED |  |
| AX-224 | repaired candidate generation | COMPLETE | VERIFIED |  |
| AX-225 | repair validation evidence | COMPLETE | VERIFIED |  |
| AX-226 | shadow-repair evidence | COMPLETE | VERIFIED |  |
| AX-227 | shadow procedure runner | COMPLETE | VERIFIED |  |
| AX-228 | source/candidate exact binding | COMPLETE | VERIFIED |  |
| AX-229 | no-live-mutation shadow invariant | COMPLETE | VERIFIED |  |
| AX-230 | procedure replacement eligibility | COMPLETE | VERIFIED |  |
| AX-231 | replacement transaction | COMPLETE | VERIFIED |  |
| AX-232 | serialized activation transaction | COMPLETE | VERIFIED |  |
| AX-233 | stale-revision rejection | COMPLETE | VERIFIED |  |
| AX-234 | exactly-one-ACTIVE invariant | COMPLETE | VERIFIED |  |
| AX-235 | RETIRED terminal invariant | COMPLETE | VERIFIED |  |
| AX-236 | rollback eligibility | COMPLETE | VERIFIED |  |
| AX-237 | rollback transaction | COMPLETE | VERIFIED |  |
| AX-238 | historical RETIRED-copy semantics | COMPLETE | VERIFIED |  |
| AX-239 | rollback creates new revision | COMPLETE | VERIFIED |  |
| AX-240 | replacement/rollback concurrency tests | COMPLETE | VERIFIED |  |
| AX-241 | transaction fault-injection tests | COMPLETE | VERIFIED |  |
| AX-242 | deliberately break real learned procedure | NOT_IMPLEMENTED | VERIFIED |  |
| AX-243 | detect and localize real degradation | NOT_IMPLEMENTED | VERIFIED |  |
| AX-244 | repair and revalidate real procedure | NOT_IMPLEMENTED | VERIFIED |  |
| AX-245 | complete break -> repair -> rollback acceptance proof | NOT_IMPLEMENTED | VERIFIED |  |
| AX-246 | Windows platform-support contract | COMPLETE | NOT_AUDITED |  |
| AX-247 | Windows process discovery | COMPLETE | NOT_AUDITED |  |
| AX-248 | process identity | COMPLETE | NOT_AUDITED |  |
| AX-249 | top-level window discovery | COMPLETE | NOT_AUDITED |  |
| AX-250 | window identity | COMPLETE | NOT_AUDITED |  |
| AX-251 | UIA observation contract | COMPLETE | NOT_AUDITED |  |
| AX-252 | UIA tree inspection | COMPLETE | NOT_AUDITED |  |
| AX-253 | UIA element identity | COMPLETE | NOT_AUDITED |  |
| AX-254 | UIA property observation | COMPLETE | NOT_AUDITED |  |
| AX-255 | UIA semantic target resolution | COMPLETE | NOT_AUDITED |  |
| AX-256 | ambiguity-preserving target resolution | COMPLETE | NOT_AUDITED |  |
| AX-257 | filesystem text-read capability | COMPLETE | NOT_AUDITED |  |
| AX-258 | filesystem text-write capability | COMPLETE | NOT_AUDITED |  |
| AX-259 | filesystem verification | COMPLETE | NOT_AUDITED |  |
| AX-260 | filesystem structural risk policy | COMPLETE | NOT_AUDITED |  |
| AX-261 | governed file move | COMPLETE | NOT_AUDITED |  |
| AX-262 | governed file rename | COMPLETE | NOT_AUDITED |  |
| AX-263 | governed directory creation | COMPLETE | NOT_AUDITED |  |
| AX-264 | governed file deletion | COMPLETE | NOT_AUDITED |  |
| AX-265 | governed directory deletion | COMPLETE | NOT_AUDITED |  |
| AX-266 | canonical native-mutation seam | COMPLETE | NOT_AUDITED |  |
| AX-267 | native seam isolation from public capability | COMPLETE | NOT_AUDITED |  |
| AX-268 | application-launch V2 contract | COMPLETE | NOT_AUDITED |  |
| AX-269 | structured executable path | COMPLETE | NOT_AUDITED |  |
| AX-270 | structured argv handling | COMPLETE | NOT_AUDITED |  |
| AX-271 | no-shell launch invariant | COMPLETE | NOT_AUDITED |  |
| AX-272 | window activation contract | COMPLETE | NOT_AUDITED |  |
| AX-273 | window minimization contract | COMPLETE | NOT_AUDITED |  |
| AX-274 | window maximization contract | COMPLETE | NOT_AUDITED |  |
| AX-275 | window restore contract | COMPLETE | NOT_AUDITED |  |
| AX-276 | bounded move/resize contract | COMPLETE | NOT_AUDITED |  |
| AX-277 | SetWindowPos native adapter | COMPLETE | NOT_AUDITED |  |
| AX-278 | keyboard text-entry contract | COMPLETE | NOT_AUDITED |  |
| AX-279 | key-combination contract | COMPLETE | NOT_AUDITED |  |
| AX-280 | bounded key vocabulary | COMPLETE | NOT_AUDITED |  |
| AX-281 | clipboard read | COMPLETE | NOT_AUDITED |  |
| AX-282 | clipboard write | COMPLETE | NOT_AUDITED |  |
| AX-283 | clipboard clear | COMPLETE | NOT_AUDITED |  |
| AX-284 | hostile clipboard data inertness | COMPLETE | NOT_AUDITED |  |
| AX-285 | no keylogger invariant | COMPLETE | NOT_AUDITED |  |
| AX-286 | no global-hotkey capture invariant | COMPLETE | NOT_AUDITED |  |
| AX-287 | Windows transition-verification boundary | COMPLETE | NOT_AUDITED |  |
| AX-288 | window-present verification | COMPLETE | NOT_AUDITED |  |
| AX-289 | window-absent verification | COMPLETE | NOT_AUDITED |  |
| AX-290 | window-visible verification | COMPLETE | NOT_AUDITED |  |
| AX-291 | window-focus verification | COMPLETE | NOT_AUDITED |  |
| AX-292 | window-bounds verification | COMPLETE | NOT_AUDITED |  |
| AX-293 | process-present verification | COMPLETE | NOT_AUDITED |  |
| AX-294 | executable-identity verification | COMPLETE | NOT_AUDITED |  |
| AX-295 | text-field-value verification | COMPLETE | NOT_AUDITED |  |
| AX-296 | clipboard independent verification | COMPLETE | NOT_AUDITED |  |
| AX-297 | keyboard-action independent verification | COMPLETE | NOT_AUDITED |  |
| AX-298 | real Windows host mutation suite | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-299 | multi-app Windows workflow benchmark | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-300 | Windows capability milestone acceptance | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-301 | browser provider abstraction | COMPLETE | NOT_AUDITED |  |
| AX-302 | page/tab identity | COMPLETE | NOT_AUDITED |  |
| AX-303 | browser-state observation | COMPLETE | NOT_AUDITED |  |
| AX-304 | DOM representation | COMPLETE | NOT_AUDITED |  |
| AX-305 | DOM element identity | COMPLETE | NOT_AUDITED |  |
| AX-306 | deterministic DOM selection | COMPLETE | NOT_AUDITED |  |
| AX-307 | governed browser action boundary | COMPLETE | NOT_AUDITED |  |
| AX-308 | governed navigation | COMPLETE | NOT_AUDITED |  |
| AX-309 | governed click | COMPLETE | NOT_AUDITED |  |
| AX-310 | navigation risk classification | COMPLETE | NOT_AUDITED |  |
| AX-311 | redirect evidence | COMPLETE | NOT_AUDITED |  |
| AX-312 | independent navigation verification | COMPLETE | NOT_AUDITED |  |
| AX-313 | cross-origin redirect fail-closed policy | COMPLETE | NOT_AUDITED |  |
| AX-314 | hostile webpage content inertness | COMPLETE | NOT_AUDITED |  |
| AX-315 | governed text/form entry — N2.27 | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-316 | text-field target binding | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-317 | form-field value validation | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-318 | governed checkbox/radio interaction | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-319 | governed select/dropdown interaction | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-320 | governed submit action | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-321 | form-state verification | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-322 | submission-result verification | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-323 | multi-field form transaction | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-324 | upload contract | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-325 | upload verification | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-326 | download contract | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-327 | download verification | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-328 | cookie operation contract | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-329 | authentication-session boundary | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-330 | popup/new-tab handling | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-331 | browser recovery after DOM change | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-332 | browser capability health | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-333 | multi-page workflow execution | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-334 | hostile-page adversarial benchmark | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-335 | browser milestone acceptance benchmark | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-336 | provider-neutral ModelId | COMPLETE | NOT_AUDITED |  |
| AX-337 | provider-neutral model request | COMPLETE | NOT_AUDITED |  |
| AX-338 | model-response contract | COMPLETE | NOT_AUDITED |  |
| AX-339 | model-usage contract | COMPLETE | NOT_AUDITED |  |
| AX-340 | model-role vocabulary | COMPLETE | NOT_AUDITED |  |
| AX-341 | Reasoner abstraction | COMPLETE | NOT_AUDITED |  |
| AX-342 | bounded output-token control | COMPLETE | NOT_AUDITED |  |
| AX-343 | model output treated as data | COMPLETE | NOT_AUDITED |  |
| AX-344 | concrete provider adapter — N2.28 | COMPLETE | NOT_AUDITED |  |
| AX-345 | provider configuration | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-346 | canonical credential retrieval | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-347 | secret-to-provider binding | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-348 | HTTP transport implementation | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-349 | provider request serialization | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-350 | provider response parsing | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-351 | usage/token extraction | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-352 | provider-error translation | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-353 | rate-limit handling | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-354 | bounded retry policy | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-355 | timeout handling | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-356 | provider cancellation | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-357 | fallback-provider policy | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-358 | provider-health tracking | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-359 | model-capability registry | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-360 | clean L5 exploratory strategy — N2.07 | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-361 | exploratory task contract | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-362 | research objective generation | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-363 | knowledge-gap detector | COMPLETE | NOT_AUDITED |  |
| AX-364 | Hive-first research lookup | COMPLETE | NOT_AUDITED |  |
| AX-365 | research provider boundary | COMPLETE | NOT_AUDITED |  |
| AX-366 | unverified research ingestion | COMPLETE | NOT_AUDITED |  |
| AX-367 | bounded research loop | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-368 | research -> execution handoff | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-369 | model-backed L4/L5 vertical | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-370 | real-model milestone acceptance | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-371 | external-content-is-data invariant | COMPLETE | NOT_AUDITED |  |
| AX-372 | verified knowledge != authority invariant | COMPLETE | NOT_AUDITED |  |
| AX-373 | permission-injection resistance | COMPLETE | NOT_AUDITED |  |
| AX-374 | risk-downgrade resistance | COMPLETE | NOT_AUDITED |  |
| AX-375 | Action-Gate bypass resistance | COMPLETE | NOT_AUDITED |  |
| AX-376 | Emergency-Stop override resistance | COMPLETE | NOT_AUDITED |  |
| AX-377 | budget-override resistance | COMPLETE | NOT_AUDITED |  |
| AX-378 | task-success fabrication resistance | COMPLETE | NOT_AUDITED |  |
| AX-379 | verification fabrication resistance | COMPLETE | NOT_AUDITED |  |
| AX-380 | procedure activation fabrication resistance | COMPLETE | NOT_AUDITED |  |
| AX-381 | arbitrary-callable rejection | COMPLETE | NOT_AUDITED |  |
| AX-382 | dynamic-code boundary tests | COMPLETE | NOT_AUDITED |  |
| AX-383 | model-output authority separation | COMPLETE | NOT_AUDITED |  |
| AX-384 | clipboard hostile-content tests | COMPLETE | NOT_AUDITED |  |
| AX-385 | UI hostile-content tests | COMPLETE | NOT_AUDITED |  |
| AX-386 | browser hostile-content tests | COMPLETE | NOT_AUDITED |  |
| AX-387 | compiler hostile-history tests | COMPLETE | NOT_AUDITED |  |
| AX-388 | repair hostile-evidence tests | COMPLETE | NOT_AUDITED |  |
| AX-389 | persistence malformed-data tests | COMPLETE | NOT_AUDITED |  |
| AX-390 | threat model foundation | COMPLETE | NOT_AUDITED |  |
| AX-391 | replay modern C4.10 suite | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-392 | research-path C4.10 tests | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-393 | knowledge-path C4.10 tests | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-394 | learning-path C4.10 tests | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-395 | repair-path C4.10 tests | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-396 | kernel-authority C4.10 tests | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-397 | tool-directive smuggling corpus | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-398 | Unicode/homoglyph adversarial corpus | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-399 | encoded-payload adversarial corpus | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-400 | SQL-shaped data poisoning tests | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-401 | secrets leakage audit | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-402 | audit-log privacy audit | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-403 | cross-subsystem privilege audit | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-404 | whole-system hostile-content chain | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-405 | security milestone signoff | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-406 | structured Windows observation foundation | COMPLETE | NOT_AUDITED |  |
| AX-407 | UIA snapshot foundation | COMPLETE | NOT_AUDITED |  |
| AX-408 | DOM snapshot foundation | COMPLETE | NOT_AUDITED |  |
| AX-409 | screen/perception representation | COMPLETE | NOT_AUDITED |  |
| AX-410 | world-state snapshot contract | COMPLETE | NOT_AUDITED |  |
| AX-411 | lazy world-state cache | COMPLETE | NOT_AUDITED |  |
| AX-412 | environment freshness semantics | COMPLETE | NOT_AUDITED |  |
| AX-413 | environment invalidation evidence | COMPLETE | NOT_AUDITED |  |
| AX-414 | application registry | COMPLETE | NOT_AUDITED |  |
| AX-415 | active-window state | COMPLETE | NOT_AUDITED |  |
| AX-416 | process-state model | COMPLETE | NOT_AUDITED |  |
| AX-417 | browser-state model | COMPLETE | NOT_AUDITED |  |
| AX-418 | filesystem-context state | COMPLETE | NOT_AUDITED |  |
| AX-419 | device-state model | COMPLETE | NOT_AUDITED |  |
| AX-420 | task-state world binding | COMPLETE | NOT_AUDITED |  |
| AX-421 | Hive/world relationship linkage | COMPLETE | NOT_AUDITED |  |
| AX-422 | screen-capture provider | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-423 | screen-frame identity | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-424 | visual-region representation | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-425 | OCR-free primary visual grounding | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-426 | visual fallback router | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-427 | visual target proposal | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-428 | structured-vs-visual evidence ranking | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-429 | visual ambiguity handling | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-430 | stale-screen rejection | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-431 | display/DPI normalization | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-432 | multi-monitor awareness | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-433 | layout-change recovery benchmark | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-434 | perception accuracy benchmark | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-435 | world-model milestone acceptance | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-436 | audio abstraction foundation | COMPLETE | NOT_AUDITED |  |
| AX-437 | microphone input provider | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-438 | speaker output provider | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-439 | provider-neutral STT interface | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-440 | concrete STT provider | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-441 | provider-neutral TTS interface | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-442 | concrete TTS provider | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-443 | realtime-session abstraction | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-444 | realtime voice provider | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-445 | voice activity detection | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-446 | turn-end detection | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-447 | interruption/barge-in | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-448 | audio cancellation handling | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-449 | voice-task bridge | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-450 | voice authorization rules | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-451 | spoken confirmation protocol | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-452 | runtime -> UI event protocol | COMPLETE | NOT_AUDITED |  |
| AX-453 | HUD state model | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-454 | listening telemetry | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-455 | reasoning telemetry | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-456 | execution telemetry | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-457 | verification telemetry | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-458 | recovery telemetry | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-459 | HUD control actions | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-460 | voice/HUD milestone demonstration | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-461 | scheduler core | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-462 | scheduled-task contract | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-463 | one-shot scheduled tasks | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-464 | recurring scheduled tasks | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-465 | event watcher framework | COMPLETE | NOT_AUDITED |  |
| AX-466 | event-triggered task launch | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-467 | file-change trigger | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-468 | process-state trigger | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-469 | browser-state trigger | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-470 | device-state trigger | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-471 | notification boundary | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-472 | proactive recommendation contract | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-473 | proactive confidence policy | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-474 | user opt-in policy | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-475 | quiet-hours policy | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-476 | attention-awareness policy | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-477 | background resource quota | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-478 | background model-call quota | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-479 | background machine-action quota | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-480 | long-running Task persistence | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-481 | restart recovery for scheduled work | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-482 | approval expiration semantics | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-483 | stale proactive-action rejection | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-484 | proactive adversarial tests | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-485 | proactivity milestone acceptance | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-486 | device abstraction foundation | COMPLETE | NOT_AUDITED |  |
| AX-487 | device protocol foundation | COMPLETE | NOT_AUDITED |  |
| AX-488 | Device Registry | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-489 | device discovery | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-490 | device capability advertisement | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-491 | device health model | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-492 | device environment identity | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-493 | Android provider | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-494 | ADB transport | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-495 | Android package discovery | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-496 | Android app launch | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-497 | accessibility-tree observation | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-498 | Android semantic target resolution | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-499 | Android tap action | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-500 | Android text entry | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-501 | Android swipe action | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-502 | Android back/home actions | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-503 | Android state verification | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-504 | Android screen fallback | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-505 | Android risk policy | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-506 | Android permission mapping | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-507 | cross-device Task DAG | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-508 | device-target routing | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-509 | PC -> phone handoff | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-510 | phone -> PC handoff | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-511 | browser -> phone handoff | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-512 | device-specific Procedure applicability | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-513 | cross-device causal episode | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-514 | PC/browser/phone workflow benchmark | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-515 | multi-device milestone acceptance | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-516 | strategy-performance evidence foundation | COMPLETE | NOT_AUDITED |  |
| AX-517 | execution-level metrics foundation | COMPLETE | NOT_AUDITED |  |
| AX-518 | deterministic strategy baseline | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-519 | strategy outcome history | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-520 | per-environment strategy statistics | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-521 | per-task-family strategy statistics | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-522 | strategy latency statistics | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-523 | strategy cost statistics | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-524 | strategy success statistics | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-525 | confidence calibration | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-526 | contextual-bandit experiment | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-527 | bandit safety constraints | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-528 | offline policy evaluation | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-529 | online preference-ranking experiment | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-530 | explicit user-correction dataset | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-531 | preference scoring model | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-532 | grounding-specialist dataset | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-533 | lightweight grounding model | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-534 | routing-specialist dataset | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-535 | lightweight routing model | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-536 | verifier-assistance model experiment | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-537 | model-distillation pipeline | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-538 | specialized-model benchmark | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-539 | rollbackable optimization policy | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-540 | optimization milestone acceptance | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-541 | missing-capability detector foundation | COMPLETE | NOT_AUDITED |  |
| AX-542 | capability-gap record | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-543 | capability research objective | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-544 | capability design proposal | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-545 | proposal provenance tracking | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-546 | generated-tool specification | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-547 | generated-code isolation environment | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-548 | generated dependency policy | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-549 | generated-code static analysis | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-550 | generated-code type checking | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-551 | generated-code lint gate | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-552 | generated unit tests | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-553 | generated adversarial tests | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-554 | sandbox execution | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-555 | resource-limited tool test | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-556 | network-isolated test mode | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-557 | filesystem-isolated test mode | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-558 | generated capability ABI validation | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-559 | generated risk declaration validation | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-560 | generated permission declaration validation | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-561 | generated verification contract validation | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-562 | repeated-success promotion threshold | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-563 | human review package | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-564 | installation approval | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-565 | versioned capability registration | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-566 | generated-capability health tracking | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-567 | generated-capability degradation | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-568 | generated-capability rollback | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-569 | Trusted-Kernel immutability proof | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-570 | self-extension milestone acceptance | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-571 | synchronize README with live architecture | COMPLETE | NOT_AUDITED |  |
| AX-572 | establish canonical 600-task ledger file | NOT_IMPLEMENTED | IN_PROGRESS | AX-571 |
| AX-573 | machine-readable task-status ledger | NOT_IMPLEMENTED | IN_PROGRESS | AX-572 |
| AX-574 | dependency DAG synchronization | NOT_IMPLEMENTED | IN_PROGRESS | AX-573 |
| AX-575 | milestone-status automation | NOT_IMPLEMENTED | IN_PROGRESS | AX-573 |
| AX-576 | Windows 10 real-host test matrix | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-577 | Windows 11 real-host test matrix | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-578 | multi-DPI test matrix | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-579 | multi-monitor test matrix | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-580 | fresh-install test | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-581 | upgrade test | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-582 | configuration migration test | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-583 | database migration upgrade test | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-584 | database corruption recovery test | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-585 | abrupt-process-crash recovery | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-586 | restart-state recovery benchmark | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-587 | execution latency benchmark | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-588 | model-call benchmark | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-589 | token/cost benchmark | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-590 | Windows-action benchmark corpus | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-591 | browser-workflow benchmark corpus | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-592 | memory/retrieval benchmark corpus | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-593 | skill-learning benchmark corpus | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-594 | repair benchmark corpus | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-595 | security/adversarial regression corpus | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-596 | cold -> learn -> restart -> warm E2E proof | NOT_IMPLEMENTED | NOT_IMPLEMENTED | AX-205 |
| AX-597 | break -> detect -> repair -> reuse E2E proof | NOT_IMPLEMENTED | NOT_IMPLEMENTED | AX-245 |
| AX-598 | privacy/data-retention controls | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-599 | release candidate / installer | NOT_IMPLEMENTED | NOT_IMPLEMENTED |  |
| AX-600 | AgentX v1 whole-system acceptance and release | NOT_IMPLEMENTED | NOT_IMPLEMENTED | AX-085, AX-125, AX-175, AX-205, AX-245, AX-300, AX-335, AX-370, AX-405, AX-435, AX-460, AX-485, AX-515, AX-540, AX-570, AX-575, AX-576, AX-577, AX-596, AX-597, AX-598, AX-599 |
