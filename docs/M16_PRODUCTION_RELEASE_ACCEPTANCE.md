# M16 Production Hardening, Release, and Whole-System Acceptance

M16 is the release-hardening layer over the existing canonical AgentX architecture. It does not
introduce a second AgentLoop, alternate authority model, generic shell/Python execution path, or
automatic M15 promotion.

## Release candidate

The branch builds `agentx 1.0.0rc1` as a Python wheel. C1.01 builds the wheel, records its
SHA-256 digest and byte size, installs it into a clean virtual environment, initializes canonical
configuration/storage, runs `agentx doctor`, and then upgrades a separate environment from the
prior canonical-main `0.0.1` wheel to the release-candidate wheel while preserving durable
state.

The release CLI is intentionally bounded:

- `agentx init` initializes the canonical data directory and SQLite schema, then performs the
  existing conservative persistence recovery assessment.
- `agentx doctor` reopens durable state, applies only canonical database migrations, and reports
  integrity/recovery disposition.
- `agentx migrate-config PATH` performs an explicit, validated, atomic, restart-safe migration
  of legacy configuration to schema version 1.
- No release command grants permissions, clears EmergencyStop, widens budgets, bypasses ActionGate,
  fabricates verification, or executes arbitrary code.

## Configuration, database, and recovery

Configuration migration validates both the source and the exact temporary result before atomic
replacement. Unknown, malformed, negative, or future schema versions fail closed. Repeating a
current-schema migration is idempotent.

SQLite continues to use the canonical migration engine. M16 adds an upgrade acceptance from a
real v8 durable database to the current schema, verifies existing rows survive, and verifies a
second reopen is idempotent. Existing corruption-recovery suites remain the canonical bounded
corruption evidence; M16 additionally exercises an abrupt child-process exit during an open
transaction and proves restart does not fabricate the uncommitted event.

## Privacy and retention

`agentx.privacy` provides explicit transactional deletion/expiry for canonical event, knowledge,
episode, procedure, negative-experience, artifact, audit, and scheduler-history data. Knowledge
relationship rows are removed consistently with deleted records. Reports contain counts, never
deleted payload content. A scoped file deletion boundary supports privacy-sensitive local files
such as M15 provenance metadata while rejecting absolute paths, traversal, and recursive directory
deletion.

Acceptance deletes data from real canonical SQLite stores, opens a new database process boundary,
and proves deleted records do not reappear.

## Benchmark integrity

`scripts/m16_benchmark.py` records every repetition using `perf_counter_ns`, retains failed runs,
uses no hidden warmup exclusion, records platform/Python/version/dataset identity, and emits all raw
samples plus summary statistics. The restart/latency numbers are real local release-startup
measurements.

Model-call/token/cost evidence is explicitly labeled `controlled_fixture` and is sourced from the
canonical cold-vs-warm instrumentation acceptance. It proves metric plumbing and comparative
accounting only. It is **not** a live-provider performance or billing claim.

## Acceptance corpora

`docs/M16_ACCEPTANCE_CORPUS.json` binds six final corpora to concrete canonical tests:

- Windows production actions through ActionGate/Executor/verification;
- real headless-Chrome controlled browser workflows plus adversarial browser actions;
- Hive/memory persistence and recovery;
- cold learning, compilation, promotion, restart, and warm procedure reuse;
- break/detect/repair/restart/reuse and rollback;
- cross-milestone security/adversarial regressions spanning M9, World Model, voice, device/Android,
  optimization, procedure promotion, M15 self-extension, and release hostile-data chains.

C1.01 runs this corpus separately before the full M0-M16 pytest regression.

## Real-environment matrix truth

C1.01 currently runs on **Microsoft Windows Server 2025**. M16 records exact system/release/build,
edition, product type, architecture, Python, monitor count when observable, and system DPI when
observable. The classifier accepts Windows 10/11 evidence only for Windows workstation product
type and the appropriate build family; Windows Server is explicitly rejected as desktop evidence.

Therefore hosted C1.01 does **not** establish:

- Windows 10 workstation release-matrix acceptance;
- Windows 11 workstation release-matrix acceptance;
- physical multi-DPI acceptance;
- physical multi-monitor acceptance.

Deterministic M10 DPI/monitor fixtures and hosted Windows-native tests remain valuable regression
evidence, but they are not relabeled as physical-host evidence.

## Whole-system release decision

Automated M16 acceptance can establish the locally provable release candidate, migration, recovery,
benchmark, corpus, security, privacy, cold/warm, and repair/reuse paths. Final AgentX v1
whole-system release remains blocked while mandatory Windows 10/11 and physical DPI/monitor
release-matrix evidence is absent, and earlier task-level real-provider/device blockers remain
truthfully represented in the canonical 600-task ledger.

The correct state is therefore **ready for final integration / real-environment acceptance**, not
an unconditional production-ready claim.
