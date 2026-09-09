# N2.21 — Governed Windows application launch V2

The capability accepts an explicit executable path, a bounded tuple of argv
entries, and an optional bounded working directory. It never accepts a command
string and never invokes a shell, `cmd.exe`, PowerShell, or script parser.
Environment mutation is intentionally out of scope.

`LaunchAdapter` is the narrow injected native port. Its result is only native
launch evidence (including an optional validated PID); it is not readiness,
health, window appearance, or user-intent verification. `verify` therefore
returns an explicit unsupported/unverified verdict. Authorization is owned by
the canonical execution loop: the descriptor requires `EXTERNAL_EFFECT` and
classifies launch as R3 external effect. The capability does not call the
ActionGate, grant permission, persist state, or terminate processes.
