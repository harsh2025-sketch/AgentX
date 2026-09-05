# A6.08 — Human-in-the-Loop Approval Protocol

A6.08 defines **data contracts only** for recording an explicit human approval or denial concerning one governed capability request.

It is not an approval UI, authentication mechanism, natural-language parser, notification service, permission manager, ActionGate replacement, execution path, audit database, or persistence layer.

## Decision vocabulary

The closed `HumanApprovalOutcome` vocabulary is intentionally minimal:

- `APPROVED` — the human explicitly approved this exact approval request.
- `DENIED` — the human explicitly denied this exact approval request.

There is no `EXPIRED`, `CANCELLED`, or `ABSTAINED` state in A6.08 because this contract owns no temporal lifecycle, timer, cancellation workflow, or undecided-state machine. A request without a `HumanApprovalDecision` simply has no represented decision at this boundary.

Unknown values fail closed. Free-form text is never converted into a decision.

## Exact request binding

`HumanApprovalRequest` binds approval evidence to all of the following:

- a fresh opaque `HumanApprovalRequestId`, identifying this one approval-request instance;
- canonical `TaskId`;
- canonical `ExecutionContext.correlation_id`;
- canonical `GateRequest`, including operation, required `Permission`, and the complete `RiskAssessment`;
- canonical `CapabilityIdentity` (name + version);
- deterministic canonical JSON for the exact typed `CapabilityParams` payload.

The request factory requires the `ExecutionContext` to carry the same `TaskId` being approved. It retains only stable task/correlation identity; mutable cancellation/deadline state is not copied into the approval evidence.

No cryptographic fingerprint is invented. The complete canonical binding fields are retained directly, which keeps the evidence inspectable and avoids treating an opaque digest as authority.

The fresh request id also means two otherwise identical requests are distinct approval instances. Replaying a decision against a new request fails unless the complete immutable request evidence—including request id—matches exactly.

`HumanApprovalDecision.validate_binding()` therefore fails closed when any material bound field differs: task, correlation, capability identity/version, operation, permission/risk request, parameters, or request instance.

## Authority boundary

`APPROVED` is **evidence**, not authority. It does not:

- create or strengthen an `AuthorityContext`;
- grant any `Permission`;
- bypass or replace `ActionGate`;
- turn `REQUIRE_CONFIRMATION` into `ALLOW`;
- lower `RiskLevel` or effective risk;
- widen or reset a `ResourceEnvelope`;
- clear `EmergencyStop`;
- authorize destructive actions, research, network access, or arbitrary code;
- execute a `Capability`;
- transition a `Task` or mark success;
- fabricate or suppress verification;
- activate a `Procedure`;
- promote `Knowledge`;
- invoke models or mutate Hive state.

`ActionGate` remains authoritative and is unchanged by A6.08. A future policy/orchestration owner may evaluate bounded approval evidence alongside the canonical gate result, authority context, risk, budget, stop state, and other required policy inputs. A6.08 itself performs no such authorization.

## Explicit human source only

A6.08 performs no natural-language or provenance inference. Construction of `HumanApprovalDecision` requires a typed `HumanApprovalOutcome`; arbitrary strings are rejected rather than interpreted.

Consequently, model output, webpage text, research results, procedure metadata, environment variables, task metadata, or a heuristic extraction such as “the user said yes” are not proof of approval at this boundary. Later trusted human-control/UI code is responsible for obtaining an explicit human choice before constructing decision evidence.

This contract models the evidence; it does not authenticate the human. Authentication, biometrics, voice confirmation, mobile confirmation, and identity proofing are explicit non-goals.

## Human operating modes

A6.07 `HumanOperatingMode` does not appear in the production approval contract because mode is not necessary to identify or authorize an approval request.

`NORMAL`, `LEARN`, `TEACH`, and `DEBUG` therefore cannot change approval semantics:

- `DEBUG` does not auto-approve or disable security;
- `TEACH` does not auto-approve or make demonstrated material trusted;
- `LEARN` does not auto-approve or promote learning material;
- `NORMAL` does not bypass confirmation.

If later orchestration carries a mode alongside approval evidence, it remains contextual data only.

## Audit and persistence

A6.08 preserves request id, task id, correlation id, capability identity, operation/permission/risk facts, parameters, and the explicit decision so a future audit layer can answer what was requested, what the human decided, and which exact operation the decision concerned.

A6.08 does **not** write an audit record or create an approval database. There is no migration, cache, singleton, background service, timer, or durable approval state. Existing/future audit and runtime owners decide how evidence is recorded and evaluated.

This separation is intentional: durable recording remains an audit/runtime responsibility, not approval authority.

## Serialization

`HumanApprovalRequest.to_json()` and `HumanApprovalDecision.to_json()` return deterministic JSON using sorted keys, compact separators, ASCII escaping, finite JSON numbers, canonical UUID strings, canonical permission/risk/capability values, and the canonicalized parameter object.

The serialized form is transport/audit data only. Deserializing and trusting persisted approval evidence is not implemented by A6.08.
