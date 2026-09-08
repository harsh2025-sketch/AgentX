# M7.04 — Governed Windows application launch

Baseline: `03a1221771ba4bcf038dca1d1432e9e8526b9550`.

## Scope and composition

`WindowsApplicationLaunchCapability` implements the canonical ABI with identity
`windows.application.launch_application@1.0.0`. It creates exactly one explicitly
selected process. It does not implement a shell-command operation, application
name lookup, PATH search, Start-menu scanning, file associations, elevation,
termination, process supervision, window management, or keyboard/mouse input.

The composition root constructs the capability with the provider's canonical
`WindowsSupport`, optionally contributes it through `WindowsProvider`, and registers
it in `CapabilityRegistry`. Registration grants nothing. Production callers must
submit its request to **CapabilityExecutionLoop**, the sole trusted execution
authority. There is no auto-registration, new registry, alternative execution loop,
permission grant, or Task mutation here.

```python
from agentx.capabilities.windows.application_launch import (
    WindowsApplicationLaunchCapability,
    WindowsApplicationLaunchParams,
    launch_application_request,
)
from agentx.capabilities.windows.provider import (
    detect_platform_facts,
    evaluate_windows_support,
)

capability = WindowsApplicationLaunchCapability(evaluate_windows_support(detect_platform_facts()))
# At the existing trusted composition root: registry.register(capability)
request = launch_application_request(
    WindowsApplicationLaunchParams(
        executable=r"C:\Program Files\Example\Example.exe",
        arguments=("--open", r"C:\Data\example.txt"),
        working_directory=r"C:\Data",
    )
)
# Use the already-wired canonical loop: loop.run(task, request, context).
# Constructing this request does not launch anything.
```

## Parameter contract

Frozen `WindowsApplicationLaunchParams` has exactly three fields:

| Field | Contract |
|---|---|
| `executable` | Required nonempty local drive-absolute Windows `.exe` path, at most 259 UTF-16 units. No PATH or executable identity resolver. |
| `arguments` | Tuple or list of strings, defensively copied to a tuple. Default empty. At most 128 elements, each at most 4096 UTF-16 units. Empty elements and Unicode are supported. |
| `working_directory` | Optional local drive-absolute Windows directory path, at most 259 UTF-16 units. Defaults to the executable's parent directory, not AgentX's current directory. A drive root is valid. |

The encoded command line is limited to **32766 UTF-16 units**, excluding its NUL
terminator. Lengths are checked before unbounded encoding; the sequence count is
checked before copying. NULs and unpaired surrogates are rejected. Paths additionally
reject relative/drive-relative forms, forward slashes, UNC/device/extended namespaces,
ADS colons, wildcards, quotes, control characters, empty/dot/parent components,
trailing spaces/dots, and reserved DOS device components. Paths are validated before
normalization. No existence check races are treated as authority: the native creation
call reports missing files, access denial, and invalid cwd where Windows distinguishes
them.

There are **no environment overrides** and no environment mutation. The child inherits
the existing process environment through the native default (`lpEnvironment=NULL`).
This is not an environment sandbox; secrets in the parent's environment may be
inherited. Arguments/environment contents are not included in execution observations
or native errors. `params.to_dict()` is an explicit parameter serialization API and
includes arguments; callers must treat those parameters as potentially sensitive.

Known shell/script/loader host basenames are explicitly refused, case-insensitively:
`cmd.exe`, `powershell.exe`, `powershell_ise.exe`, `pwsh.exe`, `wscript.exe`,
`cscript.exe`, `mshta.exe`, `rundll32.exe`, `regsvr32.exe`, `wsl.exe`, `bash.exe`,
and `sh.exe`. Batch/script extensions are not executable inputs.

This filename policy is **not** executable authentication or a comprehensive
interpreter allowlist. Renamed executables, short-path aliases, custom interpreters,
application-specific command options, junctions, and executable replacement are not
sandboxed or authenticated. A lookalike filename stays the exact requested filename;
it is never resolved to a familiar application. The caller must select a trusted
application and arguments suitable for local startup. Do not use this capability to
smuggle external-effect/destructive workflows under a local-startup descriptor.

## Native API and verification seam

The internal `_NativeLaunchSurface` is injected only by trusted composition/tests:

- `create(params) -> Result[_ProcessInstance, AgentXError]`
- `inspect(process_id) -> Result[_ProcessState, AgentXError]`

The production `_Win32LaunchSurface` uses lazy stdlib `ctypes` and explicitly declared
Win32 signatures. `CreateProcessW` receives **non-null `lpApplicationName`** separately
from a mutable command-line buffer. Each argv element is CRT-quoted (including empty
arguments and trailing backslashes). This is Windows argv encoding, not a shell DSL:
there is no shell interpolation, pipeline parsing, executable-name extraction, or
shell dispatch. Applications with non-CRT parsers still control their own argument
interpretation. Creation flags are zero, security attributes are null, and handle
inheritance is disabled. No UAC/elevation flow is requested. The process uses the
caller's existing token; an already-elevated AgentX process is not de-elevated here.

Execution records the returned PID and `GetProcessTimes` creation FILETIME before
closing the process/thread handles. A per-capability HMAC receipt binds that evidence
to the complete parameters and canonical task/correlation identity, without retaining
a receipt cache or handles. Receipt provenance is **not authority or verification**;
model-supplied `verified=true`, permission/risk text, copied PIDs, modified receipts,
and receipts from another capability/request/context cannot replace the native check.
Receipt signing material is excluded from the capability's repr and results.

`verify` independently opens that PID with only
`PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE`, reads the image path and creation
time, and uses two nonblocking `WaitForSingleObject(..., 0)` checks around the queries.
Verification requires the same PID **and creation time**, a matching case-insensitive
Windows image path, both liveness checks passing, and no observed context stop.
Unavailable/mismatched identity, query failure, PID reuse, or immediate exit fails
closed. Untrusted extra observation metadata never supplies a success verdict.

Baseline discovery is not used for verification because its process identity lacks a
creation time and a liveness check. Matching a process name/PID in a discovery snapshot
would not prove the launched instance still exists. Its canonical provider support,
ABI observation/result contracts and lazy native conventions are reused instead;
no discovery file was changed.

A passing verdict means only: **the launched process instance was running with the
expected image when inspected**. It does not mean application ready, window visible,
document opened, authenticated, or a broader user goal achieved. The process can exit
immediately after inspection. Fast-exiting launchers, single-instance handoff apps,
and package redirectors may conservatively fail verification; no retries or fallback
launches occur. The canonical loop can mark success only for this narrow postcondition.

## Governance and resources

- Permission: canonical `Permission.EXECUTE`.
- Risk: canonical **R2**, from state-changing, not explicitly reversible, local startup
  characteristics. No parameter can lower this risk or alter the permission.
- Estimate: **one machine action, one second, zero external cost**. It includes the
  bounded verification queries as part of the invocation's advisory estimate.
- Rollback: explicitly **UNSUPPORTED**. No termination or reversal is attempted.

PermissionEngine, ActionGate, EmergencyStop and ResourceBudget are used only by the
canonical runtime. Denial, exhausted budget/risk ceiling, active emergency stop,
cancellation, and preflight deadline expiry prevent the native create call. The
capability also observes context stop immediately before launch and before/after
verification. Creation is an in-flight native call, not interruptible by this adapter;
there is no hard-timeout thread or background watcher. A mid-call stop does not
terminate the child or undo a launch. The baseline runtime reports a stop observed
during verify as `VERIFICATION_FAILED` / Task `FAILED`, whereas preflight context
stops produce Task `CANCELLED`. No new cancellation runtime is invented.

Nested ownership scopes attempt to close both returned handles on success and on
post-creation errors; every independently opened verification handle has its own
`finally` cleanup. Cleanup failure is a resource error. No native handle is retained
in observations, capability state or return values. No polling, monitor thread,
process lifetime management, or child resource accounting is implemented. The
estimate is not a child CPU/memory/lifetime quota. A failure after successful native
creation can leave the child running even if its evidence could not be collected;
errors are non-retryable to avoid duplicate launches.

## Platform and errors

Importing the module performs no native library loading or platform detection.
An unsupported provider returns canonical
`capabilities.windows.unsupported_platform` evidence without touching the injected
native surface. The real adapter also checks the actual host before any native call;
deterministic fake tests can exercise supported Windows behavior on Linux/macOS.

Malformed input is a canonical `CapabilityValidationError` (or `TypeError` for wrong
ABI params), with fixed reason codes: `invalid_executable_path`, `invalid_arguments`,
`invalid_working_directory`, `forbidden_shell_host`, and `invalid_launch_operation`.
No offending argument value is interpolated into a validation error.

Operational errors use canonical `AgentXError` in `Result`, serialized into a failed
`ExecutionResult` observation. Codes under `capabilities.windows.application_launch`
include `missing_executable`, `access_denied`, `invalid_working_directory`,
`native_creation_failed`, `resource_failure`, `verification_failed`, and
`context_stopped`. Native errors expose numeric Win32 codes, not OS messages or argv.
Verification returns canonical `VerificationResult`; failed verification becomes the
baseline runtime's `runtime.verification_failed`. Creation returning success alone is
never a verification verdict.

## Validation and required architectural integration

The four owned test modules contain 161 deterministic tests (78 unit, 14 integration,
53 adversarial, 16 architecture). They cover native ABI wiring and real adapter
cleanup with a fake kernel32, independently injected creation/inspection evidence,
PID reuse, immediate exit, hostile metadata, forged receipts, resource bounds,
canonical denial paths, cumulative budgets, and verified Task transitions.

Local checks with Python 3.12.11:

- Focused tests: **161 passed**; no real applications launched.
- Full pytest: **5762 passed, 2 skipped, 3 failed**.
- Ruff lint: passed. Format check: passed.
- Full mypy on Linux: **16 errors in 3 unchanged baseline Windows-native files**;
  the exact baseline produces the same 16 errors. Owned files type-check cleanly.
- Full mypy with `--platform win32` (the CI target): passed, 323 files.

**ARCHITECTURAL_SEAM_REQUIRED** before merge. The baseline predates mutating Windows
capabilities and enforces read-only native seams globally. Three unchanged tests
necessarily reject the requested native launch implementation:

1. `test_windows_process_discovery_boundaries.py::test_native_seams_are_the_only_win32_knowledge_sites`
   permits native markers only in `_native.py` / `_uia_native.py`.
2. `test_windows_provider_boundaries.py::test_windows_package_imports_no_native_or_automation_module[application_launch.py]`
   prohibits `ctypes` outside those same two files.
3. `test_windows_provider_boundaries.py::test_windows_package_mentions_no_mutating_win32_api[application_launch.py]`
   prohibits `CreateProcess` everywhere in the Windows package.

Those existing tests and both native seam modules are outside this task's ownership.
They have **not** been edited, skipped, monkeypatched by the new architecture tests,
or evaded with dynamic imports/constructed API names. The integration owner must
explicitly recognize a narrow mutating launch seam and update those guards (or
approve a dedicated native module and corresponding ownership) before merge. No
change to `_architecture.py` or subsystem edges is necessary; its canonical content
hash is checked by the new tests. This PR is intentionally not merge-ready while
those architecture checks fail. Windows CI status is recorded in the PR/handoff;
actual OS launch behavior has not been smoke-tested in this Linux workspace.

Only the six M7.04-owned files are added. There is no dependency on another worker,
no change to canonical contracts, and no generic shell-execution API.
