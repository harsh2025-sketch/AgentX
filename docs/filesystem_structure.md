# Governed filesystem structural operations (M7.05)

> **Status:** M7.05. This task implements the smallest production-quality
> **structural** filesystem foundation: reading bounded metadata about a path,
> listing a directory, creating a single directory, and moving a path — each as
> a governed, independently-verified canonical capability. It does **not**
> read or write file *contents* (that is Worker-01's separate
> `agentx.capabilities.filesystem` surface) and performs **no delete** and **no
> recursive** traversal or cleanup.

`agentx.capabilities.filesystem_structure` provides:

| Piece | Contract |
| ----- | -------- |
| Typed result model | `PathKind`, `PathStat`, `DirectoryEntry`, `DirectoryListing`, `CreateDirectoryResult`, `MoveResult` |
| Deterministic operations | `stat_path`, `list_directory`, `create_directory`, `move_path` — each `-> Result[T, AgentXError]` |
| Capability Fabric compatibility | `StatPathCapability` (`filesystem.structure.stat@1.0.0`), `ListDirectoryCapability` (`filesystem.structure.list@1.0.0`), `CreateDirectoryCapability` (`filesystem.structure.create_directory@1.0.0`), `MovePathCapability` (`filesystem.structure.move@1.0.0`) |

Each operation is a separate canonical capability so that read and mutation
operations carry honest, distinct permission and risk declarations (see
[Risk / permission mapping](#risk--permission-mapping)) instead of being forced
under one lowest-common-denominator descriptor.

## Why separate from text read/write

Filesystem *structure* (does it exist, what kind is it, what are the bounded
immediate children, create a directory, move a path) and filesystem *content*
(reading and writing the bytes/text of a file) are different capabilities with
different risk profiles. M7.05 owns only the former. It never imports,
depends on, or edits Worker-01's `agentx.capabilities.filesystem` surface, so
the two tasks can land independently.

## Scope

Implemented operations:

1. `stat_path` — bounded `lstat` metadata: existence, kind (file / directory /
   symlink / other), size for regular files. Never reads file content, never
   follows a final symlink target.
2. `list_directory` — bounded, non-recursive, deterministically ordered
   (by entry name) listing of one directory's immediate entries, each with
   minimal structured metadata. Entry names are untrusted data.
3. `create_directory` — creates exactly one directory (no `parents=True`
   equivalent); parent must exist; an existing target is an explicit conflict.
4. `move_path` — moves one explicit path to another; no wildcard/pattern;
   fails closed on any destination conflict unless the caller explicitly
   requests a supported file-over-file overwrite.

Deliberate non-scope (never implemented here):

- no file-content read or write;
- no delete of files or directories;
- no recursive copy and no recursive traversal;
- no watch, index, search, backup, or rollback orchestration;
- no shell, no `subprocess`, no dynamic code, no module import from a target;
- no Worker-01 text surface dependency.

## Path rules

Every operation requires an explicit **absolute** path. Rejected up front
(`validation` error):

- relative paths (including `..\..`-style relative traversal);
- embedded NUL;
- empty path strings;
- unbounded path strings (longer than `MAX_PATH_CHARS` = 4096).

Paths are **not** shell-expanded and contain **no** environment-variable
expansion, wildcard expansion, or glob interpretation; `~` is never
reinterpreted. `..` / `.` components in an already-absolute path are handled
only by the documented `os.path` semantics of the host platform; this module
never hand-collapses or reinterprets path components, so a `..` cannot be used
to bypass the fail-closed destination rules (an existing target reached via
`..` is still a `destination_exists` conflict unless overwrite is explicit).

## Symbolic-link / reparse-point handling

- **Reads** (`stat`/`list`) use `lstat` semantics and never follow a final
  symlink target, so a link is reported as `kind=symlink` and its target is
  never silently read or resolved.
- **Mutations** never write *through* a final symlink:
  - `create_directory` refuses when the target already exists, including when
    it is a symlink;
  - `move_path` refuses an overwrite whose source or destination is not a
    regular file (a directory destination, a symlink destination, a
    non-file source are all refused), so a move never dereferences a link to
    reach a different location.
- Ancestor directories are resolved by the OS according to documented platform
  path semantics; this module does not expand them and grants no permission
  based on what any ancestor resolves to. Fail-closed link handling applies at
  the final mutation target, which is the link that could otherwise redirect
  an operation.

## Conflict behaviour

- `create_directory`: any existing target (file, directory, or symlink) is an
  explicit `conflict`; nothing is overwritten or merged. Single-directory
  creation only — a missing parent is `not_found`, never implicitly created.
- `move_path`:
  - default (`overwrite=False`): any existing destination is an explicit
    `destination_exists` conflict and nothing changes;
  - `overwrite=True`: only one supported replacement is permitted — moving a
    regular file over an existing regular file. Everything else (directory
    merge, overwrite through a symlink, directory-over-file) is refused;
  - source and destination are never the same path (`source_destination_alias`
    error, checked by resolved-path identity);
  - cross-volume moves are reported as an explicit `cross_device_move_unsupported`
    error and **never** degrade to an unverified copy+delete fallback.

## Bounds

- `MAX_PATH_CHARS = 4096` — hard path length ceiling.
- `DEFAULT_MAX_ENTRIES = 100`, `HARD_MAX_ENTRIES = 1000` — a single listing
  returns at most the requested/effective bound, never more. `total_entries`
  reports how many immediate entries the directory contained and `truncated`
  reports whether the result was truncated.
- One mutation per request; no hidden retries; no recursion; no hidden
  traversal.

## Risk / permission mapping

| Operation | Required permission | Risk | Rationale |
| --------- | ------------------- | ---- | --------- |
| `stat_path` | `READ` | R0 (read-only) | pure bounded observation |
| `list_directory` | `READ` | R0 (read-only) | pure bounded observation |
| `create_directory` | `WRITE` | R2 (modify) | persistent state change, not reversible by this capability |
| `move_path` | `WRITE` | R2 (modify) | persistent state change, not reversible by this capability |

Mutations are deliberately **not** classified R0/R1 just because they "look
simple": they change external filesystem state and always require explicit
`WRITE` authority. They are R2 (MODIFY) under canonical C1.07 policy rather
than R3, because a local filesystem mutation does not itself claim a
cross-machine external effect, and R3 would force a separate confirmation flow
that the governed `CapabilityExecutionLoop` does not perform — preventing any
governed structural mutation from ever being verified. Descriptor text and
hostile on-disk names can never change these permissions or lower this risk.

## Verification

`CapabilityExecutionLoop` remains authoritative. Each capability's `verify`
never trusts its own `execute` result:

- **Mutations** (`create_directory`, `move_path`) are verified by an
  **independent fresh `lstat`** after execution:
  - `create_directory`: the target exists and is a directory;
  - `move_path`: the source is absent and the destination exists.
- **Reads**:
  - `stat_path` is cross-checked with an independent fresh `lstat`
    (existence/kind/size agreement);
  - `list_directory` is checked structurally (path/limit match the request,
    entries are bounded, `truncated` is consistent, ordering is sorted) —
    it is an observation and never re-lists to avoid racing directory churn.

A mutation (or read) is reported as Task success only when verification passes;
a failed verification produces `LoopOutcome.VERIFICATION_FAILED` and never
`TaskStatus.SUCCEEDED`.

## Errors

Structured `AgentXError` values (never raised for expected outcomes):

- invalid path / unsupported path form / invalid `max_entries` → `validation`;
- not found / parent not found → `not_found`;
- destination exists / source-destination alias → `conflict`;
- wrong path kind / parent wrong kind → `precondition`;
- permission denied → `permission`;
- cross-device move unsupported, I/O failure → `resource` / `execution`;
- verification failure → `verification`.

## Owner

M7.05. Belongs to `agentx.capabilities`; imports only the standard library and
the canonical `agentx.core` / `agentx.kernel` contracts plus the canonical
capability ABI. No registry wiring, no shell, no `subprocess`, no dynamic
imports, no Worker-01 dependency, and (as of v1.0.0) no `copy_path` — copying
is deliberately omitted rather than shipping weak, unverifiable copy behaviour.
