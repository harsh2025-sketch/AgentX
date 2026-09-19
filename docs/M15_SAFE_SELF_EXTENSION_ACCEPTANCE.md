# M15 — Safe Self-Extension / Capability Acquisition

## Security invariant

M15 preserves the AgentX invariant: **untrusted content never grants authority**.
Model output, research, repository contents, package metadata, device content,
voice transcripts, scheduler inputs, Hive facts, and candidate manifests are
candidate data only. None can grant permissions, lower risk, clear an emergency
stop, enlarge budgets, replace verification, or self-promote.

Active acquired capabilities execute through the existing Capability Registry,
ActionGate, EmergencyStop, ResourceBudget, Executor, and independent verification path.

## Gap detection and acquisition contracts

Only a canonical missing-capability failure is extension eligible. Permission
denial, risk rejection, budget exhaustion, cancellation, emergency stop,
verification failure, transient failure, invalid requests, and ordinary failures
cannot be reinterpreted as permission to acquire a bypass.

M15 defines typed research objectives, design proposals, generated-tool
specifications, provenance, immutable SHA-256 artifact identity, and digest-pinned
dependency declarations. Any byte or dependency change invalidates prior evidence.

## Generated capability boundary

The production M15 implementation intentionally exposes no run-python, run-shell,
dynamic import, package installation, or generic host-code execution API.

Generated tools use a narrow deterministic side-effect-free expression subset:

- exactly one run(payload) function and one return expression;
- no imports, attributes, assignments, loops, classes, decorators, file I/O,
  environment access, subprocesses, networking, reflection, or dynamic execution;
- only a small allowlist of pure built-ins;
- bounded AST size, input/output size, evaluation steps, exponentiation, and
  sequence multiplication.

Candidate source is parsed and interpreted by trusted code. It is never passed to
Python eval, exec, compile, or import loading.

## Sandbox and validation

The generated-tool sandbox launches the safe-subset worker in a separate isolated
Python process with isolated interpreter mode, a temporary working directory, a
minimal child environment, bounded I/O, a wall-clock timeout, and bounded evaluator.
Candidate syntax has no filesystem, networking, subprocess, credential, kernel,
registry, verifier, approval, or authority primitive.

This is capability isolation by construction rather than a claim of an OS virtual
machine or network namespace. Arbitrary third-party Python or native binaries are
outside this M15 path and are rejected.

Validation is bound to the immutable artifact digest and requires static inspection,
structural type-contract validation, linting, dependency policy, varied sandbox cases,
resource limits, and unchanged Trusted Kernel fingerprints. At least three distinct
successful cases are required before promotion evidence can pass.

## Independent verification and promotion

An acquired capability cannot verify itself. The trusted wrapper recomputes the
sandbox transcript digest and independently checks the declared output contract.
Candidate claims such as SUCCESS, verified, risk=R0, or promote_self are inert text.

A validated artifact produces a human review package. Promotion requires a host-side
installation approval authenticated with an HMAC key never exposed to candidates.
The approval binds the exact artifact and complete validation-evidence digest.

The trusted manager rejects forged approval, evidence for different bytes, review
mismatch, Trusted Kernel changes, and exact-version identity collisions. Only then
is the generated capability registered in the canonical CapabilityRegistry.

## Versioning, degradation, rollback, restart

Registration remains exact-versioned. Promoting a newer version retires the previous
active version. Repeated verification failures degrade and then revoke the bad
version; its activation guard prevents further use and the latest eligible retired
version becomes the rollback target.

Persistent state is HMAC authenticated and records source, proposal, provenance,
dependencies, artifact identity, validation cases, validation time, Trusted Kernel
fingerprint, lifecycle, health, and degradation counters. Restart fails closed on
MAC failure, artifact-byte changes, Trusted Kernel changes, or malformed evidence.

## Cross-milestone boundaries

- M9: untrusted source content never grants extension authority.
- M11: voice input has no direct trusted promotion path.
- M12: scheduler/event inputs have no direct promotion path; denials are not gaps.
- M13: device orchestration has no direct promotion path; generated tools cannot
  request ADB/device/host execution authority.
- M14: optimization has no promotion API and cannot lower M15 thresholds.
- M10 World Model: platform scope is applicability data, never authority.
- Hive: provenance and lifecycle evidence are data; they do not authenticate approval.

Architecture tests enforce that M11/M12/M13/M14 and untrusted research modules do
not directly import the trusted M15 promotion boundary.

## Trusted Kernel immutability

Validation fingerprints the canonical agentx.kernel source set before and after
candidate execution, and promotion requires the bound fingerprint to remain unchanged.
Adversarial tests cover eval/exec/import attempts, filesystem traversal, environment
mutation, networking/subprocess attempts, introspection escape, forged approval,
dependency substitution, persistence tampering, and risk/permission escalation.

## End-to-end acceptance

Positive acceptance covers candidate creation, provenance, validation, review,
authenticated approval, versioned registration, canonical Executor execution,
independent verification, active reuse, and revocation.

Negative acceptance covers a malicious candidate failing security validation with no
review, no approval, no active registry entry, and an unchanged Trusted Kernel.

Final repository acceptance is C1.01, which runs Ruff, format checking, mypy, ledger
validation, real Windows host acceptance, real M7 browser acceptance, and full pytest.
