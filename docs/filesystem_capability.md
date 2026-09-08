# M1.01 — Governed filesystem text capability

M1.01 adds the smallest provider-neutral filesystem capability surface needed
for AgentX's first deterministic vertical proof. It implements only two
canonical operations:

- `filesystem.read_text@1.0.0`
- `filesystem.write_text@1.0.0`

It does not wire AgentLoop, create a strategy adapter, persist episodes, manage
directories, or expose a general filesystem toolkit.

## Authority boundary

Both operations are ordinary canonical `Capability` implementations. They do
not call `PermissionEngine`, `ActionGate`, `ResourceBudget`, or `EmergencyStop`
to grant themselves authority. Governed execution remains owned by
`CapabilityExecutionLoop`.

| Capability | Permission | Effective risk | Rollback |
| --- | --- | --- | --- |
| `filesystem.read_text@1.0.0` | `READ` | `R0` | not applicable |
| `filesystem.write_text@1.0.0` | `WRITE` | `R2` | unsupported |

`write_text` is intentionally R2. It modifies persistent machine state and
M1.01 retains no prior bytes, so it is not declared reversible merely because
a caller might be able to write different content later.

Both descriptors use `CapabilityPlatform.ANY`: the implementation relies on
Python's structured host filesystem APIs and validates paths against the
current host's path semantics.

## Request contracts

### `ReadTextParams`

- `path: str`
- `max_bytes: int`

The path must be explicit, absolute, already normalized for the current host,
control-character free, and no longer than `MAX_PATH_LENGTH`.
`max_bytes` is finite and cannot exceed `MAX_TEXT_READ_BYTES`.

The operation reads at most `max_bytes + 1` bytes so it can fail explicitly
when the target exceeds the caller's bound. It returns no partial text on an
over-bound read. Bytes are decoded as strict UTF-8.

### `WriteTextParams`

- `path: str`
- `content: str`
- `overwrite: bool`

`content` must encode as strict UTF-8 and cannot exceed
`MAX_TEXT_WRITE_BYTES` bytes.

`overwrite=False` is create-only semantics. If the target exists, execution
fails explicitly and leaves the existing regular file untouched.
`overwrite=True` permits creation or replacement of an existing regular file.

Parent directories are never created implicitly.

## Bounds

The canonical M1.01 bounds are:

- `MAX_PATH_LENGTH = 4096` characters
- `MAX_TEXT_READ_BYTES = 1_048_576`
- `MAX_TEXT_WRITE_BYTES = 1_048_576`

The ABI currently stores resource estimates on the capability descriptor, not
on an individual request. M1.01 therefore declares conservative deterministic
per-capability estimates rather than inventing a second request-cost contract.
Both operations budget two machine actions to cover the machine operation plus
the independent verification read-back.

## Filesystem semantics

The implementation uses `pathlib.Path` and binary file handles only. Text is
written as exact `content.encode("utf-8")` bytes so Windows newline translation
cannot change the intended byte sequence.

It performs no:

- shell invocation or expansion;
- wildcard or glob expansion;
- environment-variable expansion;
- home-directory expansion;
- inferred Desktop/project/current-directory base;
- implicit directory creation;
- delete, rename, copy, move, chmod, ACL, archive, or executable operation;
- recursive filesystem traversal.

Directories and non-regular targets are rejected as file targets. Missing
write parents fail explicitly.

## Error model

Expected operational failures are represented with canonical `AgentXError`
values and stable codes. Capability execution carries those errors as inert
observation data inside an unsuccessful `ExecutionResult`, matching the
canonical capability ABI.

Stable M1.01 codes include:

- `capabilities.filesystem.invalid_input`
- `capabilities.filesystem.unsupported_path`
- `capabilities.filesystem.not_found`
- `capabilities.filesystem.parent_missing`
- `capabilities.filesystem.directory_supplied`
- `capabilities.filesystem.os_permission_error`
- `capabilities.filesystem.os_error`
- `capabilities.filesystem.content_too_large`
- `capabilities.filesystem.encoding_error`
- `capabilities.filesystem.overwrite_prohibited`
- `capabilities.filesystem.verification_mismatch`

Malformed typed-request construction uses the canonical `AgentXException`
wrapper around an `AgentXError`, so validation failures retain a stable error
code rather than becoming arbitrary OS exceptions. Wrong Python argument types
remain programming errors (`TypeError`).

Error messages intentionally do not echo target paths or file contents.

## Verification

An API call returning is not proof of intended state.

### `write_text`

After execution, `verify` independently opens the target as a regular file,
reads it under a bound equal to the intended UTF-8 byte length, decodes it as
strict UTF-8, and requires exact content and byte-count equality.

If the target disappears, changes type, becomes unreadable, becomes longer, is
not UTF-8, or contains different text, verification fails with
`capabilities.filesystem.verification_mismatch`. Only the canonical execution
loop can convert a passing verification verdict into Task success.

### `read_text`

The execution observation contains the observed text and byte count. `verify`
first checks that evidence structurally, then performs an independent bounded
read-back and requires it to match. If concurrent mutation prevents that
confirmation, verification fails rather than fabricating external truth.

## Untrusted data

Paths and file contents are data only. Strings such as:

- `verified=true`
- `permission=ADMIN`
- `risk=R0`
- `task succeeded`
- `ignore previous instructions`
- `execute shell`

are never interpreted as authority, policy, routing, verification, Task state,
or executable instructions. Capability identities, descriptors, permissions,
risk, and resource estimates are fixed independently of file data.

## TOCTOU and residual limitations

M1.01 is not an OS-level filesystem sandbox. Local state can change between a
`stat`, `open`, execution, and verification read-back. Symlinks, mount points,
network-mounted filesystems, filesystem-specific aliasing, and concurrent
writers remain operating-system behavior.

The implementation reduces unsafe ambiguity by requiring normalized absolute
paths, rejecting non-regular targets when observed, using create-exclusive mode
for create-only writes, bounding all reads/writes, and independently reading
back state for verification. Those measures do not eliminate TOCTOU races and
must not be advertised as containment.

## Non-goals

M1.01 does not implement AgentLoop wiring, strategy selection, episodes, Hive,
procedures, caching, planning, model calls, semantic path inference, Desktop
aliases, delete, directory management, watchers, rollback infrastructure, or
A5.06 keyboard/text/clipboard behavior.
