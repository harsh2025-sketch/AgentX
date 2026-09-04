# C1.09 security boundaries

C1.09 establishes three small Trusted Kernel contracts: descriptive security audit records, opaque
secret references/resolved values, and a process-local emergency-stop signal. These contracts do
not add runtime orchestration or persistence.

## Audit contract

`SecurityAuditRecord` is an immutable record of one security-relevant fact. It carries a UUID
identity, timezone-aware timestamp, operation identifier, controlled `AuditOutcome`, reason,
optional `TaskId` and correlation UUID, optional `RiskLevel`, and an optional typed `AuditContext`.

`AuditContext` deliberately is not an arbitrary metadata dictionary. It can describe only an actor,
target, `Permission`, or opaque `SecretRef`. These fields are historical labels. A recorded
permission or ALLOW outcome does not create an `AuthorityContext`, grant permission, or authorize an
action.

C1.09 does not persist audit records. C2.04 owns future Artifact/Audit stores and their durability
semantics.

## Secrets boundary

Ordinary AgentX code should carry `SecretRef` identifiers rather than raw secret material. A trusted
provider/composition implementation may satisfy the `SecretResolver` protocol and return a
`SecretValue`.

`SecretValue` intentionally redacts `repr()`, `str()`, formatting, and container representations.
Normal JSON serialization fails, and pickle serialization is explicitly rejected. Content equality
is not provided: two wrappers holding identical material do not compare equal, and `SecretValue` is
unhashable.

`SecretValue.reveal()` is deliberately explicit and is intended only at the trusted point where a
provider or API must consume the material. C1.09 provides no cloud vault, Windows Credential
Manager provider, plaintext storage, SQLite storage, encryption framework, or key-management
scheme.

Python cannot guarantee secure memory zeroization or prevent trusted code that explicitly calls
`reveal()` from copying the returned string/bytes. The boundary therefore prevents accidental
ordinary representation/serialization and makes deliberate exposure visible in code; it does not
claim memory-erasure guarantees.

Audit constructors accept ordinary validated text and typed `SecretRef` values, not `SecretValue`
objects. This blocks the supported secret-wrapper path from accidentally becoming audit data.

## Emergency stop

`EmergencyStop` is a process-local, thread-safe monotonic signal with states `RUNNING` and
`STOP_REQUESTED`. `request_stop()` is idempotent and observation through `state` or
`stop_requested` is cheap.

The ordinary API intentionally exposes no `reset()`, `clear()`, or `resume()`. Trusted
restart/reinitialization re-arms v1 by constructing a new `EmergencyStop` instance. The contract
does not terminate the process, kill threads or subprocesses, close applications, publish events,
or perform rollback.

Task priority, model text, metadata, historical events, and audit records have no API by which to
clear a requested stop.

## Composition boundaries

C1.07 ActionGate remains unchanged. An emergency stop is a separate future execution prerequisite,
not another permission or risk level. C1.08 budget exhaustion and A1.07 cancellation/timeouts are
also separate signals with different scopes and must be composed later.

The C1.09 kernel modules do not import `agentx.infrastructure`, `SQLiteDatabase`, `EventJournal`,
`EventBus`, or `sqlite3`, and they introduce no runtime dependency.
