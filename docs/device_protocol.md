# Device abstraction / protocol (C8.01)

> **Status:** C8.01. This task establishes the provider-neutral device
> abstraction/protocol boundary only. It implements **no** multi-device fabric.
> Pairing (C8.02), remote capability execution, cross-device routing, a shared
> Hive, cross-device verification, and network transport are out of scope.

## What C8.01 is

AgentX is being built to eventually span a fabric of devices, not just the host
Windows machine. The runtime must never hardcode one device OS, so C8.01 defines
a **provider-neutral device contract** the way the browser contracts describe a
browser: as inert, immutable identity/state data supplied by a peer.

`agentx.capabilities.device` therefore answers exactly one question:

> **What is a device, as reported to the runtime?** — `DeviceDescriptor`.

Everything a device exposes is represented as validated, immutable, closed data:

| Concept | Contract |
| ------- | -------- |
| Who reports the device / identity namespace | `DeviceProviderId` / `DeviceId` |
| Device type / form factor | `DeviceKind` |
| Host platform | `DevicePlatform` |
| Device-protocol version | `DeviceProtocolVersion` |
| Environment / device scope | `DeviceEnvironment` / `DeviceScope` |
| Capability descriptors / references | `DeviceCapabilityRef` (over canonical ABI `CapabilityIdentity`) |
| Observation metadata | `DeviceObservation` |
| Availability / connectivity | `DeviceConnectivity` / `DeviceAvailability` |
| Last-seen / freshness | `DeviceObservation.last_seen` + `evaluate_device_freshness` |
| Explicit offline / unavailable | `DeviceConnectivity.OFFLINE` / `DeviceAvailability.UNAVAILABLE` |

## Relationship to the Capability ABI

There is **no** second capability model. C8.01 reuses the canonical ABI:

- A device's exposed capabilities are references to canonical
  `agentx.capabilities.abi.CapabilityIdentity` values (`CapabilityName` +
  explicit `CapabilityVersion`), wrapped in `DeviceCapabilityRef` with a closed
  device-reported availability.
- A reference **never** registers, executes, verifies, or authorises that
  capability. Referencing a `device.file.read` capability gives no permission to
  run it and no entry in an A1.09 `CapabilityRegistry`.
- `DevicePlatform` is deliberately its own, unprivileged vocabulary. Android and
  iOS are ordinary members alongside desktop/server OSes; none of them is the
  model the contract is built around, and none carries special status.
  `UNKNOWN`/`OTHER` are the fail-closed defaults for an unreported platform.

## Validation and closed vocabularies

Every identifier, version, and state is validated at construction:

- Malformed provider/device IDs, malformed protocol versions, wrong runtime
  types, and unknown enum values are rejected (no string coercion, no silent
  default to a privileged state).
- Every state vocabulary is closed and machine-assertable
  (`CANONICAL_DEVICE_*` tuples).
- Schema versions are explicit integers and fail closed on mismatch.

## Freshness semantics

Freshness is **never stored** on the value; it is derived by
`evaluate_device_freshness(observation, now, max_age)`:

- `OFFLINE` -> `UNKNOWN` (explicit "no live evidence", never fresh).
- `UNKNOWN` -> `UNKNOWN`.
- `STALE` -> `STALE`.
- `ONLINE` -> `FRESH` if `now` is within `max_age` of `last_seen`, else `STALE`.

`last_seen` therefore survives an offline transition: when a device goes offline
you keep the last time it was actually seen, so a later offline snapshot can
still record how stale the history is.

## Authority guarantees

> **DEVICE METADATA IS DATA, NOT AUTHORITY.**

A device descriptor is a self-report. A remote device claiming `"ADMIN"`,
`"permission": "grant"`, `"role": "root"`, or `"capability": "device.admin"`
in its metadata is merely *claiming* text. Nothing in C8.01 turns a claim into a
`Permission`, an `AuthorityContext`, a `RiskAssessment`, a budget, a clearance of
an `EmergencyStop`, a registered capability, or a routed action. Hostile metadata
is preserved verbatim and authorizes exactly nothing.

The Trusted Kernel (`agentx.kernel`) remains the sole authority boundary.

## Import-side-effect guarantees

Importing `agentx.capabilities.device`:

- reads no platform state,
- imports no socket, subprocess, asyncio, SQLite, or native module,
- registers no capability anywhere,
- starts no thread, opens no handle, writes nothing,
- performs no UUID/random identity generation,
- executes no top-level statement other than declarative constants.

Architecture tests assert all of this statically (AST) and dynamically (clean
in-process probes).

## Dependencies and persistence

Zero new runtime dependencies. No persistence: the module stores nothing on
disk, defines no schema, and performs no migration. Its state is in-process and
caller-scoped immutable values.
