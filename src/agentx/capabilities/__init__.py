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

C8.01 adds the provider-neutral device abstraction/protocol boundary in
``agentx.capabilities.device``. It represents a device as inert, immutable,
provider-neutral data: identity (``DeviceId``), type/form factor and host
platform (``DeviceKind``/``DevicePlatform``), explicit device-protocol version
(``DeviceProtocolVersion``), environment/device scope (``DeviceScope``),
capability references (``DeviceCapabilityRef`` over canonical ABI identities),
observation metadata (``DeviceObservation``) with availability/connectivity,
last-seen and derived freshness (``evaluate_device_freshness``), and an explicit
offline/unavailable state. It validates every identifier, version, and closed
state, performs no pairing, Android-companion/accessibility, remote capability
execution, cross-device routing, shared Hive, cross-device verification, or
network transport, and treats device metadata strictly as data: a remote device
claiming ``ADMIN`` never grants itself any permission.
"""
