"""Ownership boundary: ``agentx.capabilities``.

Canonical responsibility: governed machine/browser/device capability
implementations and contracts. Capabilities are provider abstractions and they
eventually execute only through governed runtime paths.

Status: A1.08 defines the canonical Capability ABI (interface contract only)
in ``agentx.capabilities.abi``, A1.09 adds the in-process Capability
Registry in ``agentx.capabilities.registry`` (registration and exact-identity
lookup only — discovery is never authority), and A1.10 adds the first
closed-loop deterministic execution path in
``agentx.capabilities.runtime`` (one governed, verified capability execution
per run: authority -> gate -> emergency stop -> budget -> execute ->
observation -> verify -> Task state -> canonical events/audit), and A2.04 adds
the narrow Executor boundary in ``agentx.capabilities.executor`` (a typed
``ExecutorRequest`` delegated to the canonical A1.10 loop — it adds no
authority, no verification, no retry, and no routing). A2.05 adds the narrow
Verifier boundary in ``agentx.capabilities.verifier`` (evaluates an
already-produced canonical A1.10 outcome against an explicit deterministic
verification requirement — it never invokes ``Capability.verify``, never
manufactures a ``VerificationResult``, never rewrites a ``ClosedLoopOutcome``,
and fails closed on missing evidence). There is still no Task Manager, Router,
agent loop, or concrete production capability implementation in this package;
demo capabilities live under ``tests/`` only.

A5.01 adds the Windows provider boundary in
``agentx.capabilities.windows`` (deterministic provider identity, explicit
platform facts/support evaluation, and contribution of already-constructed
Windows-scoped capabilities into a caller-owned A1.09 registry). A5.02 adds the
first concrete Windows capability there: read-only process/application
discovery with typed identities, explicit metadata-availability semantics,
deterministic normalization, and an isolated lazy-``ctypes`` native seam. It
performs no import-time platform detection or registration, keeps discovery
strictly read-only, and treats discovered metadata as untrusted data that never
becomes authority.

C5.01 adds the provider-neutral browser-provider boundary in
``agentx.capabilities.browser_provider``. C5.02 adds inert provider-associated
browser connection/session and target references in
``agentx.capabilities.browser_connection``. These are immutable identity/state
snapshots only: they establish no network connection, browser liveness,
execution authority, DOM access, navigation, or verification behavior.

C5.03 adds the provider-neutral read-only structured browser observation
boundary in ``agentx.capabilities.browser_dom``. DOM observations and node
references are immutable target-scoped snapshots of untrusted webpage data;
they add no browser mutation surface, liveness authority, permission,
verification, persistence, or execution path.

C5.04 adds deterministic exact-fact selection over already-observed C5.03 DOM
snapshot data in ``agentx.capabilities.browser_selection``. Selection exposes
only explicit no-match, unique, or ambiguous cardinality results and never
performs browser access, tie-breaking policy, fuzzy/model matching, authority,
verification, persistence, or browser action.

A9.01 adds deterministic missing-capability detection in
``agentx.capabilities.capability_gap``. It classifies explicit structured task
requirements against explicitly supplied capability descriptors, procedure
alternatives, and environment facts as AVAILABLE / MISSING / INCOMPATIBLE /
UNAVAILABLE / INSUFFICIENT_INFORMATION. It detects gaps only: it never searches,
discovers an SDK, generates an adapter or code, installs a dependency, registers
a capability, mutates a registry, or touches the Trusted Kernel. Crucially,
DENIED != MISSING - permission denial, risk restriction, emergency stop, and
budget exhaustion are governance restrictions that can never be reported as a
missing capability, so self-extension can never route around policy.
"""
